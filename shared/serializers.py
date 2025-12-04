"""
Utilidades de serialización compartidas para evitar código duplicado
"""
from decimal import Decimal
from typing import Any, Dict, List


def serialize_decimal(value: Any) -> Any:
    """Convierte Decimal a float recursivamente"""
    if isinstance(value, Decimal):
        return float(value)
    elif isinstance(value, dict):
        return {k: serialize_decimal(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [serialize_decimal(item) for item in value]
    return value


def serialize_order(order: Dict) -> Dict:
    """Serializa un pedido completo (items, total, etc.)"""
    serialized = {}
    for key, value in order.items():
        if key == 'items' and isinstance(value, list):
            serialized[key] = serialize_items(value)
        elif isinstance(value, Decimal):
            serialized[key] = float(value)
        else:
            serialized[key] = value
    return serialized


def serialize_items(items: List[Dict]) -> List[Dict]:
    """Convierte todos los Decimals en items a float"""
    serialized = []
    for item in items:
        serialized_item = {}
        for key, value in item.items():
            if isinstance(value, Decimal):
                serialized_item[key] = float(value)
            else:
                serialized_item[key] = value
        serialized.append(serialized_item)
    return serialized


def serialize_orders(orders: List[Dict]) -> List[Dict]:
    """Serializa una lista de pedidos"""
    return [serialize_order(order) for order in orders]

