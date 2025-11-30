import json
import uuid
import os
import boto3
from decimal import Decimal
from shared.utils import (
    response, success_response, error_response, error_handler, 
    parse_body, get_tenant_id, get_user_id, get_user_email, current_timestamp,
    get_path_param_from_path
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
    
    # Normalizar customer_id (asegurar que sea string y sin espacios)
    if customer_id:
        customer_id = str(customer_id).strip()
    if customer_email:
        customer_email = str(customer_email).strip()
    
    logger.info(f"Extracted - tenant_id: {tenant_id}, customer_id: {customer_id}, customer_email: {customer_email}")

    if not customer_id:
        raise ValidationError("customer_id es requerido (inicia sesión o inclúyelo en el body)")
    
    items = body.get('items', [])
    if not items or len(items) == 0:
        raise ValidationError("Debe incluir al menos un item en el pedido")
    
    # ✅ Validar estructura de items
    for idx, item in enumerate(items):
        if not item.get('item_id'):
            raise ValidationError(f"Item {idx + 1}: item_id es requerido")
        if not item.get('name'):
            raise ValidationError(f"Item {idx + 1}: name es requerido")
        if 'price' not in item:
            raise ValidationError(f"Item {idx + 1}: price es requerido")
        if 'quantity' not in item:
            raise ValidationError(f"Item {idx + 1}: quantity es requerido")
        
        try:
            price = float(item['price'])
            quantity = int(item['quantity'])
            if price <= 0:
                raise ValidationError(f"Item {idx + 1}: price debe ser mayor a 0")
            if quantity <= 0:
                raise ValidationError(f"Item {idx + 1}: quantity debe ser mayor a 0")
        except (ValueError, TypeError):
            raise ValidationError(f"Item {idx + 1}: price o quantity inválido")
    
    # ✅ Validar que el total coincida con los items
    calculated_total = sum(
        float(item['price']) * int(item['quantity'])
        for item in items
    )
    
    total = body.get('total', 0)
    try:
        total = float(total)
    except (ValueError, TypeError):
        raise ValidationError("Total inválido")
    
    if total <= 0:
        raise ValidationError("El total debe ser mayor a 0")
    
    # ✅ Verificar que el total enviado coincida con el calculado (tolerancia de 0.01)
    if abs(total - calculated_total) > 0.01:
        raise ValidationError(
            f"El total enviado ({total}) no coincide con el calculado ({calculated_total:.2f})"
        )
    
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
    try:
        # ✅ Construir ARN dinámicamente usando variables de entorno
        region = os.environ.get('AWS_REGION', 'us-east-1')
        account_id = os.environ.get('AWS_ACCOUNT_ID', '722204368591')
        service_name = os.environ.get('SERVERLESS_SERVICE', 'millas-backend')
        stage = os.environ.get('SERVERLESS_STAGE', 'dev')
        
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
    """Convierte todos los Decimals a float para JSON serialization"""
    serialized = []
    for item in items:
        serialized_item = {}
        for key, value in item.items():
            # Convertir Decimal a float
            if isinstance(value, Decimal):
                serialized_item[key] = float(value)
            else:
                serialized_item[key] = value
        serialized.append(serialized_item)
    return serialized

@error_handler
def get_orders(event, context):
    logger.info("Getting orders")
    
    # Debug: Log del evento para ver la estructura
    logger.info(f"Event keys: {list(event.keys())}")
    if 'requestContext' in event:
        logger.info(f"RequestContext keys: {list(event['requestContext'].keys())}")
        if 'authorizer' in event['requestContext']:
            logger.info(f"Authorizer keys: {list(event['requestContext']['authorizer'].keys())}")
            logger.info(f"Authorizer content: {event['requestContext']['authorizer']}")
    
    tenant_id = get_tenant_id(event)
    customer_id = get_user_id(event)
    
    # Normalizar customer_id (asegurar que sea string y sin espacios)
    if customer_id:
        customer_id = str(customer_id).strip()
    
    logger.info(f"Searching orders for - tenant_id: {tenant_id}, customer_id: {customer_id}")
    
    if not customer_id:
        logger.warning("No customer_id found in token, cannot filter orders")
        raise ValidationError("No se pudo identificar al usuario. Por favor, inicia sesión nuevamente.")
    
    # ✅ USAR ÍNDICE DIRECTO EN LUGAR DE FILTRAR EN MEMORIA
    items = orders_db.query_items(
        'customer_id',
        customer_id,
        index_name='customer-orders-index'
    )
    
    # ✅ Serializar Decimals correctamente
    serialized_items = []
    for order in items:
        serialized_order = dict(order)
        
        # Convertir total a float
        if 'total' in serialized_order:
            serialized_order['total'] = float(serialized_order['total'])
        
        # Serializar items dentro de la orden
        if 'items' in serialized_order:
            serialized_order['items'] = _serialize_items(serialized_order['items'])
        
        serialized_items.append(serialized_order)
    
    logger.info(f"Found {len(serialized_items)} orders for customer_id: {customer_id}")
    
    return success_response(serialized_items)

@error_handler
def get_order(event, context):
    logger.info("Getting order details")
    
    # ✅ Usar la función mejorada para extraer order_id del path
    order_id = get_path_param_from_path(event, 'order_id')
    
    logger.info(f"Extracted order_id: {order_id}")
    
    if not order_id:
        raise ValidationError("order_id es requerido")
    
    customer_id = get_user_id(event)
    
    order = orders_db.get_item({'order_id': order_id})
    
    if not order:
        raise NotFoundError(f"Pedido {order_id} no encontrado")
    
    if order.get('customer_id') != customer_id:
        raise ValidationError("No tienes permiso para ver este pedido")
    
    # ✅ Serializar correctamente el pedido completo
    serialized_order = dict(order)
    
    if 'total' in serialized_order:
        serialized_order['total'] = float(serialized_order['total'])
    
    if 'items' in serialized_order:
        serialized_order['items'] = _serialize_items(serialized_order['items'])
    
    logger.info(f"Order details retrieved: {order_id}")
    
    return success_response(serialized_order)
