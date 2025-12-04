"""
services/orders/handler.py - VERSIÓN COMPLETA Y CORREGIDA
Reemplaza TODO el contenido del archivo con este código
"""
import os
import boto3
import uuid
from decimal import Decimal
from shared.utils import (
    success_response, error_handler, get_tenant_id, get_user_id, 
    get_user_email, parse_body, current_timestamp, get_path_param_from_path,
    get_user_type
)
from shared.dynamodb import DynamoDBService
from shared.eventbridge import EventBridgeService
from shared.errors import NotFoundError, ValidationError, UnauthorizedError
from shared.logger import get_logger

logger = get_logger(__name__)
orders_db = DynamoDBService(os.environ.get('ORDERS_TABLE'))
workflow_db = DynamoDBService(os.environ.get('WORKFLOW_TABLE'))

# Cliente de Step Functions
sfn_client = boto3.client('stepfunctions')


# ============================================================================
# FUNCIÓN 1: CREATE ORDER - ✅ CORREGIDA
# ============================================================================

@error_handler
def create_order(event, context):
    """
    Crea un nuevo pedido y lanza el workflow automático con Step Functions
    
    POST /orders
    Body: {
        "items": [{"item_id": "combo-1", "name": "Combo Mega", "quantity": 1, "price": 29.99}],
        "delivery_address": "Av. Principal 123",
        "delivery_instructions": "Tocar timbre"
    }
    """
    logger.info("Creating new order")
    
    # Extraer info del usuario
    tenant_id = get_tenant_id(event)
    user_id = get_user_id(event)
    user_email = get_user_email(event)
    
    if not user_id:
        raise ValidationError("No se pudo identificar al usuario")
    
    logger.info(f"User {user_id} ({user_email}) creating order")
    
    # Parsear body
    body = parse_body(event)
    
    # Validar campos requeridos
    items = body.get('items', [])
    delivery_address = body.get('delivery_address', '').strip()
    delivery_instructions = body.get('delivery_instructions', '').strip()
    
    if not items or len(items) == 0:
        raise ValidationError("items es requerido y no puede estar vacío")
    
    if not delivery_address:
        raise ValidationError("delivery_address es requerido")
    
    # ✅ Validar y convertir items a Decimal (DynamoDB NO acepta float)
    processed_items = []
    for item in items:
        if 'item_id' not in item or 'name' not in item or 'quantity' not in item or 'price' not in item:
            raise ValidationError("Cada item debe tener: item_id, name, quantity, price")
        
        quantity = item['quantity']
        price = item['price']
        
        if not isinstance(quantity, (int, float)) or quantity <= 0:
            raise ValidationError("quantity debe ser un número positivo")
        
        if not isinstance(price, (int, float)) or price <= 0:
            raise ValidationError("price debe ser un número positivo")
        
        # ✅ Convertir a Decimal ANTES de guardar en DynamoDB
        processed_item = {
            'item_id': item['item_id'],
            'name': item['name'],
            'quantity': Decimal(str(quantity)),
            'price': Decimal(str(price))
        }
        processed_items.append(processed_item)
    
    # ✅ Calcular total (ya es Decimal)
    total = sum(item['quantity'] * item['price'] for item in processed_items)
    
    # Generar order_id único
    order_id = str(uuid.uuid4())
    timestamp = current_timestamp()
    
    # ✅ Construir orden con tipos correctos
    order = {
        'order_id': order_id,
        'tenant_id': tenant_id,
        'customer_id': user_id,
        'customer_email': user_email or f"{user_id}@200millas.com",
        'items': processed_items,  # ✅ Items con Decimal
        'total': total,  # ✅ Ya es Decimal
        'delivery_address': delivery_address,
        'delivery_instructions': delivery_instructions,
        'status': 'pending',
        'created_at': timestamp,
        'updated_at': timestamp
    }
    
    # Guardar en DynamoDB
    success = orders_db.put_item(order)
    if not success:
        raise Exception("Error al crear el pedido en la base de datos")
    
    logger.info(f"Order {order_id} created successfully")
    
    # Inicializar workflow en DynamoDB
    workflow = {
        'order_id': order_id,
        'current_status': 'pending',
        'steps': [
            {
                'status': 'pending',
                'assigned_to': 'system',
                'started_at': timestamp,
                'completed_at': None
            }
        ],
        'created_at': timestamp,
        'updated_at': timestamp
    }
    workflow_db.put_item(workflow)
    
    logger.info(f"Workflow initialized for order {order_id}")
    
    # Publicar evento de creación
    EventBridgeService.put_event(
        source='orders.service',
        detail_type='OrderCreated',
        detail={
            'order_id': order_id,
            'customer_id': user_id,
            'total': float(total),
            'status': 'pending'
        },
        tenant_id=tenant_id
    )
    
    # ✅ INICIAR STEP FUNCTIONS WORKFLOW
    try:
        state_machine_arn = _get_state_machine_arn()
        
        logger.info(f"Starting Step Functions execution for order {order_id}")
        
        execution_response = sfn_client.start_execution(
            stateMachineArn=state_machine_arn,
            name=f"order-{order_id}-{timestamp}",
            input=str({
                'order_id': order_id,
                'tenant_id': tenant_id,
                'customer_id': user_id
            }).replace("'", '"')  # Convertir a JSON válido
        )
        
        execution_arn = execution_response.get('executionArn')
        logger.info(f"✅ Step Functions started: {execution_arn}")
        
        # Guardar execution ARN en el workflow
        workflow_db.update_item(
            {'order_id': order_id},
            {'execution_arn': execution_arn}
        )
        
    except Exception as e:
        logger.error(f"⚠️ Error starting Step Functions: {str(e)}")
        # No fallar la creación del pedido, solo loggear el error
        # El pedido se puede procesar manualmente si Step Functions falla
    
    # ✅ Serializar respuesta (convertir Decimal a float para JSON)
    order_response = dict(order)
    order_response['total'] = float(total)
    order_response['items'] = _serialize_items(processed_items)
    
    logger.info(f"✅ Order {order_id} created and workflow started successfully")
    
    return success_response({
        'order_id': order_id,
        'status': 'pending',
        'total': float(total),
        'message': 'Pedido creado exitosamente. El workflow automático ha comenzado.',
        'order': order_response
    }, 201)


