import boto3
import json
import os
from datetime import datetime

events_client = boto3.client('events')

class EventBridgeService:
    @staticmethod
    def put_event(source, detail_type, detail, tenant_id):
        """
        Publica un evento en EventBridge
        
        Estos eventos disparan:
        1. Notificaciones WebSocket
        2. Logs
        3. Otras integraciones
        """
        try:
            # ✅ Usar el Event Bus personalizado
            event_bus_name = os.environ.get(
                'EVENTBRIDGE_BUS',
                f"{os.environ.get('SERVERLESS_SERVICE', 'millas-backend')}-" +
                f"{os.environ.get('SERVERLESS_STAGE', 'dev')}-event-bus"
            )
            
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
                        'EventBusName': event_bus_name
                    }
                ]
            )
            
            if response.get('FailedEntryCount', 0) > 0:
                print(f"Falló publicar evento: {response}")
                return False
            
            print(f"✓ Evento publicado a {event_bus_name}: {source}/{detail_type}")
            return True
        except Exception as e:
            print(f"Error en EventBridge: {str(e)}")
            # ✅ No fallar si EventBridge no está disponible
            return False
