"""
services/dashboard/handler.py - VERSIÓN COMPLETA CON TODAS LAS MEJORAS

Reemplaza TODO el archivo services/dashboard/handler.py con este código
"""
import os
from shared.utils import (
    success_response, error_handler, get_tenant_id, get_path_param_from_path,
    get_user_id, get_user_type, get_user_email
)
from shared.dynamodb import DynamoDBService
from shared.errors import ValidationError, NotFoundError, UnauthorizedError
from shared.logger import get_logger

logger = get_logger(__name__)
orders_db = DynamoDBService(os.environ.get('ORDERS_TABLE'))
workflow_db = DynamoDBService(os.environ.get('WORKFLOW_TABLE'))


# ============================================================================
# ✅ FUNCIÓN 1: GET /dashboard - CON RESTRICCIÓN DE ROL
# ============================================================================

@error_handler
def get_dashboard(event, context):
    """
    Obtiene métricas generales del dashboard.
    
    RESTRICCIÓN: Solo staff, chef y admin pueden acceder.
    Los clientes NO deben ver estadísticas generales del negocio.
    
    Métricas incluidas:
    - Total de pedidos
    - Pedidos por estado (pending, confirmed, cooking, etc.)
    - Ingresos totales
    - Últimos 10 pedidos
    """
    logger.info("Getting dashboard metrics")
    
    # ✅ VALIDACIÓN DE ROL - Solo staff, chef y admin
    user_type = get_user_type(event)
    user_id = get_user_id(event)
    
    logger.info(f"Dashboard access attempt by {user_type} {user_id}")
    
    if user_type not in ['staff', 'chef', 'admin']:
        logger.warning(f"Unauthorized dashboard access attempt by {user_type} {user_id}")
        raise UnauthorizedError(
            "Acceso denegado. Solo staff, chefs y administradores pueden ver el dashboard.\n"
            "Los clientes pueden ver sus pedidos en GET /orders"
        )
    
    logger.info(f"Dashboard access granted to {user_type} {user_id}")
    
    # ✅ Obtener tenant
    tenant_id = get_tenant_id(event)
    
    # Obtener todos los pedidos del tenant
    all_orders = orders_db.query_items('tenant_id', tenant_id, index_name='tenant-created-index')
    
    # Calcular métricas
    metrics = {
        'total_orders': len(all_orders),
        'pending': len([o for o in all_orders if o.get('status') == 'pending']),
        'confirmed': len([o for o in all_orders if o.get('status') == 'confirmed']),
        'cooking': len([o for o in all_orders if o.get('status') == 'cooking']),
        'packing': len([o for o in all_orders if o.get('status') == 'packing']),
        'ready': len([o for o in all_orders if o.get('status') == 'ready']),
        'in_delivery': len([o for o in all_orders if o.get('status') == 'in_delivery']),
        'delivered': len([o for o in all_orders if o.get('status') == 'delivered']),
        'total_revenue': sum([float(o.get('total', 0)) for o in all_orders])
    }
    
    # Obtener últimos 10 pedidos
    recent_orders = sorted(
        all_orders, 
        key=lambda x: x.get('created_at', 0), 
        reverse=True
    )[:10]
    
    # Serializar totales de los pedidos recientes
    for order in recent_orders:
        if 'total' in order:
            order['total'] = float(order['total'])
    
    logger.info(f"Dashboard metrics calculated: {metrics['total_orders']} orders for {user_type} {user_id}")
    
    return success_response({
        'metrics': metrics,
        'recent_orders': recent_orders
    })


# ============================================================================
# ✅ FUNCIÓN 2: GET /dashboard/timeline/{order_id} - CON VALIDACIÓN DE PERMISOS
# ============================================================================

