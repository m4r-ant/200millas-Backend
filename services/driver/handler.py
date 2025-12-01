import os
import json
from decimal import Decimal
from shared.utils import (
    success_response, error_handler, get_tenant_id, get_user_id, 
    get_user_email, parse_body, current_timestamp, get_path_param_from_path
)
from shared.dynamodb import DynamoDBService
from shared.errors import NotFoundError, ValidationError, UnauthorizedError
from shared.logger import get_logger

logger = get_logger(__name__)
orders_db = DynamoDBService(os.environ.get('ORDERS_TABLE'))
workflow_db = DynamoDBService(os.environ.get('WORKFLOW_TABLE'))

"""
Driver Handler - Read-Only Mode
================================
Compatible con Step Functions - Solo consulta, NO modifica estados

IMPORTANTE: Step Functions maneja TODAS las transiciones de estado automáticamente.
Los endpoints del driver son solo para:
  1. Ver pedidos disponibles
  2. Ver pedidos asignados
  3. Ver estadísticas personales
  4. Ver timeline de entregas

NO hay endpoints para cambiar estados manualmente (pickup, complete, etc.)
porque Step Functions ya los maneja automáticamente.
"""

# ============================================================================
# ENDPOINTS READ-ONLY - NO MODIFICAN ESTADO (Compatible con Step Functions)
# ============================================================================

@error_handler
def get_available_orders(event, context):
    """
    Obtiene pedidos listos para recoger (estado: ready o packing)
    Solo consulta, NO modifica estado
    """
    logger.info("Getting available orders for driver")
    
    tenant_id = get_tenant_id(event)
    user_email = get_user_email(event)
    user_id = get_user_id(event)
    
    driver_identifier = user_email or user_id
    logger.info(f"Driver {driver_identifier} requesting available orders")
    
    # Query pedidos del tenant
    all_orders = orders_db.query_items(
        'tenant_id',
        tenant_id,
        index_name='tenant-created-index'
    )
    
    # Filtrar solo los que están listos para recoger
    # Step Functions pone los pedidos en 'packing' o 'ready'
    available_orders = [
        o for o in all_orders 
        if o.get('status') in ['ready', 'packing']
    ]
    
    # Enriquecer con información del workflow
    enriched_orders = []
    for order in available_orders:
        order_id = order.get('order_id')
        workflow = workflow_db.get_item({'order_id': order_id})
        
        enriched_order = dict(order)
        
        # Agregar info del workflow
        if workflow:
            enriched_order['workflow'] = {
                'current_status': workflow.get('current_status'),
                'updated_at': workflow.get('updated_at'),
                'steps_completed': len([s for s in workflow.get('steps', []) if s.get('completed_at')])
            }
        
        # Serializar Decimals
        if 'total' in enriched_order:
            enriched_order['total'] = float(enriched_order['total'])
        if 'items' in enriched_order:
            for item in enriched_order.get('items', []):
                if 'price' in item:
                    item['price'] = float(item['price'])
        
        enriched_orders.append(enriched_order)
    
    # Ordenar por tiempo de creación (más reciente primero)
    enriched_orders.sort(key=lambda x: x.get('created_at', 0), reverse=True)
    
    logger.info(f"Found {len(enriched_orders)} available orders")
    
    return success_response({
        'orders': enriched_orders,
        'count': len(enriched_orders),
        'message': 'Estos pedidos están siendo procesados automáticamente por el sistema'
    })


