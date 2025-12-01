import os
import json
from shared.dynamodb import DynamoDBService
from shared.email_service import EmailService
from shared.logger import get_logger

logger = get_logger(__name__)
orders_db = DynamoDBService(os.environ.get('ORDERS_TABLE'))


def send_order_notifications(event, context):
    try:
        logger.info(f"Processing order notification event")
        
        detail = event.get('detail', {})
        detail_type = event.get('detail-type', '')
        order_id = detail.get('order_id')
        
        if not order_id:
            logger.warning("No order_id in event")
            return {'statusCode': 400}
        
        logger.info(f"Event: {detail_type}, Order: {order_id}")
        
        order = orders_db.get_item({'order_id': order_id})
        if not order:
            logger.warning(f"Order not found: {order_id}")
            return {'statusCode': 404}
        
        customer_email = order.get('customer_email')
        customer_name = order.get('customer_id', 'Cliente')
        total = order.get('total', 0)
        items = order.get('items', [])
        
        if not customer_email:
            logger.warning(f"No customer email for order {order_id}")
            return {'statusCode': 400}
        
        logger.info(f"Sending email to {customer_email}")
        
        sent = False
        
        if detail_type == 'OrderCreated':
            sent = EmailService.send_order_created(
                customer_email=customer_email,
                customer_name=customer_name,
                order_id=order_id,
                total=float(total),
                items=items
            )
        
        elif detail_type == 'OrderConfirmed':
            sent = EmailService.send_order_confirmed(
                customer_email=customer_email,
                customer_name=customer_name,
                order_id=order_id
            )
        
        elif detail_type == 'OrderCooking':
            sent = EmailService.send_order_cooking(
                customer_email=customer_email,
                customer_name=customer_name,
                order_id=order_id
            )
        
        elif detail_type == 'OrderReady':
            sent = EmailService.send_order_ready(
                customer_email=customer_email,
                customer_name=customer_name,
                order_id=order_id
            )
        
        elif detail_type == 'OrderPickedUp' or detail_type == 'OrderInDelivery':
            driver_name = detail.get('driver_identifier', detail.get('driver_email'))
            sent = EmailService.send_order_on_the_way(
                customer_email=customer_email,
                customer_name=customer_name,
                order_id=order_id,
                driver_name=driver_name
            )
        
        elif detail_type == 'OrderDelivered':
            delivery_time = detail.get('delivery_duration_minutes')
            sent = EmailService.send_order_delivered(
                customer_email=customer_email,
                customer_name=customer_name,
                order_id=order_id,
                delivery_time=delivery_time
            )
        
        elif detail_type == 'OrderPickupCanceled':
            reason = detail.get('reason', 'Sin especificar')
            sent = EmailService.send_order_canceled(
                customer_email=customer_email,
                customer_name=customer_name,
                order_id=order_id,
                reason=reason
            )
        
        if sent:
            logger.info(f"Email sent successfully for order {order_id}")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'order_id': order_id,
                    'email_sent': True,
                    'event_type': detail_type
                })
            }
        else:
            logger.warning(f"Failed to send email for order {order_id}")
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'order_id': order_id,
                    'email_sent': False,
                    'event_type': detail_type
                })
            }
    
    except Exception as e:
        logger.error(f"Error in send_order_notifications: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
