import os
from shared.utils import (
    response, success_response, error_response, error_handler,
    parse_body, get_tenant_id, current_timestamp, get_path_param_from_path
)
from shared.dynamodb import DynamoDBService
from shared.eventbridge import EventBridgeService
from shared.errors import NotFoundError, ValidationError
from shared.logger import get_logger

logger = get_logger(__name__)
orders_db = DynamoDBService(os.environ.get('ORDERS_TABLE'))
workflow_db = DynamoDBService(os.environ.get('WORKFLOW_TABLE'))

VALID_STATUSES = ['pending', 'confirmed', 'cooking', 'packing', 'ready', 'in_delivery', 'delivered']

@error_handler
def update_workflow(event, context):
    logger.info("Updating workflow")
    
    # ✅ Usar la función mejorada para extraer order_id del path
    order_id = get_path_param_from_path(event, 'order_id')
    
    body = parse_body(event)
    tenant_id = get_tenant_id(event)
    
    logger.info(f"Extracted order_id: {order_id}")
    
    if not order_id:
        raise ValidationError("order_id es requerido")
    
    new_status = body.get('status', '').strip()
    assigned_to = body.get('assigned_to', '').strip()
    
    if not new_status:
        raise ValidationError("status es requerido")
    
    if new_status not in VALID_STATUSES:
        raise ValidationError(f"Status inválido. Válidos: {', '.join(VALID_STATUSES)}")
    
    timestamp = current_timestamp()
    
    workflow = workflow_db.get_item({'order_id': order_id})
    if not workflow:
        workflow = {
            'order_id': order_id,
            'steps': []
        }
    
    step = {
        'status': new_status,
        'assigned_to': assigned_to if assigned_to else 'system',
        'started_at': timestamp,
        'completed_at': None
    }
    
    workflow['steps'].append(step)
    workflow['current_status'] = new_status
    workflow['updated_at'] = timestamp
    
    success = workflow_db.put_item(workflow)
    if not success:
        logger.error(f"Failed to update workflow for {order_id}")
        raise Exception("Error al actualizar el workflow")
    
    orders_db.update_item(
        {'order_id': order_id},
        {'status': new_status, 'updated_at': timestamp}
    )
    
    EventBridgeService.put_event(
        source='workflow.service',
        detail_type='WorkflowUpdated',
        detail={
            'order_id': order_id,
            'status': new_status,
            'assigned_to': assigned_to,
            'timestamp': timestamp
        },
        tenant_id=tenant_id
    )
    
    logger.info(f"Workflow updated for {order_id}: {new_status}")
    
    return success_response(workflow)

@error_handler
def get_workflow(event, context):
    logger.info("Getting workflow")
    
    # ✅ Usar la función mejorada para extraer order_id del path
    order_id = get_path_param_from_path(event, 'order_id')
    
    logger.info(f"Extracted order_id: {order_id}")
    
    if not order_id:
        raise ValidationError("order_id es requerido")
    
    workflow = workflow_db.get_item({'order_id': order_id})
    
    if not workflow:
        return success_response({
            'order_id': order_id,
            'current_status': 'pending',
            'steps': []
        })
    
    logger.info(f"Workflow retrieved for {order_id}")
    
    return success_response(workflow)
