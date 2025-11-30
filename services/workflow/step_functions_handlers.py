"""
Lambda handlers para Step Functions - Workflow automatizado
Incluye estado 'ready' para que drivers puedan ver pedidos listos
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
    """
    Paso 1: Confirma el pedido
    Estado: pending → confirmed
    """
    try:
        logger.info("Confirming order")
        
        order_id = event.get('order_id')
        if not order_id:
            raise ValueError("order_id is required")
            
        tenant_id = event.get('tenant_id', os.environ.get('TENANT_ID'))
        
        timestamp = current_timestamp()
        
        # Verificar que la orden existe
        order = orders_db.get_item({'order_id': order_id})
        if not order:
            raise ValueError(f"Order {order_id} not found")
        
        # ✅ ACTUALIZAR ORDERS TABLE
        orders_db.update_item(
            {'order_id': order_id},
            {'status': 'confirmed', 'updated_at': timestamp}
        )
        
        # Actualizar workflow
        workflow = workflow_db.get_item({'order_id': order_id}) or {
            'order_id': order_id,
            'steps': []
        }
        
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
    Paso 2: Asigna cocinero al pedido
    Estado: confirmed → cooking
    """
    try:
        logger.info("Assigning cook to order")
        
        order_id = event.get('order_id')
        if not order_id:
            raise ValueError("order_id is required")
            
        tenant_id = event.get('tenant_id', os.environ.get('TENANT_ID'))
        assigned_to = event.get('assigned_to', 'chef@200millas.com')
        
        timestamp = current_timestamp()
        
        # Verificar que la orden existe
        order = orders_db.get_item({'order_id': order_id})
        if not order:
            raise ValueError(f"Order {order_id} not found")
        
        # ✅ ACTUALIZAR ORDERS TABLE
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
        
        logger.info(f"Cook assigned to order {order_id}")
        
        return {
            'order_id': order_id,
            'status': 'cooking',
            'assigned_to': assigned_to,
            'timestamp': timestamp,
            'success': True
        }
        
    except Exception as e:
        logger.error(f"Error assigning cook: {str(e)}")
        raise Exception(f"AssignCookError: {str(e)}")


def complete_cooking(event, context):
    """
    Paso 3: Completa la cocción
    Estado: cooking → ready
    ✅ CAMBIO: Ahora pone estado 'ready' en lugar de 'packing'
    """
    try:
        logger.info("Completing cooking - marking as ready")
        
        order_id = event.get('order_id')
        if not order_id:
            raise ValueError("order_id is required")
            
        tenant_id = event.get('tenant_id', os.environ.get('TENANT_ID'))
        
        timestamp = current_timestamp()
        
        # Verificar que la orden existe
        order = orders_db.get_item({'order_id': order_id})
        if not order:
            raise ValueError(f"Order {order_id} not found")
        
        # ✅ ACTUALIZAR ORDERS TABLE - Ahora con estado 'ready'
        orders_db.update_item(
            {'order_id': order_id},
            {'status': 'ready', 'updated_at': timestamp, 'ready_at': timestamp}
        )
        
        # Actualizar workflow - completar step de cooking
        workflow = workflow_db.get_item({'order_id': order_id})
        if not workflow:
            raise ValueError(f"Workflow for order {order_id} not found")
            
        if workflow.get('steps'):
            last_step = workflow['steps'][-1]
            if last_step.get('status') == 'cooking':
                last_step['completed_at'] = timestamp
        
        # ✅ Agregar nuevo step con estado 'ready'
        step = {
            'status': 'ready',
            'assigned_to': 'system',
            'started_at': timestamp,
            'completed_at': None,
            'notes': 'Pedido listo para recoger'
        }
        workflow['steps'].append(step)
        workflow['current_status'] = 'ready'
        workflow['updated_at'] = timestamp
        workflow_db.put_item(workflow)
        
        # Publicar evento
        EventBridgeService.put_event(
            source='workflow.service',
            detail_type='OrderReady',
            detail={
                'order_id': order_id, 
                'status': 'ready',
                'message': 'Pedido listo para recoger por driver'
            },
            tenant_id=tenant_id
        )
        
        logger.info(f"Order {order_id} marked as ready for pickup")
        
        return {
            'order_id': order_id,
            'status': 'ready',
            'timestamp': timestamp,
            'success': True
        }
        
    except Exception as e:
        logger.error(f"Error completing cooking: {str(e)}")
        raise Exception(f"CompleteCookingError: {str(e)}")