# ============================================================================
# FUNCIÓN 2: GET ORDERS
# ============================================================================

@error_handler
def get_orders(event, context):
    """
    Obtiene pedidos según el rol del usuario.
    
    ROLES Y PERMISOS:
    - Cliente (customer): solo sus propios pedidos
    - Chef/Staff (chef/staff): todos los pedidos del tenant (con filtros opcionales)
    - Admin (admin): todos los pedidos del tenant (sin restricciones)
    - Driver (driver): debe usar endpoints específicos (/driver/available, /driver/assigned)
    """
    logger.info("Getting orders with role-based logic")
    
    tenant_id = get_tenant_id(event)
    user_type = get_user_type(event)
    user_id = get_user_id(event)
    user_email = get_user_email(event)
    
    logger.info(f"User: {user_id} ({user_email}), Type: {user_type}, Tenant: {tenant_id}")
    
    # CASO 1: CLIENTE - Solo sus propios pedidos
    if user_type == 'customer':
        logger.info(f"Customer {user_id} requesting their orders")
        
        if not user_id:
            raise ValidationError("No se pudo identificar al usuario")
        
        items = orders_db.query_items(
            'customer_id',
            user_id,
            index_name='customer-orders-index'
        )
        
        logger.info(f"Found {len(items)} orders for customer {user_id}")
    
    # CASO 2: CHEF/STAFF - Todos los pedidos del tenant
    elif user_type in ['chef', 'staff']:
        logger.info(f"Chef/Staff {user_id} requesting orders")
        
        items = orders_db.query_items(
            'tenant_id',
            tenant_id,
            index_name='tenant-created-index'
        )
        
        logger.info(f"Chef/Staff retrieved {len(items)} orders from tenant")
        
        # Filtros opcionales
        query_params = event.get('queryStringParameters') or {}
        
        status_filter = query_params.get('status', '').strip().lower()
        if status_filter:
            original_count = len(items)
            items = [o for o in items if o.get('status') == status_filter]
            logger.info(f"Filtered by status '{status_filter}': {len(items)}/{original_count} orders")
        
        statuses_filter = query_params.get('statuses', '').strip().lower()
        if statuses_filter:
            allowed_statuses = [s.strip() for s in statuses_filter.split(',')]
            original_count = len(items)
            items = [o for o in items if o.get('status') in allowed_statuses]
            logger.info(f"Filtered by statuses {allowed_statuses}: {len(items)}/{original_count} orders")
    
    # CASO 3: ADMIN - Todos los pedidos sin restricciones
    elif user_type == 'admin':
        logger.info(f"Admin {user_id} requesting all orders")
        
        items = orders_db.query_items(
            'tenant_id',
            tenant_id,
            index_name='tenant-created-index'
        )
        
        logger.info(f"Admin retrieved {len(items)} orders from tenant")
        
        query_params = event.get('queryStringParameters') or {}
        
        status_filter = query_params.get('status', '').strip().lower()
        if status_filter:
            original_count = len(items)
            items = [o for o in items if o.get('status') == status_filter]
            logger.info(f"Admin filtered by status '{status_filter}': {len(items)}/{original_count}")
        
        customer_filter = query_params.get('customer_id', '').strip()
        if customer_filter:
            original_count = len(items)
            items = [o for o in items if o.get('customer_id') == customer_filter]
            logger.info(f"Admin filtered by customer '{customer_filter}': {len(items)}/{original_count}")
    
    # CASO 4: DRIVER - Redirigir a endpoints específicos
    elif user_type == 'driver':
        logger.warning(f"Driver {user_id} using wrong endpoint")
        raise ValidationError(
            "Como driver, usa estos endpoints específicos:\n"
            "• GET /driver/available - Ver pedidos listos para recoger\n"
            "• GET /driver/assigned - Ver tus pedidos asignados\n"
            "• GET /driver/orders/{order_id} - Ver detalle de un pedido"
        )
    
    else:
        logger.error(f"Unknown user_type: {user_type}")
        raise UnauthorizedError(f"Tipo de usuario no autorizado: {user_type}")
    
    # ✅ Serializar respuesta (Convertir Decimals a float)
    serialized_items = []
    for order in items:
        serialized_order = dict(order)
        
        if 'total' in serialized_order:
            serialized_order['total'] = float(serialized_order['total'])
        
        if 'items' in serialized_order:
            serialized_order['items'] = _serialize_items(serialized_order['items'])
        
        serialized_items.append(serialized_order)
    
    # Ordenar por fecha de creación
    serialized_items.sort(key=lambda x: x.get('created_at', 0), reverse=True)
    
    logger.info(f"Returning {len(serialized_items)} serialized orders")
    
    return success_response(serialized_items)