@error_handler
def get_assigned_orders(event, context):
    """
    Obtiene pedidos asignados al driver actual
    Basado en el workflow, NO modifica nada
    
    ✅ FIXED: Mejor manejo de email fallido
    """
    logger.info("Getting assigned orders for driver")
    
    tenant_id = get_tenant_id(event)
    user_email = get_user_email(event)
    user_id = get_user_id(event)
    
    # ✅ CAMBIO: Si no hay email, usar user_id como fallback
    driver_identifier = user_email or user_id
    
    if not driver_identifier:
        raise UnauthorizedError("No se pudo identificar al usuario. Email o ID no encontrado.")
    
    logger.info(f"Driver {driver_identifier} requesting assigned orders")
    
    # Obtener todos los pedidos del tenant
    all_orders = orders_db.query_items(
        'tenant_id',
        tenant_id,
        index_name='tenant-created-index'
    )
    
    assigned_orders = []
    
    # Buscar en workflow cuáles están asignados a este driver
    for order in all_orders:
        order_id = order.get('order_id')
        workflow = workflow_db.get_item({'order_id': order_id})
        
        if workflow:
            # Buscar si este driver está asignado en algún step activo
            for step in workflow.get('steps', []):
                # Step Functions asigna drivers automáticamente
                assigned_to = step.get('assigned_to', '')
                step_status = step.get('status', '')
                
                # ✅ CAMBIO: Comparar tanto con email como con user_id
                is_assigned = (assigned_to == user_email or assigned_to == user_id or 
                              assigned_to == driver_identifier)
                
                # Si el step está en delivery y asignado a este driver
                if (is_assigned and 
                    step_status in ['in_delivery', 'dispatched'] and 
                    not step.get('completed_at')):
                    
                    order_with_workflow = dict(order)
                    order_with_workflow['workflow_status'] = step_status
                    order_with_workflow['assigned_at'] = step.get('started_at')
                    order_with_workflow['step_info'] = {
                        'status': step_status,
                        'started_at': step.get('started_at'),
                        'notes': step.get('notes')
                    }
                    
                    # Serializar Decimals
                    if 'total' in order_with_workflow:
                        order_with_workflow['total'] = float(order_with_workflow['total'])
                    if 'items' in order_with_workflow:
                        for item in order_with_workflow.get('items', []):
                            if 'price' in item:
                                item['price'] = float(item['price'])
                    
                    assigned_orders.append(order_with_workflow)
                    break  # Ya encontramos el step activo
    
    logger.info(f"Found {len(assigned_orders)} assigned orders for {driver_identifier}")
    
    return success_response({
        'orders': assigned_orders,
        'count': len(assigned_orders),
        'driver_identifier': driver_identifier,
        'message': 'Step Functions asigna automáticamente estos pedidos'
    })


@error_handler
def get_order_detail(event, context):
    """
    Obtiene detalle completo de un pedido incluyendo workflow
    Solo consulta, NO modifica
    """
    logger.info("Getting order detail for driver")
    
    order_id = get_path_param_from_path(event, 'order_id')
    user_email = get_user_email(event)
    user_id = get_user_id(event)
    
    driver_identifier = user_email or user_id
    
    if not order_id:
        raise ValidationError("order_id es requerido")
    
    logger.info(f"Driver {driver_identifier} requesting order {order_id}")
    
    # Obtener orden
    order = orders_db.get_item({'order_id': order_id})
    if not order:
        raise NotFoundError(f"Pedido {order_id} no encontrado")
    
    # Obtener workflow
    workflow = workflow_db.get_item({'order_id': order_id})
    
    # Construir respuesta enriquecida
    order_detail = dict(order)
    
    # Serializar Decimals
    if 'total' in order_detail:
        order_detail['total'] = float(order_detail['total'])
    if 'items' in order_detail:
        for item in order_detail.get('items', []):
            if 'price' in item:
                item['price'] = float(item['price'])
    
    # Agregar información del workflow
    if workflow:
        order_detail['workflow'] = {
            'current_status': workflow.get('current_status'),
            'updated_at': workflow.get('updated_at'),
            'steps': workflow.get('steps', [])
        }
        
        # Calcular progreso
        total_steps = len(workflow.get('steps', []))
        completed_steps = len([s for s in workflow.get('steps', []) if s.get('completed_at')])
        order_detail['progress'] = {
            'total_steps': total_steps,
            'completed_steps': completed_steps,
            'percentage': int((completed_steps / total_steps * 100)) if total_steps > 0 else 0
        }
    
    logger.info(f"Order detail retrieved for {order_id}")
    
    return success_response(order_detail)


