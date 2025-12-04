"""
Dashboard Handler - VERSIÓN CORREGIDA
Fix: Mejor manejo de errores y serialización de Decimals
"""
import os
from decimal import Decimal
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


def _serialize_decimal(obj):
    """Convierte Decimal a float para JSON"""
    if isinstance(obj, Decimal):
        return float(obj)
    return obj


@error_handler
def get_dashboard(event, context):
    """
    Obtiene métricas generales del dashboard
    Solo staff, chef y admin
    """
    logger.info("Getting dashboard metrics")
    
    user_type = get_user_type(event)
    user_id = get_user_id(event)
    
    if user_type not in ['staff', 'chef', 'admin']:
        raise UnauthorizedError(
            "Solo staff, chefs y administradores pueden ver el dashboard"
        )
    
    tenant_id = get_tenant_id(event)
    
    try:
        # Obtener pedidos
        all_orders = orders_db.query_items(
            'tenant_id',
            tenant_id,
            index_name='tenant-created-index'
        )
        
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
            'total_revenue': sum([
                _serialize_decimal(o.get('total', 0))
                for o in all_orders
            ])
        }
        
        # Últimos 10 pedidos
        recent_orders = sorted(
            all_orders,
            key=lambda x: x.get('created_at', 0),
            reverse=True
        )[:10]
        
        # Serializar decimals en pedidos recientes
        serialized_orders = []
        for order in recent_orders:
            serialized_order = {}
            for key, value in order.items():
                if isinstance(value, Decimal):
                    serialized_order[key] = float(value)
                elif isinstance(value, list):
                    # Serializar items
                    serialized_items = []
                    for item in value:
                        if isinstance(item, dict):
                            serialized_item = {
                                k: float(v) if isinstance(v, Decimal) else v
                                for k, v in item.items()
                            }
                            serialized_items.append(serialized_item)
                        else:
                            serialized_items.append(item)
                    serialized_order[key] = serialized_items
                else:
                    serialized_order[key] = value
            serialized_orders.append(serialized_order)
        
        logger.info(f"Dashboard metrics: {metrics['total_orders']} orders")
        
        return success_response({
            'metrics': metrics,
            'recent_orders': serialized_orders
        })
        
    except Exception as e:
        logger.error(f"Error getting dashboard: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        raise e


@error_handler
def get_order_timeline(event, context):
    """Obtiene timeline de un pedido con validación de permisos"""
    logger.info("Getting order timeline")
    
    order_id = get_path_param_from_path(event, 'order_id')
    tenant_id = get_tenant_id(event)
    user_type = get_user_type(event)
    user_id = get_user_id(event)
    user_email = get_user_email(event)
    
    if not order_id:
        raise ValidationError("order_id es requerido")
    
    order = orders_db.get_item({'order_id': order_id})
    if not order:
        raise NotFoundError(f"Pedido {order_id} no encontrado")
    
    if order.get('tenant_id') != tenant_id:
        raise UnauthorizedError("El pedido no pertenece a tu organización")
    
    # Validaciones por rol
    if user_type == 'customer':
        if order.get('customer_id') != user_id:
            raise UnauthorizedError("No tienes permiso para ver este timeline")
    
    elif user_type == 'driver':
        order_status = order.get('status')
        assigned_driver = order.get('assigned_driver')
        driver_identifier = user_email or user_id
        
        is_available = order_status == 'ready'
        is_assigned = (assigned_driver == user_email or 
                      assigned_driver == user_id or 
                      assigned_driver == driver_identifier)
        
        if not (is_available or is_assigned):
            raise UnauthorizedError(
                "Solo puedes ver el timeline de pedidos disponibles o asignados a ti"
            )
    
    # Obtener workflow
    workflow = workflow_db.get_item({'order_id': order_id})
    
    if not workflow:
        return success_response({
            'order_id': order_id,
            'timeline': [],
            'total_duration_seconds': 0,
            'total_duration_readable': '0s'
        })
    
    # Construir timeline
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
        
        if step.get('completed_at') and step.get('started_at'):
            duration = step['completed_at'] - step['started_at']
            step_info['duration_seconds'] = duration
            step_info['duration_readable'] = _format_duration(duration)
        
        timeline.append(step_info)
    
    # Duración total
    total_duration = 0
    if steps and len(steps) > 0:
        first_start = steps[0].get('started_at', 0)
        last_end = steps[-1].get('completed_at') or steps[-1].get('started_at', 0)
        total_duration = last_end - first_start
    
    return success_response({
        'order_id': order_id,
        'timeline': timeline,
        'total_duration_seconds': total_duration,
        'total_duration_readable': _format_duration(total_duration)
    })


@error_handler
def get_staff_performance(event, context):
    """Obtiene rendimiento del staff - Solo admin"""
    logger.info("Getting staff performance")
    
    user_type = get_user_type(event)
    user_id = get_user_id(event)
    
    if user_type != 'admin':
        raise UnauthorizedError(
            "Solo administradores pueden ver el rendimiento del staff"
        )
    
    tenant_id = get_tenant_id(event)
    
    all_orders = orders_db.query_items(
        'tenant_id',
        tenant_id,
        index_name='tenant-created-index'
    )
    
    staff_stats = {}
    
    for order in all_orders:
        order_id = order.get('order_id')
        workflow = workflow_db.get_item({'order_id': order_id})
        
        if workflow:
            for step in workflow.get('steps', []):
                staff = step.get('assigned_to', 'system')
                
                if staff not in staff_stats:
                    staff_stats[staff] = {
                        'name': staff,
                        'total_tasks': 0,
                        'completed_tasks': 0,
                        'avg_time_seconds': 0,
                        'total_time_seconds': 0
                    }
                
                staff_stats[staff]['total_tasks'] += 1
                
                if step.get('completed_at'):
                    staff_stats[staff]['completed_tasks'] += 1
                    duration = step['completed_at'] - step['started_at']
                    staff_stats[staff]['total_time_seconds'] += duration
    
    for staff in staff_stats.values():
        if staff['completed_tasks'] > 0:
            avg_seconds = int(staff['total_time_seconds'] / staff['completed_tasks'])
            staff['avg_time_seconds'] = avg_seconds
            staff['avg_time_readable'] = _format_duration(avg_seconds)
        
        staff['completion_rate'] = round(
            (staff['completed_tasks'] / staff['total_tasks'] * 100) 
            if staff['total_tasks'] > 0 else 0, 
            2
        )
    
    return success_response(list(staff_stats.values()))


def _format_duration(seconds):
    """Formatea duración en segundos"""
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
