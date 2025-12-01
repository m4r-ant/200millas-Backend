"""
Driver Manual Operations - Permite a drivers tomar y completar pedidos manualmente
Compatible con Step Functions pero permite control manual
"""
import os
from shared.utils import (
    success_response, error_handler, get_tenant_id, get_user_email, 
    parse_body, current_timestamp, get_path_param_from_path, get_user_id
)
from shared.dynamodb import DynamoDBService
from shared.eventbridge import EventBridgeService
from shared.errors import NotFoundError, ValidationError, UnauthorizedError
from shared.logger import get_logger

logger = get_logger(__name__)
orders_db = DynamoDBService(os.environ.get('ORDERS_TABLE'))
workflow_db = DynamoDBService(os.environ.get('WORKFLOW_TABLE'))


@error_handler
def pickup_order(event, context):
    """
    Driver toma un pedido disponible
    
    POST /driver/pickup/{order_id}
    
    Transición: ready → in_delivery
    """
    logger.info("Driver picking up order")
    
    order_id = get_path_param_from_path(event, 'order_id')
    user_email = get_user_email(event)
    user_id = get_user_id(event)
    tenant_id = get_tenant_id(event)
    
    # ✅ Usar email si existe, sino user_id
    driver_identifier = user_email or user_id
    
    if not order_id:
        raise ValidationError("order_id es requerido")
    
    if not driver_identifier:
        raise UnauthorizedError("No se pudo identificar al usuario")
    
    # Verificar que el pedido existe y está en estado 'ready'
    order = orders_db.get_item({'order_id': order_id})
    if not order:
        raise NotFoundError(f"Pedido {order_id} no encontrado")
    
    current_status = order.get('status')
    if current_status != 'ready':
        raise ValidationError(
            f"El pedido no está disponible para recoger. Estado actual: {current_status}. "
            f"Debe estar en estado 'ready'."
        )
    
    timestamp = current_timestamp()
    
    # ✅ Actualizar Orders Table
    logger.info(f"Updating order {order_id} to in_delivery, assigned to {driver_identifier}")
    orders_db.update_item(
        {'order_id': order_id},
        {
            'status': 'in_delivery',
            'assigned_driver': driver_identifier,
            'pickup_time': timestamp,
            'updated_at': timestamp,
            'updated_by': driver_identifier
        }
    )
    
    # ✅ Actualizar Workflow
    workflow = workflow_db.get_item({'order_id': order_id})
    if not workflow:
        logger.warning(f"Workflow not found for order {order_id}, creating new one")
        workflow = {
            'order_id': order_id,
            'steps': []
        }
    
    # Completar step anterior (ready)
    if workflow.get('steps'):
        last_step = workflow['steps'][-1]
        if last_step.get('status') == 'ready' and not last_step.get('completed_at'):
            last_step['completed_at'] = timestamp
            logger.info(f"Completed 'ready' step for order {order_id}")
    
    # Agregar nuevo step
    new_step = {
        'status': 'in_delivery',
        'assigned_to': driver_identifier,
        'started_at': timestamp,
        'completed_at': None,
        'notes': f'Pedido recogido por {driver_identifier}'
    }
    workflow['steps'].append(new_step)
    workflow['current_status'] = 'in_delivery'
    workflow['updated_at'] = timestamp
    workflow_db.put_item(workflow)
    
    logger.info(f"Workflow updated for order {order_id}")
    
    # Publicar evento
    EventBridgeService.put_event(
        source='driver.service',
        detail_type='OrderPickedUp',
        detail={
            'order_id': order_id,
            'driver_identifier': driver_identifier,
            'driver_email': user_email,
            'driver_id': user_id,
            'pickup_time': timestamp,
            'previous_status': 'ready',
            'new_status': 'in_delivery'
        },
        tenant_id=tenant_id
    )
    
    logger.info(f"✅ Order {order_id} picked up successfully by {driver_identifier}")
    
    return success_response({
        'order_id': order_id,
        'status': 'in_delivery',
        'assigned_driver': driver_identifier,
        'pickup_time': timestamp,
        'message': '¡Pedido recogido exitosamente! Ya puedes proceder con la entrega.'
    }, 200)


