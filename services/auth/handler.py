import os
from shared.utils import (
    response, success_response, error_response, error_handler, 
    parse_body, current_timestamp
)
from shared.security import create_access_token, verify_token, hash_password
from shared.errors import UnauthorizedError, ValidationError, ConflictError
from shared.logger import get_logger
from shared.dynamodb import DynamoDBService

logger = get_logger(__name__)
users_db = DynamoDBService(os.environ.get('USERS_TABLE'))


@error_handler
def register(event, context):
    logger.info("Register attempt")

    body = parse_body(event)
    email = body.get('email', '').strip().lower()
    password = body.get('password', '')
    name = body.get('name', '').strip()
    user_type = body.get('user_type', 'customer')

    if not email or not password or not name:
        raise ValidationError("email, password y name son requeridos")

    if '@' not in email:
        raise ValidationError("Email inválido")

    if len(password) < 6:
        raise ValidationError("El password debe tener al menos 6 caracteres")

    # ✅ FIX: Agregar 'driver' y 'chef' a los tipos válidos
    if user_type not in ['customer', 'staff', 'chef', 'driver', 'admin']:
        raise ValidationError("user_type inválido. Válidos: customer, staff, chef, driver, admin")

    existing = users_db.get_item({'email': email})
    if existing:
        raise ConflictError("El email ya está registrado")

    user = {
        'email': email,
        'name': name,
        'user_type': user_type,
        'tenant_id': os.environ.get('TENANT_ID', '200millas'),
        'password': hash_password(password),
        'created_at': current_timestamp()
    }

    users_db.put_item(user)

    logger.info(f"User registered {email} as {user_type}")

    return success_response({
        'message': 'Usuario creado correctamente',
        'user': {
            'email': email,
            'name': name,
            'user_type': user_type
        }
    }, 201)

@error_handler
def login(event, context):
    logger.info("Login attempt")
    
    body = parse_body(event)
    logger.info(f"Parsed body keys: {list(body.keys()) if body else 'None'}")
    
    email = body.get('email', '')
    if email:
        email = email.strip().lower()
    password = body.get('password', '')
    
    logger.info(f"Email received: '{email}' (length: {len(email)})")
    
    if not email or not password:
        logger.warning(f"Missing credentials - email: {bool(email)}, password: {bool(password)}")
        raise ValidationError("Email y password son requeridos")
    
    if '@' not in email:
        logger.warning(f"Invalid email format: '{email}'")
        raise ValidationError("Email inválido")
    
    user = users_db.get_item({'email': email})
    if not user or not _verify_password(password, user['password']):
        logger.warning(f"Login failed for {email}")
        raise UnauthorizedError("Email o password incorrecto")
    
    user_id = email.split('@')[0]
    token = create_access_token(
        user_id=user_id,
        tenant_id=os.environ.get('TENANT_ID', '200millas'),
        user_type=user['user_type'],
        email=email
    )
    
    # ============================================
    # MARCAR CHEF COMO DISPONIBLE AL HACER LOGIN
    # ============================================
    if user['user_type'] in ['staff', 'chef']:
        try:
            from shared.dynamodb import DynamoDBService
            availability_db = DynamoDBService(os.environ.get('STAFF_AVAILABILITY_TABLE', 'dev-StaffAvailability'))
            staff_id = email
            timestamp = current_timestamp()
            
            # Obtener registro actual si existe
            current_record = availability_db.get_item({'staff_id': staff_id})
            
            # Marcar como disponible al hacer login
            availability_data = {
                'staff_id': staff_id,
                'staff_type': 'chef',
                'email': email,
                'user_id': user_id,
                'tenant_id': os.environ.get('TENANT_ID', '200millas'),
                'status': 'available',
                'updated_at': timestamp,
                'expires_at': timestamp + 86400,  # TTL 24 horas
                'orders_completed': current_record.get('orders_completed', 0) if current_record else 0,
                'current_order_id': None  # Limpiar cualquier pedido anterior
            }
            
            availability_db.put_item(availability_data)
            logger.info(f"✅ Chef {email} marked as available on login")
        except Exception as e:
            logger.warning(f"Could not mark chef as available on login: {str(e)}")
            # No fallar el login si esto falla
    
    logger.info(f"Login successful for {email}")
    
    return success_response({
        'token': token,
        'email': email,
        'name': user['name'],
        'user_type': user['user_type'],
        'expires_in': 86400
    }, 200)

@error_handler
def logout(event, context):
    logger.info("Logout")
    return success_response({'message': 'Sesión cerrada correctamente'})

def authorize(event, context):
    """Autorizador Lambda para API Gateway - IMPORTANTE: context debe contener solo strings"""
    logger.info(f"Authorizer invoked. Event keys: {list(event.keys())}")
    
    # El token puede venir con o sin "Bearer " prefix
    token = event.get('authorizationToken', '')
    if token.startswith('Bearer '):
        token = token.replace('Bearer ', '')
    token = token.strip()
    
    method_arn = event.get('methodArn', '')
    
    logger.info(f"Token received: {token[:20]}... (length: {len(token)})")
    logger.info(f"Method ARN: {method_arn}")
    
    if not token:
        logger.warning("No authorization token provided")
        raise Exception('Unauthorized')
    
    try:
        payload = verify_token(token)
        logger.info(f"Token verified successfully. User ID: {payload.get('user_id')}, Email: {payload.get('email')}")
        
        # Construir ARN base para permitir acceso a todos los métodos del API
        # method_arn formato: arn:aws:execute-api:region:account-id:api-id/stage/method/resource-path
        arn_parts = method_arn.split('/')
        if len(arn_parts) >= 2:
            # Permitir acceso a todos los métodos del API en este stage
            api_arn = f"{arn_parts[0]}/{arn_parts[1]}/*"
        else:
            api_arn = method_arn
        
        logger.info(f"Allowing access to: {api_arn}")
        
        # ✅ CORRECCIÓN CRÍTICA: API Gateway requiere que todos los valores en context sean STRINGS
        response = {
            'principalId': str(payload['user_id']),
            'policyDocument': {
                'Version': '2012-10-17',
                'Statement': [
                    {
                        'Action': 'execute-api:Invoke',
                        'Effect': 'Allow',
                        'Resource': api_arn
                    }
                ]
            },
            'context': {
                # ✅ IMPORTANTE: Todo debe ser STRING (no dict, no int, no objetos complejos)
                'user_id': str(payload['user_id']),
                'email': str(payload['email']),
                'tenant_id': str(payload['tenant_id']),
                'user_type': str(payload['user_type'])
            }
        }
        
        logger.info(f"Authorization successful. Context: {response['context']}")
        return response
        
    except UnauthorizedError as e:
        logger.warning(f"Authorization failed: {str(e)}")
        raise Exception('Unauthorized')
    except Exception as e:
        logger.error(f"Unexpected error in authorization: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        raise Exception('Unauthorized')

def _verify_password(password, hashed):
    from shared.security import hash_password
    return hash_password(password) == hashed
