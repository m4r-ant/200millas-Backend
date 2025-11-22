"""
Lambda handlers para Step Functions - Workflow automatizado
Cada función maneja un estado específico del workflow
"""
import os
import json
from shared.utils import get_logger
from shared.dynamodb import DynamoDBService
from shared.eventbridge import EventBridgeService
from shared.utils import current_timestamp

logger = get_logger(__name__)
orders_db = DynamoDBService(os.environ.get('ORDERS_TABLE'))
workflow_db = DynamoDBService(os.environ.get('WORKFLOW_TABLE'))


def confirm_order(event, context):
    """Confirma el pedido - Estado: confirmed"""
    logger.info("Confirming order")
    
    order_id = event.get('order_id')
    tenant_id = event.get('tenant_id', os.environ.get('TENANT_ID'))
    
    timestamp = current_timestamp()
    
    # Actualizar orden
    orders_db.update_item(
        {'order_id': order_id},
        {'status': 'confirmed', 'updated_at': timestamp}
    )
    
    # Actualizar workflow
    workflow = workflow_db.get_item({'order_id': order_id}) or {'order_id': order_id, 'steps': []}
    
    # Completar step anterior si existe
    if workflow.get('steps'):
        last_step = workflow['steps'][-1]
        if last_step.get('status') == 'pending' and not last_step.get('completed_at'):
            last_step['completed_at'] = timestamp
    
    # Agregar nuevo step
    step = {
        'status': 'confirmed',
        'assigned_to': 'system',
        'started_at': timestamp,
        'completed_at': timestamp
    }
    workflow['steps'].append(step)
    workflow['current_status'] = 'confirmed'
    workflow['updated_at'] = timestamp
    workflow_db.put_item(workflow)
    
    # Publicar evento
    EventBridgeService.put_event(
        source='workflow.service',
        detail_type='OrderConfirmed',
        detail={'order_id': order_id, 'status': 'confirmed'},
        tenant_id=tenant_id
    )
    
    return {
        'order_id': order_id,
        'status': 'confirmed',
        'timestamp': timestamp
    }


def assign_cook(event, context):
    """Asigna cocinero al pedido - Estado: cooking"""
    logger.info("Assigning cook to order")
    
    order_id = event.get('order_id')
    tenant_id = event.get('tenant_id', os.environ.get('TENANT_ID'))
    assigned_to = event.get('assigned_to', 'chef@200millas.com')
    
    timestamp = current_timestamp()
    
    # Actualizar orden
    orders_db.update_item(
        {'order_id': order_id},
        {'status': 'cooking', 'updated_at': timestamp}
    )
    
    # Actualizar workflow
    workflow = workflow_db.get_item({'order_id': order_id}) or {'order_id': order_id, 'steps': []}
    
    # Completar step anterior
    if workflow.get('steps'):
        last_step = workflow['steps'][-1]
        if last_step.get('status') == 'confirmed' and not last_step.get('completed_at'):
            last_step['completed_at'] = timestamp
    
    # Agregar nuevo step
    step = {
        'status': 'cooking',
        'assigned_to': assigned_to,
        'started_at': timestamp,
        'completed_at': None
    }
    workflow['steps'].append(step)
    workflow['current_status'] = 'cooking'
    workflow['updated_at'] = timestamp
    workflow_db.put_item(workflow)
    
    # Publicar evento
    EventBridgeService.put_event(
        source='workflow.service',
        detail_type='OrderCooking',
        detail={'order_id': order_id, 'status': 'cooking', 'assigned_to': assigned_to},
        tenant_id=tenant_id
    )
    
    return {
        'order_id': order_id,
        'status': 'cooking',
        'assigned_to': assigned_to,
        'timestamp': timestamp
    }


