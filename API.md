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

Incluye `test_e2e.py` (ciclo completo cliente → panel → repartidor → entregado, solo por HTTP) y `test_adversarial.py` (ataques: tokens cruzados o alterados, IDOR, mass assignment, entradas hostiles, flujo fuera de orden, fuerza bruta).

Prueba de punta a punta **contra el servidor real** (con `runserver` corriendo). Escribe en la base del `.env`: crea un cliente, un repartidor y un pedido de prueba, recorre el ciclo y al final borra todo y repone el stock. Necesita un restaurante abierto con un producto con stock:

```
python manage.py e2e_vivo --confirmar
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
| POST | `registro/` | — | `{nombre, email, telefono, contrasena, direccion?, repartidor?: true, vehiculo?, patente?}` → sesión (`access`, `refresh`, datos, `perfil`). Con `repartidor: true` el vehículo es requerido; `patente` opcional (hasta 15) |
| POST | `login/` | — | `{email, contrasena}` → sesión |
| POST | `token/refresh/` | — | `{refresh}` → `{access}` |
| GET / PATCH | `perfil/` | cliente | PATCH parcial: `nombre`, `telefono`, `direccion`; para cambiar la contraseña `contrasena_actual` + `contrasena_nueva`. El email no se cambia. Si cambia la contraseña, la respuesta trae además `access` y `refresh` nuevos (los anteriores dejan de valer: guardarlos) |
| GET | `restaurantes/` | — | Locales con `costo_envio`, `pedido_minimo`, `horario_hoy`, `abierto`, categorías |
| GET | `restaurantes/<id>/productos/` | — | Menú (solo disponibles), `imagen_url` absoluta |
| GET | `pedidos/` | cliente | Mis pedidos, del más nuevo al más viejo |
| POST | `pedidos/` | cliente | `{id_restaurante, items: [{id_producto, cantidad}], tipo_entrega: "domicilio"\|"retiro", direccion_entrega?, metodo_pago}`. Valida stock, horario y pedido mínimo; suma el envío si es a domicilio |

Cada pedido trae `repartidor: {nombre, telefono, vehiculo}` cuando alguien lo tomó (si no, `null`). También `hora_salida` y `hora_entrega` (ISO, o `null`): las pone el back cuando el repartidor marca en camino y entregado.

## App de repartidores (`/api/repartidor/`)

Es la misma app con otra cara. Mismo registro, login, refresh y perfil que el cliente (`/api/cliente/...`). La sesión trae `perfil: "cliente" | "repartidor"` y, si es repartidor, `repartidor: {id_repartidor, vehiculo, patente, estado, disponible}`. `estado` es `disponible` / `ocupado` (tiene pedidos en curso, puede tomar más) / `desconectado`; `disponible` = no está desconectado. Todo lo de abajo requiere token de una cuenta de repartidor: sin token → `401`, cuenta de cliente → `403`.

Flujo: el restaurante maneja `pendiente → preparando → listo` desde el panel. El repartidor ve los pedidos a domicilio sin repartidor y no terminados, **toma** uno, lo pasa a **en camino** cuando lo retira y a **entregado** cuando lo entrega. Al entregar, el pago queda cobrado.

| Método | Ruta | Body / notas |
|---|---|---|
| GET / PATCH | `estado/` | Mis datos. PATCH parcial: `disponible` (bool: prende/apaga; `ocupado` lo pone el back), `vehiculo`, `patente` |
| GET | `disponibles/` | Pedidos para tomar (más viejo primero), con dirección y teléfono del restaurante. Sin datos del cliente: de la entrega solo la calle, sin altura (la dirección completa, al tomarlo) |
| GET | `pedidos/` | Mis entregas: activas primero, después el historial. Incluyen `cliente: {nombre, telefono}` |
| POST | `pedidos/<id>/tomar/` | Hay que no estar `desconectado`. Si otro lo tomó antes → `409` |
| POST | `pedidos/<id>/soltar/` | Lo devuelve a disponibles (solo si todavía no está en camino) |
| POST | `pedidos/<id>/estado/` | `{estado: "en camino" \| "entregado"}`. `entregado` solo desde `en camino` |

En el panel, `GET /api/orders/` muestra `tipo_entrega` y el nombre del `repartidor`.

## Decisiones de seguridad

- **Contraseñas:** hash de Django (PBKDF2). Las cuentas viejas del panel en texto plano se migran a hash en su primer login.
- **Inyección SQL:** todo el acceso a datos es por el ORM de Django (consultas parametrizadas); no hay SQL armado a mano. Los textos se validan (tipo y largo) antes de guardarse.
- **XSS:** la API solo devuelve JSON; el escapado lo hace el front al renderizar.
- **IDOR:** cada endpoint filtra por el restaurante, el cliente o el repartidor del token; un id ajeno da `404`/`409`. El repartidor ve los datos del cliente solo después de tomar el pedido.
- **Carreras:** tomar un pedido y descontar stock son `UPDATE` condicionales: si dos lo hacen a la vez, solo uno gana.
- **Tiempo real:** WebSocket que solo avisa (ver abajo); los datos siempre se leen por REST. La app además consulta cada 60 s como respaldo.

## Avisos en tiempo real (WebSocket)

`runserver` sirve HTTP y WebSocket (daphne). Conexiones de solo salida, con el access token en la URL:

| Ruta | Token | Quién |
|---|---|---|
| `ws://<host>:8000/ws/cliente/?token=<access>` | de la app | cliente |
| `ws://<host>:8000/ws/repartidor/?token=<access>` | de la app, cuenta de repartidor | repartidor |
| `ws://<host>:8000/ws/restaurante/?token=<access>` | del panel | restaurante |

Token inválido, vencido, de otro tipo o cuenta no vigente: la conexión se cierra con código `4401` (renovar el token y reconectar). El token se valida al conectar.

Cada aviso es `{"tipo": "pedido", "id_pedido": N}`: **recargá por REST** (`cliente/pedidos/`, `repartidor/disponibles/` y `pedidos/`, o `orders/` en el panel).

| Evento | Reciben aviso |
|---|---|
| Pedido nuevo | cliente que pidió, restaurante y (si es a domicilio) todos los repartidores |
| El panel cambia el estado | cliente, restaurante, repartidor asignado y (si es a domicilio) todos los repartidores |
| Un repartidor toma o suelta | cliente, restaurante, el repartidor y todos los repartidores |
| En camino / entregado | cliente, restaurante y el repartidor asignado |

Los avisos viajan en memoria (`InMemoryChannelLayer`): funciona con un solo proceso de servidor. Para varios procesos, cambiar a Redis (`channels_redis`).
