"""
Token Management - Endpoints para gestionar y probar Wait Tokens
Permite al frontend ver el estado de los tokens y simular acciones
"""
import os
import json
import boto3
from shared.utils import (
    success_response, error_handler, get_tenant_id, get_user_id, 
    get_user_email, parse_body, current_timestamp, get_path_param_from_path,
    get_user_type
)
from shared.dynamodb import DynamoDBService
from shared.errors import NotFoundError, ValidationError, UnauthorizedError
from shared.logger import get_logger

logger = get_logger(__name__)
workflow_db = DynamoDBService(os.environ.get('WORKFLOW_TABLE'))
orders_db = DynamoDBService(os.environ.get('ORDERS_TABLE'))

# Step Functions client
sfn_client = boto3.client('stepfunctions')


@error_handler
def get_wait_token_status(event, context):
    """
    GET /workflow/{order_id}/wait-tokens
    Obtiene el estado de todos los wait tokens para un pedido
    Útil para debugging y para que el frontend sepa qué tokens están activos
    """
    logger.info("Getting wait token status")
    
    order_id = get_path_param_from_path(event, 'order_id')
    user_type = get_user_type(event)
    
    if not order_id:
        raise ValidationError("order_id es requerido")
    
    # Solo staff, chef, admin y driver pueden ver tokens
    if user_type not in ['chef', 'staff', 'admin', 'driver']:
        raise UnauthorizedError("No tienes permiso para ver wait tokens")
    
    # Obtener workflow
    workflow = workflow_db.get_item({'order_id': order_id})
    if not workflow:
        raise NotFoundError(f"Workflow no encontrado para pedido {order_id}")
    
    # Construir respuesta con estado de tokens
    token_status = {
        'order_id': order_id,
        'tokens': {
            'confirmation': {
                'has_token': bool(workflow.get('confirmation_task_token')),
                'wait_started_at': workflow.get('confirmation_wait_started_at'),
                'is_waiting': bool(workflow.get('confirmation_task_token'))
            },
            'cooking': {
                'has_token': bool(workflow.get('cooking_task_token')),
                'wait_started_at': workflow.get('cooking_wait_started_at'),
                'is_waiting': bool(workflow.get('cooking_task_token'))
            },
            'packing': {
                'has_token': bool(workflow.get('packing_task_token')),
                'wait_started_at': workflow.get('packing_wait_started_at'),
                'is_waiting': bool(workflow.get('packing_task_token'))
            },
            'driver_pickup': {
                'has_token': bool(workflow.get('driver_pickup_task_token')),
                'wait_started_at': workflow.get('driver_pickup_wait_started_at'),
                'is_waiting': bool(workflow.get('driver_pickup_task_token'))
            },
            'driver_delivery': {
                'has_token': bool(workflow.get('driver_delivery_task_token')),
                'wait_started_at': workflow.get('driver_delivery_wait_started_at'),
                'is_waiting': bool(workflow.get('driver_delivery_task_token'))
            }
        },
        'execution_arn': workflow.get('execution_arn'),
        'current_status': workflow.get('current_status')
    }
    
    logger.info(f"Token status retrieved for order {order_id}")
    
    return success_response(token_status)


