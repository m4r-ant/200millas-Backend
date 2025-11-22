import boto3
import os

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
            
            update_expr = "SET " + ", ".join([f"{k}=:{k}" for k in updates.keys()])
            expr_values = {f":{k}": v for k, v in updates.items()}
            
            response = self.table.update_item(
                Key=key,
                UpdateExpression=update_expr,
                ExpressionAttributeValues=expr_values,
                ReturnValues="ALL_NEW"
            )
            return response.get('Attributes')
        except Exception as e:
            print(f"Error en update_item: {str(e)}")
            return None
    
    def query_items(self, partition_key, partition_value):
        try:
            response = self.table.query(
                KeyConditionExpression=f'{partition_key} = :{partition_key}',
                ExpressionAttributeValues={f':{partition_key}': partition_value}
            )
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