def complete_cooking(event, context):
    """Completa la cocción - Estado: packing"""
    logger.info("Completing cooking")
    
    order_id = event.get('order_id')
    tenant_id = event.get('tenant_id', os.environ.get('TENANT_ID'))
    
    timestamp = current_timestamp()
    
    # Actualizar orden
    orders_db.update_item(
        {'order_id': order_id},
        {'status': 'packing', 'updated_at': timestamp}
    )
    
    # Actualizar workflow - completar step de cooking
    workflow = workflow_db.get_item({'order_id': order_id})
    if workflow and workflow.get('steps'):
        last_step = workflow['steps'][-1]
        if last_step.get('status') == 'cooking':
            last_step['completed_at'] = timestamp
    
    # Agregar nuevo step de packing
    step = {
        'status': 'packing',
        'assigned_to': event.get('packer', 'packer@200millas.com'),
        'started_at': timestamp,
        'completed_at': None
    }
    workflow['steps'].append(step)
    workflow['current_status'] = 'packing'
    workflow['updated_at'] = timestamp
    workflow_db.put_item(workflow)
    
    # Publicar evento
    EventBridgeService.put_event(
        source='workflow.service',
        detail_type='OrderPacking',
        detail={'order_id': order_id, 'status': 'packing'},
        tenant_id=tenant_id
    )
    
    return {
        'order_id': order_id,
        'status': 'packing',
        'timestamp': timestamp
    }


def assign_driver(event, context):
    """Asigna repartidor - Estado: in_delivery"""
    logger.info("Assigning driver to order")
    
    order_id = event.get('order_id')
    tenant_id = event.get('tenant_id', os.environ.get('TENANT_ID'))
    assigned_to = event.get('assigned_to', 'driver@200millas.com')
    
    timestamp = current_timestamp()
    
    # Actualizar orden
    orders_db.update_item(
        {'order_id': order_id},
        {'status': 'in_delivery', 'updated_at': timestamp}
    )
    
    # Actualizar workflow - completar step de packing
    workflow = workflow_db.get_item({'order_id': order_id})
    if workflow and workflow.get('steps'):
        last_step = workflow['steps'][-1]
        if last_step.get('status') == 'packing':
            last_step['completed_at'] = timestamp
    
    # Agregar nuevo step
    step = {
        'status': 'in_delivery',
        'assigned_to': assigned_to,
        'started_at': timestamp,
        'completed_at': None
    }
    workflow['steps'].append(step)
    workflow['current_status'] = 'in_delivery'
    workflow['updated_at'] = timestamp
    workflow_db.put_item(workflow)
    
    # Publicar evento
    EventBridgeService.put_event(
        source='workflow.service',
        detail_type='OrderInDelivery',
        detail={'order_id': order_id, 'status': 'in_delivery', 'assigned_to': assigned_to},
        tenant_id=tenant_id
    )
    
    return {
        'order_id': order_id,
        'status': 'in_delivery',
        'assigned_to': assigned_to,
        'timestamp': timestamp
    }


def complete_delivery(event, context):
    """Completa la entrega - Estado: delivered"""
    logger.info("Completing delivery")
    
    order_id = event.get('order_id')
    tenant_id = event.get('tenant_id', os.environ.get('TENANT_ID'))
    
    timestamp = current_timestamp()
    
    # Actualizar orden
    orders_db.update_item(
        {'order_id': order_id},
        {'status': 'delivered', 'updated_at': timestamp}
    )
    
    # Actualizar workflow - completar step de delivery
    workflow = workflow_db.get_item({'order_id': order_id})
    if workflow and workflow.get('steps'):
        last_step = workflow['steps'][-1]
        if last_step.get('status') == 'in_delivery':
            last_step['completed_at'] = timestamp
    
    workflow['current_status'] = 'delivered'
    workflow['updated_at'] = timestamp
    workflow_db.put_item(workflow)
    
    # Publicar evento
    EventBridgeService.put_event(
        source='workflow.service',
        detail_type='OrderDelivered',
        detail={'order_id': order_id, 'status': 'delivered'},
        tenant_id=tenant_id
    )
    
    logger.info(f"Order {order_id} delivered successfully")
    
    return {
        'order_id': order_id,
        'status': 'delivered',
        'timestamp': timestamp,
        'success': True
    }

