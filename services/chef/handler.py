"""
Chef Handler - Cocinar y Empaquetar
===================================
Los chefs cocinan y luego empaquetan los pedidos
"""
import os
import json
import boto3
from decimal import Decimal
from shared.utils import (
    success_response, error_handler, get_tenant_id, get_user_id, 
    get_user_email, parse_body, current_timestamp, get_path_param_from_path,
    get_user_type
)
from shared.dynamodb import DynamoDBService
from shared.errors import NotFoundError, ValidationError, UnauthorizedError
from shared.logger import get_logger
from shared.eventbridge import EventBridgeService

logger = get_logger(__name__)
orders_db = DynamoDBService(os.environ.get('ORDERS_TABLE'))
workflow_db = DynamoDBService(os.environ.get('WORKFLOW_TABLE'))
availability_db = DynamoDBService(os.environ.get('STAFF_AVAILABILITY_TABLE', 'dev-StaffAvailability'))

# Step Functions client para enviar tokens
sfn_client = boto3.client('stepfunctions')


@error_handler
def get_assigned_orders(event, context):
    """
    GET /chef/assigned
    Obtiene pedidos asignados al chef actual (en cooking o packing)
    """
    logger.info("Getting assigned orders for chef")
    
    user_type = get_user_type(event)
    if user_type not in ['chef', 'staff', 'admin']:
        raise UnauthorizedError("Solo chefs pueden ver sus pedidos asignados")
    
    tenant_id = get_tenant_id(event)
    user_email = get_user_email(event)
    user_id = get_user_id(event)
    
    chef_identifier = user_email or user_id
    
    if not chef_identifier:
        raise UnauthorizedError("No se pudo identificar al usuario")
    
    logger.info(f"Chef {chef_identifier} requesting assigned orders")
    
    # Obtener todos los pedidos del tenant
    all_orders = orders_db.query_items(
        'tenant_id',
        tenant_id,
        index_name='tenant-created-index'
    )
    
    assigned_orders = []
    
    # Buscar pedidos asignados a este chef
    for order in all_orders:
        order_id = order.get('order_id')
        assigned_chef = order.get('assigned_chef')
        status = order.get('status')
        
        # Si está asignado a este chef y en cooking o packing
        if (assigned_chef == chef_identifier and 
            status in ['cooking', 'packing']):
            
            workflow = workflow_db.get_item({'order_id': order_id})
            
            order_with_workflow = dict(order)
            order_with_workflow['workflow_status'] = status
            
            if workflow:
                order_with_workflow['workflow'] = {
                    'current_status': workflow.get('current_status'),
                    'steps': workflow.get('steps', [])
                }
            
            # Serializar Decimals
            if 'total' in order_with_workflow:
                order_with_workflow['total'] = float(order_with_workflow['total'])
            if 'items' in order_with_workflow:
                for item in order_with_workflow.get('items', []):
                    if 'price' in item:
                        item['price'] = float(item['price'])
            
            assigned_orders.append(order_with_workflow)
    
    logger.info(f"Found {len(assigned_orders)} assigned orders for {chef_identifier}")
    
    return success_response({
        'orders': assigned_orders,
        'count': len(assigned_orders),
        'chef_identifier': chef_identifier
    })


