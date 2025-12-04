"""
Lambda handlers para Step Functions - CON INTEGRACIÓN SQS
"""
import os
import json
import boto3
from shared.utils import current_timestamp, get_logger
from shared.dynamodb import DynamoDBService
from shared.eventbridge import EventBridgeService

logger = get_logger(__name__)
orders_db = DynamoDBService(os.environ.get('ORDERS_TABLE'))
workflow_db = DynamoDBService(os.environ.get('WORKFLOW_TABLE'))
availability_db = DynamoDBService(os.environ.get('STAFF_AVAILABILITY_TABLE', 'dev-StaffAvailability'))

# SQS Client
sqs = boto3.client('sqs')

# URLs de las colas
CHEF_QUEUE_URL = os.environ.get('CHEF_ASSIGNMENT_QUEUE')
# NOTA: Los chefs también empaquetan, no hay cola separada de packers
# NOTA: Drivers son asignados manualmente, no usan SQS


def confirm_order(event, context):
    """Paso 1: Confirma el pedido"""
    try:
        logger.info("Confirming order")
        
        order_id = event.get('order_id')
        tenant_id = event.get('tenant_id', os.environ.get('TENANT_ID'))
        
        timestamp = current_timestamp()
        
        orders_db.update_item(
            {'order_id': order_id},
            {'status': 'confirmed', 'updated_at': timestamp}
        )
        
        workflow = workflow_db.get_item({'order_id': order_id}) or {
            'order_id': order_id,
            'steps': []
        }
        
        if workflow.get('steps'):
            last_step = workflow['steps'][-1]
            if last_step.get('status') == 'pending' and not last_step.get('completed_at'):
                last_step['completed_at'] = timestamp
        
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
        
        EventBridgeService.put_event(
            source='workflow.service',
            detail_type='OrderConfirmed',
            detail={'order_id': order_id, 'status': 'confirmed'},
            tenant_id=tenant_id
        )
        
        logger.info(f"Order {order_id} confirmed successfully")
        
        return {
            'order_id': order_id,
            'status': 'confirmed',
            'timestamp': timestamp,
            'success': True
        }
        
    except Exception as e:
        logger.error(f"Error confirming order: {str(e)}")
        raise Exception(f"ConfirmOrderError: {str(e)}")


def assign_cook(event, context):
    """
    Paso 2: ENVÍA PEDIDO A COLA SQS para asignación de chef
    ✅ CAMBIO: Ya no asigna directamente, usa SQS
    """
    try:
        logger.info("Sending order to chef assignment queue")
        
        order_id = event.get('order_id')
        tenant_id = event.get('tenant_id', os.environ.get('TENANT_ID'))
        
        if not CHEF_QUEUE_URL:
            logger.error("CHEF_QUEUE_URL not configured")
            raise Exception("Chef queue not configured")
        
        # ============================================
        # ENVIAR MENSAJE A COLA SQS
        # ============================================
        message_body = json.dumps({
            'order_id': order_id,
            'tenant_id': tenant_id,
            'timestamp': current_timestamp()
        })
        
        response = sqs.send_message(
            QueueUrl=CHEF_QUEUE_URL,
            MessageBody=message_body
        )
        
        message_id = response.get('MessageId')
        logger.info(f"✅ Order {order_id} sent to chef queue. MessageId: {message_id}")
        
        # Publicar evento
        EventBridgeService.put_event(
            source='workflow.service',
            detail_type='OrderSentToChefQueue',
            detail={
                'order_id': order_id,
                'message_id': message_id,
                'queue': 'chef_assignment'
            },
            tenant_id=tenant_id
        )
        
        return {
            'order_id': order_id,
            'status': 'queued_for_chef',
            'message_id': message_id,
            'success': True
        }
        
    except Exception as e:
        logger.error(f"Error sending to chef queue: {str(e)}")
        raise Exception(f"AssignCookError: {str(e)}")


def complete_cooking(event, context):
    """
    Paso 3: Marca como packing (el mismo chef empaqueta)
    ✅ CAMBIO: El chef cocina y luego empaqueta, no se envía a otra cola
    """
    try:
        logger.info("Completing cooking, chef will now pack")
        
        order_id = event.get('order_id')
        tenant_id = event.get('tenant_id', os.environ.get('TENANT_ID'))
        
        timestamp = current_timestamp()
        
        # Obtener el pedido para saber qué chef estaba asignado
        order = orders_db.get_item({'order_id': order_id})
        assigned_chef = order.get('assigned_chef') if order else None
        
        # Marcar como packing (el mismo chef empaqueta)
        orders_db.update_item(
            {'order_id': order_id},
            {'status': 'packing', 'updated_at': timestamp}
        )
        
        workflow = workflow_db.get_item({'order_id': order_id})
        if workflow:
            if workflow.get('steps'):
                last_step = workflow['steps'][-1]
                if last_step.get('status') == 'cooking':
                    last_step['completed_at'] = timestamp
            
            # Agregar step de packing (mismo chef)
            step = {
                'status': 'packing',
                'assigned_to': assigned_chef or 'system',
                'started_at': timestamp,
                'completed_at': None,
                'notes': 'Cocción completada, empaquetando'
            }
            workflow['steps'].append(step)
            workflow['current_status'] = 'packing'
            workflow['updated_at'] = timestamp
            workflow_db.put_item(workflow)
        
        EventBridgeService.put_event(
            source='workflow.service',
            detail_type='OrderCookingCompleted',
            detail={'order_id': order_id, 'status': 'packing', 'chef': assigned_chef},
            tenant_id=tenant_id
        )
        
        logger.info(f"Order {order_id} cooking completed, now packing by {assigned_chef}")
        
        return {
            'order_id': order_id,
            'status': 'packing',
            'timestamp': timestamp,
            'success': True
        }
        
    except Exception as e:
        logger.error(f"Error completing cooking: {str(e)}")
        raise Exception(f"CompleteCookingError: {str(e)}")


