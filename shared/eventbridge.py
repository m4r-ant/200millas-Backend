import boto3
import json
import os
from datetime import datetime

events_client = boto3.client('events')

class EventBridgeService:
    @staticmethod
    def put_event(source, detail_type, detail, tenant_id):
        try:
            response = events_client.put_events(
                Entries=[
                    {
                        'Source': source,
                        'DetailType': detail_type,
                        'Detail': json.dumps({
                            **detail,
                            'tenant_id': tenant_id,
                            'timestamp': datetime.utcnow().isoformat()
                        }),
                        'EventBusName': os.environ.get('EVENTBRIDGE_BUS', 'default')
                    }
                ]
            )
            
            if response.get('FailedEntryCount', 0) > 0:
                print(f"Falló publicar evento: {response}")
                return False
            
            print(f"Evento publicado: {source}/{detail_type}")
            return True
        except Exception as e:
            print(f"Error en EventBridge: {str(e)}")
            return False
