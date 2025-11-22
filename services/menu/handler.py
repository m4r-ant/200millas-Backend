import os
import boto3
from shared.utils import success_response, error_handler
from shared.logger import get_logger

logger = get_logger(__name__)
s3_client = boto3.client('s3')
MENU_IMAGES_BUCKET = os.environ.get('MENU_IMAGES_BUCKET', '')

def _get_image_url(image_name):
    """Genera URL pública de S3 para imagen del menú"""
    if not MENU_IMAGES_BUCKET or not image_name:
        return None
    
    try:
        # URL pública de S3
        region = os.environ.get('AWS_REGION', 'us-east-1')
        url = f"https://{MENU_IMAGES_BUCKET}.s3.{region}.amazonaws.com/{image_name}"
        return url
    except Exception as e:
        logger.warning(f"Error generating image URL: {str(e)}")
        return None

MENU_DATA = {
    'categories': [
        {'id': 'combos', 'name': 'Combos', 'description': 'Ofertas especiales'},
        {'id': 'burgers', 'name': 'Hamburguesas', 'description': 'Hamburguesas variadas'},
        {'id': 'sides', 'name': 'Acompañamientos', 'description': 'Papas, ensaladas y más'},
        {'id': 'drinks', 'name': 'Bebidas', 'description': 'Refrescos y jugos'},
        {'id': 'desserts', 'name': 'Postres', 'description': 'Dulces para terminar'}
    ],
    'items': [
        {
            'item_id': 'combo-1',
            'category': 'combos',
            'name': 'Combo Mega',
            'description': 'Hamburguesa + papas + bebida grande',
            'price': 29.99,
            'image': 'combo-mega.jpg',
            'available': True
        },
        {
            'item_id': 'combo-2',
            'category': 'combos',
            'name': 'Combo Especial',
            'description': 'Hamburguesa doble + papas + bebida',
            'price': 34.99,
            'image': 'combo-especial.jpg',
            'available': True
        },
        {
            'item_id': 'burger-1',
            'category': 'burgers',
            'name': 'Hamburguesa Clásica',
            'description': 'Con carne, lechuga, tomate y cebolla',
            'price': 18.99,
            'image': 'burger-classic.jpg',
            'available': True
        },
        {
            'item_id': 'burger-2',
            'category': 'burgers',
            'name': 'Hamburguesa Doble',
            'description': 'Dos carnes con queso derretido',
            'price': 24.99,
            'image': 'burger-double.jpg',
            'available': True
        },
        {
            'item_id': 'sides-1',
            'category': 'sides',
            'name': 'Papas Grandes',
            'description': 'Papas fritas extra crujientes',
            'price': 7.99,
            'image': 'papas-grandes.jpg',
            'available': True
        },
        {
            'item_id': 'drink-1',
            'category': 'drinks',
            'name': 'Refresco Grande',
            'description': 'Coca Cola, Sprite o Fanta',
            'price': 4.99,
            'image': 'refresco.jpg',
            'available': True
        }
    ]
}

@error_handler
def get_categories(event, context):
    logger.info("Getting menu categories")
    return success_response(MENU_DATA['categories'])

@error_handler
def get_items(event, context):
    logger.info("Getting menu items")
    
    query_params = event.get('queryStringParameters') or {}
    category = query_params.get('category', '').strip()
    search = query_params.get('search', '').strip().lower()
    
    items = MENU_DATA['items']
    
    if category:
        items = [item for item in items if item['category'] == category]
    
    if search:
        items = [
            item for item in items 
            if search in item['name'].lower() or search in item['description'].lower()
        ]
    
    items = [item for item in items if item.get('available', True)]
    
    # Agregar URLs de S3 para las imágenes
    for item in items:
        if item.get('image'):
            item['image_url'] = _get_image_url(item['image'])
    
    logger.info(f"Found {len(items)} menu items")
    
    return success_response(items)
