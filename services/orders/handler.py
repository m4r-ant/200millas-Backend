import json
import uuid
import os
import boto3
from decimal import Decimal
from shared.utils import (
    response, success_response, error_response, error_handler, 
    parse_body, get_tenant_id, get_user_id, get_user_email, current_timestamp,
    get_path_param_from_path, get_user_type
)
from shared.dynamodb import DynamoDBService
from shared.eventbridge import EventBridgeService
from shared.errors import NotFoundError, ValidationError, UnauthorizedError
from shared.logger import get_logger

logger = get_logger(__name__)
orders_db = DynamoDBService(os.environ.get('ORDERS_TABLE'))
workflow_db = DynamoDBService(os.environ.get('WORKFLOW_TABLE'))
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


# ============================================================================
# NUEVA FUNCIÓN: Actualizar estado de pedido
# ============================================================================

@error_handler
def update_order_status(event, context):
    """
    Actualiza el estado de una orden.
    
    Flujo de estados válidos:
    pending → cooking → ready → dispatched → delivered
    
    Permisos:
    - Chef (staff): puede cambiar a cooking, ready, packing
    - Repartidor (driver): puede cambiar a dispatched, delivered
    - Admin: puede cambiar a cualquier estado
    
    Path: PATCH /orders/{order_id}/status
    Body: { "status": "ready" }
    """
    
    logger.info("Updating order status")
    
    # ✅ Extraer parámetros
    order_id = get_path_param_from_path(event, 'order_id')
    tenant_id = get_tenant_id(event)
    user_id = get_user_id(event)
    user_email = get_user_email(event)
    user_type = get_user_type(event)
    body = parse_body(event)
    
    logger.info(f"Order: {order_id} | User: {user_id} ({user_type}) | New Status: {body.get('status')}")
    
    # ✅ Validar order_id
    if not order_id:
        raise ValidationError("order_id es requerido")
    
    # ✅ Extraer y validar nuevo estado
    new_status = body.get('status', '').strip().lower()
    if not new_status:
        raise ValidationError("status es requerido en el body")
    
    notes = body.get('notes', '').strip()
    
    # ✅ Obtener orden actual
    order = orders_db.get_item({'order_id': order_id})
    if not order:
        logger.error(f"Order {order_id} not found")
        raise NotFoundError(f"Pedido {order_id} no encontrado")
    
    current_status = order.get('status')
    logger.info(f"Current status: {current_status}")
    
    # ✅ Validar transición de estado
    if not _is_valid_transition(current_status, new_status):
        logger.warning(f"Invalid transition: {current_status} → {new_status}")
        raise ValidationError(
            f"Transición no permitida: {current_status} → {new_status}"
        )
    
    # ✅ Validar permisos por rol
    _validate_permissions(user_type, current_status, new_status)
    
    # ✅ Validar que el cliente no actualice su propia orden
    if user_type == 'customer':
        raise UnauthorizedError("Clientes no pueden cambiar el estado de sus pedidos")
    
    timestamp = current_timestamp()
    
    # ✅ Actualizar orden en DynamoDB
    update_data = {
        'status': new_status,
        'updated_at': timestamp,
        'updated_by': user_email or user_id
    }
    
    # Si es el primer cambio a cooking, registrar cuándo empezó
    if new_status == 'cooking' and current_status == 'pending':
        update_data['cooking_started_at'] = timestamp
    
    # Si es entrega, registrar cuándo se completó
    if new_status == 'delivered':
        update_data['delivered_at'] = timestamp
    
    orders_db.update_item({'order_id': order_id}, update_data)
    logger.info(f"Order {order_id} updated to {new_status}")
    
    # ✅ Actualizar workflow
    workflow = workflow_db.get_item({'order_id': order_id})
    if not workflow:
        workflow = {'order_id': order_id, 'steps': []}
    
    # Completar el paso anterior si existe
    if workflow.get('steps'):
        last_step = workflow['steps'][-1]
        if last_step.get('status') == current_status and not last_step.get('completed_at'):
            last_step['completed_at'] = timestamp
            logger.info(f"Completed previous step: {current_status}")
    
    # Agregar nuevo step
    new_step = {
        'status': new_status,
        'assigned_to': user_email or user_id,
        'started_at': timestamp,
        'completed_at': None,
        'notes': notes if notes else None
    }
    
    workflow['steps'].append(new_step)
    workflow['current_status'] = new_status
    workflow['updated_at'] = timestamp
    
    workflow_db.put_item(workflow)
    logger.info(f"Workflow for {order_id} updated with new step: {new_status}")
    
    # ✅ Publicar evento en EventBridge para notificaciones en tiempo real
    EventBridgeService.put_event(
        source='orders.service',
        detail_type='OrderStatusChanged',
        detail={
            'order_id': order_id,
            'old_status': current_status,
            'new_status': new_status,
            'updated_by': user_email or user_id,
            'user_type': user_type,
            'timestamp': timestamp,
            'notes': notes
        },
        tenant_id=tenant_id
    )
    logger.info(f"Event published: OrderStatusChanged for {order_id}")
    
    # ✅ Retornar respuesta
    return success_response({
        'order_id': order_id,
        'old_status': current_status,
        'new_status': new_status,
        'updated_at': timestamp,
        'message': f'Pedido actualizado a {new_status}'
    }, 200)