# ============================================================================
# FUNCIÓN 3: GET ORDER (DETALLE)
# ============================================================================

@error_handler
def get_order(event, context):
    """
    Obtiene el detalle de un pedido específico con validación de permisos
    """
    logger.info("Getting order detail with role-based logic")
    
    order_id = get_path_param_from_path(event, 'order_id')
    user_type = get_user_type(event)
    user_id = get_user_id(event)
    user_email = get_user_email(event)
    tenant_id = get_tenant_id(event)
    
    logger.info(f"User: {user_id} ({user_email}), Type: {user_type}, Requesting order: {order_id}")
    
    if not order_id:
        raise ValidationError("order_id es requerido")
    
    # Obtener el pedido
    order = orders_db.get_item({'order_id': order_id})
    
    if not order:
        logger.warning(f"Order {order_id} not found")
        raise NotFoundError(f"Pedido {order_id} no encontrado")
    
    # Verificar tenant
    if order.get('tenant_id') != tenant_id:
        logger.error(f"Order {order_id} belongs to different tenant")
        raise UnauthorizedError("El pedido no pertenece a tu organización")
    
    # Validaciones por rol
    if user_type == 'customer':
        if order.get('customer_id') != user_id:
            logger.warning(f"Customer {user_id} tried to access order {order_id}")
            raise UnauthorizedError("No tienes permiso para ver este pedido")
    
    elif user_type == 'driver':
        order_status = order.get('status')
        assigned_driver = order.get('assigned_driver')
        driver_identifier = user_email or user_id
        
        is_available = order_status == 'ready'
        is_assigned = (assigned_driver == user_email or 
                      assigned_driver == user_id or 
                      assigned_driver == driver_identifier)
        
        if not (is_available or is_assigned):
            logger.warning(f"Driver {driver_identifier} tried to access unauthorized order")
            raise UnauthorizedError("Solo puedes ver pedidos disponibles o asignados a ti")
    
    # ✅ Serializar respuesta
    serialized_order = dict(order)
    
    if 'total' in serialized_order:
        serialized_order['total'] = float(serialized_order['total'])
    
    if 'items' in serialized_order:
        serialized_order['items'] = _serialize_items(serialized_order['items'])
    
    logger.info(f"Order {order_id} details retrieved successfully")
    
    return success_response(serialized_order)


