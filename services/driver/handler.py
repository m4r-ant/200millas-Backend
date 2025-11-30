import os
import json
from decimal import Decimal
from shared.utils import (
    success_response, error_handler, get_tenant_id, get_user_id, 
    get_user_email, parse_body, current_timestamp, get_path_param_from_path
)
from shared.dynamodb import DynamoDBService
from shared.eventbridge import EventBridgeService
from shared.errors import NotFoundError, ValidationError, UnauthorizedError
from shared.logger import get_logger

logger = get_logger(__name__)
orders_db = DynamoDBService(os.environ.get('ORDERS_TABLE'))
workflow_db = DynamoDBService(os.environ.get('WORKFLOW_TABLE'))

VALID_STATUS_TRANSITIONS = {
    'pending': ['cooking'],
    'cooking': ['ready', 'packing'],
    'ready': ['dispatched'],
    'dispatched': ['delivered'],
    'delivered': []
}

@error_handler
def get_available_orders(event, context):
    """Obtiene pedidos listos para recoger (estado: ready)"""
    logger.info("Getting available orders for driver")
    
    tenant_id = get_tenant_id(event)
    user_email = get_user_email(event)
    
    logger.info(f"Driver {user_email} requesting available orders")
    
    # Query pedidos con status 'ready'
    orders = orders_db.query_items(
        'tenant_id',
        tenant_id,
        index_name='tenant-status-index'
    )
    
    # Filtrar solo los que están listos
    available_orders = [
        o for o in orders 
        if o.get('status') == 'ready'
    ]
    
    # Serializar Decimals
    for order in available_orders:
        if 'total' in order:
            order['total'] = float(order['total'])
        if 'items' in order:
            for item in order.get('items', []):
                if 'price' in item:
                    item['price'] = float(item['price'])
    
    logger.info(f"Found {len(available_orders)} available orders")
    
    return success_response({
        'orders': available_orders,
        'count': len(available_orders)
    })

@error_handler
def get_assigned_orders(event, context):
    """Obtiene pedidos asignados al driver actual"""
    logger.info("Getting assigned orders for driver")
    
    tenant_id = get_tenant_id(event)
    user_email = get_user_email(event)
    
    if not user_email:
        raise UnauthorizedError("Email del usuario no encontrado")
    
    logger.info(f"Driver {user_email} requesting assigned orders")
    
    # Obtener todos los pedidos del tenant
    all_orders = orders_db.query_items(
        'tenant_id',
        tenant_id,
        index_name='tenant-created-index'
    )
    
    assigned_orders = []
    
    # Buscar en workflow quién está asignado
    for order in all_orders:
        order_id = order.get('order_id')
        workflow = workflow_db.get_item({'order_id': order_id})
        
        if workflow:
            # Buscar el step donde este driver está asignado
            for step in workflow.get('steps', []):
                if step.get('assigned_to') == user_email and step.get('status') in ['ready', 'dispatched']:
                    order_with_workflow = dict(order)
                    order_with_workflow['workflow_status'] = step.get('status')
                    order_with_workflow['assigned_at'] = step.get('started_at')
                    
                    # Serializar Decimals
                    if 'total' in order_with_workflow:
                        order_with_workflow['total'] = float(order_with_workflow['total'])
                    if 'items' in order_with_workflow:
                        for item in order_with_workflow.get('items', []):
                            if 'price' in item:
                                item['price'] = float(item['price'])
                    
                    assigned_orders.append(order_with_workflow)
                    break
    
    logger.info(f"Found {len(assigned_orders)} assigned orders for {user_email}")
    
    return success_response({
        'orders': assigned_orders,
        'count': len(assigned_orders)
    })