@error_handler
def get_order_detail(event, context):
    """
    GET /chef/orders/{order_id}
    Obtiene detalle completo de un pedido asignado al chef
    """
    logger.info("Getting order detail for chef")
    
    order_id = get_path_param_from_path(event, 'order_id')
    user_email = get_user_email(event)
    user_id = get_user_id(event)
    
    chef_identifier = user_email or user_id
    
    if not order_id:
        raise ValidationError("order_id es requerido")
    
    logger.info(f"Chef {chef_identifier} requesting order {order_id}")
    
    # Obtener orden
    order = orders_db.get_item({'order_id': order_id})
    if not order:
        raise NotFoundError(f"Pedido {order_id} no encontrado")
    
    # Verificar que está asignado a este chef
    if order.get('assigned_chef') != chef_identifier:
        raise UnauthorizedError("Este pedido no está asignado a ti")
    
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
def complete_cooking(event, context):
    """
    POST /chef/complete-cooking/{order_id}
    Marca que terminó de cocinar y pasa a empaquetar
    """
    logger.info("Chef completing cooking")
    
    user_type = get_user_type(event)
    if user_type not in ['chef', 'staff', 'admin']:
        raise UnauthorizedError("Solo chefs pueden completar cocción")
    
    order_id = get_path_param_from_path(event, 'order_id')
    tenant_id = get_tenant_id(event)
    user_email = get_user_email(event)
    user_id = get_user_id(event)
    
    chef_identifier = user_email or user_id
    
    if not order_id:
        raise ValidationError("order_id es requerido")
    
    body = parse_body(event) or {}
    notes = body.get('notes', '').strip()
    
    logger.info(f"Chef {chef_identifier} completing cooking for order {order_id}")
    
    # Obtener orden
    order = orders_db.get_item({'order_id': order_id})
    if not order:
        raise NotFoundError(f"Pedido {order_id} no encontrado")
    
    # Verificar que está asignado a este chef y en estado cooking
    if order.get('assigned_chef') != chef_identifier:
        raise UnauthorizedError("Este pedido no está asignado a ti")
    
    if order.get('status') != 'cooking':
        raise ValidationError(f"El pedido debe estar en estado 'cooking'. Estado actual: {order.get('status')}")
    
    timestamp = current_timestamp()
    
    # Actualizar orden a packing (el chef ahora empaqueta)
    orders_db.update_item(
        {'order_id': order_id},
        {
            'status': 'packing',
            'updated_at': timestamp,
            'updated_by': chef_identifier
        }
    )
    
    # Actualizar workflow
    workflow = workflow_db.get_item({'order_id': order_id})
    if workflow:
        # Completar step de cooking
        if workflow.get('steps'):
            last_step = workflow['steps'][-1]
            if last_step.get('status') == 'cooking' and not last_step.get('completed_at'):
                last_step['completed_at'] = timestamp
                if notes:
                    last_step['notes'] = notes
        
        # Agregar step de packing (el mismo chef)
        step = {
            'status': 'packing',
            'assigned_to': chef_identifier,
            'started_at': timestamp,
            'completed_at': None,
            'notes': notes if notes else 'Cocción completada, empezando a empaquetar'
        }
        workflow['steps'].append(step)
        workflow['current_status'] = 'packing'
        workflow['updated_at'] = timestamp
        workflow_db.put_item(workflow)
    
    # Publicar evento
    EventBridgeService.put_event(
        source='chef.service',
        detail_type='OrderCookingCompleted',
        detail={
            'order_id': order_id,
            'chef_identifier': chef_identifier,
            'chef_email': user_email,
            'chef_id': user_id,
            'completed_at': timestamp,
            'notes': notes
        },
        tenant_id=tenant_id
    )
    
    # ============================================
    # ENVIAR TASK TOKEN A STEP FUNCTIONS
    # ============================================
    try:
        workflow = workflow_db.get_item({'order_id': order_id})
        if workflow and workflow.get('cooking_task_token'):
            task_token = workflow['cooking_task_token']
            logger.info(f"Sending TaskSuccess to Step Functions for cooking - order_id: {order_id}")
            
            sfn_client.send_task_success(
                taskToken=task_token,
                output=json.dumps({
                    'order_id': order_id,
                    'status': 'packing',
                    'completed_at': timestamp,
                    'chef': chef_identifier
                })
            )
            
            # Limpiar el token del workflow
            workflow['cooking_task_token'] = None
            workflow['cooking_wait_started_at'] = None
            workflow_db.put_item(workflow)
            
            logger.info(f"✅ TaskSuccess sent to Step Functions for order {order_id}")
        else:
            logger.info(f"No cooking_task_token found for order {order_id} - Step Function may not be waiting")
    except Exception as e:
        logger.warning(f"Error sending TaskSuccess to Step Functions: {str(e)}")
        # No fallar la operación si esto falla - el pedido ya se actualizó
    
    logger.info(f"Order {order_id} cooking completed by {chef_identifier}, now packing")
    
    return success_response({
        'order_id': order_id,
        'status': 'packing',
        'chef': chef_identifier,
        'completed_at': timestamp,
        'message': 'Cocción completada. Ahora puedes empaquetar el pedido.'
    })