@error_handler
def get_order_timeline(event, context):
    """
    Obtiene el timeline completo de un pedido.
    
    PERMISOS POR ROL:
    - Cliente: solo puede ver timeline de sus propios pedidos
    - Chef/Staff: puede ver timeline de cualquier pedido del tenant
    - Admin: puede ver timeline de cualquier pedido del tenant
    - Driver: puede ver timeline de pedidos disponibles o asignados
    
    VALIDACIONES:
    1. El pedido debe existir
    2. El pedido debe pertenecer al mismo tenant
    3. Según el rol, validar permisos adicionales
    """
    logger.info("Getting order timeline with role-based validation")
    
    # ✅ Extraer información del usuario y pedido
    order_id = get_path_param_from_path(event, 'order_id')
    tenant_id = get_tenant_id(event)
    user_type = get_user_type(event)  # ✅ CRÍTICO: Verificar el rol
    user_id = get_user_id(event)
    user_email = get_user_email(event)
    
    logger.info(f"User: {user_id} ({user_type}), requesting timeline for order: {order_id}")
    
    if not order_id:
        raise ValidationError("order_id es requerido")
    
    # ✅ VALIDACIÓN 1: Verificar que el pedido existe
    order = orders_db.get_item({'order_id': order_id})
    if not order:
        logger.warning(f"Order {order_id} not found")
        raise NotFoundError(f"Pedido {order_id} no encontrado")
    
    # ✅ VALIDACIÓN 2: El pedido debe pertenecer al mismo tenant
    if order.get('tenant_id') != tenant_id:
        logger.error(f"Order {order_id} belongs to different tenant")
        raise UnauthorizedError("El pedido no pertenece a tu organización")
    
    # ============================================================================
    # VALIDACIÓN DE PERMISOS POR ROL
    # ============================================================================
    
    # ✅ CASO 1: CLIENTE - Solo puede ver timeline de sus propios pedidos
    if user_type == 'customer':
        logger.info(f"Customer {user_id} requesting timeline for order {order_id}")
        
        if order.get('customer_id') != user_id:
            logger.warning(f"Customer {user_id} tried to access timeline of order {order_id} owned by {order.get('customer_id')}")
            raise UnauthorizedError("No tienes permiso para ver el timeline de este pedido")
        
        logger.info(f"Customer {user_id} authorized to view timeline")
    
    # ✅ CASO 2: CHEF/STAFF - Puede ver timeline de cualquier pedido del tenant
    elif user_type in ['chef', 'staff']:
        logger.info(f"Chef/Staff {user_id} accessing timeline for order {order_id}")
        # ✅ No valida customer_id, puede ver cualquier timeline del tenant
        logger.info(f"Chef/Staff {user_id} authorized to view timeline")
    
    # ✅ CASO 3: ADMIN - Puede ver timeline de cualquier pedido del tenant
    elif user_type == 'admin':
        logger.info(f"Admin {user_id} accessing timeline for order {order_id}")
        # ✅ No valida customer_id, puede ver cualquier timeline del tenant
        logger.info(f"Admin {user_id} authorized to view timeline")
    
    # ✅ CASO 4: DRIVER - Puede ver timeline de pedidos disponibles o asignados
    elif user_type == 'driver':
        logger.info(f"Driver {user_id} ({user_email}) accessing timeline for order {order_id}")
        
        order_status = order.get('status')
        assigned_driver = order.get('assigned_driver')
        driver_identifier = user_email or user_id
        
        is_available = order_status == 'ready'
        is_assigned = (assigned_driver == user_email or 
                      assigned_driver == user_id or 
                      assigned_driver == driver_identifier)
        
        if not (is_available or is_assigned):
            logger.warning(f"Driver {driver_identifier} tried to access unauthorized timeline {order_id}")
            raise UnauthorizedError(
                "Solo puedes ver el timeline de pedidos que estén:\n"
                "• En estado 'ready' (disponibles para recoger)\n"
                "• Asignados a ti"
            )
        
        logger.info(f"Driver {driver_identifier} authorized to view timeline")
    
    # ✅ CASO 5: ROL DESCONOCIDO
    else:
        logger.error(f"Unknown user_type: {user_type}")
        raise UnauthorizedError(f"Tipo de usuario no autorizado: {user_type}")
    
    # ============================================================================
    # OBTENER WORKFLOW Y CONSTRUIR TIMELINE
    # ============================================================================
    
    workflow = workflow_db.get_item({'order_id': order_id})
    
    if not workflow:
        logger.info(f"No workflow found for order {order_id}")
        return success_response({
            'order_id': order_id,
            'timeline': [],
            'total_duration_seconds': 0,
            'total_duration_readable': '0s'
        })
    
    # Construir timeline legible
    steps = workflow.get('steps', [])
    timeline = []
    
    for i, step in enumerate(steps):
        step_info = {
            'step_number': i + 1,
            'status': step.get('status'),
            'assigned_to': step.get('assigned_to'),
            'started_at': step.get('started_at'),
            'completed_at': step.get('completed_at'),
            'duration_seconds': None,
            'duration_readable': None
        }
        
        # Calcular duración si está completado
        if step.get('completed_at') and step.get('started_at'):
            duration = step['completed_at'] - step['started_at']
            step_info['duration_seconds'] = duration
            step_info['duration_readable'] = _format_duration(duration)
        
        timeline.append(step_info)
    
    # Calcular duración total
    total_duration = 0
    if steps and len(steps) > 0:
        first_start = steps[0].get('started_at', 0)
        last_end = steps[-1].get('completed_at') or steps[-1].get('started_at', 0)
        total_duration = last_end - first_start
    
    logger.info(f"Timeline retrieved for order {order_id} by {user_type} {user_id}")
    
    return success_response({
        'order_id': order_id,
        'timeline': timeline,
        'total_duration_seconds': total_duration,
        'total_duration_readable': _format_duration(total_duration)
    })


