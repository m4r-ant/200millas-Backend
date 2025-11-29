"""
WebSocket API Handler para seguimiento en tiempo real de pedidos
Maneja conexiones WebSocket para notificar cambios de estado
"""
import json
import os
import boto3
from shared.utils import get_logger
from shared.dynamodb import DynamoDBService

logger = get_logger(__name__)
orders_db = DynamoDBService(os.environ.get('ORDERS_TABLE'))
workflow_db = DynamoDBService(os.environ.get('WORKFLOW_TABLE'))

# Cliente de API Gateway Management API para WebSocket
apigw_management = boto3.client('apigatewaymanagementapi', 
    endpoint_url=os.environ.get('WEBSOCKET_API_ENDPOINT'))

def connect(event, context):
    """Maneja conexión WebSocket"""
    connection_id = event['requestContext']['connectionId']
    logger.info(f"WebSocket connected: {connection_id}")
    
    return {
        'statusCode': 200,
        'body': json.dumps({'message': 'Connected'})
    }

def disconnect(event, context):
    """Maneja desconexión WebSocket"""
    connection_id = event['requestContext']['connectionId']
    logger.info(f"WebSocket disconnected: {connection_id}")
    
    return {
        'statusCode': 200,
        'body': json.dumps({'message': 'Disconnected'})
    }

def default(event, context):
    """Maneja mensajes WebSocket por defecto"""
    connection_id = event['requestContext']['connectionId']
    body = json.loads(event.get('body', '{}'))
    
    logger.info(f"WebSocket message from {connection_id}: {body}")
    
    action = body.get('action')
    
    if action == 'subscribe_order':
        # Cliente quiere suscribirse a actualizaciones de un pedido
        order_id = body.get('order_id')
        if order_id:
            # En producción, guardarías la suscripción en DynamoDB
            # Por ahora, solo respondemos
            try:
                apigw_management.post_to_connection(
                    ConnectionId=connection_id,
                    Data=json.dumps({
                        'action': 'subscribed',
                        'order_id': order_id,
                        'message': 'Subscribed to order updates'
                    })
                )
            except Exception as e:
                logger.error(f"Error sending message: {str(e)}")
    
    return {
        'statusCode': 200,
        'body': json.dumps({'message': 'Message received'})
    }

def notify_order_update(order_id, status, connection_ids=None):
    """Notifica actualización de pedido a clientes conectados"""
    try:
        # Obtener información del pedido
        order = orders_db.get_item({'order_id': order_id})
        workflow = workflow_db.get_item({'order_id': order_id})
        
        message = {
            'type': 'order_update',
            'order_id': order_id,
            'status': status,
            'order': order,
            'workflow': workflow
        }
        
        # Si se especifican connection_ids, notificar solo a esos
        # Si no, notificar a todos (en producción usarías DynamoDB para trackear conexiones)
        if connection_ids:
            for conn_id in connection_ids:
                try:
                    apigw_management.post_to_connection(
                        ConnectionId=conn_id,
                        Data=json.dumps(message)
                    )
                except Exception as e:
                    logger.error(f"Error notifying {conn_id}: {str(e)}")
        
        return True
    except Exception as e:
        logger.error(f"Error notifying order update: {str(e)}")
        return False

