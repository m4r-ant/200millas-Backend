"""
Email Handler - Envía notificaciones por email cuando hay cambios en pedidos
"""
import os
import json
import boto3
from shared.logger import get_logger
from shared.utils import current_timestamp

logger = get_logger(__name__)

# Cliente de SNS para enviar emails
sns_client = boto3.client('sns')
SNS_TOPIC_ARN = os.environ.get('SNS_TOPIC_ARN', '')


def send_order_notifications(event, context):
    """
    Lambda que se ejecuta cuando EventBridge detecta cambios en pedidos
    Envía notificaciones por email a través de SNS
    
    Event structure (EventBridge):
    {
        "source": "orders.service",
        "detail-type": "OrderCreated",
        "detail": {
            "order_id": "...",
            "customer_id": "...",
            "status": "..."
        }
    }
    """
    logger.info("Processing order email notification")
    
    if not SNS_TOPIC_ARN:
        logger.warning("SNS_TOPIC_ARN not configured, skipping email notification")
        return {'statusCode': 200, 'body': json.dumps({'message': 'SNS not configured'})}
    
    try:
        # Extraer información del evento
        detail = event.get('detail', {})
        detail_type = event.get('detail-type', '')
        source = event.get('source', '')
        
        order_id = detail.get('order_id', '')
        customer_id = detail.get('customer_id', '')
        status = detail.get('status', '')
        
        logger.info(f"Order event: {detail_type} for order {order_id}, status: {status}")
        
        # Construir mensaje según el tipo de evento
        subject = f"Actualización de Pedido #{order_id[:8]}"
        message = _build_email_message(detail_type, detail)
        
        # Enviar a SNS
        response = sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=message
        )
        
        logger.info(f"Email notification sent: {response.get('MessageId')}")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'success': True,
                'message_id': response.get('MessageId')
            })
        }
        
    except Exception as e:
        logger.error(f"Error sending email notification: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        # No fallar el workflow si el email falla
        return {
            'statusCode': 200,
            'body': json.dumps({
                'success': False,
                'error': str(e)
            })
        }


def _build_email_message(detail_type, detail):
    """Construye el mensaje de email según el tipo de evento"""
    order_id = detail.get('order_id', 'N/A')
    status = detail.get('status', 'N/A')
    
    messages = {
        'OrderCreated': f"Tu pedido #{order_id[:8]} ha sido creado y está siendo procesado.",
        'OrderConfirmed': f"Tu pedido #{order_id[:8]} ha sido confirmado y está en preparación.",
        'OrderCooking': f"Tu pedido #{order_id[:8]} está siendo cocinado.",
        'OrderCookingCompleted': f"Tu pedido #{order_id[:8]} ha terminado de cocinarse y está siendo empaquetado.",
        'OrderPacked': f"Tu pedido #{order_id[:8]} está listo y será recogido pronto.",
        'OrderReady': f"Tu pedido #{order_id[:8]} está listo para recoger.",
        'OrderPickedUp': f"Tu pedido #{order_id[:8]} ha sido recogido y está en camino.",
        'OrderInDelivery': f"Tu pedido #{order_id[:8]} está en camino a tu dirección.",
        'OrderDelivered': f"¡Tu pedido #{order_id[:8]} ha sido entregado! ¡Disfruta tu comida!",
        'OrderStatusChanged': f"Tu pedido #{order_id[:8]} ha cambiado de estado: {status}",
    }
    
    default_message = f"Tu pedido #{order_id[:8]} ha sido actualizado. Estado: {status}"
    
    return messages.get(detail_type, default_message)

