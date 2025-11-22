import json
import uuid
import os
import boto3
from decimal import Decimal
from shared.utils import (
    response, success_response, error_response, error_handler, 
    parse_body, get_tenant_id, get_user_id, get_user_email, current_timestamp
)
from shared.dynamodb import DynamoDBService
from shared.eventbridge import EventBridgeService
from shared.errors import NotFoundError, ValidationError
from shared.logger import get_logger

logger = get_logger(__name__)
orders_db = DynamoDBService(os.environ.get('ORDERS_TABLE'))
stepfunctions = boto3.client('stepfunctions')

@error_handler
def create_order(event, context):
    logger.info("Creating new order")
    
    # Debug: Log del evento para ver la estructura
    logger.info(f"Event keys: {list(event.keys())}")
    if 'requestContext' in event:
        logger.info(f"RequestContext keys: {list(event['requestContext'].keys())}")
        if 'authorizer' in event['requestContext']:
            logger.info(f"Authorizer keys: {list(event['requestContext']['authorizer'].keys())}")
    
    body = parse_body(event)
    tenant_id = get_tenant_id(event)
    customer_id = get_user_id(event) or body.get('customer_id')
    customer_email = get_user_email(event) or body.get('customer_email')
    
    logger.info(f"Extracted - tenant_id: {tenant_id}, customer_id: {customer_id}, customer_email: {customer_email}")

    if not customer_id:
        raise ValidationError("customer_id es requerido (inicia sesión o inclúyelo en el body)")
    
    items = body.get('items', [])
    if not items or len(items) == 0:
        raise ValidationError("Debe incluir al menos un item en el pedido")
    
    total = body.get('total', 0)
    if total <= 0:
        raise ValidationError("El total debe ser mayor a 0")
    
    order_id = str(uuid.uuid4())
    timestamp = current_timestamp()
    
    normalized_items = _normalize_items(items)

    order = {
        'order_id': order_id,
        'tenant_id': tenant_id,
        'customer_id': customer_id,
        'customer_email': customer_email,
        'items': normalized_items,
        'status': 'pending',
        'total': Decimal(str(total)),
        'created_at': timestamp,
        'updated_at': timestamp
    }
    
    success = orders_db.put_item(order)
    if not success:
        logger.error(f"Failed to save order {order_id}")
        raise Exception("Error al crear el pedido")
    
    EventBridgeService.put_event(
        source='orders.service',
        detail_type='OrderCreated',
        detail={
            'order_id': order_id,
            'customer_id': customer_id,
            'customer_email': customer_email,
            'items': items,
            'total': float(total)
        },
        tenant_id=tenant_id
    )
    
    # Iniciar Step Function para workflow automatizado
    # Nota: El ARN se obtiene del output de CloudFormation después del deploy
    try:
        # Construir el ARN de la Step Function
        region = os.environ.get('AWS_REGION', 'us-east-1')
        account_id = context.invoked_function_arn.split(':')[4] if context else None
        service_name = os.environ.get('SERVERLESS_SERVICE', 'millas-backend')
        stage = os.environ.get('SERVERLESS_STAGE', 'dev')
        
        if account_id:
            step_function_arn = f"arn:aws:states:{region}:{account_id}:stateMachine:{service_name}-{stage}-order-workflow"
            stepfunctions.start_execution(
                stateMachineArn=step_function_arn,
                name=f"order-{order_id}",
                input=json.dumps({
                    'order_id': order_id,
                    'tenant_id': tenant_id,
                    'customer_id': customer_id,
                    'customer_email': customer_email
                })
            )
            logger.info(f"Step Function started for order {order_id}")
    except Exception as e:
        logger.warning(f"Could not start Step Function: {str(e)}")
        # No fallar si Step Function no está disponible
    
    logger.info(f"Order created: {order_id}")
    
    response_order = {
        **order,
        'total': float(order['total']),
        'items': _serialize_items(order['items'])
    }
    
    return success_response(response_order, 201)


def _normalize_items(items):
    normalized = []
    for item in items:
        normalized_item = dict(item)
        if 'price' in normalized_item:
            normalized_item['price'] = Decimal(str(normalized_item['price']))
        if 'quantity' in normalized_item:
            normalized_item['quantity'] = int(normalized_item['quantity'])
        normalized.append(normalized_item)
    return normalized


def _serialize_items(items):
    serialized = []
    for item in items:
        serialized_item = dict(item)
        if 'price' in serialized_item:
            serialized_item['price'] = float(serialized_item['price'])
        serialized.append(serialized_item)
    return serialized

@error_handler
def get_orders(event, context):
    logger.info("Getting orders")
    
    tenant_id = get_tenant_id(event)
    customer_id = get_user_id(event)
    
    logger.info(f"Searching orders for - tenant_id: {tenant_id}, customer_id: {customer_id}")
    
    items = orders_db.query_items('tenant_id', tenant_id, index_name='tenant-created-index')
    
    logger.info(f"Total orders found for tenant: {len(items)}")
    
    customer_orders = [
        item for item in items 
        if item.get('customer_id') == customer_id
    ]
    
    # Debug: mostrar customer_ids de todas las órdenes
    if items:
        all_customer_ids = [item.get('customer_id') for item in items]
        logger.info(f"Customer IDs in all orders: {all_customer_ids}")
    
    for order in customer_orders:
        if 'total' in order:
            order['total'] = float(order['total'])
    
    logger.info(f"Found {len(customer_orders)} orders for customer_id: {customer_id}")
    
    return success_response(customer_orders)

@error_handler
def get_order(event, context):
    logger.info("Getting order details")
    
    order_id = event.get('pathParameters', {}).get('order_id')
    customer_id = get_user_id(event)
    
    if not order_id:
        raise ValidationError("order_id es requerido")
    
    order = orders_db.get_item({'order_id': order_id})
    
    if not order:
        raise NotFoundError(f"Pedido {order_id} no encontrado")
    
    if order.get('customer_id') != customer_id:
        raise ValidationError("No tienes permiso para ver este pedido")
    
    if 'total' in order:
        order['total'] = float(order['total'])
    
    logger.info(f"Order details retrieved: {order_id}")
    
    return success_response(order)