# ============================================================================
# FUNCIONES AUXILIARES
# ============================================================================

def _is_valid_transition(current_status, new_status):
    """
    Valida si una transición de estado es permitida.
    
    Flujo de estados:
    pending → cooking → ready → dispatched → delivered
    
    También permite:
    pending → cooking → packing → dispatched → delivered
    
    Y estados de fallo:
    Cualquier estado → failed (para manejar errores)
    """
    
    valid_transitions = {
        'pending': ['cooking', 'confirmed'],
        'confirmed': ['cooking'],
        'cooking': ['ready', 'packing'],
        'ready': ['dispatched', 'packing'],
        'packing': ['dispatched'],
        'dispatched': ['delivered'],
        'delivered': [],
        'failed': []
    }
    
    allowed = valid_transitions.get(current_status, [])
    is_valid = new_status in allowed
    
    logger.info(f"Transition validation: {current_status} → {new_status} = {is_valid}")
    return is_valid


def _validate_permissions(user_type, current_status, new_status):
    """
    Valida que el usuario tenga permiso para hacer este cambio de estado.
    
    Permisos:
    - chef/staff: cooking, ready, packing
    - driver: dispatched, delivered
    - admin: todo
    """
    
    logger.info(f"Validating permissions: user_type={user_type}, transition={current_status}→{new_status}")
    
    # Admin puede hacer todo
    if user_type == 'admin':
        logger.info("Admin user, permissions granted")
        return True
    
    # Chef solo puede cambiar a estados de cocina
    if user_type == 'staff' or user_type == 'chef':
        allowed_statuses = ['cooking', 'ready', 'packing', 'confirmed']
        if new_status not in allowed_statuses:
            logger.warning(f"Chef tried to change to {new_status}")
            raise UnauthorizedError(
                f"Como chef, solo puedes cambiar a: {', '.join(allowed_statuses)}"
            )
        logger.info("Chef permissions validated")
        return True
    
    # Driver solo puede cambiar a estados de entrega
    if user_type == 'driver':
        allowed_statuses = ['dispatched', 'delivered']
        if new_status not in allowed_statuses:
            logger.warning(f"Driver tried to change to {new_status}")
            raise UnauthorizedError(
                f"Como repartidor, solo puedes cambiar a: {', '.join(allowed_statuses)}"
            )
        logger.info("Driver permissions validated")
        return True
    
    # Otros tipos no pueden cambiar estado
    logger.warning(f"User type {user_type} not authorized to change order status")
    raise UnauthorizedError(
        f"Tu tipo de usuario ({user_type}) no puede cambiar el estado de pedidos"
    )