def complete_packing(event, context):
    """
    Paso 4: Marca como completamente empaquetado (listo para driver)
    El chef terminó de empaquetar
    """
    try:
        logger.info("Completing packing")
        
        order_id = event.get('order_id')
        tenant_id = event.get('tenant_id', os.environ.get('TENANT_ID'))
        
        timestamp = current_timestamp()
        
        # Obtener el pedido para saber qué chef estaba asignado
        order = orders_db.get_item({'order_id': order_id})
        assigned_chef = order.get('assigned_chef') if order else None
        
        # Marcar como ready (listo para driver)
        orders_db.update_item(
            {'order_id': order_id},
            {'status': 'ready', 'updated_at': timestamp, 'ready_at': timestamp, 'packed_at': timestamp}
        )
        
        # ============================================
        # MARCAR CHEF COMO DISPONIBLE NUEVAMENTE
        # ============================================
        if assigned_chef:
            try:
                # Obtener registro actual del chef
                chef_record = availability_db.get_item({'staff_id': assigned_chef})
                if chef_record:
                    # Incrementar contador de pedidos completados
                    orders_completed = chef_record.get('orders_completed', 0) + 1
                    
                    # Marcar chef como disponible y limpiar current_order_id
                    availability_db.update_item(
                        {'staff_id': assigned_chef},
                        {
                            'status': 'available',
                            'current_order_id': None,
                            'orders_completed': orders_completed,
                            'updated_at': timestamp
                        }
                    )
                    logger.info(f"✅ Chef {assigned_chef} marked as available after completing order {order_id}")
                else:
                    logger.warning(f"Chef {assigned_chef} not found in availability table")
            except Exception as e:
                logger.error(f"Error marking chef as available: {str(e)}")
                # No fallar el proceso si esto falla
        
        workflow = workflow_db.get_item({'order_id': order_id})
        if workflow:
            if workflow.get('steps'):
                last_step = workflow['steps'][-1]
                if last_step.get('status') == 'packing':
                    last_step['completed_at'] = timestamp
            
            step = {
                'status': 'ready',
                'assigned_to': 'system',
                'started_at': timestamp,
                'completed_at': timestamp,
                'notes': 'Empaquetado y listo para recoger por repartidor'
            }
            workflow['steps'].append(step)
            workflow['current_status'] = 'ready'
            workflow['updated_at'] = timestamp
            workflow_db.put_item(workflow)
        
        EventBridgeService.put_event(
            source='workflow.service',
            detail_type='OrderPacked',
            detail={'order_id': order_id, 'status': 'ready', 'chef': assigned_chef},
            tenant_id=tenant_id
        )
        
        # ============================================
        # NOTA: Drivers son asignados MANUALMENTE
        # No se envía a cola SQS, el driver debe recoger el pedido manualmente
        # cuando esté listo (usando POST /driver/pickup/{order_id})
        # ============================================
        
        logger.info(f"Order {order_id} packed and ready for driver")
        
        return {
            'order_id': order_id,
            'status': 'ready',
            'timestamp': timestamp,
            'success': True
        }
        
    except Exception as e:
        logger.error(f"Error completing packing: {str(e)}")
        raise Exception(f"CompletePackingError: {str(e)}")


def handle_order_failure(event, context):
    """Maneja fallos en el workflow"""
    try:
        logger.error(f"Order workflow failed: {json.dumps(event)}")
        
        order_id = event.get('order_id') or event.get('Input', {}).get('order_id')
        error_info = event.get('error', {})
        
        if not order_id:
            return {'status': 'failed', 'error': 'No order_id provided'}
        
        tenant_id = event.get('tenant_id') or os.environ.get('TENANT_ID')
        timestamp = current_timestamp()
        
        orders_db.update_item(
            {'order_id': order_id},
            {'status': 'failed', 'updated_at': timestamp, 'error': str(error_info)}
        )
        
        workflow = workflow_db.get_item({'order_id': order_id})
        if workflow:
            workflow['current_status'] = 'failed'
            workflow['error'] = error_info
            workflow['updated_at'] = timestamp
            workflow_db.put_item(workflow)
        
        EventBridgeService.put_event(
            source='workflow.service',
            detail_type='OrderFailed',
            detail={'order_id': order_id, 'status': 'failed', 'error': error_info},
            tenant_id=tenant_id
        )
        
        return {
            'order_id': order_id,
            'status': 'failed',
            'error': error_info,
            'timestamp': timestamp
        }
        
    except Exception as e:
        logger.error(f"Error handling failure: {str(e)}")
        return {'status': 'failed', 'error': str(e)}