# ============================================================================
# FUNCIÓN 4: UPDATE ORDER STATUS (Opcional - Chef/Admin)
# ============================================================================

@error_handler
def update_order_status(event, context):
    """
    Actualiza el estado de un pedido manualmente
    Solo para Chef/Staff/Admin
    
    PATCH /orders/{order_id}/status
    Body: {"status": "cooking", "notes": "Comenzando preparación"}
    """
    logger.info("Updating order status manually")
    
    order_id = get_path_param_from_path(event, 'order_id')
    user_type = get_user_type(event)
    user_id = get_user_id(event)
    
    if not order_id:
        raise ValidationError("order_id es requerido")
    
    # Solo chef, staff y admin pueden actualizar estados
    if user_type not in ['chef', 'staff', 'admin']:
        raise UnauthorizedError("Solo chef, staff y admin pueden actualizar estados de pedidos")
    
    body = parse_body(event)
    new_status = body.get('status', '').strip().lower()
    notes = body.get('notes', '').strip()
    
    if not new_status:
        raise ValidationError("status es requerido")
    
    valid_statuses = ['pending', 'confirmed', 'cooking', 'packing', 'ready', 'in_delivery', 'delivered']
    if new_status not in valid_statuses:
        raise ValidationError(f"Estado inválido. Válidos: {', '.join(valid_statuses)}")
    
    # Verificar que el pedido existe
    order = orders_db.get_item({'order_id': order_id})
    if not order:
        raise NotFoundError(f"Pedido {order_id} no encontrado")
    
    timestamp = current_timestamp()
    
    # Actualizar Orders table
    orders_db.update_item(
        {'order_id': order_id},
        {
            'status': new_status,
            'updated_at': timestamp,
            'updated_by': user_id
        }
    )
    
    # Actualizar Workflow
    workflow = workflow_db.get_item({'order_id': order_id})
    if workflow:
        # Completar step anterior si existe
        if workflow.get('steps'):
            last_step = workflow['steps'][-1]
            if not last_step.get('completed_at'):
                last_step['completed_at'] = timestamp
        
        # Agregar nuevo step
        new_step = {
            'status': new_status,
            'assigned_to': user_id,
            'started_at': timestamp,
            'completed_at': None,
            'notes': notes or f'Actualizado manualmente por {user_type} {user_id}'
        }
        workflow['steps'].append(new_step)
        workflow['current_status'] = new_status
        workflow['updated_at'] = timestamp
        workflow_db.put_item(workflow)
    
    # Publicar evento
    EventBridgeService.put_event(
        source='orders.service',
        detail_type='OrderStatusChanged',
        detail={
            'order_id': order_id,
            'status': new_status,
            'updated_by': user_id,
            'notes': notes
        },
        tenant_id=get_tenant_id(event)
    )
    
    logger.info(f"Order {order_id} status updated to {new_status} by {user_type} {user_id}")
    
    return success_response({
        'order_id': order_id,
        'status': new_status,
        'message': f'Estado actualizado a {new_status}'
    })


# ============================================================================
# FUNCIONES AUXILIARES
# ============================================================================

def _serialize_items(items):
    """Convierte todos los Decimals a float para JSON serialization"""
    serialized = []
    for item in items:
        serialized_item = {}
        for key, value in item.items():
            if isinstance(value, Decimal):
                serialized_item[key] = float(value)
            else:
                serialized_item[key] = value
        serialized.append(serialized_item)
    return serialized


def _get_state_machine_arn():
    """Construye el ARN de la Step Function"""
    region = os.environ.get('AWS_REGION', 'us-east-1')
    account_id = os.environ.get('AWS_ACCOUNT_ID', '722204368591')
    service = os.environ.get('SERVERLESS_SERVICE', 'millas-backend')
    stage = os.environ.get('SERVERLESS_STAGE', 'dev')
    
    state_machine_name = f"{service}-{stage}-order-workflow"
    arn = f"arn:aws:states:{region}:{account_id}:stateMachine:{state_machine_name}"
    
    logger.info(f"State Machine ARN: {arn}")
    return arn
