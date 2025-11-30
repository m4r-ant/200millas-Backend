import json
import os
import re
from datetime import datetime
from decimal import Decimal
from shared.errors import CustomError
from shared.logger import get_logger

logger = get_logger(__name__)

class DecimalEncoder(json.JSONEncoder):
    """JSON Encoder que convierte Decimals a float"""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)

def response(status_code, body):
    """Respuesta HTTP estándar con CORS"""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
            'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE,OPTIONS'
        },
        'body': json.dumps(body, cls=DecimalEncoder),
        'body_json': body
    }

def success_response(data, status_code=200):
    """Respuesta exitosa"""
    return response(status_code, {
        'success': True,
        'data': data
    })

def error_response(error, status_code=500):
    """Respuesta de error"""
    return response(status_code, {
        'success': False,
        'error': str(error)
    })

def get_path_param_from_path(event, param_name):
    """
    Extrae parámetro del path - VERSIÓN SIMPLIFICADA Y FUNCIONAL
    """
    try:
        logger.info(f"🔍 Buscando '{param_name}'...")
        
        # ✅ OPCIÓN 1: pathParameters es dict
        path_params = event.get('pathParameters')
        if isinstance(path_params, dict) and param_name in path_params:
            value = str(path_params[param_name]).strip()
            logger.info(f"✓✓✓ ENCONTRADO en pathParameters: {value}")
            return value
        
        # ✅ OPCIÓN 2: path es dict (LA ESTRUCTURA TUYA)
        path = event.get('path')
        if isinstance(path, dict) and param_name in path:
            value = str(path[param_name]).strip()
            logger.info(f"✓✓✓ ENCONTRADO en path dict: {value}")
            return value  # ← RETORNA AQUI INMEDIATAMENTE
        
        # ✅ OPCIÓN 3: path es string con UUID directamente
        if isinstance(path, str):
            # Si el path es solo un UUID
            if re.match(r'^[a-f0-9\-]+$', path):
                logger.info(f"✓✓✓ ENCONTRADO como UUID directo: {path}")
                return path
            
            # Si el path tiene estructura /orders/{uuid}
            patterns = [
                r'/orders/([a-f0-9\-]+)',
                r'/workflow/([a-f0-9\-]+)',
                r'/dashboard/timeline/([a-f0-9\-]+)',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, path)
                if match:
                    value = match.group(1)
                    logger.info(f"✓✓✓ ENCONTRADO en path regex: {value}")
                    return value
        
        # ✅ OPCIÓN 4: Directamente en event
        if param_name in event:
            value = str(event[param_name]).strip()
            logger.info(f"✓✓✓ ENCONTRADO en event: {value}")
            return value
        
        logger.warning(f"❌ NO ENCONTRADO '{param_name}'")
        logger.warning(f"path type: {type(path)}, value: {path}")
        logger.warning(f"pathParameters type: {type(path_params)}, value: {path_params}")
        return None
        
    except Exception as e:
        logger.error(f"❌ ERROR: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return None

def get_tenant_id(event):
    """Extrae tenant_id del contexto del autorizador"""
    try:
        # ✅ Intentar en requestContext primero (API Gateway REST moderno)
        authorizer = event.get('requestContext', {}).get('authorizer', {})
        tenant_id = authorizer.get('tenant_id')
        
        # ✅ Si no está, intentar directamente en el evento (Lambda Proxy Integration antigua)
        if not tenant_id:
            tenant_id = event.get('tenant_id')
        
        if tenant_id:
            return str(tenant_id).strip()
            
        return os.environ.get('TENANT_ID', '200millas')
    except Exception as e:
        logger.error(f"Error getting tenant_id: {str(e)}")
        return os.environ.get('TENANT_ID', '200millas')

def get_user_id(event):
    """Extrae user_id del contexto del autorizador - COMPATIBLE CON AMBAS ESTRUCTURAS"""
    try:
        logger.info(f"get_user_id - Event keys: {list(event.keys())}")
        
        # ✅ PRIMERO: Intentar en requestContext.authorizer (API Gateway REST moderno)
        request_context = event.get('requestContext', {})
        if request_context:
            authorizer = request_context.get('authorizer', {})
            logger.info(f"get_user_id - Authorizer from requestContext: {json.dumps(authorizer)}")
            
            user_id = authorizer.get('user_id')
            if user_id:
                result = str(user_id).strip()
                logger.info(f"✓ user_id encontrado en requestContext.authorizer.user_id: {result}")
                return result
            
            principal = authorizer.get('principalId')
            if principal:
                result = str(principal).strip()
                logger.info(f"✓ user_id encontrado en requestContext.authorizer.principalId: {result}")
                return result
        
        # ✅ SEGUNDO: Intentar directamente en el evento (Lambda Proxy Integration antigua)
        user_id = event.get('user_id')
        if user_id:
            result = str(user_id).strip()
            logger.info(f"✓ user_id encontrado directamente en event.user_id: {result}")
            return result
        
        principal_id = event.get('principalId')
        if principal_id:
            result = str(principal_id).strip()
            logger.info(f"✓ user_id encontrado en event.principalId: {result}")
            return result
        
        # ✅ TERCERO: Intentar en el body (para debug/testing)
        try:
            body = parse_body(event)
            if 'user_id' in body:
                logger.warning(f"⚠ user_id encontrado en body: {body['user_id']}")
                return body['user_id']
        except:
            pass
        
        logger.error("✗ No se encontró user_id en ningún lugar")
        logger.error(f"Event completo (primeras 20 claves): {list(event.keys())[:20]}")
        return None
        
    except Exception as e:
        logger.error(f"Error crítico en get_user_id: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return None

def get_user_email(event):
    """Extrae email del contexto del autorizador"""
    try:
        # ✅ Intentar en requestContext.authorizer primero
        authorizer = event.get('requestContext', {}).get('authorizer', {})
        email = authorizer.get('email')
        
        # ✅ Si no, intentar directamente en el evento
        if not email:
            email = event.get('email')
        
        if email:
            return str(email).strip()
        return None
    except Exception as e:
        logger.error(f"Error getting email: {str(e)}")
        return None

def get_user_type(event):
    """
    Extrae user_type del contexto del autorizador.
    
    Retorna: 'customer', 'staff', 'chef', 'driver', 'admin'
    """
    try:
        logger.info("Extracting user_type from event")
        
        # ✅ Intentar en requestContext.authorizer (API Gateway REST)
        request_context = event.get('requestContext', {})
        if request_context:
            authorizer = request_context.get('authorizer', {})
            user_type = authorizer.get('user_type')
            
            if user_type:
                result = str(user_type).strip().lower()
                logger.info(f"✓ user_type found in authorizer: {result}")
                return result
        
        # ✅ Intentar directamente en el evento
        user_type = event.get('user_type')
        if user_type:
            result = str(user_type).strip().lower()
            logger.info(f"✓ user_type found in event: {result}")
            return result
        
        # ✅ Default a customer si no se especifica
        logger.warning("user_type not found, defaulting to 'customer'")
        return 'customer'
        
    except Exception as e:
        logger.error(f"Error getting user_type: {str(e)}")
        return 'customer'

def parse_body(event):
    """Parsea el body del evento"""
    body = event.get('body')
    if isinstance(body, str):
        try:
            return json.loads(body)
        except:
            return {}
    return body or {}

def current_timestamp():
    """Retorna timestamp actual en segundos"""
    return int(datetime.utcnow().timestamp())

def error_handler(func):
    """Decorador para manejo centralizado de errores"""
    def wrapper(event, context):
        try:
            return func(event, context)
        except CustomError as e:
            print(f"CustomError: {e.message}")
            return error_response(e.message, e.status_code)
        except json.JSONDecodeError as e:
            return error_response("JSON inválido en el body", 400)
        except Exception as e:
            print(f"Error no manejado: {str(e)}")
            import traceback
            traceback.print_exc()
            return error_response("Error interno del servidor", 500)
    return wrapper
