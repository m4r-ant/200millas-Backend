# 200millas-Backend

Sistema de Gestión de Pedidos - Arquitectura Serverless Multi-tenant

## 📋 Descripción

Sistema completo de gestión de pedidos para restaurantes de comida rápida, implementado con arquitectura serverless en AWS. Incluye flujo completo desde la creación del pedido por el cliente hasta la entrega por el repartidor, con asignación automática de chefs mediante SQS y workflow automatizado con Step Functions.

## 🏗️ Arquitectura

- **Multi-tenancy**: Aislamiento de datos por organización
- **Serverless**: Lambda, API Gateway, DynamoDB, S3, Step Functions
- **Basada en Eventos (EDA)**: EventBridge, SQS, WebSocket
- **11 Microservicios**: Auth, Orders, Menu, Chef, Driver, Admin, Dashboard, Workflow, Queue, WebSocket, Email

## 🚀 Servicios AWS Utilizados

- ✅ API Gateway (REST API)
- ✅ EventBridge (Bus de eventos)
- ✅ Step Functions (Workflow automatizado)
- ✅ Lambda (30+ funciones)
- ✅ DynamoDB (7 tablas)
- ✅ S3 (Imágenes del menú)
- ✅ SQS (Asignación de chefs)

## 📚 Endpoints de la API

### 🔐 Autenticación

#### `POST /auth/register`
Registrar nuevo usuario

**Body:**
```json
{
  "email": "usuario@200millas.com",
  "password": "password123",
  "name": "Nombre Usuario",
  "user_type": "customer" | "chef" | "driver" | "admin"
}
```

**Respuesta:**
```json
{
  "success": true,
  "data": {
    "message": "Usuario creado correctamente",
    "user": {
      "email": "usuario@200millas.com",
      "name": "Nombre Usuario",
      "user_type": "customer"
    }
  }
}
```

#### `POST /auth/login`
Iniciar sesión

**Body:**
```json
{
  "email": "usuario@200millas.com",
  "password": "password123"
}
```

**Respuesta:**
```json
{
  "success": true,
  "data": {
    "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "email": "usuario@200millas.com",
    "name": "Nombre Usuario",
    "user_type": "customer",
    "expires_in": 86400
  }
}
```

#### `POST /auth/logout`
Cerrar sesión

**Headers:** `Authorization: Bearer <token>`

---

### 👤 Cliente (Customer)

#### `GET /menu/categories`
Ver categorías del menú

**Sin autenticación**

**Respuesta:**
```json
{
  "success": true,
  "data": {
    "categories": ["Combos", "Bebidas", "Postres"]
  }
}
```

#### `GET /menu/items`
Ver productos del menú

**Query params (opcionales):**
- `category`: Filtrar por categoría

**Sin autenticación**

#### `POST /orders`
Crear nuevo pedido

**Headers:** `Authorization: Bearer <token>`

**Body:**
```json
{
  "items": [
    {
      "item_id": "combo-1",
      "name": "Combo Mega",
      "quantity": 1,
      "price": 29.99
    }
  ],
  "delivery_address": "Av. Principal 123",
  "delivery_instructions": "Tocar timbre"
}
```

**Respuesta:**
```json
{
  "success": true,
  "data": {
    "order_id": "abc-123",
    "status": "pending",
    "total": 29.99,
    "message": "Pedido creado exitosamente. El workflow automático ha comenzado."
  }
}
```

#### `GET /orders`
Ver mis pedidos

**Headers:** `Authorization: Bearer <token>`

**Query params (opcionales):**
- `status`: Filtrar por estado

**Respuesta:**
```json
{
  "success": true,
  "data": {
    "orders": [
      {
        "order_id": "abc-123",
        "status": "cooking",
        "total": 29.99,
        "items": [...],
        "created_at": 1234567890
      }
    ],
    "count": 1
  }
}
```

#### `GET /orders/{order_id}`
Ver detalle de un pedido

**Headers:** `Authorization: Bearer <token>`

