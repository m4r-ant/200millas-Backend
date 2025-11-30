import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource('dynamodb')

class DynamoDBService:
    def __init__(self, table_name):
        self.table = dynamodb.Table(table_name)
        self.table_name = table_name
    
    def get_item(self, key):
        try:
            response = self.table.get_item(Key=key)
            return response.get('Item')
        except Exception as e:
            print(f"Error en get_item: {str(e)}")
            return None
    
    def put_item(self, item):
        try:
            self.table.put_item(Item=item)
            return True
        except Exception as e:
            print(f"Error en put_item: {str(e)}")
            return False
    
    def update_item(self, key, updates):
        try:
            if not updates:
                return None
            
            # ✅ PALABRAS RESERVADAS en DynamoDB que necesitan escaparse
            reserved_keywords = {
                'status', 'data', 'type', 'name', 'value', 'key', 'range',
                'order', 'index', 'table', 'timestamp', 'size', 'date',
                'time', 'count', 'level', 'state', 'role', 'version'
            }
            
            # Construir UpdateExpression con nombres escapados
            update_parts = []
            expr_names = {}
            expr_values = {}
            
            for k, v in updates.items():
                # Si la clave es una palabra reservada, escaparla
                if k.lower() in reserved_keywords:
                    placeholder = f"#{k}"
                    expr_names[placeholder] = k
                else:
                    placeholder = k
                
                value_placeholder = f":{k}"
                expr_values[value_placeholder] = v
                update_parts.append(f"{placeholder} = {value_placeholder}")
            
            update_expr = "SET " + ", ".join(update_parts)
            
            # Construir parámetros
            params = {
                'Key': key,
                'UpdateExpression': update_expr,
                'ExpressionAttributeValues': expr_values,
                'ReturnValues': "ALL_NEW"
            }
            
            # Solo agregar ExpressionAttributeNames si hay palabras reservadas
            if expr_names:
                params['ExpressionAttributeNames'] = expr_names
            
            response = self.table.update_item(**params)
            return response.get('Attributes')
        except Exception as e:
            print(f"Error en update_item: {str(e)}")
            return None
    
    def query_items(self, partition_key, partition_value, index_name=None):
        try:
            params = {
                'KeyConditionExpression': Key(partition_key).eq(partition_value)
            }

            if index_name:
                params['IndexName'] = index_name

            response = self.table.query(**params)
            return response.get('Items', [])
        except Exception as e:
            print(f"Error en query_items: {str(e)}")
            return []
    
    def scan_items(self, limit=None):
        try:
            params = {}
            if limit:
                params['Limit'] = limit
            
            response = self.table.scan(**params)
            return response.get('Items', [])
        except Exception as e:
            print(f"Error en scan_items: {str(e)}")
            return []
    
    def delete_item(self, key):
        try:
            self.table.delete_item(Key=key)
            return True
        except Exception as e:
            print(f"Error en delete_item: {str(e)}")
            return False
