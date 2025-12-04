"""
Driver Queue Processor - Procesa asignaciones de pedidos a drivers disponibles

FLUJO:
1. Pedido ready llega a la cola SQS
2. Lambda busca driver disponible
3. Si hay driver disponible → Asigna y marca en delivery
4. Si NO hay driver disponible → Mensaje regresa a cola (retry)
"""
import os
import json
import boto3
from shared.utils import current_timestamp, get_logger
from shared.dynamodb import DynamoDBService
from shared.eventbridge import EventBridgeService

logger = get_logger(__name__)

# DynamoDB Tables
orders_db = DynamoDBService(os.environ.get('ORDERS_TABLE'))
workflow_db = DynamoDBService(os.environ.get('WORKFLOW_TABLE'))
availability_db = DynamoDBService(os.environ.get('STAFF_AVAILABILITY_TABLE', 'dev-StaffAvailability'))

# SQS Client
sqs = boto3.client('sqs')


def process_driver_assignments(event, context):
    """
    Lambda que procesa la cola SQS de asignación de drivers
    
    Event structure (SQS):
    {
        "Records": [
            {
                "body": "{\"order_id\": \"xxx\", \"tenant_id\": \"200millas\"}",
                "messageId": "...",
                "receiptHandle": "..."
            }
        ]
    }
    """
    logger.info("Processing driver assignment queue")
    
    for record in event.get('Records', []):
        try:
            # Parsear mensaje
            body = json.loads(record['body'])
            order_id = body.get('order_id')
            tenant_id = body.get('tenant_id')
            
            logger.info(f"Processing driver assignment for order {order_id}")
            
            if not order_id:
                logger.error("No order_id in message")
                continue
            
            # ============================================
            # 1. BUSCAR DRIVER DISPONIBLE
            # ============================================
            available_driver = _find_available_driver(tenant_id)
            
            if not available_driver:
                logger.warning(f"No available drivers for order {order_id}")
                # ❌ NO hay drivers disponibles
                # El mensaje volverá a la cola automáticamente
                raise Exception("No available drivers - message will retry")
            
            driver_id = available_driver['staff_id']
            driver_email = available_driver.get('email', driver_id)
            
            logger.info(f"Found available driver: {driver_email}")
            
            # ============================================
            # 2. ASIGNAR DRIVER AL PEDIDO
            # ============================================
            timestamp = current_timestamp()
            
            # Actualizar Orders table
            orders_db.update_item(
                {'order_id': order_id},
                {
                    'status': 'in_delivery',
                    'assigned_driver': driver_email,
                    'pickup_time': timestamp,
                    'updated_at': timestamp
                }
            )
            
            # Actualizar Workflow
            workflow = workflow_db.get_item({'order_id': order_id})
            if workflow:
                # Completar step anterior (ready)
                if workflow.get('steps'):
                    last_step = workflow['steps'][-1]
                    if last_step.get('status') == 'ready' and not last_step.get('completed_at'):
                        last_step['completed_at'] = timestamp
                
                # Agregar nuevo step
                new_step = {
                    'status': 'in_delivery',
                    'assigned_to': driver_email,
                    'started_at': timestamp,
                    'completed_at': None,
                    'notes': f'Asignado automáticamente desde cola SQS'
                }
                workflow['steps'].append(new_step)
                workflow['current_status'] = 'in_delivery'
                workflow['updated_at'] = timestamp
                workflow_db.put_item(workflow)
            
            # ============================================
            # 3. MARCAR DRIVER COMO OCUPADO
            # ============================================
            availability_db.update_item(
                {'staff_id': driver_id},
                {
                    'status': 'busy',
                    'current_order_id': order_id,
                    'assigned_at': timestamp,
                    'updated_at': timestamp
                }
            )
            
            # ============================================
            # 4. PUBLICAR EVENTO
            # ============================================
            EventBridgeService.put_event(
                source='queue.service',
                detail_type='OrderAssignedToDriver',
                detail={
                    'order_id': order_id,
                    'driver_id': driver_id,
                    'driver_email': driver_email,
                    'assigned_at': timestamp,
                    'assignment_method': 'sqs_queue'
                },
                tenant_id=tenant_id
            )
            
            logger.info(f"✅ Order {order_id} assigned to driver {driver_email}")
            
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
            # ❌ El mensaje regresará a la cola para retry
            raise e
    
    return {
        'statusCode': 200,
        'body': json.dumps({'message': 'Driver assignments processed'})
    }


def _find_available_driver(tenant_id):
    """
    Busca un driver disponible en el tenant
    
    Estrategia:
    1. Buscar drivers con status='available'
    2. Ordenar por 'deliveries_completed' (el que menos ha hecho)
    3. Retornar el primero
    """
    try:
        # Query drivers disponibles
        available_drivers = availability_db.query_items(
            'staff_type',
            'driver',
            index_name='staff-type-index'
        )
        
        # Filtrar solo los disponibles del tenant correcto
        available_drivers = [
            driver for driver in available_drivers
            if driver.get('status') == 'available' and 
               driver.get('tenant_id') == tenant_id
        ]
        
        if not available_drivers:
            return None
        
        # Ordenar por carga de trabajo (menos entregas = prioridad)
        available_drivers.sort(key=lambda x: x.get('deliveries_completed', 0))
        
        return available_drivers[0]
        
    except Exception as e:
        logger.error(f"Error finding available driver: {str(e)}")
        return None