@error_handler
def confirm_order_manual(event, context):
    """
    POST /workflow/{order_id}/confirm
    Confirma manualmente un pedido (activa el wait token de confirmación)
    Solo para admin/staff
    """
    logger.info("Manually confirming order")
    
    order_id = get_path_param_from_path(event, 'order_id')
    user_type = get_user_type(event)
    user_id = get_user_id(event)
    tenant_id = get_tenant_id(event)
    
    if not order_id:
        raise ValidationError("order_id es requerido")
    
    # Solo admin y staff pueden confirmar manualmente
    if user_type not in ['admin', 'staff']:
        raise UnauthorizedError("Solo admin y staff pueden confirmar pedidos manualmente")
    
    # Obtener workflow y verificar que hay token
    workflow = workflow_db.get_item({'order_id': order_id})
    if not workflow:
        raise NotFoundError(f"Workflow no encontrado para pedido {order_id}")
    
    confirmation_token = workflow.get('confirmation_task_token')
    if not confirmation_token:
        raise ValidationError("No hay token de confirmación activo para este pedido")
    
    # Obtener orden
    order = orders_db.get_item({'order_id': order_id})
    if not order:
        raise NotFoundError(f"Pedido {order_id} no encontrado")
    
    timestamp = current_timestamp()
    
    # Actualizar orden a confirmed
    orders_db.update_item(
        {'order_id': order_id},
        {'status': 'confirmed', 'updated_at': timestamp, 'updated_by': user_id}
    )
    
    # Actualizar workflow
    if workflow.get('steps'):
        last_step = workflow['steps'][-1]
        if last_step.get('status') == 'pending' and not last_step.get('completed_at'):
            last_step['completed_at'] = timestamp
    
    step = {
        'status': 'confirmed',
        'assigned_to': user_id,
        'started_at': timestamp,
        'completed_at': timestamp,
        'notes': 'Confirmado manualmente'
    }
    workflow['steps'].append(step)
    workflow['current_status'] = 'confirmed'
    workflow['updated_at'] = timestamp
    
    # Enviar TaskSuccess al Step Function
    try:
        logger.info(f"Sending TaskSuccess for order confirmation - order_id: {order_id}")
        sfn_client.send_task_success(
            taskToken=confirmation_token,
            output=json.dumps({
                'order_id': order_id,
                'status': 'confirmed',
                'confirmed_at': timestamp,
                'confirmed_by': user_id
            })
        )
        
        # Limpiar el token
        workflow['confirmation_task_token'] = None
        workflow['confirmation_wait_started_at'] = None
        logger.info(f"✅ TaskSuccess sent for order {order_id}")
    except Exception as e:
        logger.error(f"Error sending TaskSuccess: {str(e)}")
        raise Exception(f"Error al confirmar pedido: {str(e)}")
    
    workflow_db.put_item(workflow)
    
    logger.info(f"Order {order_id} confirmed manually by {user_id}")
    
    return success_response({
        'order_id': order_id,
        'status': 'confirmed',
        'confirmed_at': timestamp,
        'confirmed_by': user_id,
        'message': 'Pedido confirmado exitosamente. El workflow continuará.'
    })


@error_handler
def get_all_waiting_orders(event, context):
    """
    GET /workflow/waiting-orders
    Obtiene todos los pedidos que están esperando en algún wait token
    Útil para que el frontend muestre qué pedidos necesitan acción
    """
    logger.info("Getting all waiting orders")
    
    user_type = get_user_type(event)
    tenant_id = get_tenant_id(event)
    
    # Solo staff, chef, admin y driver pueden ver esto
    if user_type not in ['chef', 'staff', 'admin', 'driver']:
        raise UnauthorizedError("No tienes permiso para ver pedidos en espera")
    
    # Obtener todos los workflows del tenant
    # Nota: Esto requiere un scan, pero para producción deberías usar un GSI
    all_workflows = workflow_db.scan_items()
    
    waiting_orders = []
    
    for workflow in all_workflows:
        order_id = workflow.get('order_id')
        if not order_id:
            continue
        
        # Obtener orden para verificar tenant
        order = orders_db.get_item({'order_id': order_id})
        if not order or order.get('tenant_id') != tenant_id:
            continue
        
        # Verificar qué tokens están activos
        waiting_info = {
            'order_id': order_id,
            'current_status': workflow.get('current_status'),
            'waiting_for': []
        }
        
        if workflow.get('confirmation_task_token'):
            waiting_info['waiting_for'].append({
                'step': 'confirmation',
                'label': 'Confirmación de Pedido',
                'started_at': workflow.get('confirmation_wait_started_at'),
                'action_required_by': 'admin/staff'
            })
        
        if workflow.get('cooking_task_token'):
            waiting_info['waiting_for'].append({
                'step': 'cooking',
                'label': 'Completar Cocción',
                'started_at': workflow.get('cooking_wait_started_at'),
                'action_required_by': 'chef'
            })
        
        if workflow.get('packing_task_token'):
            waiting_info['waiting_for'].append({
                'step': 'packing',
                'label': 'Completar Empaquetado',
                'started_at': workflow.get('packing_wait_started_at'),
                'action_required_by': 'chef'
            })
        
        if workflow.get('driver_pickup_task_token'):
            waiting_info['waiting_for'].append({
                'step': 'driver_pickup',
                'label': 'Recoger Pedido',
                'started_at': workflow.get('driver_pickup_wait_started_at'),
                'action_required_by': 'driver'
            })
        
        if workflow.get('driver_delivery_task_token'):
            waiting_info['waiting_for'].append({
                'step': 'driver_delivery',
                'label': 'Completar Entrega',
                'started_at': workflow.get('driver_delivery_wait_started_at'),
                'action_required_by': 'driver'
            })
        
        if waiting_info['waiting_for']:
            waiting_orders.append(waiting_info)
    
    logger.info(f"Found {len(waiting_orders)} orders waiting for action")
    
    return success_response({
        'waiting_orders': waiting_orders,
        'count': len(waiting_orders)
    })