**Respuesta:**
```json
{
  "success": true,
  "data": {
    "order_id": "abc-123",
    "status": "cooking",
    "items": [...],
    "total": 29.99,
    "delivery_address": "Av. Principal 123",
    "created_at": 1234567890
  }
}
```

#### `GET /dashboard/timeline/{order_id}`
Ver timeline completo del pedido (estado, tiempos, quién atendió)

**Headers:** `Authorization: Bearer <token>`

**Respuesta:**
```json
{
  "success": true,
  "data": {
    "order_id": "abc-123",
    "timeline": [
      {
        "step_number": 1,
        "status": "confirmed",
        "assigned_to": "system",
        "started_at": 1234567890,
        "completed_at": 1234567900,
        "duration_seconds": 10,
        "duration_readable": "10s"
      },
      {
        "step_number": 2,
        "status": "cooking",
        "assigned_to": "chef@200millas.com",
        "started_at": 1234567900,
        "completed_at": null,
        "duration_seconds": null,
        "duration_readable": null
      }
    ],
    "total_duration_seconds": 600,
    "total_duration_readable": "10m"
  }
}
```

---

### 👨‍🍳 Chef

#### `POST /chef/availability`
Reportar disponibilidad

**Headers:** `Authorization: Bearer <token>`

**Body:**
```json
{
  "status": "available" | "busy" | "offline"
}
```

**Respuesta:**
```json
{
  "success": true,
  "data": {
    "staff_id": "chef@200millas.com",
    "status": "available",
    "message": "Disponibilidad actualizada a available"
  }
}
```

#### `GET /chef/available`
Ver todos los chefs y su estado

**Headers:** `Authorization: Bearer <token>`

**Respuesta:**
```json
{
  "success": true,
  "data": {
    "available": [
      {
        "staff_id": "chef@200millas.com",
        "status": "available",
        "orders_completed": 15
      }
    ],
    "busy": [
      {
        "staff_id": "chef2@200millas.com",
        "status": "busy",
        "current_order_id": "abc-123",
        "current_order": {
          "order_id": "abc-123",
          "status": "cooking",
          "total": 29.99
        }
      }
    ],
    "offline": [],
    "summary": {
      "total": 2,
      "available_count": 1,
      "busy_count": 1,
      "offline_count": 0
    }
  }
}
```

#### `GET /chef/assigned`
Ver mis pedidos asignados (en cooking o packing)

**Headers:** `Authorization: Bearer <token>`

**Respuesta:**
```json
{
  "success": true,
  "data": {
    "orders": [
      {
        "order_id": "abc-123",
        "status": "cooking",
        "items": [...],
        "total": 29.99,
        "assigned_chef": "chef@200millas.com",
        "workflow_status": "cooking"
      }
    ],
    "count": 1,
    "chef_identifier": "chef@200millas.com"
  }
}
```

#### `GET /chef/orders/{order_id}`
Ver detalle completo de un pedido asignado

**Headers:** `Authorization: Bearer <token>`

**Respuesta:**
```json
{
  "success": true,
  "data": {
    "order_id": "abc-123",
    "status": "cooking",
    "items": [...],
    "total": 29.99,
    "workflow": {
      "current_status": "cooking",
      "steps": [...],
      "progress": {
        "total_steps": 3,
        "completed_steps": 1,
        "percentage": 33
      }
    }
  }
}
```

#### `POST /chef/complete-cooking/{order_id}`
Completar cocción (cambia a packing)

**Headers:** `Authorization: Bearer <token>`

**Body (opcional):**
```json
{
  "notes": "Cocción completada"
}
```

**Respuesta:**
```json
{
  "success": true,
  "data": {
    "order_id": "abc-123",
    "status": "packing",
    "chef": "chef@200millas.com",
    "completed_at": 1234567890,
    "message": "Cocción completada. Ahora puedes empaquetar el pedido."
  }
}
```

#### `POST /chef/complete-packing/{order_id}`
Completar empaquetado (cambia a ready)

**Headers:** `Authorization: Bearer <token>`

**Body (opcional):**
```json
{
  "notes": "Empaquetado y listo"
}
```