# ============================================================================
# ✅ FUNCIÓN 3: GET /dashboard/staff-performance - SOLO ADMIN
# ============================================================================

@error_handler
def get_staff_performance(event, context):
    """
    Obtiene estadísticas de rendimiento del staff (cocineros, drivers, etc.).
    
    RESTRICCIÓN: Solo administradores pueden ver esta información.
    
    Métricas incluidas:
    - Total de tareas por staff
    - Tareas completadas
    - Tiempo promedio de ejecución
    - Tasa de completitud
    
    Esta información es sensible y solo debe ser accesible para administradores
    que necesitan evaluar el desempeño del equipo.
    """
    logger.info("Getting staff performance metrics")
    
    # ✅ VALIDACIÓN DE ROL - SOLO ADMIN
    user_type = get_user_type(event)
    user_id = get_user_id(event)
    
    logger.info(f"Staff performance access attempt by {user_type} {user_id}")
    
    if user_type != 'admin':
        logger.warning(f"Unauthorized staff performance access attempt by {user_type} {user_id}")
        raise UnauthorizedError(
            "Acceso denegado. Solo administradores pueden ver el rendimiento del staff.\n"
            "Esta información contiene datos sensibles de evaluación de desempeño."
        )
    
    logger.info(f"Staff performance access granted to admin {user_id}")
    
    # ✅ Obtener tenant
    tenant_id = get_tenant_id(event)
    
    # Obtener todos los pedidos del tenant
    all_orders = orders_db.query_items('tenant_id', tenant_id, index_name='tenant-created-index')
    
    # Diccionario para acumular estadísticas por staff
    staff_stats = {}
    
    for order in all_orders:
        order_id = order.get('order_id')
        workflow = workflow_db.get_item({'order_id': order_id})
        
        if workflow:
            for step in workflow.get('steps', []):
                staff = step.get('assigned_to', 'system')
                
                # Inicializar estadísticas si no existen
                if staff not in staff_stats:
                    staff_stats[staff] = {
                        'name': staff,
                        'total_tasks': 0,
                        'completed_tasks': 0,
                        'avg_time_seconds': 0,
                        'total_time_seconds': 0
                    }
                
                # Contar tarea
                staff_stats[staff]['total_tasks'] += 1
                
                # Si está completada, sumar tiempo
                if step.get('completed_at'):
                    staff_stats[staff]['completed_tasks'] += 1
                    duration = step['completed_at'] - step['started_at']
                    staff_stats[staff]['total_time_seconds'] += duration
    
    # Calcular promedios y tasas de completitud
    for staff in staff_stats.values():
        if staff['completed_tasks'] > 0:
            avg_seconds = int(staff['total_time_seconds'] / staff['completed_tasks'])
            staff['avg_time_seconds'] = avg_seconds
            staff['avg_time_readable'] = _format_duration(avg_seconds)
        
        # Tasa de completitud
        staff['completion_rate'] = round(
            (staff['completed_tasks'] / staff['total_tasks'] * 100) 
            if staff['total_tasks'] > 0 else 0, 
            2
        )
    
    logger.info(f"Staff performance calculated for {len(staff_stats)} staff members by admin {user_id}")
    
    return success_response(list(staff_stats.values()))


# ============================================================================
# FUNCIÓN AUXILIAR
# ============================================================================

def _format_duration(seconds):
    """Formatea duración en segundos a formato legible"""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes}m {secs}s"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m"
