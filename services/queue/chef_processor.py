"""
Chef Queue Processor - Procesa asignaciones de pedidos a chefs disponibles

FLUJO:
1. Pedido llega a la cola SQS
2. Lambda busca chef disponible
3. Si hay chef disponible → Asigna y empieza cocina
4. Si NO hay chef disponible → Mensaje regresa a cola (retry)
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


def process_chef_assignments(event, context):
    """
    Lambda que procesa la cola SQS de asignación de chefs
    
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
    logger.info("Processing chef assignment queue")
    
    for record in event.get('Records', []):
        try:
            # Parsear mensaje
            body = json.loads(record['body'])
            order_id = body.get('order_id')
            tenant_id = body.get('tenant_id')
            
            logger.info(f"Processing assignment for order {order_id}")
            
            if not order_id:
                logger.error("No order_id in message")
                continue
            
            # ============================================
            # 1. BUSCAR CHEF DISPONIBLE
            # ============================================
            available_chef = _find_available_chef(tenant_id)
            
            if not available_chef:
                logger.warning(f"No available chefs for order {order_id}")
                # ❌ NO hay chefs disponibles
                # El mensaje volverá a la cola automáticamente después del VisibilityTimeout
                # Y se reintentará hasta 3 veces (maxReceiveCount)
                raise Exception("No available chefs - message will retry")
            
            chef_id = available_chef['staff_id']
            chef_email = available_chef.get('email', chef_id)
            
            logger.info(f"Found available chef: {chef_email}")
            
            # ============================================
            # 2. ASIGNAR CHEF AL PEDIDO
            # ============================================
            timestamp = current_timestamp()
            
            # Actualizar Orders table
            orders_db.update_item(
                {'order_id': order_id},
                {
                    'status': 'cooking',
                    'assigned_chef': chef_email,
                    'assigned_at': timestamp,
                    'updated_at': timestamp
                }
            )
            
            # Actualizar Workflow
            workflow = workflow_db.get_item({'order_id': order_id})
            if workflow:
                # Completar step anterior (confirmed)
                if workflow.get('steps'):
                    last_step = workflow['steps'][-1]
                    if last_step.get('status') == 'confirmed' and not last_step.get('completed_at'):
                        last_step['completed_at'] = timestamp
                
                # Agregar nuevo step
                new_step = {
                    'status': 'cooking',
                    'assigned_to': chef_email,
                    'started_at': timestamp,
                    'completed_at': None,
                    'notes': f'Asignado automáticamente desde cola SQS'
                }
                workflow['steps'].append(new_step)
                workflow['current_status'] = 'cooking'
                workflow['updated_at'] = timestamp
                workflow_db.put_item(workflow)
            
            # ============================================
            # 3. MARCAR CHEF COMO OCUPADO
            # ============================================
            availability_db.update_item(
                {'staff_id': chef_id},
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
                detail_type='OrderAssignedToChef',
                detail={
                    'order_id': order_id,
                    'chef_id': chef_id,
                    'chef_email': chef_email,
                    'assigned_at': timestamp,
                    'assignment_method': 'sqs_queue'
                },
                tenant_id=tenant_id
            )
            
            logger.info(f"✅ Order {order_id} assigned to chef {chef_email}")
            
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
            # ❌ El mensaje regresará a la cola para retry
            raise e
    
    return {
        'statusCode': 200,
        'body': json.dumps({'message': 'Chef assignments processed'})
    }


def _find_available_chef(tenant_id):
    """
    Busca un chef disponible en el tenant
    
    Estrategia:
    1. Buscar chefs con status='available'
    2. Ordenar por 'orders_completed' (el que menos ha hecho)
    3. Retornar el primero
    """
    try:
        # Query chefs disponibles
        available_chefs = availability_db.query_items(
            'staff_type',
            'chef',
            index_name='staff-type-index'
        )
        
        # Filtrar solo los disponibles del tenant correcto
        available_chefs = [
            chef for chef in available_chefs
            if chef.get('status') == 'available' and 
               chef.get('tenant_id') == tenant_id
        ]
        
        if not available_chefs:
            return None
        
        # Ordenar por carga de trabajo (menos pedidos completados = prioridad)
        available_chefs.sort(key=lambda x: x.get('orders_completed', 0))
        
        return available_chefs[0]
        
    except Exception as e:
        logger.error(f"Error finding available chef: {str(e)}")
        return None
