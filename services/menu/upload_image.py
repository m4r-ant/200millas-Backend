"""
Función Lambda para subir imágenes del menú a S3
"""
import os
import boto3
import json
import uuid
from shared.utils import success_response, error_response, error_handler, parse_body
from shared.logger import get_logger

logger = get_logger(__name__)
s3_client = boto3.client('s3')
MENU_IMAGES_BUCKET = os.environ.get('MENU_IMAGES_BUCKET', '')

@error_handler
def upload_image(event, context):
    """Genera URL presignada para subir imagen del menú"""
    logger.info("Generating presigned URL for image upload")
    
    body = parse_body(event)
    image_name = body.get('image_name')
    content_type = body.get('content_type', 'image/jpeg')
    
    if not image_name:
        return error_response("image_name es requerido", 400)
    
    if not MENU_IMAGES_BUCKET:
        return error_response("S3 bucket no configurado", 500)
    
    try:
        # Generar nombre único
        file_extension = image_name.split('.')[-1] if '.' in image_name else 'jpg'
        unique_name = f"{uuid.uuid4()}.{file_extension}"
        
        # Generar URL presignada para PUT
        presigned_url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': MENU_IMAGES_BUCKET,
                'Key': unique_name,
                'ContentType': content_type
            },
            ExpiresIn=3600  # 1 hora
        )
        
        # URL pública final
        region = os.environ.get('AWS_REGION', 'us-east-1')
        public_url = f"https://{MENU_IMAGES_BUCKET}.s3.{region}.amazonaws.com/{unique_name}"
        
        logger.info(f"Presigned URL generated for {unique_name}")
        
        return success_response({
            'presigned_url': presigned_url,
            'image_url': public_url,
            'image_name': unique_name,
            'expires_in': 3600
        })
    except Exception as e:
        logger.error(f"Error generating presigned URL: {str(e)}")
        return error_response(f"Error al generar URL: {str(e)}", 500)

