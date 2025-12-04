"""
Microservicio de Direcciones
Gestiona las direcciones de entrega de los usuarios
"""

import os
import uuid
from shared.utils import (
    get_user_id, get_user_email, get_tenant_id,
    parse_body, current_timestamp, success_response, error_response
)
from shared.errors import ValidationError, NotFoundError, UnauthorizedError
from shared.dynamodb import DynamoDBService
from shared.logger import get_logger
from shared.utils import error_handler

logger = get_logger(__name__)

# Tabla de direcciones
addresses_db = DynamoDBService(os.environ.get('ADDRESSES_TABLE', 'dev-Addresses'))


# ============================================================================
# FUNCIÓN 1: GET ADDRESSES - Listar direcciones del usuario
# ============================================================================

@error_handler
def get_addresses(event, context):
    """
    Obtiene todas las direcciones del usuario autenticado
    
    GET /addresses
    """
    logger.info("Getting user addresses")
    
    user_email = get_user_email(event)
    user_id = get_user_id(event)
    tenant_id = get_tenant_id(event)
    
    if not user_email:
        raise ValidationError("No se pudo identificar al usuario")
    
    logger.info(f"User {user_id} ({user_email}) requesting addresses")
    
    # Obtener todas las direcciones del usuario
    # Usar GSI si existe, sino hacer scan y filtrar
    try:
        # Intentar usar GSI user-addresses-index
        addresses = addresses_db.query_items(
            'user_email',
            user_email,
            index_name='user-addresses-index'
        )
    except Exception as e:
        logger.warning(f"GSI query failed, using scan: {str(e)}")
        # Fallback: scan y filtrar
        all_addresses = addresses_db.scan_items()
        addresses = [
            addr for addr in all_addresses
            if addr.get('user_email') == user_email and addr.get('tenant_id') == tenant_id
        ]
    
    logger.info(f"Found {len(addresses)} addresses for {user_email}")
    
    return success_response(addresses)


# ============================================================================
# FUNCIÓN 2: CREATE ADDRESS - Crear nueva dirección
# ============================================================================

@error_handler
def create_address(event, context):
    """
    Crea una nueva dirección para el usuario autenticado
    
    POST /addresses
    Body: {
        "label": "Casa",
        "street": "Av. Principal 123",
        "district": "San Isidro",
        "city": "Lima",
        "postal_code": "15036",
        "reference": "Frente al parque",
        "is_default": false
    }
    """
    logger.info("Creating new address")
    
    user_email = get_user_email(event)
    user_id = get_user_id(event)
    tenant_id = get_tenant_id(event)
    body = parse_body(event)
    
    if not user_email:
        raise ValidationError("No se pudo identificar al usuario")
    
    # Validar campos requeridos
    required_fields = ['street', 'district', 'city']
    for field in required_fields:
        if not body.get(field):
            raise ValidationError(f"El campo '{field}' es requerido")
    
    # Si se marca como default, desmarcar las demás
    if body.get('is_default', False):
        try:
            existing_addresses = addresses_db.query_items(
                'user_email',
                user_email,
                index_name='user-addresses-index'
            )
            for addr in existing_addresses:
                if addr.get('is_default'):
                    addresses_db.update_item(
                        {'address_id': addr['address_id']},
                        {'is_default': False}
                    )
        except Exception as e:
            logger.warning(f"Could not update default addresses: {str(e)}")
    
    # Generar ID único
    address_id = str(uuid.uuid4())
    timestamp = current_timestamp()
    
    address = {
        'address_id': address_id,
        'user_id': user_id,
        'user_email': user_email,
        'tenant_id': tenant_id,
        'label': body.get('label', 'Dirección'),
        'street': body.get('street'),
        'district': body.get('district'),
        'city': body.get('city'),
        'postal_code': body.get('postal_code', ''),
        'reference': body.get('reference', ''),
        'is_default': body.get('is_default', False),
        'created_at': timestamp,
        'updated_at': timestamp
    }
    
    # Guardar en DynamoDB
    success = addresses_db.put_item(address)
    if not success:
        raise Exception("Error al crear la dirección en la base de datos")
    
    logger.info(f"Address {address_id} created successfully for {user_email}")
    
    return success_response(address, 201)


