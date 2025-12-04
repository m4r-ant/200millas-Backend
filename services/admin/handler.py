"""
NUEVO ENDPOINT: GET /admin/chefs - Listar todos los chefs

1. Crea un nuevo archivo: services/admin/handler.py
2. Agrega esta función
3. Configura el endpoint en serverless.yml
"""
import os
from shared.utils import (
    success_response, error_handler, get_tenant_id, get_user_type, get_user_id
)
from shared.dynamodb import DynamoDBService
from shared.errors import UnauthorizedError
from shared.logger import get_logger

logger = get_logger(__name__)
users_db = DynamoDBService(os.environ.get('USERS_TABLE'))


@error_handler
def list_chefs(event, context):
    """
    Lista todos los chefs registrados en el sistema.
    
    RESTRICCIÓN: Solo administradores pueden ver esta lista.
    
    Información incluida:
    - Email del chef
    - Nombre
    - Fecha de registro
    - Tenant al que pertenece
    
    NO se incluye:
    - Password (por seguridad)
    """
    logger.info("Listing all chefs")
    
    # ✅ VALIDACIÓN: Solo admin puede listar chefs
    user_type = get_user_type(event)
    user_id = get_user_id(event)
    
    logger.info(f"List chefs request - user_type: '{user_type}', user_id: '{user_id}'")
    logger.info(f"Event keys: {list(event.keys())}")
    if 'requestContext' in event:
        logger.info(f"RequestContext keys: {list(event['requestContext'].keys())}")
        if 'authorizer' in event['requestContext']:
            logger.info(f"Authorizer keys: {list(event['requestContext']['authorizer'].keys())}")
            if 'context' in event['requestContext']['authorizer']:
                logger.info(f"Authorizer context: {event['requestContext']['authorizer']['context']}")
    
    # Permitir 'admin' o 'staff' como admin (flexibilidad)
    if user_type not in ['admin']:
        logger.warning(f"Unauthorized list chefs attempt by user_type='{user_type}' user_id='{user_id}'")
        raise UnauthorizedError(
            f"Acceso denegado. Solo administradores pueden ver la lista de chefs. (user_type recibido: '{user_type}')"
        )
    
    logger.info(f"List chefs access granted to admin {user_id}")
    
    # ✅ Obtener tenant
    tenant_id = get_tenant_id(event)
    
    # ✅ Obtener TODOS los usuarios (DynamoDB Scan)
    # Nota: En producción con muchos usuarios, usa un GSI por user_type
    all_users = users_db.scan_items()
    
    # ✅ Filtrar solo chefs del mismo tenant
    chefs = [
        {
            'email': user.get('email'),
            'name': user.get('name'),
            'user_type': user.get('user_type'),
            'tenant_id': user.get('tenant_id'),
            'created_at': user.get('created_at')
            # ❌ NO incluir password por seguridad
        }
        for user in all_users
        if user.get('user_type') in ['chef', 'staff'] and 
           user.get('tenant_id') == tenant_id
    ]
    
    # ✅ Ordenar por fecha de creación (más reciente primero)
    chefs.sort(key=lambda x: x.get('created_at', 0), reverse=True)
    
    logger.info(f"Found {len(chefs)} chefs for tenant {tenant_id}")
    
    return success_response({
        'chefs': chefs,
        'count': len(chefs)
    })


@error_handler
def list_drivers(event, context):
    """
    Lista todos los drivers registrados en el sistema.
    
    RESTRICCIÓN: Solo administradores pueden ver esta lista.
    """
    logger.info("Listing all drivers")
    
    # ✅ VALIDACIÓN: Solo admin puede listar drivers
    user_type = get_user_type(event)
    user_id = get_user_id(event)
    
    logger.info(f"List drivers request - user_type: '{user_type}', user_id: '{user_id}'")
    
    if user_type not in ['admin']:
        logger.warning(f"Unauthorized list drivers attempt by user_type='{user_type}' user_id='{user_id}'")
        raise UnauthorizedError(
            f"Acceso denegado. Solo administradores pueden ver la lista de drivers. (user_type recibido: '{user_type}')"
        )
    
    logger.info(f"List drivers access granted to admin {user_id}")
    
    # ✅ Obtener tenant
    tenant_id = get_tenant_id(event)
    
    # ✅ Obtener TODOS los usuarios
    all_users = users_db.scan_items()
    
    # ✅ Filtrar solo drivers del mismo tenant
    drivers = [
        {
            'email': user.get('email'),
            'name': user.get('name'),
            'user_type': user.get('user_type'),
            'tenant_id': user.get('tenant_id'),
            'created_at': user.get('created_at')
        }
        for user in all_users
        if user.get('user_type') == 'driver' and 
           user.get('tenant_id') == tenant_id
    ]
    
    # ✅ Ordenar por fecha de creación
    drivers.sort(key=lambda x: x.get('created_at', 0), reverse=True)
    
    logger.info(f"Found {len(drivers)} drivers for tenant {tenant_id}")
    
    return success_response({
        'drivers': drivers,
        'count': len(drivers)
    })


@error_handler
def list_all_users(event, context):
    """
    Lista TODOS los usuarios del sistema (clientes, chefs, drivers, admins).
    
    RESTRICCIÓN: Solo administradores pueden ver esta lista completa.
    
    Query params opcionales:
    - ?user_type=chef - Filtra por tipo de usuario
    """
    logger.info("Listing all users")
    
    # ✅ VALIDACIÓN: Solo admin puede listar todos los usuarios
    user_type = get_user_type(event)
    user_id = get_user_id(event)
    
    logger.info(f"List all users request - user_type: '{user_type}', user_id: '{user_id}'")
    
    if user_type not in ['admin']:
        logger.warning(f"Unauthorized list users attempt by user_type='{user_type}' user_id='{user_id}'")
        raise UnauthorizedError(
            f"Acceso denegado. Solo administradores pueden ver la lista de usuarios. (user_type recibido: '{user_type}')"
        )
    
    logger.info(f"List users access granted to admin {user_id}")
    
    # ✅ Obtener tenant
    tenant_id = get_tenant_id(event)
    
    # ✅ Obtener TODOS los usuarios
    all_users = users_db.scan_items()
    
    # ✅ Filtrar por tenant
    users = [
        {
            'email': user.get('email'),
            'name': user.get('name'),
            'user_type': user.get('user_type'),
            'tenant_id': user.get('tenant_id'),
            'created_at': user.get('created_at')
        }
        for user in all_users
        if user.get('tenant_id') == tenant_id
    ]
    
    # ✅ FILTRO OPCIONAL: Por tipo de usuario
    query_params = event.get('queryStringParameters') or {}
    user_type_filter = query_params.get('user_type', '').strip().lower()
    
    if user_type_filter:
        users = [u for u in users if u.get('user_type') == user_type_filter]
        logger.info(f"Filtered by user_type '{user_type_filter}': {len(users)} users")
    
    # ✅ Ordenar por fecha de creación
    users.sort(key=lambda x: x.get('created_at', 0), reverse=True)
    
    logger.info(f"Found {len(users)} users for tenant {tenant_id}")
    
    return success_response({
        'users': users,
        'count': len(users),
        'breakdown': {
            'customers': len([u for u in users if u.get('user_type') == 'customer']),
            'chefs': len([u for u in users if u.get('user_type') in ['chef', 'staff']]),
            'drivers': len([u for u in users if u.get('user_type') == 'driver']),
            'admins': len([u for u in users if u.get('user_type') == 'admin'])
        }
    })