@error_handler
def update_order_status(event, context):
    """Actualiza el estado de un pedido con validaciones"""
    logger.info("Updating order status")
    
    order_id = get_path_param_from_path(event, 'order_id')
    tenant_id = get_tenant_id(event)
    user_email = get_user_email(event)
    body = parse_body(event)
    
    if not order_id:
        raise ValidationError("order_id es requerido")
    
    new_status = body.get('status', '').strip()
    notes = body.get('notes', '').strip()
    
    if not new_status:
        raise ValidationError("status es requerido")
    
    logger.info(f"Attempting to update order {order_id} to status {new_status}")
    
    # Obtener pedido actual
    order = orders_db.get_item({'order_id': order_id})
    if not order:
        raise NotFoundError(f"Pedido {order_id} no encontrado")
    
    current_status = order.get('status')
    
    # Validar transición
    if current_status not in VALID_STATUS_TRANSITIONS:
        raise ValidationError(f"Status actual inválido: {current_status}")
    
    if new_status not in VALID_STATUS_TRANSITIONS[current_status]:
        raise ValidationError(f"Transición no permitida: {current_status} → {new_status}")
    
    timestamp = current_timestamp()
    
    # Actualizar orden
    orders_db.update_item(
        {'order_id': order_id},
        {
            'status': new_status,
            'updated_at': timestamp,
            'updated_by': user_email
        }
    )
    
    # Actualizar workflow
    workflow = workflow_db.get_item({'order_id': order_id})
    if not workflow:
        workflow = {'order_id': order_id, 'steps': []}
    
    # Completar paso anterior
    if workflow.get('steps'):
        last_step = workflow['steps'][-1]
        if last_step.get('status') == current_status and not last_step.get('completed_at'):
            last_step['completed_at'] = timestamp
    
    # Agregar nuevo step
    new_step = {
        'status': new_status,
        'assigned_to': user_email,
        'started_at': timestamp,
        'completed_at': None,
        'notes': notes
    }
    
    workflow['steps'].append(new_step)
    workflow['current_status'] = new_status
    workflow['updated_at'] = timestamp
    
    workflow_db.put_item(workflow)
    
    # Emitir evento
    EventBridgeService.put_event(
        source='orders.service',
        detail_type='OrderStatusUpdated',
        detail={
            'order_id': order_id,
            'old_status': current_status,
            'new_status': new_status,
            'updated_by': user_email,
            'timestamp': timestamp
        },
        tenant_id=tenant_id
    )
    
    logger.info(f"Order {order_id} updated: {current_status} → {new_status}")
    
    return success_response({
        'order_id': order_id,
        'old_status': current_status,
        'new_status': new_status,
        'updated_at': timestamp
    })

@error_handler
def pickup_order(event, context):
    """Marca un pedido como recogido"""
    logger.info("Marking order as picked up")
    
    order_id = get_path_param_from_path(event, 'order_id')
    tenant_id = get_tenant_id(event)
    user_email = get_user_email(event)
    body = parse_body(event)
    
    if not order_id:
        raise ValidationError("order_id es requerido")
    
    # Obtener pedido
    order = orders_db.get_item({'order_id': order_id})
    if not order:
        raise NotFoundError(f"Pedido {order_id} no encontrado")
    
    if order.get('status') != 'ready':
        raise ValidationError(f"Solo se pueden recoger pedidos en estado 'ready', actual: {order.get('status')}")
    
    timestamp = current_timestamp()
    location = body.get('location', {})
    
    # Actualizar pedido
    orders_db.update_item(
        {'order_id': order_id},
        {
            'status': 'dispatched',
            'updated_at': timestamp,
            'pickup_time': timestamp,
            'pickup_location': location,
            'assigned_driver': user_email
        }
    )
    
    # Actualizar workflow
    workflow = workflow_db.get_item({'order_id': order_id})
    if workflow and workflow.get('steps'):
        last_step = workflow['steps'][-1]
        if last_step.get('status') == 'ready':
            last_step['completed_at'] = timestamp
    
    dispatch_step = {
        'status': 'dispatched',
        'assigned_to': user_email,
        'started_at': timestamp,
        'completed_at': None,
        'location': location
    }
    
    workflow['steps'].append(dispatch_step)
    workflow['current_status'] = 'dispatched'
    workflow['updated_at'] = timestamp
    workflow_db.put_item(workflow)
    
    # Emitir evento
    EventBridgeService.put_event(
        source='orders.service',
        detail_type='OrderPickedUp',
        detail={
            'order_id': order_id,
            'driver': user_email,
            'pickup_time': timestamp,
            'location': location
        },
        tenant_id=tenant_id
    )
    
    logger.info(f"Order {order_id} picked up by {user_email}")
    
    return success_response({
        'order_id': order_id,
        'status': 'dispatched',
        'pickup_time': timestamp
    })