@error_handler
def complete_order(event, context):
    """
    Driver marca pedido como entregado
    
    POST /driver/complete/{order_id}
    Body: { "notes": "Entregado en puerta principal" }
    
    Transición: in_delivery → delivered
    """
    logger.info("Driver completing order delivery")
    
    order_id = get_path_param_from_path(event, 'order_id')
    user_email = get_user_email(event)
    user_id = get_user_id(event)
    tenant_id = get_tenant_id(event)
    body = parse_body(event)
    
    # ✅ Usar email si existe, sino user_id
    driver_identifier = user_email or user_id
    
    if not order_id:
        raise ValidationError("order_id es requerido")
    
    # Verificar que el pedido existe
    order = orders_db.get_item({'order_id': order_id})
    if not order:
        raise NotFoundError(f"Pedido {order_id} no encontrado")
    
    # Verificar que está asignado a este driver
    assigned_driver = order.get('assigned_driver')
    if assigned_driver != driver_identifier:
        raise UnauthorizedError(
            f"Este pedido está asignado a {assigned_driver}. "
            f"Solo el driver asignado puede completar la entrega."
        )
    
    # Verificar que está en delivery
    current_status = order.get('status')
    if current_status != 'in_delivery':
        raise ValidationError(
            f"El pedido no está en entrega. Estado actual: {current_status}. "
            f"Debe estar en estado 'in_delivery'."
        )
    
    timestamp = current_timestamp()
    notes = body.get('notes', '')
    
    # ✅ Actualizar Orders Table
    logger.info(f"Marking order {order_id} as delivered by {driver_identifier}")
    orders_db.update_item(
        {'order_id': order_id},
        {
            'status': 'delivered',
            'delivered_at': timestamp,
            'updated_at': timestamp,
            'updated_by': driver_identifier,
            'delivery_notes': notes if notes else 'Entrega completada'
        }
    )
    
    # ✅ Actualizar Workflow
    workflow = workflow_db.get_item({'order_id': order_id})
    if workflow and workflow.get('steps'):
        last_step = workflow['steps'][-1]
        if last_step.get('status') == 'in_delivery' and not last_step.get('completed_at'):
            last_step['completed_at'] = timestamp
            if notes:
                last_step['notes'] = notes
            logger.info(f"Completed 'in_delivery' step for order {order_id}")
    
    workflow['current_status'] = 'delivered'
    workflow['updated_at'] = timestamp
    workflow_db.put_item(workflow)
    
    logger.info(f"Workflow completed for order {order_id}")
    
    # Calcular duración de la entrega
    pickup_time = order.get('pickup_time', timestamp)
    delivery_duration = timestamp - pickup_time
    delivery_duration_minutes = int(delivery_duration / 60)
    
    # Publicar evento
    EventBridgeService.put_event(
        source='driver.service',
        detail_type='OrderDelivered',
        detail={
            'order_id': order_id,
            'driver_identifier': driver_identifier,
            'driver_email': user_email,
            'driver_id': user_id,
            'delivered_at': timestamp,
            'delivery_duration_seconds': delivery_duration,
            'delivery_duration_minutes': delivery_duration_minutes,
            'notes': notes
        },
        tenant_id=tenant_id
    )
    
    logger.info(f"✅ Order {order_id} delivered successfully by {driver_identifier} in {delivery_duration_minutes} minutes")
    
    return success_response({
        'order_id': order_id,
        'status': 'delivered',
        'delivered_at': timestamp,
        'delivery_duration_minutes': delivery_duration_minutes,
        'message': f'¡Pedido entregado exitosamente! Tiempo de entrega: {delivery_duration_minutes} minutos.'
    }, 200)


@error_handler
def cancel_pickup(event, context):
    """
    Driver cancela un pickup (regresa a ready)
    
    POST /driver/cancel/{order_id}
    Body: { "reason": "Cliente no responde" }
    
    Transición: in_delivery → ready
    """
    logger.info("Driver canceling pickup")
    
    order_id = get_path_param_from_path(event, 'order_id')
    user_email = get_user_email(event)
    user_id = get_user_id(event)
    tenant_id = get_tenant_id(event)
    body = parse_body(event)
    
    # ✅ Usar email si existe, sino user_id
    driver_identifier = user_email or user_id
    
    if not order_id:
        raise ValidationError("order_id es requerido")
    
    reason = body.get('reason', 'Sin especificar')
    
    # Verificar que el pedido existe
    order = orders_db.get_item({'order_id': order_id})
    if not order:
        raise NotFoundError(f"Pedido {order_id} no encontrado")
    
    # Verificar que está asignado a este driver
    assigned_driver = order.get('assigned_driver')
    if assigned_driver != driver_identifier:
        raise UnauthorizedError(
            f"Este pedido está asignado a {assigned_driver}"
        )
    
    # Verificar que está en delivery
    current_status = order.get('status')
    if current_status != 'in_delivery':
        raise ValidationError(
            f"El pedido no está en entrega. Estado actual: {current_status}"
        )
    
    timestamp = current_timestamp()
    
    # ✅ Actualizar Orders Table - regresar a 'ready'
    logger.info(f"Returning order {order_id} to ready status")
    orders_db.update_item(
        {'order_id': order_id},
        {
            'status': 'ready',
            'assigned_driver': None,
            'pickup_time': None,
            'updated_at': timestamp,
            'updated_by': driver_identifier,
            'cancellation_reason': reason
        }
    )
    
    # ✅ Actualizar Workflow
    workflow = workflow_db.get_item({'order_id': order_id})
    if workflow and workflow.get('steps'):
        # Completar el step de in_delivery con nota de cancelación
        last_step = workflow['steps'][-1]
        if last_step.get('status') == 'in_delivery':
            last_step['completed_at'] = timestamp
            last_step['notes'] = f'Cancelado por {driver_identifier}. Razón: {reason}'
        
        # Agregar nuevo step de vuelta a ready
        new_step = {
            'status': 'ready',
            'assigned_to': 'system',
            'started_at': timestamp,
            'completed_at': None,
            'notes': f'Regresado a disponible. Razón: {reason}'
        }
        workflow['steps'].append(new_step)
    
    workflow['current_status'] = 'ready'
    workflow['updated_at'] = timestamp
    workflow_db.put_item(workflow)
    
    # Publicar evento
    EventBridgeService.put_event(
        source='driver.service',
        detail_type='OrderPickupCanceled',
        detail={
            'order_id': order_id,
            'driver_identifier': driver_identifier,
            'driver_email': user_email,
            'driver_id': user_id,
            'canceled_at': timestamp,
            'reason': reason
        },
        tenant_id=tenant_id
    )
    
    logger.info(f"✅ Order {order_id} pickup canceled by {driver_identifier}")
    
    return success_response({
        'order_id': order_id,
        'status': 'ready',
        'message': 'Pickup cancelado. El pedido está disponible para otros drivers.',
        'reason': reason
    }, 200)