**Respuesta:**
```json
{
  "success": true,
  "data": {
    "order_id": "abc-123",
    "status": "ready",
    "chef": "chef@200millas.com",
    "packed_at": 1234567890,
    "message": "Pedido empaquetado y listo para recoger por el repartidor"
  }
}
```

#### `GET /orders`
Ver todos los pedidos (con filtros)

**Headers:** `Authorization: Bearer <token>`

**Query params (opcionales):**
- `status`: Filtrar por estado
- `limit`: Número de resultados
- `offset`: Paginación

#### `GET /dashboard`
Ver dashboard con métricas

**Headers:** `Authorization: Bearer <token>`

**Respuesta:**
```json
{
  "success": true,
  "data": {
    "metrics": {
      "total_orders": 50,
      "pending": 2,
      "confirmed": 1,
      "cooking": 3,
      "packing": 1,
      "ready": 2,
      "in_delivery": 5,
      "delivered": 36,
      "total_revenue": 1500.00
    },
    "recent_orders": [...]
  }
}
```

#### `GET /dashboard/timeline/{order_id}`
Ver timeline de un pedido

**Headers:** `Authorization: Bearer <token>`

---

### 🚗 Repartidor (Driver)

#### `POST /driver/availability`
Reportar disponibilidad

**Headers:** `Authorization: Bearer <token>`

**Body:**
```json
{
  "status": "available" | "busy" | "offline"
}
```

#### `GET /driver/available-list`
Ver todos los drivers y su estado

**Headers:** `Authorization: Bearer <token>`

#### `GET /driver/available`
Ver pedidos disponibles para recoger (estado: ready)

**Headers:** `Authorization: Bearer <token>`

**Respuesta:**
```json
{
  "success": true,
  "data": {
    "orders": [
      {
        "order_id": "abc-123",
        "status": "ready",
        "items": [...],
        "total": 29.99,
        "delivery_address": "Av. Principal 123"
      }
    ],
    "count": 1
  }
}
```

#### `GET /driver/assigned`
Ver mis pedidos asignados

**Headers:** `Authorization: Bearer <token>`

**Respuesta:**
```json
{
  "success": true,
  "data": {
    "orders": [
      {
        "order_id": "abc-123",
        "status": "in_delivery",
        "assigned_driver": "driver@200millas.com",
        "workflow_status": "in_delivery"
      }
    ],
    "count": 1
  }
}
```

#### `GET /driver/orders/{order_id}`
Ver detalle completo de un pedido

**Headers:** `Authorization: Bearer <token>`

#### `GET /driver/stats`
Ver mis estadísticas personales

**Headers:** `Authorization: Bearer <token>`

**Respuesta:**
```json
{
  "success": true,
  "data": {
    "driver_identifier": "driver@200millas.com",
    "total_deliveries": 25,
    "in_transit": 1,
    "avg_delivery_time_minutes": 15,
    "rating": 4.8,
    "total_earnings": 312.50
  }
}
```

#### `GET /driver/timeline/{order_id}`
Ver timeline completo de una entrega

**Headers:** `Authorization: Bearer <token>`

#### `POST /driver/pickup/{order_id}`
Recoger pedido (ready → in_delivery)

**Headers:** `Authorization: Bearer <token>`

**Respuesta:**
```json
{
  "success": true,
  "data": {
    "order_id": "abc-123",
    "status": "in_delivery",
    "assigned_driver": "driver@200millas.com",
    "pickup_time": 1234567890,
    "message": "¡Pedido recogido exitosamente! Ya puedes proceder con la entrega."
  }
}
```

#### `POST /driver/complete/{order_id}`
Completar entrega (in_delivery → delivered)

**Headers:** `Authorization: Bearer <token>`

**Body (opcional):**
```json
{
  "notes": "Entregado en puerta principal"
}
```

**Respuesta:**
```json
{
  "success": true,
  "data": {
    "order_id": "abc-123",
    "status": "delivered",
    "driver": "driver@200millas.com",
    "delivered_at": 1234567890,
    "message": "¡Entrega completada exitosamente!"
  }
}
```

#### `POST /driver/cancel/{order_id}`
Cancelar recogida de pedido

