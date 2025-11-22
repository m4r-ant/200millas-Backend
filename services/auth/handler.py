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

    if user_type not in ['customer', 'staff', 'admin']:
        raise ValidationError("user_type inválido")

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

    logger.info(f"User registered {email}")

    return success_response({'message': 'Usuario creado correctamente'}, 201)

@error_handler
def login(event, context):
    logger.info("Login attempt")
    
    body = parse_body(event)
    email = body.get('email', '').strip()
    password = body.get('password', '')
    
    if not email or not password:
        raise ValidationError("Email y password son requeridos")
    
    if '@' not in email:
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
    token = event.get('authorizationToken', '').replace('Bearer ', '')
    method_arn = event.get('methodArn')
    
    if not token:
        raise UnauthorizedError("Token no proporcionado")
    
    try:
        payload = verify_token(token)
        
        return {
            'principalId': payload['user_id'],
            'policyDocument': {
                'Version': '2012-10-17',
                'Statement': [
                    {
                        'Action': 'execute-api:Invoke',
                        'Effect': 'Allow',
                        'Resource': method_arn
                    }
                ]
            },
            'context': {
                'user_id': payload['user_id'],
                'email': payload['email'],
                'tenant_id': payload['tenant_id'],
                'user_type': payload['user_type']
            }
        }
    except UnauthorizedError as e:
        logger.warning(f"Authorization failed: {str(e)}")
        raise Exception('Unauthorized')

def _verify_password(password, hashed):
    from shared.security import hash_password
    return hash_password(password) == hashed
