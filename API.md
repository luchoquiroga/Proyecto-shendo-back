# API Delivery Gaturnia

Back Django + DRF sobre la base Supabase. Todas las respuestas son JSON; los errores tienen la forma `{"error": "mensaje"}`.

## Correr

```
venv\Scripts\activate
python manage.py runserver 0.0.0.0:8000
```

Tests (SQLite en memoria, no tocan Supabase):

```
python manage.py test apps.clientes apps.core.test_seguridad --settings=config.settings_test
```

### Variables del `.env`

| Variable | Para qué | Por defecto |
|---|---|---|
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | Conexión a Supabase | — |
| `SECRET_KEY` | Firma de los JWT. Usar 32+ caracteres al azar | clave de desarrollo |
| `DEBUG` | `False` en producción | `True` |
| `ALLOWED_HOSTS` | Hosts extra, separados por coma (ej. IP de la PC para un celular físico) | localhost, 10.0.2.2 |
| `CORS_ALLOWED_ORIGINS` | Orígenes del panel React, separados por coma | `http://localhost:5173` |

## Autenticación

JWT en el header `Authorization: Bearer <access>`. Hay dos tipos de token que no se mezclan:

- **Panel del restaurante:** `POST /api/login/`. El access dura 5 minutos y lleva `id_restaurante`.
- **App de clientes:** `POST /api/cliente/login/` o `registro/`. El access dura 1 hora y lleva `tipo=cliente`; la app lo renueva con el refresh (30 días).

Con el `refresh` se pide un access nuevo sin volver a loguearse. Token vencido o ausente → `401`.

Login y registro aceptan 10 intentos por minuto por IP; después responden `429`.

## Panel del restaurante (`/api/`)

Todo requiere token del panel y trabaja solo sobre el restaurante del token.

| Método | Ruta | Body / notas |
|---|---|---|
| POST | `login/` | `{nombre, contrasena}` → `access`, `refresh`, datos del usuario y del restaurante |
| POST | `token/refresh/` | `{refresh}` → `{access}` |
| GET / PATCH | `restaurante/` | Datos del local. PATCH parcial: `nombre`, `direccion`, `telefono`, `estado` (`"cerrado"` lo cierra), `costo_envio`, `pedido_minimo` (números ≥ 0 o `null`) |
| GET / POST | `products/` | Lista / crea producto. POST: `nombre`, `precio` (requeridos), `descripcion`, `stock`, `disponible`, archivo `imagen` (multipart) |
| GET | `orders/` | Pedidos del restaurante |
| PUT | `orders/<id>/` | `{estado}` (texto, requerido). Ej.: `pendiente`, `preparando`, `en camino`, `entregado` |
| GET / POST | `categorias/` | Lista / crea `{nombre}` |
| PUT / DELETE | `categorias/<id>/` | Edita `{nombre}` / borra. Si tiene productos → `409` |

Los horarios del local se cargan desde el admin de Django (`/admin/`).

## App de clientes (`/api/cliente/`)

| Método | Ruta | Auth | Body / notas |
|---|---|---|---|
| POST | `registro/` | — | `{nombre, email, telefono, contrasena, direccion?, repartidor?: true, vehiculo?}` → sesión (`access`, `refresh`, datos, `perfil`). Con `repartidor: true` el vehículo es requerido |
| POST | `login/` | — | `{email, contrasena}` → sesión |
| POST | `token/refresh/` | — | `{refresh}` → `{access}` |
| GET / PATCH | `perfil/` | cliente | PATCH parcial: `nombre`, `telefono`, `direccion`; para cambiar la contraseña `contrasena_actual` + `contrasena_nueva`. El email no se cambia |
| GET | `restaurantes/` | — | Locales con `costo_envio`, `pedido_minimo`, `horario_hoy`, `abierto`, categorías |
| GET | `restaurantes/<id>/productos/` | — | Menú (solo disponibles), `imagen_url` absoluta |
| GET | `pedidos/` | cliente | Mis pedidos, del más nuevo al más viejo |
| POST | `pedidos/` | cliente | `{id_restaurante, items: [{id_producto, cantidad}], tipo_entrega: "domicilio"\|"retiro", direccion_entrega?, metodo_pago}`. Valida stock, horario y pedido mínimo; suma el envío si es a domicilio |

Cada pedido trae `repartidor: {nombre, telefono, vehiculo}` cuando alguien lo tomó (si no, `null`).

## App de repartidores (`/api/repartidor/`)

Es la misma app con otra cara. Mismo registro, login, refresh y perfil que el cliente (`/api/cliente/...`). La sesión trae `perfil: "cliente" | "repartidor"` y, si es repartidor, `repartidor: {id_repartidor, vehiculo, disponible}`. Todo lo de abajo requiere token de una cuenta de repartidor: sin token → `401`, cuenta de cliente → `403`.

Flujo: el restaurante maneja `pendiente → preparando → listo` desde el panel. El repartidor ve los pedidos a domicilio sin repartidor y no terminados, **toma** uno, lo pasa a **en camino** cuando lo retira y a **entregado** cuando lo entrega. Al entregar, el pago queda cobrado.

| Método | Ruta | Body / notas |
|---|---|---|
| GET / PATCH | `estado/` | Mis datos. PATCH parcial: `disponible` (bool), `vehiculo` |
| GET | `disponibles/` | Pedidos para tomar (más viejo primero), con dirección y teléfono del restaurante. Sin datos del cliente |
| GET | `pedidos/` | Mis entregas: activas primero, después el historial. Incluyen `cliente: {nombre, telefono}` |
| POST | `pedidos/<id>/tomar/` | Hay que estar `disponible`. Si otro lo tomó antes → `409` |
| POST | `pedidos/<id>/soltar/` | Lo devuelve a disponibles (solo si todavía no está en camino) |
| POST | `pedidos/<id>/estado/` | `{estado: "en camino" \| "entregado"}`. `entregado` solo desde `en camino` |

En el panel, `GET /api/orders/` muestra `tipo_entrega` y el nombre del `repartidor`.

## Decisiones de seguridad

- **Contraseñas:** hash de Django (PBKDF2). Las cuentas viejas del panel en texto plano se migran a hash en su primer login.
- **Inyección SQL:** todo el acceso a datos es por el ORM de Django (consultas parametrizadas); no hay SQL armado a mano. Los textos se validan (tipo y largo) antes de guardarse.
- **XSS:** la API solo devuelve JSON; el escapado lo hace el front al renderizar.
- **IDOR:** cada endpoint filtra por el restaurante, el cliente o el repartidor del token; un id ajeno da `404`/`409`. El repartidor ve los datos del cliente solo después de tomar el pedido.
- **Carreras:** tomar un pedido y descontar stock son `UPDATE` condicionales: si dos lo hacen a la vez, solo uno gana.
- **Tiempo real:** sin WebSocket. La app consulta `pedidos/` periódicamente (polling) para ver los cambios de estado; evita sumar Django Channels y un servidor ASGI.
