import json
import os
from datetime import datetime
from shared.errors import CustomError

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
        'body': json.dumps(body),
        # Campo adicional para facilitar la lectura cuando se invoca la Lambda directamente
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

def get_tenant_id(event):
    """Extrae tenant_id del contexto del autorizador"""
    try:
        return event.get('requestContext', {}).get('authorizer', {}).get('tenant_id') or \
               os.environ.get('TENANT_ID', '200millas')
    except:
        return os.environ.get('TENANT_ID', '200millas')

def get_user_id(event):
    """Extrae user_id del contexto del autorizador"""
    try:
        return event.get('requestContext', {}).get('authorizer', {}).get('user_id')
    except:
        return None

def get_user_email(event):
    """Extrae email del contexto del autorizador"""
    try:
        return event.get('requestContext', {}).get('authorizer', {}).get('email')
    except:
        return None

def parse_body(event):
    """Parsea el body del evento"""
    body = event.get('body')
    if isinstance(body, str):
        return json.loads(body)
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
