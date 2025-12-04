"""
Chef Availability Management
Permite a chefs reportar su disponibilidad (available/busy/offline)
"""
import os
from shared.utils import (
    success_response, error_handler, get_tenant_id, get_user_id,
    get_user_email, parse_body, current_timestamp, get_user_type
)
from shared.dynamodb import DynamoDBService
from shared.errors import ValidationError, UnauthorizedError
from shared.logger import get_logger

logger = get_logger(__name__)
availability_db = DynamoDBService(os.environ.get('STAFF_AVAILABILITY_TABLE', 'dev-StaffAvailability'))


@error_handler
def report_availability(event, context):
    """
    POST /chef/availability
    Body: {"status": "available" | "busy" | "offline"}
    
    Permite a un chef reportar su disponibilidad actual
    """
    logger.info("Chef reporting availability")
    
    # Verificar que es un chef
    user_type = get_user_type(event)
    if user_type not in ['chef', 'staff']:
        raise UnauthorizedError("Solo chefs pueden reportar disponibilidad")
    
    user_id = get_user_id(event)
    user_email = get_user_email(event)
    tenant_id = get_tenant_id(event)
    
    body = parse_body(event)
    status = body.get('status', '').strip().lower()
    
    if status not in ['available', 'busy', 'offline']:
        raise ValidationError("status debe ser: available, busy, o offline")
    
    staff_id = user_email or user_id
    timestamp = current_timestamp()
    
    # Obtener registro actual si existe
    current_record = availability_db.get_item({'staff_id': staff_id})
    
    # Preparar datos
    availability_data = {
        'staff_id': staff_id,
        'staff_type': 'chef',
        'email': user_email,
        'user_id': user_id,
        'tenant_id': tenant_id,
        'status': status,
        'updated_at': timestamp,
        'expires_at': timestamp + 86400,  # TTL 24 horas
        'orders_completed': current_record.get('orders_completed', 0) if current_record else 0,
        'current_order_id': current_record.get('current_order_id') if current_record else None
    }
    
    # Si cambia a available, limpiar current_order_id
    if status == 'available':
        availability_data['current_order_id'] = None
    
    # Guardar en DynamoDB
    availability_db.put_item(availability_data)
    
    logger.info(f"Chef {staff_id} status updated to {status}")
    
    return success_response({
        'staff_id': staff_id,
        'status': status,
        'message': f'Disponibilidad actualizada a {status}'
    })


@error_handler
def get_available_chefs(event, context):
    """
    GET /chef/available
    
    Lista todos los chefs con su estado actual y pedido asignado (si está trabajando)
    Permite a chefs ver su propia información y a admins/staff ver todos
    """
    logger.info("Getting available chefs")
    
    user_type = get_user_type(event)
    logger.info(f"User type: {user_type}")
    
    # Permitir a todos los tipos de usuario ver esta información
    # (chefs pueden ver su estado, admins pueden ver todos)
    if user_type not in ['admin', 'staff', 'chef', 'customer']:
        logger.warning(f"Unauthorized user_type: {user_type}")
        raise UnauthorizedError("No autorizado")
    
    tenant_id = get_tenant_id(event)
    
    # Importar orders_db para obtener información del pedido
    from shared.dynamodb import DynamoDBService
    orders_db = DynamoDBService(os.environ.get('ORDERS_TABLE'))
    
    # Query todos los chefs
    all_chefs = availability_db.query_items(
        'staff_type',
        'chef',
        index_name='staff-type-index'
    )
    
    # Filtrar por tenant
    tenant_chefs = [
        chef for chef in all_chefs
        if chef.get('tenant_id') == tenant_id
    ]
    
    # Enriquecer información de chefs ocupados con datos del pedido
    for chef in tenant_chefs:
        if chef.get('status') == 'busy' and chef.get('current_order_id'):
            order_id = chef['current_order_id']
            try:
                order = orders_db.get_item({'order_id': order_id})
                if order:
                    chef['current_order'] = {
                        'order_id': order_id,
                        'status': order.get('status'),
                        'total': float(order.get('total', 0)),
                        'items_count': len(order.get('items', [])),
                        'created_at': order.get('created_at'),
                        'assigned_at': chef.get('assigned_at')
                    }
            except Exception as e:
                logger.warning(f"Error getting order {order_id} for chef {chef.get('staff_id')}: {str(e)}")
                chef['current_order'] = {'order_id': order_id, 'error': 'No se pudo obtener información'}
    
    # Separar por status
    available = [c for c in tenant_chefs if c.get('status') == 'available']
    busy = [c for c in tenant_chefs if c.get('status') == 'busy']
    offline = [c for c in tenant_chefs if c.get('status') == 'offline']
    
    logger.info(f"Found {len(available)} available, {len(busy)} busy, {len(offline)} offline chefs")
    
    return success_response({
        'available': available,
        'busy': busy,
        'offline': offline,
        'summary': {
            'total': len(tenant_chefs),
            'available_count': len(available),
            'busy_count': len(busy),
            'offline_count': len(offline)
        }
    })