**Headers:** `Authorization: Bearer <token>`

---

### 👨‍💼 Administrador (Admin)

#### `GET /admin/chefs`
Listar todos los chefs

**Headers:** `Authorization: Bearer <token>`

**Respuesta:**
```json
{
  "success": true,
  "data": {
    "chefs": [
      {
        "email": "chef@200millas.com",
        "name": "Chef Principal",
        "user_type": "chef",
        "created_at": 1234567890
      }
    ],
    "count": 1
  }
}
```

#### `GET /admin/drivers`
Listar todos los drivers

**Headers:** `Authorization: Bearer <token>`

#### `GET /admin/users`
Listar todos los usuarios

**Headers:** `Authorization: Bearer <token>`

**Respuesta:**
```json
{
  "success": true,
  "data": {
    "users": [
      {
        "email": "usuario@200millas.com",
        "name": "Nombre Usuario",
        "user_type": "customer",
        "created_at": 1234567890
      }
    ],
    "count": 1
  }
}
```

#### `GET /dashboard`
Ver dashboard completo con métricas

**Headers:** `Authorization: Bearer <token>`

#### `GET /dashboard/timeline/{order_id}`
Ver timeline de cualquier pedido

**Headers:** `Authorization: Bearer <token>`

#### `GET /dashboard/staff-performance`
Ver rendimiento del staff

**Headers:** `Authorization: Bearer <token>`

**Respuesta:**
```json
{
  "success": true,
  "data": [
    {
      "name": "chef@200millas.com",
      "total_tasks": 25,
      "completed_tasks": 25,
      "avg_time_seconds": 300,
      "avg_time_readable": "5m",
      "completion_rate": 100.0
    }
  ]
}
```

#### `GET /orders`
Ver todos los pedidos (sin restricciones)

**Headers:** `Authorization: Bearer <token>`

---

### 📊 Dashboard (Staff, Chef, Admin)

#### `GET /dashboard`
Métricas generales del sistema

**Headers:** `Authorization: Bearer <token>`

**Permisos:** staff, chef, admin

#### `GET /dashboard/timeline/{order_id}`
Timeline completo de un pedido con:
- Estado de cada paso
- Tiempo de inicio y fin
- Quién atendió cada paso
- Duración de cada paso
- Duración total

**Headers:** `Authorization: Bearer <token>`

**Permisos:** 
- Customer: solo sus pedidos
- Driver: pedidos disponibles o asignados
- Chef/Staff/Admin: cualquier pedido del tenant

#### `GET /dashboard/staff-performance`
Rendimiento del staff (solo admin)

**Headers:** `Authorization: Bearer <token>`

---

### 🍽️ Menú

#### `GET /menu/categories`
Ver categorías del menú

**Sin autenticación**

#### `GET /menu/items`
Ver productos del menú

**Query params (opcionales):**
- `category`: Filtrar por categoría

**Sin autenticación**

#### `POST /menu/upload-image`
Subir imagen de producto

**Headers:** `Authorization: Bearer <token>`

**Permisos:** staff, chef, admin

---

### 📦 Pedidos (Orders)

#### `POST /orders`
Crear nuevo pedido

**Headers:** `Authorization: Bearer <token>`

**Permisos:** customer

#### `GET /orders`
Listar pedidos

**Headers:** `Authorization: Bearer <token>`

**Permisos:**
- Customer: solo sus pedidos
- Chef/Staff/Admin: todos los pedidos del tenant

**Query params (opcionales):**
- `status`: Filtrar por estado
- `limit`: Número de resultados
- `offset`: Paginación

#### `GET /orders/{order_id}`
Ver detalle de un pedido

**Headers:** `Authorization: Bearer <token>`

**Permisos:**
- Customer: solo sus pedidos
- Driver: pedidos disponibles o asignados
- Chef/Staff/Admin: cualquier pedido del tenant

#### `PATCH /orders/{order_id}/status`
Actualizar estado de un pedido manualmente

**Headers:** `Authorization: Bearer <token>`

**Permisos:** chef, staff, admin