@error_handler
def complete_packing(event, context):
    """
    POST /chef/complete-packing/{order_id}
    Marca que terminó de empaquetar (listo para driver)
    """
    logger.info("Chef completing packing")
    
    user_type = get_user_type(event)
    if user_type not in ['chef', 'staff', 'admin']:
        raise UnauthorizedError("Solo chefs pueden completar empaquetado")
    
    order_id = get_path_param_from_path(event, 'order_id')
    tenant_id = get_tenant_id(event)
    user_email = get_user_email(event)
    user_id = get_user_id(event)
    
    chef_identifier = user_email or user_id
    
    if not order_id:
        raise ValidationError("order_id es requerido")
    
    body = parse_body(event) or {}
    notes = body.get('notes', '').strip()
    
    logger.info(f"Chef {chef_identifier} completing packing for order {order_id}")
    
    # Obtener orden
    order = orders_db.get_item({'order_id': order_id})
    if not order:
        raise NotFoundError(f"Pedido {order_id} no encontrado")
    
    # Verificar que está asignado a este chef y en estado packing
    if order.get('assigned_chef') != chef_identifier:
        raise UnauthorizedError("Este pedido no está asignado a ti")
    
    if order.get('status') != 'packing':
        raise ValidationError(f"El pedido debe estar en estado 'packing'. Estado actual: {order.get('status')}")
    
    timestamp = current_timestamp()
    
    # Actualizar orden a ready (listo para driver)
    orders_db.update_item(
        {'order_id': order_id},
        {
            'status': 'ready',
            'ready_at': timestamp,
            'packed_at': timestamp,
            'updated_at': timestamp,
            'updated_by': chef_identifier
        }
    )
    
    # Actualizar workflow
    workflow = workflow_db.get_item({'order_id': order_id})
    if workflow:
        # Completar step de packing
        if workflow.get('steps'):
            last_step = workflow['steps'][-1]
            if last_step.get('status') == 'packing' and not last_step.get('completed_at'):
                last_step['completed_at'] = timestamp
                if notes:
                    last_step['notes'] = notes
        
        # Agregar step de ready
        step = {
            'status': 'ready',
            'assigned_to': 'system',
            'started_at': timestamp,
            'completed_at': timestamp,
            'notes': notes if notes else 'Empaquetado y listo para recoger por repartidor'
        }
        workflow['steps'].append(step)
        workflow['current_status'] = 'ready'
        workflow['updated_at'] = timestamp
        workflow_db.put_item(workflow)
    
    # Marcar chef como available
    try:
        chef_record = availability_db.get_item({'staff_id': chef_identifier})
        if chef_record:
            orders_completed = chef_record.get('orders_completed', 0) + 1
            availability_db.update_item(
                {'staff_id': chef_identifier},
                {
                    'status': 'available',
                    'current_order_id': None,
                    'orders_completed': orders_completed,
                    'updated_at': timestamp
                }
            )
            logger.info(f"✅ Chef {chef_identifier} marked as available after completing order {order_id}")
    except Exception as e:
        logger.warning(f"Error marking chef as available: {str(e)}")
    
    # Publicar evento
    EventBridgeService.put_event(
        source='chef.service',
        detail_type='OrderPacked',
        detail={
            'order_id': order_id,
            'chef_identifier': chef_identifier,
            'chef_email': user_email,
            'chef_id': user_id,
            'packed_at': timestamp,
            'notes': notes
        },
        tenant_id=tenant_id
    )
    
    # ============================================
    # ENVIAR TASK TOKEN A STEP FUNCTIONS
    # ============================================
    try:
        workflow = workflow_db.get_item({'order_id': order_id})
        if workflow and workflow.get('packing_task_token'):
            task_token = workflow['packing_task_token']
            logger.info(f"Sending TaskSuccess to Step Functions for packing - order_id: {order_id}")
            
            sfn_client.send_task_success(
                taskToken=task_token,
                output=json.dumps({
                    'order_id': order_id,
                    'status': 'ready',
                    'packed_at': timestamp,
                    'chef': chef_identifier
                })
            )
            
            # Limpiar el token del workflow
            workflow['packing_task_token'] = None
            workflow['packing_wait_started_at'] = None
            workflow_db.put_item(workflow)
            
            logger.info(f"✅ TaskSuccess sent to Step Functions for order {order_id}")
        else:
            logger.info(f"No packing_task_token found for order {order_id} - Step Function may not be waiting")
    except Exception as e:
        logger.warning(f"Error sending TaskSuccess to Step Functions: {str(e)}")
        # No fallar la operación si esto falla - el pedido ya se actualizó
    
    logger.info(f"Order {order_id} packing completed by {chef_identifier}")
    
    return success_response({
        'order_id': order_id,
        'status': 'ready',
        'chef': chef_identifier,
        'packed_at': timestamp,
        'message': 'Pedido empaquetado y listo para recoger por el repartidor'
    })