@error_handler
def get_driver_stats(event, context):
    """
    Obtiene estadísticas del driver
    Basado en workflow histórico
    
    ✅ FIXED: Mejor manejo cuando email no está disponible
    """
    logger.info("Getting driver statistics")
    
    tenant_id = get_tenant_id(event)
    user_email = get_user_email(event)
    user_id = get_user_id(event)
    
    # ✅ CAMBIO: Si no hay email, usar user_id como fallback
    driver_identifier = user_email or user_id
    
    if not driver_identifier:
        raise UnauthorizedError("No se pudo identificar al usuario. Email o ID no encontrado.")
    
    logger.info(f"Getting stats for driver {driver_identifier}")
    
    # Obtener todos los pedidos del tenant
    all_orders = orders_db.query_items(
        'tenant_id',
        tenant_id,
        index_name='tenant-created-index'
    )
    
    # Analizar workflows para estadísticas
    delivered = 0
    in_transit = 0
    total_delivery_time = 0
    deliveries_with_time = 0
    
    for order in all_orders:
        order_id = order.get('order_id')
        workflow = workflow_db.get_item({'order_id': order_id})
        
        if workflow:
            steps = workflow.get('steps', [])
            
            for i, step in enumerate(steps):
                assigned_to = step.get('assigned_to', '')
                step_status = step.get('status', '')
                
                # ✅ CAMBIO: Comparar tanto con email como con user_id
                is_assigned = (assigned_to == user_email or assigned_to == user_id or 
                              assigned_to == driver_identifier)
                
                # Solo contar si está asignado a este driver
                if is_assigned:
                    # Pedidos entregados
                    if step_status == 'delivered' and step.get('completed_at'):
                        delivered += 1
                        
                        # Calcular tiempo de entrega
                        # Buscar cuándo empezó el delivery
                        for prev_step in steps[:i+1]:
                            if prev_step.get('status') == 'in_delivery':
                                start_time = prev_step.get('started_at')
                                end_time = step.get('completed_at')
                                if start_time and end_time:
                                    total_delivery_time += (end_time - start_time)
                                    deliveries_with_time += 1
                                break
                    
                    # Pedidos en tránsito
                    elif step_status in ['in_delivery', 'dispatched'] and not step.get('completed_at'):
                        in_transit += 1
    
    # Calcular tiempo promedio
    avg_time_seconds = int(total_delivery_time / deliveries_with_time) if deliveries_with_time > 0 else 0
    avg_time_minutes = int(avg_time_seconds / 60)
    
    stats = {
        'driver_identifier': driver_identifier,
        'driver_email': user_email,
        'driver_id': user_id,
        'total_deliveries': delivered,
        'in_transit': in_transit,
        'avg_delivery_time_minutes': avg_time_minutes,
        'avg_delivery_time_seconds': avg_time_seconds,
        'rating': 4.8,  # Placeholder - implementar sistema de ratings después
        'total_earnings': delivered * 12.5,  # $12.50 por entrega
        'message': 'Estadísticas basadas en entregas completadas por Step Functions'
    }
    
    logger.info(f"Stats for {driver_identifier}: {stats}")
    
    return success_response(stats)


@error_handler
def get_delivery_timeline(event, context):
    """
    Obtiene el timeline completo de una entrega
    Muestra todas las transiciones de Step Functions
    """
    logger.info("Getting delivery timeline")
    
    order_id = get_path_param_from_path(event, 'order_id')
    user_email = get_user_email(event)
    user_id = get_user_id(event)
    
    driver_identifier = user_email or user_id
    
    if not order_id:
        raise ValidationError("order_id es requerido")
    
    logger.info(f"Driver {driver_identifier} requesting timeline for {order_id}")
    
    # Obtener workflow
    workflow = workflow_db.get_item({'order_id': order_id})
    
    if not workflow:
        return success_response({
            'order_id': order_id,
            'timeline': [],
            'message': 'El pedido aún no tiene workflow registrado'
        })
    
    # Construir timeline legible
    timeline = []
    steps = workflow.get('steps', [])
    
    for i, step in enumerate(steps):
        step_info = {
            'step_number': i + 1,
            'status': step.get('status'),
            'status_label': _get_status_label(step.get('status')),
            'assigned_to': step.get('assigned_to'),
            'started_at': step.get('started_at'),
            'completed_at': step.get('completed_at'),
            'is_completed': step.get('completed_at') is not None,
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
        last_completed = steps[-1].get('completed_at')
        if last_completed:
            total_duration = last_completed - first_start
    
    logger.info(f"Timeline retrieved for {order_id}")
    
    return success_response({
        'order_id': order_id,
        'current_status': workflow.get('current_status'),
        'timeline': timeline,
        'total_duration_seconds': total_duration,
        'total_duration_readable': _format_duration(total_duration),
        'message': 'Timeline generado por Step Functions'
    })


# ============================================================================
# FUNCIONES AUXILIARES
# ============================================================================

def _get_status_label(status):
    """Traduce estados técnicos a labels amigables"""
    labels = {
        'pending': 'Pendiente',
        'confirmed': 'Confirmado',
        'cooking': 'En cocina',
        'packing': 'Empaquetando',
        'ready': 'Listo para recoger',
        'in_delivery': 'En camino',
        'dispatched': 'Despachado',
        'delivered': 'Entregado',
        'failed': 'Fallido'
    }
    return labels.get(status, status)


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
