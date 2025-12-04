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
# El endpoint se construye dinámicamente desde el evento o variables de entorno

def get_websocket_management_endpoint(event=None):
    """
    Obtiene el endpoint de API Gateway Management API para enviar mensajes
    
    El endpoint es: https://{api-id}.execute-api.{region}.amazonaws.com/{stage}
    """
    # Intentar obtener del evento (si viene de WebSocket handler)
    if event and 'requestContext' in event:
        domain = event['requestContext'].get('domainName')
        stage = event['requestContext'].get('stage')
        if domain and stage:
            # Convertir wss:// a https:// para Management API
            endpoint = domain.replace('wss://', 'https://').replace('ws://', 'https://')
            return endpoint
    
    # Intentar construir desde variables de entorno
    region = os.environ.get('AWS_REGION', 'us-east-1')
    api_id = os.environ.get('WEBSOCKET_API_ID', '')
    stage = os.environ.get('SERVERLESS_STAGE', 'dev')
    
    if api_id:
        return f"https://{api_id}.execute-api.{region}.amazonaws.com/{stage}"
    
    # Fallback: intentar desde WEBSOCKET_ENDPOINT si está configurado
    endpoint = os.environ.get('WEBSOCKET_ENDPOINT', '')
    if endpoint:
        # Convertir wss:// a https://
        return endpoint.replace('wss://', 'https://').replace('ws://', 'https://')
    
    logger.warning("No WebSocket Management API endpoint found")
    return None

# ============================================================================
# HANDLERS PRINCIPALES
# ============================================================================

def connect(event, context):
    """
    Lambda ejecutada cuando cliente abre conexión WebSocket
    
    Event contiene:
    - requestContext.connectionId: ID único de la conexión
    - queryStringParameters.token: JWT del usuario (opcional)
    """
    try:
        connection_id = event['requestContext']['connectionId']
        logger.info(f"WebSocket Connect: {connection_id}")
        
        # Extraer token de query parameters
        query_params = event.get('queryStringParameters') or {}
        token = query_params.get('token', '')
        
        # Intentar verificar token si está presente
        user_id = None
        user_type = 'customer'
        user_email = None
        
        if token:
            try:
                from shared.security import verify_token
                payload = verify_token(token)
                user_id = payload.get('user_id')
                user_type = payload.get('user_type', 'customer')
                user_email = payload.get('email')
                logger.info(f"Token verified: {user_id} ({user_type})")
            except Exception as e:
                logger.warning(f"Token verification failed: {str(e)}")
                # Continuar sin autenticación (permite conexiones anónimas)
        
        # Si no hay token o falló, usar valores por defecto
        if not user_id:
            user_id = query_params.get('user_id', f'anonymous_{uuid.uuid4()}')
            user_type = query_params.get('user_type', 'customer')
        
        timestamp = current_timestamp()
        expires_at = timestamp + (86400 * 7)  # 7 días de TTL
        
        # Guardar conexión en DynamoDB
        connection_data = {
            'connection_id': connection_id,
            'user_id': user_id,
            'user_type': user_type,
            'email': user_email,
            'connected_at': timestamp,
            'expires_at': expires_at,  # Para TTL
            'subscribed_orders': []
        }
        
        connections_db.put_item(connection_data)
        logger.info(f"Connection saved: {user_id} ({user_type})")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Connected',
                'connection_id': connection_id,
                'user_id': user_id,
                'user_type': user_type
            })
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
            }, event)
        
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
            }, event)
        
        # ============================================================================
        # ACTION: get_subscriptions
        # ============================================================================
        elif action == 'get_subscriptions':
            logger.info(f"User {user_id} getting subscriptions")
            
            return send_message(connection_id, {
                'type': 'subscriptions',
                'orders': connection.get('subscribed_orders', [])
            }, event)
        
        # Acción desconocida
        else:
            return send_message(connection_id, {
                'type': 'error',
                'message': f'Acción desconocida: {action}'
            }, event)
        
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
        
        # Obtener el endpoint de Management API desde variables de entorno o construir
        # Cuando se invoca desde EventBridge, no tenemos el evento de WebSocket
        # Necesitamos construir el endpoint desde variables de entorno
        region = os.environ.get('AWS_REGION', 'us-east-1')
        api_id = os.environ.get('WEBSOCKET_API_ID', '')
        stage = os.environ.get('SERVERLESS_STAGE', 'dev')
        
        if not api_id:
            # Intentar obtener desde WEBSOCKET_ENDPOINT
            endpoint_str = os.environ.get('WEBSOCKET_ENDPOINT', '')
            if endpoint_str:
                # Extraer API ID del endpoint: wss://{api-id}.execute-api.{region}.amazonaws.com/{stage}
                import re
                match = re.search(r'wss://([^.]+)\.execute-api', endpoint_str)
                if match:
                    api_id = match.group(1)
        
        if not api_id:
            logger.error("WEBSOCKET_API_ID not configured. Cannot send messages.")
            return {
                'statusCode': 500,
                'body': json.dumps({'error': 'WebSocket API ID not configured'})
            }
        
        management_endpoint = f"https://{api_id}.execute-api.{region}.amazonaws.com/{stage}"
        logger.info(f"Using Management API endpoint: {management_endpoint}")
        
        # Crear cliente una sola vez
        client = boto3.client(
            'apigatewaymanagementapi',
            endpoint_url=management_endpoint
        )
        
        for connection_id in connection_ids:
            try:
                # Enviar mensaje directamente usando el cliente
                client.post_to_connection(
                    ConnectionId=connection_id,
                    Data=json.dumps(message).encode('utf-8')
                )
                sent += 1
                logger.info(f"Message sent to {connection_id}")
            except client.exceptions.GoneException:
                logger.warning(f"Connection gone (closed): {connection_id}")
                failed += 1
                # Eliminar conexión de DynamoDB
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

def send_message(connection_id, message, event=None):
    """
    Envía un mensaje a través de WebSocket a una conexión específica
    
    Usa API Gateway Management API
    
    Args:
        connection_id: ID de la conexión WebSocket
        message: Mensaje a enviar (dict)
        event: Evento opcional para obtener el endpoint (si viene de WebSocket handler)
    """
    try:
        # Obtener endpoint de Management API
        endpoint = get_websocket_management_endpoint(event)
        
        if not endpoint:
            logger.error("No WebSocket Management API endpoint available")
            return {'statusCode': 500, 'error': 'No endpoint configured'}
        
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
        
    except Exception as e:
        error_str = str(e)
        error_type = type(e).__name__
        
        # Verificar si es GoneException (conexión cerrada)
        if 'GoneException' in error_type or '410' in error_str or 'gone' in error_str.lower():
            logger.warning(f"Connection gone (closed): {connection_id}")
            # Eliminar conexión de DynamoDB
            try:
                connections_db.delete_item({'connection_id': connection_id})
            except:
                pass
            return {'statusCode': 410}
        
        logger.error(f"Error sending message to {connection_id}: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
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
