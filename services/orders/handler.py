"""
SOLUCIÓN: GET /orders y GET /orders/{order_id} con lógica diferenciada por rol

Reemplaza estas dos funciones en services/orders/handler.py
"""
import os
from decimal import Decimal
from shared.utils import (
    success_response, error_handler, get_tenant_id, get_user_id, 
    get_user_email, parse_body, current_timestamp, get_path_param_from_path,
    get_user_type
)
from shared.dynamodb import DynamoDBService
from shared.errors import NotFoundError, ValidationError, UnauthorizedError
from shared.logger import get_logger

logger = get_logger(__name__)
orders_db = DynamoDBService(os.environ.get('ORDERS_TABLE'))
workflow_db = DynamoDBService(os.environ.get('WORKFLOW_TABLE'))


# ============================================================================
# ✅ FUNCIÓN 1: GET /orders - CORREGIDA
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
    
    FILTROS DISPONIBLES (query params):
    - ?status=pending - Filtra por un estado específico
    - ?statuses=pending,cooking,ready - Filtra por múltiples estados
    - ?customer_id=john - Solo para admins, filtra por cliente
    """
    logger.info("Getting orders with role-based logic")
    
    # ✅ Extraer información del usuario autenticado
    tenant_id = get_tenant_id(event)
    user_type = get_user_type(event)  # ✅ CRÍTICO: Verificar el rol
    user_id = get_user_id(event)
    user_email = get_user_email(event)
    
    logger.info(f"User: {user_id} ({user_email}), Type: {user_type}, Tenant: {tenant_id}")
    
    # ============================================================================
    # LÓGICA DIFERENCIADA POR ROL
    # ============================================================================
    
    # ✅ CASO 1: CLIENTE - Solo sus propios pedidos
    if user_type == 'customer':
        logger.info(f"Customer {user_id} requesting their orders")
        
        if not user_id:
            raise ValidationError("No se pudo identificar al usuario. Por favor, inicia sesión nuevamente.")
        
        # Query directo al índice customer-orders-index
        items = orders_db.query_items(
            'customer_id',
            user_id,
            index_name='customer-orders-index'
        )
        
        logger.info(f"Found {len(items)} orders for customer {user_id}")
    
    # ✅ CASO 2: CHEF/STAFF - Todos los pedidos del tenant (con filtros)
    elif user_type in ['chef', 'staff']:
        logger.info(f"Chef/Staff {user_id} requesting orders")
        
        # Obtener TODOS los pedidos del tenant
        items = orders_db.query_items(
            'tenant_id',
            tenant_id,
            index_name='tenant-created-index'
        )
        
        logger.info(f"Chef/Staff retrieved {len(items)} orders from tenant")
        
        # ✅ FILTROS OPCIONALES por query parameters
        query_params = event.get('queryStringParameters') or {}
        
        # Filtro 1: Por un solo estado (?status=pending)
        status_filter = query_params.get('status', '').strip().lower()
        if status_filter:
            original_count = len(items)
            items = [o for o in items if o.get('status') == status_filter]
            logger.info(f"Filtered by status '{status_filter}': {len(items)}/{original_count} orders")
        
        # Filtro 2: Por múltiples estados (?statuses=pending,cooking,ready)
        statuses_filter = query_params.get('statuses', '').strip().lower()
        if statuses_filter:
            allowed_statuses = [s.strip() for s in statuses_filter.split(',')]
            original_count = len(items)
            items = [o for o in items if o.get('status') in allowed_statuses]
            logger.info(f"Filtered by statuses {allowed_statuses}: {len(items)}/{original_count} orders")
        
        logger.info(f"Chef/Staff final result: {len(items)} orders")
    
    # ✅ CASO 3: ADMIN - Todos los pedidos sin restricciones
    elif user_type == 'admin':
        logger.info(f"Admin {user_id} requesting all orders")
        
        # Obtener TODOS los pedidos del tenant
        items = orders_db.query_items(
            'tenant_id',
            tenant_id,
            index_name='tenant-created-index'
        )
        
        logger.info(f"Admin retrieved {len(items)} orders from tenant")
        
        # ✅ Admin puede filtrar opcionalmente
        query_params = event.get('queryStringParameters') or {}
        
        # Filtro por estado
        status_filter = query_params.get('status', '').strip().lower()
        if status_filter:
            original_count = len(items)
            items = [o for o in items if o.get('status') == status_filter]
            logger.info(f"Admin filtered by status '{status_filter}': {len(items)}/{original_count}")
        
        # Filtro por cliente (solo admin puede filtrar por customer_id)
        customer_filter = query_params.get('customer_id', '').strip()
        if customer_filter:
            original_count = len(items)
            items = [o for o in items if o.get('customer_id') == customer_filter]
            logger.info(f"Admin filtered by customer '{customer_filter}': {len(items)}/{original_count}")
        
        logger.info(f"Admin final result: {len(items)} orders")
    
    # ✅ CASO 4: DRIVER - Redirigir a endpoints específicos
    elif user_type == 'driver':
        logger.warning(f"Driver {user_id} using wrong endpoint")
        raise ValidationError(
            "Como driver, usa estos endpoints específicos:\n"
            "• GET /driver/available - Ver pedidos listos para recoger\n"
            "• GET /driver/assigned - Ver tus pedidos asignados\n"
            "• GET /driver/orders/{order_id} - Ver detalle de un pedido"
        )
    
    # ✅ CASO 5: ROL DESCONOCIDO
    else:
        logger.error(f"Unknown user_type: {user_type}")
        raise UnauthorizedError(f"Tipo de usuario no autorizado: {user_type}")
    
    # ============================================================================
    # SERIALIZAR RESPUESTA (Convertir Decimals a float)
    # ============================================================================
    
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
    
    # ✅ Ordenar por fecha de creación (más reciente primero)
    serialized_items.sort(key=lambda x: x.get('created_at', 0), reverse=True)
    
    logger.info(f"Returning {len(serialized_items)} serialized orders")
    
    return success_response(serialized_items)


# ============================================================================
# ✅ FUNCIÓN 2: GET /orders/{order_id} - CORREGIDA
# ============================================================================

@error_handler
def get_order(event, context):
    """
    Obtiene el detalle de un pedido específico.
    
    ROLES Y PERMISOS:
    - Cliente (customer): solo puede ver sus propios pedidos
    - Chef/Staff (chef/staff): puede ver cualquier pedido del tenant
    - Admin (admin): puede ver cualquier pedido del tenant
    - Driver (driver): puede ver pedidos disponibles o asignados a él
    
    VALIDACIONES:
    1. El pedido debe existir
    2. El pedido debe pertenecer al mismo tenant
    3. Según el rol, se validan permisos adicionales
    """
    logger.info("Getting order detail with role-based logic")
    
    # ✅ Extraer información del usuario y pedido
    order_id = get_path_param_from_path(event, 'order_id')
    user_type = get_user_type(event)  # ✅ CRÍTICO: Verificar el rol
    user_id = get_user_id(event)
    user_email = get_user_email(event)
    tenant_id = get_tenant_id(event)
    
    logger.info(f"User: {user_id} ({user_email}), Type: {user_type}, Requesting order: {order_id}")
    
    if not order_id:
        raise ValidationError("order_id es requerido")
    
    # ✅ Obtener el pedido de DynamoDB
    order = orders_db.get_item({'order_id': order_id})
    
    if not order:
        logger.warning(f"Order {order_id} not found")
        raise NotFoundError(f"Pedido {order_id} no encontrado")
    
    # ✅ VALIDACIÓN 1: El pedido debe pertenecer al mismo tenant
    if order.get('tenant_id') != tenant_id:
        logger.error(f"Order {order_id} belongs to different tenant")
        raise UnauthorizedError("El pedido no pertenece a tu organización")
    
    # ============================================================================
    # LÓGICA DIFERENCIADA POR ROL
    # ============================================================================
    
    # ✅ CASO 1: CLIENTE - Solo puede ver sus propios pedidos
    if user_type == 'customer':
        logger.info(f"Customer {user_id} requesting order {order_id}")
        
        # Validar que el pedido pertenece a este cliente
        if order.get('customer_id') != user_id:
            logger.warning(f"Customer {user_id} tried to access order {order_id} owned by {order.get('customer_id')}")
            raise UnauthorizedError("No tienes permiso para ver este pedido")
        
        logger.info(f"Customer {user_id} authorized to view order {order_id}")
    
    # ✅ CASO 2: CHEF/STAFF - Puede ver cualquier pedido del tenant
    elif user_type in ['chef', 'staff']:
        logger.info(f"Chef/Staff {user_id} accessing order {order_id}")
        # ✅ No se valida customer_id, puede ver cualquier pedido del tenant
        logger.info(f"Chef/Staff {user_id} authorized to view order {order_id}")
    
    # ✅ CASO 3: ADMIN - Puede ver cualquier pedido del tenant
    elif user_type == 'admin':
        logger.info(f"Admin {user_id} accessing order {order_id}")
        # ✅ No se valida customer_id, puede ver cualquier pedido del tenant
        logger.info(f"Admin {user_id} authorized to view order {order_id}")
    
    # ✅ CASO 4: DRIVER - Puede ver pedidos disponibles o asignados
    elif user_type == 'driver':
        logger.info(f"Driver {user_id} ({user_email}) accessing order {order_id}")
        
        # Drivers pueden ver:
        # 1. Pedidos en estado 'ready' (disponibles para recoger)
        # 2. Pedidos asignados a ellos (assigned_driver coincide)
        
        order_status = order.get('status')
        assigned_driver = order.get('assigned_driver')
        driver_identifier = user_email or user_id
        
        is_available = order_status == 'ready'
        is_assigned = (assigned_driver == user_email or 
                      assigned_driver == user_id or 
                      assigned_driver == driver_identifier)
        
        if not (is_available or is_assigned):
            logger.warning(f"Driver {driver_identifier} tried to access unauthorized order {order_id}")
            raise UnauthorizedError(
                "Solo puedes ver pedidos que estén:\n"
                "• En estado 'ready' (disponibles para recoger)\n"
                "• Asignados a ti (assigned_driver coincide)"
            )
        
        logger.info(f"Driver {driver_identifier} authorized to view order {order_id}")
    
    # ✅ CASO 5: ROL DESCONOCIDO
    else:
        logger.error(f"Unknown user_type: {user_type}")
        raise UnauthorizedError(f"Tipo de usuario no autorizado: {user_type}")
    
    # ============================================================================
    # SERIALIZAR RESPUESTA
    # ============================================================================
    
    serialized_order = dict(order)
    
    # Convertir total a float
    if 'total' in serialized_order:
        serialized_order['total'] = float(serialized_order['total'])
    
    # Serializar items dentro de la orden
    if 'items' in serialized_order:
        serialized_order['items'] = _serialize_items(serialized_order['items'])
    
    logger.info(f"Order {order_id} details retrieved successfully for {user_type} {user_id}")
    
    return success_response(serialized_order)


# ============================================================================
# FUNCIÓN AUXILIAR (ya existe en tu código)
# ============================================================================

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
