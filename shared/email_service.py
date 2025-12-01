import boto3
import os
import json
from shared.logger import get_logger

logger = get_logger(__name__)

sns_client = boto3.client('sns', region_name='us-east-1')

SNS_TOPIC_ARN = os.environ.get('SNS_TOPIC_ARN', '')


class EmailService:
    
    @staticmethod
    def send_order_created(customer_email, customer_name, order_id, total, items):
        try:
            subject = f"✓ Pedido Confirmado - {order_id}"
            
            items_html = ""
            for item in items:
                quantity = item.get('quantity', 1)
                price = item.get('price', 0)
                name = item.get('name', 'Item')
                items_html += f"""
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #eee;">{name}</td>
                    <td style="padding: 10px; border-bottom: 1px solid #eee; text-align: center;">{quantity}</td>
                    <td style="padding: 10px; border-bottom: 1px solid #eee; text-align: right;">${price:.2f}</td>
                </tr>
                """
            
            html_body = f"""
            <html>
                <head>
                    <meta charset="UTF-8">
                    <style>
                        body {{ font-family: Arial, sans-serif; color: #333; }}
                        .container {{ max-width: 600px; margin: 0 auto; background: #f9f9f9; padding: 20px; border-radius: 8px; }}
                        .header {{ background: #2196F3; color: white; padding: 20px; border-radius: 8px 8px 0 0; text-align: center; }}
                        .content {{ background: white; padding: 20px; }}
                        .order-id {{ background: #f0f0f0; padding: 10px; border-radius: 4px; margin: 15px 0; }}
                        table {{ width: 100%; border-collapse: collapse; margin: 15px 0; }}
                        .total {{ font-size: 18px; font-weight: bold; text-align: right; padding: 15px 0; border-top: 2px solid #2196F3; }}
                        .status {{ background: #e8f5e9; padding: 15px; border-left: 4px solid #4CAF50; margin: 15px 0; border-radius: 4px; }}
                        .footer {{ color: #999; font-size: 12px; margin-top: 20px; padding-top: 20px; border-top: 1px solid #eee; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="header">
                            <h1>¡Tu pedido ha sido confirmado!</h1>
                        </div>
                        <div class="content">
                            <p>Hola {customer_name},</p>
                            
                            <p>Gracias por tu pedido. Aquí están los detalles:</p>
                            
                            <div class="order-id">
                                <strong>Número de Pedido:</strong> {order_id}
                            </div>
                            
                            <table>
                                <thead>
                                    <tr style="background: #f0f0f0;">
                                        <th style="padding: 10px; text-align: left;">Producto</th>
                                        <th style="padding: 10px; text-align: center;">Cantidad</th>
                                        <th style="padding: 10px; text-align: right;">Precio</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {items_html}
                                </tbody>
                            </table>
                            
                            <div class="total">
                                Total: ${total:.2f}
                            </div>
                            
                            <div class="status">
                                <strong>Estado:</strong> Tu pedido está siendo procesado. El chef comenzará a prepararlo pronto.
                            </div>
                            
                            <p>Recibirás actualizaciones por email mientras tu pedido se prepara y se entrega.</p>
                            
                            <div class="footer">
                                <p>200 Millas - Entregas Rápidas</p>
                                <p>Este es un email automático, por favor no respondas.</p>
                            </div>
                        </div>
                    </div>
                </body>
            </html>
            """
            
            return EmailService._send_email_sns(subject, html_body, customer_email)
        
        except Exception as e:
            logger.error(f"Error sending order created email: {str(e)}")
            return False
    
    
    @staticmethod
    def send_order_confirmed(customer_email, customer_name, order_id):
        try:
            subject = f"✓ Tu pedido está siendo preparado"
            
            html_body = f"""
            <html>
                <head>
                    <meta charset="UTF-8">
                    <style>
                        body {{ font-family: Arial, sans-serif; color: #333; }}
                        .container {{ max-width: 600px; margin: 0 auto; background: #f9f9f9; padding: 20px; border-radius: 8px; }}
                        .header {{ background: #4CAF50; color: white; padding: 20px; border-radius: 8px 8px 0 0; text-align: center; }}
                        .content {{ background: white; padding: 20px; }}
                        .status {{ background: #e8f5e9; padding: 15px; border-left: 4px solid #4CAF50; margin: 15px 0; border-radius: 4px; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="header">
                            <h1>👨‍🍳 Tu pedido está siendo preparado</h1>
                        </div>
                        <div class="content">
                            <p>Hola {customer_name},</p>
                            
                            <div class="status">
                                <strong>Pedido #{order_id}</strong><br>
                                El chef ha confirmado tu pedido y comenzó a prepararlo.
                            </div>
                            
                            <p>Tu comida estará lista pronto. Recibirás otro email cuando esté lista para recoger.</p>
                        </div>
                    </div>
                </body>
            </html>
            """
            
            return EmailService._send_email_sns(subject, html_body, customer_email)
        
        except Exception as e:
            logger.error(f"Error sending order confirmed email: {str(e)}")
            return False
    
    
    @staticmethod
    def send_order_cooking(customer_email, customer_name, order_id):
        try:
            subject = f"👨‍🍳 Tu pedido se está cocinando"
            
            html_body = f"""
            <html>
                <head>
                    <meta charset="UTF-8">
                    <style>
                        body {{ font-family: Arial, sans-serif; color: #333; }}
                        .container {{ max-width: 600px; margin: 0 auto; background: #f9f9f9; padding: 20px; border-radius: 8px; }}
                        .header {{ background: #FF9800; color: white; padding: 20px; border-radius: 8px 8px 0 0; text-align: center; }}
                        .content {{ background: white; padding: 20px; }}
                        .status {{ background: #fff3e0; padding: 15px; border-left: 4px solid #FF9800; margin: 15px 0; border-radius: 4px; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="header">
                            <h1>👨‍🍳 ¡Se está cocinando!</h1>
                        </div>
                        <div class="content">
                            <p>Hola {customer_name},</p>
                            
                            <div class="status">
                                <strong>Pedido #{order_id}</strong><br>
                                Tu comida se está preparando en la cocina. ¡Falta poco!
                            </div>
                            
                            <p>Te notificaremos cuando esté lista para recoger.</p>
                        </div>
                    </div>
                </body>
            </html>
            """
            
            return EmailService._send_email_sns(subject, html_body, customer_email)
        
        except Exception as e:
            logger.error(f"Error sending order cooking email: {str(e)}")
            return False
    
    
    @staticmethod
    def send_order_ready(customer_email, customer_name, order_id):
        try:
            subject = f"🎉 ¡Tu pedido está listo!"
            
            html_body = f"""
            <html>
                <head>
                    <meta charset="UTF-8">
                    <style>
                        body {{ font-family: Arial, sans-serif; color: #333; }}
                        .container {{ max-width: 600px; margin: 0 auto; background: #f9f9f9; padding: 20px; border-radius: 8px; }}
                        .header {{ background: #4CAF50; color: white; padding: 20px; border-radius: 8px 8px 0 0; text-align: center; }}
                        .content {{ background: white; padding: 20px; }}
                        .status {{ background: #e8f5e9; padding: 15px; border-left: 4px solid #4CAF50; margin: 15px 0; border-radius: 4px; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="header">
                            <h1>🎉 ¡Tu pedido está listo!</h1>
                        </div>
                        <div class="content">
                            <p>Hola {customer_name},</p>
                            
                            <div class="status">
                                <strong>Pedido #{order_id}</strong><br>
                                ¡Tu comida está lista para ser recogida! Un repartidor está en camino para entregarla.
                            </div>
                            
                            <p>Tu pedido será entregado pronto. Te notificaremos cuando el repartidor esté en camino.</p>
                        </div>
                    </div>
                </body>
            </html>
            """
            
            return EmailService._send_email_sns(subject, html_body, customer_email)
        
        except Exception as e:
            logger.error(f"Error sending order ready email: {str(e)}")
            return False
    
    
    @staticmethod
    def send_order_on_the_way(customer_email, customer_name, order_id, driver_name=None):
        try:
            subject = f"🚗 Tu pedido está en camino"
            
            driver_info = f"Tu repartidor es {driver_name}" if driver_name else "Tu repartidor está en camino"
            
            html_body = f"""
            <html>
                <head>
                    <meta charset="UTF-8">
                    <style>
                        body {{ font-family: Arial, sans-serif; color: #333; }}
                        .container {{ max-width: 600px; margin: 0 auto; background: #f9f9f9; padding: 20px; border-radius: 8px; }}
                        .header {{ background: #2196F3; color: white; padding: 20px; border-radius: 8px 8px 0 0; text-align: center; }}
                        .content {{ background: white; padding: 20px; }}
                        .status {{ background: #e3f2fd; padding: 15px; border-left: 4px solid #2196F3; margin: 15px 0; border-radius: 4px; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="header">
                            <h1>🚗 Tu pedido está en camino</h1>
                        </div>
                        <div class="content">
                            <p>Hola {customer_name},</p>
                            
                            <div class="status">
                                <strong>Pedido #{order_id}</strong><br>
                                {driver_info}. ¡Llegará en los próximos minutos!
                            </div>
                            
                            <p>Mantente atento, tu pedido llegará pronto.</p>
                        </div>
                    </div>
                </body>
            </html>
            """
            
            return EmailService._send_email_sns(subject, html_body, customer_email)
        
        except Exception as e:
            logger.error(f"Error sending order on the way email: {str(e)}")
            return False
    
    
    @staticmethod
    def send_order_delivered(customer_email, customer_name, order_id, delivery_time=None):
        try:
            subject = f"✅ Tu pedido ha sido entregado"
            
            time_info = f"Tiempo de entrega: {delivery_time} minutos" if delivery_time else ""
            
            html_body = f"""
            <html>
                <head>
                    <meta charset="UTF-8">
                    <style>
                        body {{ font-family: Arial, sans-serif; color: #333; }}
                        .container {{ max-width: 600px; margin: 0 auto; background: #f9f9f9; padding: 20px; border-radius: 8px; }}
                        .header {{ background: #4CAF50; color: white; padding: 20px; border-radius: 8px 8px 0 0; text-align: center; }}
                        .content {{ background: white; padding: 20px; }}
                        .status {{ background: #e8f5e9; padding: 15px; border-left: 4px solid #4CAF50; margin: 15px 0; border-radius: 4px; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="header">
                            <h1>✅ ¡Tu pedido ha sido entregado!</h1>
                        </div>
                        <div class="content">
                            <p>Hola {customer_name},</p>
                            
                            <div class="status">
                                <strong>Pedido #{order_id}</strong><br>
                                Tu comida ha sido entregada exitosamente. {time_info}
                            </div>
                            
                            <p>¡Gracias por tu compra! Esperamos que disfrutes tu comida.</p>
                        </div>
                    </div>
                </body>
            </html>
            """
            
            return EmailService._send_email_sns(subject, html_body, customer_email)
        
        except Exception as e:
            logger.error(f"Error sending order delivered email: {str(e)}")
            return False
    
    
    @staticmethod
    def send_order_canceled(customer_email, customer_name, order_id, reason=None):
        try:
            subject = f"❌ Tu pedido ha sido cancelado"
            
            reason_info = f"Razón: {reason}" if reason else ""
            
            html_body = f"""
            <html>
                <head>
                    <meta charset="UTF-8">
                    <style>
                        body {{ font-family: Arial, sans-serif; color: #333; }}
                        .container {{ max-width: 600px; margin: 0 auto; background: #f9f9f9; padding: 20px; border-radius: 8px; }}
                        .header {{ background: #f44336; color: white; padding: 20px; border-radius: 8px 8px 0 0; text-align: center; }}
                        .content {{ background: white; padding: 20px; }}
                        .status {{ background: #ffebee; padding: 15px; border-left: 4px solid #f44336; margin: 15px 0; border-radius: 4px; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="header">
                            <h1>❌ Tu pedido ha sido cancelado</h1>
                        </div>
                        <div class="content">
                            <p>Hola {customer_name},</p>
                            
                            <div class="status">
                                <strong>Pedido #{order_id}</strong><br>
                                Tu pedido ha sido cancelado. {reason_info}
                            </div>
                            
                            <p>Si tienes preguntas, por favor contacta a nuestro equipo de soporte.</p>
                        </div>
                    </div>
                </body>
            </html>
            """
            
            return EmailService._send_email_sns(subject, html_body, customer_email)
        
        except Exception as e:
            logger.error(f"Error sending order canceled email: {str(e)}")
            return False
    
    
    @staticmethod
    def _send_email_sns(subject, html_body, customer_email):
        try:
            message = {
                'default': subject,
                'email': html_body,
                'email-html': html_body
            }
            
            response = sns_client.publish(
                TopicArn=SNS_TOPIC_ARN,
                Subject=subject,
                Message=json.dumps(message),
                MessageStructure='json',
                MessageAttributes={
                    'email': {
                        'DataType': 'String',
                        'StringValue': customer_email
                    }
                }
            )
            
            logger.info(f"Email sent to {customer_email}. MessageId: {response['MessageId']}")
            return True
        
        except Exception as e:
            logger.error(f"Error sending email to {customer_email}: {str(e)}")
            return False
