"""
WebSocket Handler para 200 Millas
Maneja conexiones en tiempo real para notificaciones de pedidos
"""
import os
import json
import boto3
import uuid
from shared.dynamodb import DynamoDBService
from shared.logger import get_logger
from shared.utils import current_timestamp

logger = get_logger(__name__)

# ============================================================================
# CLIENTES DYNAMODB
# ============================================================================
connections_db = DynamoDBService(os.environ.get('WEBSOCKET_CONNECTIONS_TABLE'))
subscriptions_db = DynamoDBService(os.environ.get('WEBSOCKET_SUBSCRIPTIONS_TABLE'))

# API Gateway Management API para enviar mensajes a WebSocket
apigateway = boto3.client('apigatewaymanagementapi')

# Obtener el endpoint de WebSocket
def get_websocket_endpoint():
    """Obtiene el endpoint de API Gateway WebSocket"""
    # Se pasa en tiempo de ejecución en los parámetros del evento
    # En local: http://localhost:3001
    # En prod: https://xxxxx.execute-api.region.amazonaws.com/stage
    return os.environ.get('WEBSOCKET_ENDPOINT', '')

# ============================================================================
# HANDLERS PRINCIPALES
# ============================================================================

def connect(event, context):
    """
    Lambda ejecutada cuando cliente abre conexión WebSocket
    
    Event contiene:
    - requestContext.connectionId: ID único de la conexión
    - queryStringParameters.token: JWT del usuario
    """
    try:
        connection_id = event['requestContext']['connectionId']
        logger.info(f"WebSocket Connect: {connection_id}")
        
        # Extraer token de query parameters
        query_params = event.get('queryStringParameters') or {}
        token = query_params.get('token', '')
        
        # Extraer información del usuario del token (simplificado)
        # En producción, verificarías el token
        user_id = query_params.get('user_id', f'user_{uuid.uuid4()}')
        user_type = query_params.get('user_type', 'customer')  # customer, chef, driver, admin
        
        timestamp = current_timestamp()
        expires_at = timestamp + (86400 * 7)  # 7 días de TTL
        
        # Guardar conexión en DynamoDB
        connection_data = {
            'connection_id': connection_id,
            'user_id': user_id,
            'user_type': user_type,
            'connected_at': timestamp,
            'expires_at': expires_at,  # Para TTL
            'subscribed_orders': []
        }
        
        connections_db.put_item(connection_data)
        logger.info(f"Connection saved: {user_id} ({user_type})")
        
        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'Connected'})
        }
        
    except Exception as e:
        logger.error(f"Error in connect: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }


def disconnect(event, context):
    """
    Lambda ejecutada cuando cliente cierra conexión WebSocket
    """
    try:
        connection_id = event['requestContext']['connectionId']
        logger.info(f"WebSocket Disconnect: {connection_id}")
        
        # Obtener conexión
        connection = connections_db.get_item({'connection_id': connection_id})
        
        if connection:
            user_id = connection.get('user_id')
            logger.info(f"Removing connection for user: {user_id}")
        
        # Eliminar conexión de DynamoDB
        connections_db.delete_item({'connection_id': connection_id})
        
        # Eliminar todas las suscripciones de esta conexión
        # (Se limpian automáticamente por TTL, pero podemos hacerlo manualmente)
        
        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'Disconnected'})
        }
        
    except Exception as e:
        logger.error(f"Error in disconnect: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }


def default(event, context):
    """
    Lambda ejecutada cuando cliente envía un mensaje por WebSocket
    
    Mensajes soportados:
    - {"action": "subscribe_order", "order_id": "xyz"}
    - {"action": "unsubscribe_order", "order_id": "xyz"}
    - {"action": "get_subscriptions"}
    """
    try:
        connection_id = event['requestContext']['connectionId']
        body = json.loads(event.get('body', '{}'))
        
        action = body.get('action', '')
        logger.info(f"WebSocket message from {connection_id}: {action}")
        
        # Obtener conexión
        connection = connections_db.get_item({'connection_id': connection_id})
        if not connection:
            logger.error(f"Connection not found: {connection_id}")
            return {'statusCode': 400}
        
        user_id = connection.get('user_id')
        
        # ============================================================================
        # ACTION: subscribe_order
        # ============================================================================
        if action == 'subscribe_order':
            order_id = body.get('order_id')
            
            if not order_id:
                return send_message(connection_id, {
                    'type': 'error',
                    'message': 'order_id es requerido'
                })
            
            logger.info(f"User {user_id} subscribing to order {order_id}")
            
            # Crear o actualizar suscripción
            subscription_id = f"{order_id}#{connection_id}"
            
            subscription_data = {
                'subscription_id': subscription_id,
                'order_id': order_id,
                'connection_ids': [connection_id],
                'user_id': user_id,
                'user_type': connection.get('user_type'),
                'created_at': current_timestamp()
            }
            
            subscriptions_db.put_item(subscription_data)
            
            # Actualizar la lista de órdenes suscritas en la conexión
            if 'subscribed_orders' not in connection:
                connection['subscribed_orders'] = []
            if order_id not in connection['subscribed_orders']:
                connection['subscribed_orders'].append(order_id)
            
            connections_db.put_item(connection)
            
            # Responder al cliente
            return send_message(connection_id, {
                'type': 'subscribed',
                'order_id': order_id,
                'message': f'Suscrito a actualizaciones del pedido {order_id}'
            })
        
        # ============================================================================
        # ACTION: unsubscribe_order
        # ============================================================================
        elif action == 'unsubscribe_order':
            order_id = body.get('order_id')
            
            if not order_id:
                return send_message(connection_id, {
                    'type': 'error',
                    'message': 'order_id es requerido'
                })
            
            logger.info(f"User {user_id} unsubscribing from order {order_id}")
            
            # Eliminar suscripción
            subscription_id = f"{order_id}#{connection_id}"
            subscriptions_db.delete_item({'subscription_id': subscription_id})
            
            # Actualizar conexión
            if 'subscribed_orders' in connection and order_id in connection['subscribed_orders']:
                connection['subscribed_orders'].remove(order_id)
            
            connections_db.put_item(connection)
            
            return send_message(connection_id, {
                'type': 'unsubscribed',
                'order_id': order_id,
                'message': f'Desuscrito del pedido {order_id}'
            })
        
        # ============================================================================
        # ACTION: get_subscriptions
        # ============================================================================
        elif action == 'get_subscriptions':
            logger.info(f"User {user_id} getting subscriptions")
            
            return send_message(connection_id, {
                'type': 'subscriptions',
                'orders': connection.get('subscribed_orders', [])
            })
        
        # Acción desconocida
        else:
            return send_message(connection_id, {
                'type': 'error',
                'message': f'Acción desconocida: {action}'
            })
        
    except Exception as e:
        logger.error(f"Error in default: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {'statusCode': 500}


def notify_order_update(event, context):
    """
    Lambda ejecutada por EventBridge cuando hay un cambio de estado de orden
    
    Envía notificación a todos los clientes suscritos a esa orden
    """
    try:
        logger.info(f"Notify order update event: {json.dumps(event)}")
        
        # Extraer información del evento
        detail = event.get('detail', {})
        order_id = detail.get('order_id')
        detail_type = event.get('detail-type', '')
        
        if not order_id:
            logger.warning("No order_id in event")
            return {'statusCode': 400}
        
        logger.info(f"Processing update for order {order_id}, type: {detail_type}")
        
        # ============================================================================
        # Construir mensaje según el tipo de evento
        # ============================================================================
        
        message_map = {
            'OrderCreated': {
                'type': 'order_created',
                'title': '🆕 Nuevo Pedido',
                'message': f'Pedido {order_id} creado exitosamente',
                'order_id': order_id,
                'status': 'pending'
            },
            'OrderConfirmed': {
                'type': 'order_confirmed',
                'title': '✓ Pedido Confirmado',
                'message': f'Tu pedido ha sido confirmado',
                'order_id': order_id,
                'status': 'confirmed'
            },
            'OrderCooking': {
                'type': 'order_cooking',
                'title': '👨‍🍳 En Cocina',
                'message': f'El chef comenzó a cocinar tu pedido',
                'order_id': order_id,
                'status': 'cooking'
            },
            'OrderReady': {
                'type': 'order_ready',
                'title': '🎉 ¡Listo!',
                'message': f'Tu pedido está listo para recoger',
                'order_id': order_id,
                'status': 'ready'
            },
            'OrderPickedUp': {
                'type': 'order_picked_up',
                'title': '🚗 En Camino',
                'message': f'Tu pedido está en camino',
                'order_id': order_id,
                'status': 'in_delivery',
                'driver': detail.get('driver_identifier')
            },
            'OrderInDelivery': {
                'type': 'order_in_delivery',
                'title': '🚗 En Camino',
                'message': f'Tu pedido está en camino',
                'order_id': order_id,
                'status': 'in_delivery'
            },
            'OrderDelivered': {
                'type': 'order_delivered',
                'title': '✅ Entregado',
                'message': f'Tu pedido ha sido entregado',
                'order_id': order_id,
                'status': 'delivered',
                'delivery_time': detail.get('delivery_duration_minutes')
            },
            'OrderPickupCanceled': {
                'type': 'order_pickup_canceled',
                'title': '⚠️ Pickup Cancelado',
                'message': f'El pickup del pedido fue cancelado: {detail.get("reason")}',
                'order_id': order_id,
                'status': 'ready',
                'reason': detail.get('reason')
            }
        }
        
        message = message_map.get(detail_type, {
            'type': 'order_update',
            'message': f'Actualización del pedido {order_id}',
            'order_id': order_id,
            'detail': detail
        })
        
        # ============================================================================
        # Buscar todas las suscripciones a esta orden
        # ============================================================================
        
        # Query por order_id en el índice
        subscriptions = subscriptions_db.query_items(
            'order_id',
            order_id,
            index_name='order-id-index'
        )
        
        logger.info(f"Found {len(subscriptions)} subscriptions for order {order_id}")
        
        # Conjunto de connection_ids únicos
        connection_ids = set()
        
        for subscription in subscriptions:
            conn_ids = subscription.get('connection_ids', [])
            connection_ids.update(conn_ids)
        
        logger.info(f"Sending to {len(connection_ids)} connections")
        
        # ============================================================================
        # Enviar mensaje a cada conexión
        # ============================================================================
        
        sent = 0
        failed = 0
        
        for connection_id in connection_ids:
            try:
                result = send_message(connection_id, message)
                if result.get('statusCode') == 200:
                    sent += 1
                else:
                    failed += 1
                    # Si falla, eliminar la conexión (probablemente esté desconectada)
                    try:
                        connections_db.delete_item({'connection_id': connection_id})
                        logger.info(f"Removed stale connection: {connection_id}")
                    except:
                        pass
            except Exception as e:
                logger.error(f"Error sending to {connection_id}: {str(e)}")
                failed += 1
        
        logger.info(f"Notification sent: {sent} success, {failed} failed")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'order_id': order_id,
                'sent': sent,
                'failed': failed
            })
        }
        
    except Exception as e:
        logger.error(f"Error in notify_order_update: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }


# ============================================================================
# FUNCIONES AUXILIARES
# ============================================================================

def send_message(connection_id, message):
    """
    Envía un mensaje a través de WebSocket a una conexión específica
    
    Usa API Gateway Management API
    """
    try:
        # Obtener endpoint de API Gateway
        endpoint = get_websocket_endpoint()
        
        # Si no hay endpoint en env, construirlo desde el contexto
        if not endpoint:
            # En ejecución real, AWS lo proporciona automáticamente
            logger.warning("No websocket endpoint configured")
            return {'statusCode': 500}
        
        # Crear cliente con el endpoint
        client = boto3.client(
            'apigatewaymanagementapi',
            endpoint_url=endpoint
        )
        
        # Enviar mensaje
        response = client.post_to_connection(
            ConnectionId=connection_id,
            Data=json.dumps(message).encode('utf-8')
        )
        
        logger.info(f"Message sent to {connection_id}")
        return {'statusCode': 200, 'response': response}
        
    except client.exceptions.GoneException:
        logger.warning(f"Connection gone (closed): {connection_id}")
        # Eliminar conexión de DynamoDB
        try:
            connections_db.delete_item({'connection_id': connection_id})
        except:
            pass
        return {'statusCode': 410}
    
    except Exception as e:
        logger.error(f"Error sending message to {connection_id}: {str(e)}")
        return {'statusCode': 500, 'error': str(e)}


def get_connections_for_user(user_id):
    """
    Obtiene todas las conexiones WebSocket de un usuario
    """
    try:
        connections = connections_db.query_items(
            'user_id',
            user_id,
            index_name='user-id-index'
        )
        return connections
    except Exception as e:
        logger.error(f"Error getting connections for user {user_id}: {str(e)}")
        return []


def broadcast_to_user_type(user_type, message, exclude_order_id=None):
    """
    Envía mensaje a todas las conexiones de un tipo de usuario
    
    Ej: Enviar a todos los "driver" cuando hay un nuevo pedido ready
    """
    try:
        # Escanear tabla de conexiones
        all_connections = connections_db.scan_items()
        
        sent = 0
        for connection in all_connections:
            if connection.get('user_type') == user_type:
                # Si exclude_order_id está especificado, no enviar a los suscritos a esa orden
                if exclude_order_id:
                    if exclude_order_id in connection.get('subscribed_orders', []):
                        continue
                
                try:
                    send_message(connection.get('connection_id'), message)
                    sent += 1
                except:
                    pass
        
        logger.info(f"Broadcast to {user_type}: sent to {sent} connections")
        return sent
        
    except Exception as e:
        logger.error(f"Error broadcasting to {user_type}: {str(e)}")
        return 0
