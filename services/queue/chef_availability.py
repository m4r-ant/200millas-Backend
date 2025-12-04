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
    
    Lista todos los chefs disponibles (para admins/staff)
    """
    logger.info("Getting available chefs")
    
    user_type = get_user_type(event)
    if user_type not in ['admin', 'staff', 'chef']:
        raise UnauthorizedError("No autorizado")
    
    tenant_id = get_tenant_id(event)
    
    # Query todos los chefs
    all_chefs = availability_db.query_items(
        'staff_type',
        'chef',
        index_name='staff-type-index'
    )
    
    # Filtrar por tenant y ordenar por disponibilidad
    tenant_chefs = [
        chef for chef in all_chefs
        if chef.get('tenant_id') == tenant_id
    ]
    
    # Separar por status
    available = [c for c in tenant_chefs if c.get('status') == 'available']
    busy = [c for c in tenant_chefs if c.get('status') == 'busy']
    offline = [c for c in tenant_chefs if c.get('status') == 'offline']
    
    logger.info(f"Found {len(available)} available chefs")
    
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
