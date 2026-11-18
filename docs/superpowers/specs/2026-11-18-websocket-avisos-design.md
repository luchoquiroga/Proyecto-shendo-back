# Avisos en tiempo real por WebSocket (tarjeta "Preparacion e implementacion de Web Soquet")

## Por qué
Es para la entrega del proyecto (tarjeta del Sprint 4). Hoy la app consulta cada 15 s; los
usuarios consideran tolerable esa demora, así que el objetivo es una versión **acotada y
demostrable**, sin infraestructura extra.

## Objetivos
- Que cliente, repartidor y restaurante se enteren al instante de los cambios de un pedido.
- Mantener la API REST como única fuente de datos.

## No objetivos / anti-objetivos
- Sin Redis ni servicios nuevos (sería un fracaso si la demo necesitara levantar más cosas).
- Sin notificaciones con la app cerrada (push).
- Sin mandar el pedido completo por el socket.
- El panel React no es nuestro: el endpoint queda listo y documentado, conectarlo es del panel.

## Enfoques considerados
1. **Aviso + recarga por REST (elegido).** El socket manda `{"tipo": "pedido", "id_pedido": N}`
   y la app recarga como hoy. Mínimo código, un solo lugar donde se arma cada pedido, y el
   polling sigue de respaldo si el socket se cae.
2. Pedido completo por el socket: duplica la serialización; ganancia mínima.
3. Supabase Realtime: obliga a exponer la base a la app con RLS que hoy no existe.

## Diseño
**Back**
- Dependencias `channels` y `daphne`; `daphne` primero en `INSTALLED_APPS` para que `runserver`
  sirva ASGI (HTTP + WebSocket). `ASGI_APPLICATION = 'config.asgi.application'`.
- `CHANNEL_LAYERS` = `InMemoryChannelLayer` (un solo proceso; comentario: Redis si se escala).
- `config/asgi.py`: `ProtocolTypeRouter` con HTTP (Django) y WebSocket (`URLRouter`).
- Rutas: `ws/cliente/`, `ws/repartidor/`, `ws/restaurante/`. Token en la URL `?token=` (el
  navegador del panel no puede mandar headers en un WebSocket). Validación igual que REST:
  cliente/repartidor con `usuario_vigente` (cuenta vigente, no admin, contraseña sin cambiar;
  repartidor además con fila en `repartidor`); restaurante con el token del panel
  (`id_restaurante`). Token inválido o de otro tipo: se cierra la conexión (código 4401).
- Grupos: `cliente_<id_usuario>`, `repartidor_<id_repartidor>`, `repartidores` (todos),
  `restaurante_<id_restaurante>`.
- Un helper `avisar_pedido(pedido, *, repartidores=False)` que manda el aviso a los grupos del
  pedido (cliente, restaurante, repartidor asignado) y, si `repartidores=True`, también a
  `repartidores` (cambia la lista de disponibles). Se llama después de:
  crear pedido (repartidores=True), PUT de estado del panel (repartidores=True), tomar y
  soltar (repartidores=True), en camino / entregado (repartidores=False).
  Si el envío falla, no rompe la request (el polling cubre).
- El socket es solo de salida: lo que el cliente mande se ignora.

**App Android**
- Dependencia OkHttp (WebSocket). Un `Avisos` en `data/` que abre `ws/cliente/` o
  `ws/repartidor/` con el token actual, reintenta con espera creciente si se corta, y expone los
  avisos al ViewModel.
- Conectado mientras están visibles Mis pedidos (cliente) o la cara de repartidor; al llegar un
  aviso recarga en silencio (`cargarMisPedidos(silencioso)` / `cargarRepartidor(silencioso)`).
- Si el socket responde 4401 (token vencido), renueva el token como hace la API y reconecta.
- El polling pasa de 15 s a 60 s como respaldo.

**Documentación y pruebas**
- `API.md`: sección WebSocket (rutas, token, formato del aviso, qué evento llega a quién).
- Tests con `channels.testing.WebsocketCommunicator`: rechaza sin token / token de otro tipo /
  cuenta de cliente en `ws/repartidor/`; cada actor recibe solo sus avisos en cada evento;
  un cliente no recibe avisos de pedidos ajenos.
- Prueba en el emulador: con dos sesiones (cliente por API, repartidor en la app) el cambio se
  ve sin esperar el polling.

## Queda para quien implemente
Nombres internos, estructura exacta del consumer y del cliente OkHttp, valores del backoff.
