"""
Driver Availability Management
Permite a drivers reportar su disponibilidad
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
    POST /driver/availability
    Body: {"status": "available" | "busy" | "offline"}
    
    Permite a un driver reportar su disponibilidad actual
    """
    logger.info("Driver reporting availability")
    
    # Verificar que es un driver
    user_type = get_user_type(event)
    if user_type != 'driver':
        raise UnauthorizedError("Solo drivers pueden reportar disponibilidad")
    
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
        'staff_type': 'driver',
        'email': user_email,
        'user_id': user_id,
        'tenant_id': tenant_id,
        'status': status,
        'updated_at': timestamp,
        'expires_at': timestamp + 86400,  # TTL 24 horas
        'deliveries_completed': current_record.get('deliveries_completed', 0) if current_record else 0,
        'current_order_id': current_record.get('current_order_id') if current_record else None
    }
    
    # Si cambia a available, limpiar current_order_id
    if status == 'available':
        availability_data['current_order_id'] = None
    
    # Guardar en DynamoDB
    availability_db.put_item(availability_data)
    
    logger.info(f"Driver {staff_id} status updated to {status}")
    
    return success_response({
        'staff_id': staff_id,
        'status': status,
        'message': f'Disponibilidad actualizada a {status}'
    })


@error_handler
def get_available_drivers(event, context):
    """
    GET /driver/available-list
    
    Lista todos los drivers disponibles (para admins/staff)
    """
    logger.info("Getting available drivers")
    
    user_type = get_user_type(event)
    if user_type not in ['admin', 'staff', 'chef']:
        raise UnauthorizedError("No autorizado")
    
    tenant_id = get_tenant_id(event)
    
    # Query todos los drivers
    all_drivers = availability_db.query_items(
        'staff_type',
        'driver',
        index_name='staff-type-index'
    )
    
    # Filtrar por tenant
    tenant_drivers = [
        driver for driver in all_drivers
        if driver.get('tenant_id') == tenant_id
    ]
    
    # Separar por status
    available = [d for d in tenant_drivers if d.get('status') == 'available']
    busy = [d for d in tenant_drivers if d.get('status') == 'busy']
    offline = [d for d in tenant_drivers if d.get('status') == 'offline']
    
    logger.info(f"Found {len(available)} available drivers")
    
    return success_response({
        'available': available,
        'busy': busy,
        'offline': offline,
        'summary': {
            'total': len(tenant_drivers),
            'available_count': len(available),
            'busy_count': len(busy),
            'offline_count': len(offline)
        }
    })