@error_handler
def complete_delivery(event, context):
    """Marca un pedido como entregado"""
    logger.info("Marking order as delivered")
    
    order_id = get_path_param_from_path(event, 'order_id')
    tenant_id = get_tenant_id(event)
    user_email = get_user_email(event)
    body = parse_body(event)
    
    if not order_id:
        raise ValidationError("order_id es requerido")
    
    # Obtener pedido
    order = orders_db.get_item({'order_id': order_id})
    if not order:
        raise NotFoundError(f"Pedido {order_id} no encontrado")
    
    if order.get('status') != 'dispatched':
        raise ValidationError(f"Solo se pueden entregar pedidos en estado 'dispatched', actual: {order.get('status')}")
    
    timestamp = current_timestamp()
    location = body.get('location', {})
    notes = body.get('notes', '')
    
    # Actualizar pedido
    orders_db.update_item(
        {'order_id': order_id},
        {
            'status': 'delivered',
            'updated_at': timestamp,
            'delivery_time': timestamp,
            'delivery_location': location,
            'delivery_notes': notes
        }
    )
    
    # Actualizar workflow
    workflow = workflow_db.get_item({'order_id': order_id})
    if workflow and workflow.get('steps'):
        last_step = workflow['steps'][-1]
        if last_step.get('status') == 'dispatched':
            last_step['completed_at'] = timestamp
    
    delivery_step = {
        'status': 'delivered',
        'assigned_to': user_email,
        'started_at': timestamp,
        'completed_at': timestamp,
        'location': location,
        'notes': notes
    }
    
    workflow['steps'].append(delivery_step)
    workflow['current_status'] = 'delivered'
    workflow['updated_at'] = timestamp
    workflow_db.put_item(workflow)
    
    # Emitir evento
    EventBridgeService.put_event(
        source='orders.service',
        detail_type='OrderDelivered',
        detail={
            'order_id': order_id,
            'driver': user_email,
            'delivery_time': timestamp,
            'location': location
        },
        tenant_id=tenant_id
    )
    
    logger.info(f"Order {order_id} delivered by {user_email}")
    
    return success_response({
        'order_id': order_id,
        'status': 'delivered',
        'delivery_time': timestamp
    })

@error_handler
def get_driver_stats(event, context):
    """Obtiene estadísticas del driver"""
    logger.info("Getting driver statistics")
    
    tenant_id = get_tenant_id(event)
    user_email = get_user_email(event)
    
    logger.info(f"Getting stats for driver {user_email}")
    
    all_orders = orders_db.query_items(
        'tenant_id',
        tenant_id,
        index_name='tenant-created-index'
    )
    
    delivered = 0
    in_transit = 0
    total_delivery_time = 0
    
    for order in all_orders:
        order_id = order.get('order_id')
        workflow = workflow_db.get_item({'order_id': order_id})
        
        if workflow:
            for step in workflow.get('steps', []):
                if step.get('assigned_to') == user_email:
                    if step.get('status') == 'delivered':
                        delivered += 1
                        pickup_time = [s.get('started_at') for s in workflow.get('steps', []) if s.get('status') == 'dispatched']
                        if pickup_time and step.get('completed_at'):
                            total_delivery_time += step['completed_at'] - pickup_time[0]
                    elif step.get('status') == 'dispatched':
                        in_transit += 1
    
    avg_time = int(total_delivery_time / delivered) if delivered > 0 else 0
    
    stats = {
        'total_deliveries': delivered,
        'in_transit': in_transit,
        'avg_delivery_time_minutes': int(avg_time / 60),
        'rating': 4.8,
        'total_earnings': delivered * 12.5  # $12.50 por entrega
    }
    
    logger.info(f"Stats for {user_email}: {stats}")
    
    return success_response(stats)