def assign_driver(event, context):
    """
    Paso 4: Asigna repartidor
    Estado: ready → in_delivery
    ✅ CAMBIO: Ahora transiciona desde 'ready' en lugar de 'packing'
    """
    try:
        logger.info("Assigning driver to order")
        
        order_id = event.get('order_id')
        if not order_id:
            raise ValueError("order_id is required")
            
        tenant_id = event.get('tenant_id', os.environ.get('TENANT_ID'))
        assigned_to = event.get('assigned_to', 'driver@200millas.com')
        
        timestamp = current_timestamp()
        
        # Verificar que la orden existe
        order = orders_db.get_item({'order_id': order_id})
        if not order:
            raise ValueError(f"Order {order_id} not found")
        
        # ✅ ACTUALIZAR ORDERS TABLE
        orders_db.update_item(
            {'order_id': order_id},
            {
                'status': 'in_delivery', 
                'updated_at': timestamp,
                'assigned_driver': assigned_to,
                'pickup_time': timestamp
            }
        )
        
        # Actualizar workflow - completar step de ready
        workflow = workflow_db.get_item({'order_id': order_id})
        if not workflow:
            raise ValueError(f"Workflow for order {order_id} not found")
            
        if workflow.get('steps'):
            last_step = workflow['steps'][-1]
            # ✅ Completar el step 'ready'
            if last_step.get('status') == 'ready':
                last_step['completed_at'] = timestamp
        
        # Agregar nuevo step
        step = {
            'status': 'in_delivery',
            'assigned_to': assigned_to,
            'started_at': timestamp,
            'completed_at': None,
            'notes': f'Asignado a {assigned_to}'
        }
        workflow['steps'].append(step)
        workflow['current_status'] = 'in_delivery'
        workflow['updated_at'] = timestamp
        workflow_db.put_item(workflow)
        
        # Publicar evento
        EventBridgeService.put_event(
            source='workflow.service',
            detail_type='OrderInDelivery',
            detail={
                'order_id': order_id, 
                'status': 'in_delivery', 
                'assigned_to': assigned_to,
                'pickup_time': timestamp
            },
            tenant_id=tenant_id
        )
        
        logger.info(f"Driver {assigned_to} assigned to order {order_id}")
        
        return {
            'order_id': order_id,
            'status': 'in_delivery',
            'assigned_to': assigned_to,
            'timestamp': timestamp,
            'success': True
        }
        
    except Exception as e:
        logger.error(f"Error assigning driver: {str(e)}")
        raise Exception(f"AssignDriverError: {str(e)}")


def complete_delivery(event, context):
    """
    Paso 5: Completa la entrega
    Estado: in_delivery → delivered
    """
    try:
        logger.info("Completing delivery")
        
        order_id = event.get('order_id')
        if not order_id:
            raise ValueError("order_id is required")
            
        tenant_id = event.get('tenant_id', os.environ.get('TENANT_ID'))
        
        timestamp = current_timestamp()
        
        # Verificar que la orden existe
        order = orders_db.get_item({'order_id': order_id})
        if not order:
            raise ValueError(f"Order {order_id} not found")
        
        # ✅ ACTUALIZAR ORDERS TABLE
        orders_db.update_item(
            {'order_id': order_id},
            {
                'status': 'delivered', 
                'updated_at': timestamp,
                'delivered_at': timestamp
            }
        )
        
        # Actualizar workflow - completar step de delivery
        workflow = workflow_db.get_item({'order_id': order_id})
        if not workflow:
            raise ValueError(f"Workflow for order {order_id} not found")
            
        if workflow.get('steps'):
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
            detail={
                'order_id': order_id, 
                'status': 'delivered',
                'delivered_at': timestamp
            },
            tenant_id=tenant_id
        )
        
        logger.info(f"Order {order_id} delivered successfully")
        
        return {
            'order_id': order_id,
            'status': 'delivered',
            'timestamp': timestamp,
            'success': True
        }
        
    except Exception as e:
        logger.error(f"Error completing delivery: {str(e)}")
        raise Exception(f"CompleteDeliveryError: {str(e)}")


def handle_order_failure(event, context):
    """Maneja fallos en el workflow"""
    try:
        logger.error(f"Order workflow failed: {json.dumps(event)}")
        
        order_id = event.get('order_id') or event.get('Input', {}).get('order_id')
        error_info = event.get('error', {})
        
        if not order_id:
            logger.error("No order_id found in failure event")
            return {
                'status': 'failed',
                'error': 'No order_id provided'
            }
        
        tenant_id = event.get('tenant_id') or event.get('Input', {}).get('tenant_id') or os.environ.get('TENANT_ID')
        timestamp = current_timestamp()
        
        # ✅ ACTUALIZAR ORDERS TABLE
        try:
            orders_db.update_item(
                {'order_id': order_id},
                {
                    'status': 'failed',
                    'updated_at': timestamp,
                    'error': str(error_info)
                }
            )
        except Exception as e:
            logger.error(f"Error updating order status: {str(e)}")
        
        # Actualizar workflow
        try:
            workflow = workflow_db.get_item({'order_id': order_id})
            if workflow:
                workflow['current_status'] = 'failed'
                workflow['error'] = error_info
                workflow['updated_at'] = timestamp
                workflow_db.put_item(workflow)
        except Exception as e:
            logger.error(f"Error updating workflow: {str(e)}")
        
        # Publicar evento
        EventBridgeService.put_event(
            source='workflow.service',
            detail_type='OrderFailed',
            detail={
                'order_id': order_id,
                'status': 'failed',
                'error': error_info
            },
            tenant_id=tenant_id
        )
        
        logger.info(f"Order {order_id} failure handled successfully")
        
        return {
            'order_id': order_id,
            'status': 'failed',
            'error': error_info,
            'timestamp': timestamp
        }
        
    except Exception as e:
        logger.error(f"Error handling order failure: {str(e)}")
        return {
            'status': 'failed',
            'error': str(e)
        }
