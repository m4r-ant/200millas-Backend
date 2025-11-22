import os
from shared.utils import (
    response, success_response, error_response, error_handler, 
    parse_body, current_timestamp
)
from shared.security import create_access_token, verify_token, hash_password
from shared.errors import UnauthorizedError, ValidationError
from shared.logger import get_logger

logger = get_logger(__name__)

USERS_DB = {
    'customer@200millas.com': {
        'password': hash_password('password123'),
        'user_type': 'customer',
        'name': 'Cliente 200 Millas'
    },
    'chef@200millas.com': {
        'password': hash_password('password123'),
        'user_type': 'staff',
        'name': 'Chef Juan'
    },
    'admin@200millas.com': {
        'password': hash_password('admin123'),
        'user_type': 'admin',
        'name': 'Admin'
    }
}

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
    
    user = USERS_DB.get(email)
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
