import os
from shared.utils import success_response, error_handler, get_tenant_id
from shared.dynamodb import DynamoDBService
from shared.errors import ValidationError
from shared.logger import get_logger

logger = get_logger(__name__)
orders_db = DynamoDBService(os.environ.get('ORDERS_TABLE'))
workflow_db = DynamoDBService(os.environ.get('WORKFLOW_TABLE'))

@error_handler
def get_dashboard(event, context):
    logger.info("Getting dashboard metrics")
    
    tenant_id = get_tenant_id(event)
    
    all_orders = orders_db.query_items('tenant_id', tenant_id)
    
    metrics = {
        'total_orders': len(all_orders),
        'pending': len([o for o in all_orders if o.get('status') == 'pending']),
        'confirmed': len([o for o in all_orders if o.get('status') == 'confirmed']),
        'cooking': len([o for o in all_orders if o.get('status') == 'cooking']),
        'packing': len([o for o in all_orders if o.get('status') == 'packing']),
        'in_delivery': len([o for o in all_orders if o.get('status') == 'in_delivery']),
        'delivered': len([o for o in all_orders if o.get('status') == 'delivered']),
        'total_revenue': sum([float(o.get('total', 0)) for o in all_orders])
    }
    
    recent_orders = sorted(
        all_orders, 
        key=lambda x: x.get('created_at', 0), 
        reverse=True
    )[:10]
    
    for order in recent_orders:
        if 'total' in order:
            order['total'] = float(order['total'])
    
    logger.info(f"Dashboard metrics calculated: {metrics['total_orders']} orders")
    
    return success_response({
        'metrics': metrics,
        'recent_orders': recent_orders
    })

@error_handler
def get_order_timeline(event, context):
    logger.info("Getting order timeline")
    
    order_id = event.get('pathParameters', {}).get('order_id')
    
    if not order_id:
        raise ValidationError("order_id es requerido")
    
    workflow = workflow_db.get_item({'order_id': order_id})
    
    if not workflow:
        return success_response({
            'order_id': order_id,
            'steps': [],
            'total_duration': 0
        })
    
    steps = workflow.get('steps', [])
    timeline = []
    
    for i, step in enumerate(steps):
        step_info = {
            'step_number': i + 1,
            'status': step.get('status'),
            'assigned_to': step.get('assigned_to'),
            'started_at': step.get('started_at'),
            'completed_at': step.get('completed_at'),
            'duration_seconds': None
        }
        
        if step.get('completed_at'):
            duration = step['completed_at'] - step['started_at']
            step_info['duration_seconds'] = duration
            step_info['duration_readable'] = _format_duration(duration)
        
        timeline.append(step_info)
    
    total_duration = 0
    if steps and len(steps) > 0:
        first_start = steps[0].get('started_at', 0)
        last_end = steps[-1].get('completed_at') or steps[-1].get('started_at', 0)
        total_duration = last_end - first_start
    
    logger.info(f"Timeline retrieved for {order_id}")
    
    return success_response({
        'order_id': order_id,
        'timeline': timeline,
        'total_duration_seconds': total_duration,
        'total_duration_readable': _format_duration(total_duration)
    })

@error_handler
def get_staff_performance(event, context):
    logger.info("Getting staff performance")
    
    tenant_id = get_tenant_id(event)
    
    all_orders = orders_db.query_items('tenant_id', tenant_id)
    
    staff_stats = {}
    
    for order in all_orders:
        order_id = order.get('order_id')
        workflow = workflow_db.get_item({'order_id': order_id})
        
        if workflow:
            for step in workflow.get('steps', []):
                staff = step.get('assigned_to', 'system')
                
                if staff not in staff_stats:
                    staff_stats[staff] = {
                        'name': staff,
                        'total_tasks': 0,
                        'completed_tasks': 0,
                        'avg_time_seconds': 0,
                        'total_time_seconds': 0
                    }
                
                staff_stats[staff]['total_tasks'] += 1
                
                if step.get('completed_at'):
                    staff_stats[staff]['completed_tasks'] += 1
                    duration = step['completed_at'] - step['started_at']
                    staff_stats[staff]['total_time_seconds'] += duration
    
    for staff in staff_stats.values():
        if staff['completed_tasks'] > 0:
            avg_seconds = int(staff['total_time_seconds'] / staff['completed_tasks'])
            staff['avg_time_seconds'] = avg_seconds
            staff['avg_time_readable'] = _format_duration(avg_seconds)
        
        staff['completion_rate'] = round(
            (staff['completed_tasks'] / staff['total_tasks'] * 100) 
            if staff['total_tasks'] > 0 else 0, 
            2
        )
    
    logger.info(f"Staff performance calculated for {len(staff_stats)} staff members")
    
    return success_response(list(staff_stats.values()))

def _