# ============================================================================
# FUNCIÓN 3: UPDATE ADDRESS - Actualizar dirección
# ============================================================================

@error_handler
def update_address(event, context):
    """
    Actualiza una dirección existente
    
    PATCH /addresses/{address_id}
    Body: { "label": "Nuevo nombre", "street": "...", ... }
    """
    logger.info("Updating address")
    
    user_email = get_user_email(event)
    user_id = get_user_id(event)
    tenant_id = get_tenant_id(event)
    body = parse_body(event)
    
    if not user_email:
        raise ValidationError("No se pudo identificar al usuario")
    
    # Obtener address_id del path
    address_id = event.get('pathParameters', {}).get('address_id')
    if not address_id:
        raise ValidationError("address_id es requerido")
    
    # Obtener dirección existente
    address = addresses_db.get_item({'address_id': address_id})
    if not address:
        raise NotFoundError("Dirección no encontrada")
    
    # Verificar que pertenece al usuario
    if address.get('user_email') != user_email or address.get('tenant_id') != tenant_id:
        raise UnauthorizedError("No tienes permiso para modificar esta dirección")
    
    # Campos permitidos para actualizar
    allowed_fields = ['label', 'street', 'district', 'city', 'postal_code', 'reference', 'is_default']
    update_data = {}
    
    for field in allowed_fields:
        if field in body:
            update_data[field] = body[field]
    
    if not update_data:
        raise ValidationError("No hay campos válidos para actualizar")
    
    # Si se marca como default, desmarcar las demás
    if update_data.get('is_default', False):
        try:
            existing_addresses = addresses_db.query_items(
                'user_email',
                user_email,
                index_name='user-addresses-index'
            )
            for addr in existing_addresses:
                if addr.get('is_default') and addr['address_id'] != address_id:
                    addresses_db.update_item(
                        {'address_id': addr['address_id']},
                        {'is_default': False}
                    )
        except Exception as e:
            logger.warning(f"Could not update default addresses: {str(e)}")
    
    update_data['updated_at'] = current_timestamp()
    
    # Actualizar en base de datos
    addresses_db.update_item({'address_id': address_id}, update_data)
    
    logger.info(f"Address {address_id} updated successfully")
    
    # Obtener dirección actualizada
    updated_address = addresses_db.get_item({'address_id': address_id})
    
    return success_response(updated_address)


# ============================================================================
# FUNCIÓN 4: DELETE ADDRESS - Eliminar dirección
# ============================================================================

@error_handler
def delete_address(event, context):
    """
    Elimina una dirección
    
    DELETE /addresses/{address_id}
    """
    logger.info("Deleting address")
    
    user_email = get_user_email(event)
    user_id = get_user_id(event)
    tenant_id = get_tenant_id(event)
    
    if not user_email:
        raise ValidationError("No se pudo identificar al usuario")
    
    # Obtener address_id del path
    address_id = event.get('pathParameters', {}).get('address_id')
    if not address_id:
        raise ValidationError("address_id es requerido")
    
    # Obtener dirección existente
    address = addresses_db.get_item({'address_id': address_id})
    if not address:
        raise NotFoundError("Dirección no encontrada")
    
    # Verificar que pertenece al usuario
    if address.get('user_email') != user_email or address.get('tenant_id') != tenant_id:
        raise UnauthorizedError("No tienes permiso para eliminar esta dirección")
    
    # Eliminar de DynamoDB
    addresses_db.delete_item({'address_id': address_id})
    
    logger.info(f"Address {address_id} deleted successfully")
    
    return success_response({'message': 'Dirección eliminada correctamente'})