**Body:**
```json
{
  "status": "cooking" | "packing" | "ready" | "in_delivery" | "delivered",
  "notes": "Notas opcionales"
}
```

---

## 🔄 Flujo de Trabajo Automático

El sistema utiliza **Step Functions** para automatizar el workflow:

```
1. Cliente crea pedido (POST /orders)
   ↓
2. Step Functions inicia automáticamente
   ↓
3. ConfirmOrder: pending → confirmed
   ↓
4. AssignCook: Envía a cola SQS
   ↓
5. processChefQueue: Asigna chef disponible automáticamente
   ↓ Estado: cooking
6. WaitForCooking: Espera 5 minutos (o chef completa manualmente)
   ↓
7. CompleteCooking: cooking → packing
   ↓
8. WaitForPacking: Espera 2 minutos (o chef completa manualmente)
   ↓
9. CompletePacking: packing → ready
   ↓
10. WaitForDriverPickup: Espera 2 horas (driver recoge manualmente)
    ↓
11. Driver recoge: POST /driver/pickup/{order_id}
    ↓ Estado: in_delivery
12. Driver entrega: POST /driver/complete/{order_id}
    ↓ Estado: delivered
```

## 📊 Estados de Pedido

- `pending` - Pedido creado, esperando confirmación
- `confirmed` - Pedido confirmado, esperando asignación a chef
- `cooking` - Chef cocinando
- `packing` - Chef empaquetando
- `ready` - Listo para recoger por repartidor
- `in_delivery` - Repartidor en camino
- `delivered` - Entregado al cliente
- `failed` - Pedido fallido

## 🔐 Autenticación

Todos los endpoints (excepto `/menu/*` y `/auth/register`, `/auth/login`) requieren autenticación JWT.

**Header requerido:**
```
Authorization: Bearer <token>
```

El token se obtiene mediante `POST /auth/login` y expira en 24 horas.

## 🚀 Despliegue

```bash
# Instalar dependencias
npm install

# Desplegar a AWS
sls deploy

# Desplegar a un stage específico
sls deploy --stage prod
```

## 📝 Variables de Entorno

Las variables de entorno se configuran automáticamente en `serverless.yml`:

- `ORDERS_TABLE`: Tabla de pedidos
- `WORKFLOW_TABLE`: Tabla de workflow
- `MENU_TABLE`: Tabla de menú
- `USERS_TABLE`: Tabla de usuarios
- `STAFF_AVAILABILITY_TABLE`: Tabla de disponibilidad de staff
- `TENANT_ID`: ID del tenant (200millas)
- `EVENTBRIDGE_BUS`: Bus de eventos personalizado

## 🧪 Testing

Usa Postman o cualquier cliente HTTP para probar los endpoints. Asegúrate de:

1. Registrar usuarios de cada tipo (customer, chef, driver, admin)
2. Hacer login para obtener el token
3. Usar el token en el header `Authorization: Bearer <token>`
4. Probar el flujo completo: crear pedido → chef cocina → chef empaqueta → driver entrega

## 📚 Documentación Adicional

- Ver `serverless.yml` para configuración completa
- Ver logs en CloudWatch para debugging
- Ver Step Functions en AWS Console para monitorear el workflow

## 🏗️ Estructura del Proyecto

```
services/
├── auth/              # Autenticación y autorización
├── orders/            # Gestión de pedidos
├── menu/              # Catálogo de productos
├── chef/              # Operaciones de chefs (cocinar y empaquetar)
├── driver/            # Operaciones de repartidores
├── admin/             # Gestión de usuarios
├── dashboard/         # Métricas y estadísticas
├── workflow/          # Step Functions handlers
├── queue/             # Procesadores de colas SQS
├── websocket/         # Notificaciones en tiempo real
└── email/             # Notificaciones por email

shared/
├── utils.py           # Utilidades compartidas
├── security.py        # JWT y hashing
├── dynamodb.py        # Cliente DynamoDB
├── eventbridge.py     # Cliente EventBridge
└── errors.py          # Clases de error personalizadas
```

## 📞 Soporte

Para problemas o preguntas, revisa los logs en CloudWatch o contacta al equipo de desarrollo.
