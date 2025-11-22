import jwt
import os
import hashlib
from datetime import datetime, timedelta
from shared.errors import UnauthorizedError

SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'dev-secret-key-change-in-prod')
ALGORITHM = "HS256"

def create_access_token(user_id, tenant_id, user_type="customer", email=""):
    payload = {
        'user_id': user_id,
        'tenant_id': tenant_id,
        'user_type': user_type,
        'email': email,
        'exp': datetime.utcnow() + timedelta(hours=24),
        'iat': datetime.utcnow()
    }
    
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    return token

def verify_token(token):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise UnauthorizedError("Token expirado")
    except jwt.InvalidTokenError:
        raise UnauthorizedError("Token inválido")

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, hashed):
    return hash_password(password) == hashed
