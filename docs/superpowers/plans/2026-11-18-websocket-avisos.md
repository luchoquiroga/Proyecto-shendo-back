# Avisos en tiempo real por WebSocket — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que cliente, repartidor y restaurante reciban un aviso al instante cuando cambia un pedido, y que la app recargue por REST al recibirlo.

**Architecture:** Django Channels con `InMemoryChannelLayer` (un proceso, sin Redis), servido por `daphne` a través de `runserver`. Tres consumers de solo salida (`ws/cliente/`, `ws/repartidor/`, `ws/restaurante/`) autenticados con el token en la URL; un helper `avisar_pedido()` llamado desde las vistas que cambian pedidos. La app abre el socket con OkHttp y, ante cada aviso, llama a las recargas silenciosas que ya existen.

**Tech Stack:** Django 5.2, DRF, simplejwt, channels 4.3.1, daphne 4.2.2; Android Kotlin + Compose, OkHttp 4.12.0.

**Spec:** `docs/superpowers/specs/2026-11-18-websocket-avisos-design.md`

**Repos:** back = `C:\Users\lucia\Project Visual Studio Code\back nuevo lpm` (git, rama `app-mobile-cliente-repartidor`); app = `C:\Users\lucia\AndroidStudioProjects\Mobileshendo` (no es repo git: no hay commits ahí).

## Global Constraints
- Sin Redis ni servicios nuevos: `CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}`.
- Aviso exacto: `{"tipo": "pedido", "id_pedido": <int>}`. Nada más en el mensaje.
- Token en la URL: `?token=<access>`. Inválido, de otro tipo o cuenta no vigente → cerrar con código `4401`.
- El socket es solo de salida: lo que mande el cliente se ignora.
- Si el envío del aviso falla, la request HTTP no falla (se loguea y sigue).
- La API REST no cambia de contrato.
- Polling de respaldo en la app: 60 s (hoy 15 s).
- Tests del back: `./venv/Scripts/python.exe manage.py test apps.clientes apps.core.test_seguridad --settings=config.settings_test` (tiene que seguir verde; hoy 233 OK, 1 expected failure).

## Review Focus
- Token vencido con el socket abierto: la validación es solo al conectar; una conexión abierta sigue recibiendo avisos (aceptable para la demo; al reconectar se revalida). Test: Task 1 `test_token_vencido_se_rechaza_al_conectar`.
- Cliente que intenta escuchar a otro cliente: solo está en su grupo; un pedido ajeno no le llega. Test: Task 2 `test_cliente_no_recibe_avisos_de_pedidos_ajenos`.
- El channel layer falla al mandar (por ejemplo, error interno): la vista igual responde 2xx. Test: Task 2 `test_si_falla_el_aviso_la_request_igual_responde`.
- Servidor reiniciado / socket cortado: la app reconecta sola con espera creciente y, mientras tanto, el polling de 60 s cubre. Verificación manual en Task 4.
- Repartidor que deja de serlo o cuenta de cliente en `ws/repartidor/`: se rechaza con 4401. Test: Task 1 `test_cliente_no_entra_a_ws_repartidor`.

---

### Task 1: Infra de Channels y consumers autenticados (back)

**Files:**
- Modify: `requirements.txt` (agregar `channels==4.3.1`, `daphne==4.2.2`)
- Modify: `config/settings.py` (`'daphne'` primero en `INSTALLED_APPS`, `'channels'` en la lista, `ASGI_APPLICATION = 'config.asgi.application'`, `CHANNEL_LAYERS` de Global Constraints con comentario "un solo proceso; Redis si se escala")
- Modify: `config/asgi.py` (`ProtocolTypeRouter({'http': get_asgi_application(), 'websocket': URLRouter(websocket_urlpatterns)})`; `get_asgi_application()` antes de importar las rutas)
- Create: `apps/clientes/avisos.py` (consumers, grupos y, en la Task 2, `avisar_pedido`)
- Create: `apps/clientes/routing.py` (`websocket_urlpatterns`)
- Test: `apps/clientes/test_avisos.py`

**Interfaces:**
- Produces:
  - `grupo_cliente(id_usuario: int) -> str` → `f'cliente_{id_usuario}'`
  - `grupo_repartidor(id_repartidor: int) -> str` → `f'repartidor_{id_repartidor}'`
  - `GRUPO_REPARTIDORES = 'repartidores'`
  - `grupo_restaurante(id_restaurante: int) -> str` → `f'restaurante_{id_restaurante}'`
  - Consumers (`channels.generic.websocket.AsyncJsonWebsocketConsumer`): `ClienteConsumer`, `RepartidorConsumer`, `RestauranteConsumer`. Cada uno en `connect()` valida el token de `self.scope['query_string']`, se une a sus grupos y acepta; si no, `await self.close(code=4401)`. Handler de grupo `async def pedido(self, event)` que hace `send_json({'tipo': 'pedido', 'id_pedido': event['id_pedido']})`. `receive_json` no hace nada.
  - Rutas: `ws/cliente/`, `ws/repartidor/`, `ws/restaurante/`.
- Consumes (existentes): `apps.clientes.auth.ClienteAccessToken`, `usuario_vigente(token) -> int | None`; el token del panel se valida con `rest_framework_simplejwt.tokens.AccessToken` y su claim `id_restaurante` (igual que `apps/core/views.get_restaurante_from_token`). Las consultas a la base desde código async van envueltas en `channels.db.database_sync_to_async`.
- Grupos al conectar: cliente → `grupo_cliente(id_usuario)`; repartidor → `grupo_repartidor(id_repartidor)` + `GRUPO_REPARTIDORES` (requiere fila en `Repartidor` con ese `id_usuario`); restaurante → `grupo_restaurante(id_restaurante)`.

- [ ] **Step 1: Instalar dependencias** — `./venv/Scripts/pip.exe install channels==4.3.1 daphne==4.2.2` y agregarlas a `requirements.txt`. Esperado: instala sin errores.
- [ ] **Step 2: Escribir los tests de conexión** en `apps/clientes/test_avisos.py`, usando `channels.testing.WebsocketCommunicator(application, path)` con `application` = `config.asgi.application`, en clases `TransactionTestCase` (o `TestCase` con `database_sync_to_async`; lo que funcione con SQLite en memoria). Tests y aserciones:
  - `test_cliente_con_token_valido_conecta`: `connected, _ = await com.connect()` → `connected is True`.
  - `test_sin_token_se_rechaza`, `test_token_basura_se_rechaza`: conectar a `ws/cliente/` → `connected is False` y código de cierre `4401` (`subprotocol` / `close_code` según la API de `WebsocketCommunicator.connect()` en esta versión).
  - `test_token_del_panel_no_entra_a_ws_cliente` y `test_token_de_cliente_no_entra_a_ws_restaurante` → rechazo 4401.
  - `test_cliente_no_entra_a_ws_repartidor` (cuenta sin fila en `Repartidor`) → 4401.
  - `test_token_vencido_se_rechaza_al_conectar`: access de cliente con `set_exp(lifetime=-timedelta(seconds=1))` (como en `test_e2e.py`) → 4401.
  - `test_cuenta_con_contrasena_cambiada_se_rechaza`: emitir tokens, cambiar `contrasena` en la base → 4401.
  - `test_lo_que_manda_el_cliente_se_ignora`: conectar, `send_json_to({'x': 1})`, `assert await com.receive_nothing()`.
  - Crear datos y tokens como en `apps/clientes/test_e2e.py` (registro por HTTP o `apps.clientes.auth.tokens(usuario)`; panel con `POST /api/login/`).
- [ ] **Step 3: Correr y ver que fallan** — `./venv/Scripts/python.exe manage.py test apps.clientes.test_avisos --settings=config.settings_test`. Esperado: error de import / rutas inexistentes.
- [ ] **Step 4: Implementar** settings, `asgi.py`, `routing.py` y los tres consumers en `avisos.py` según Interfaces. `settings_test.py` no necesita cambios (el layer en memoria sirve en tests).
- [ ] **Step 5: Correr los tests nuevos y la suite completa** — ambos verdes (suite: 233 + los nuevos, 1 expected failure).
- [ ] **Step 6: Verificar que runserver sirve ASGI** — `./venv/Scripts/python.exe manage.py runserver 0.0.0.0:8000 --noreload`: la primera línea dice `Starting ASGI/Daphne ...`; `curl http://127.0.0.1:8000/api/cliente/restaurantes/` → 200.
- [ ] **Step 7: Commit** — `git add requirements.txt config/ apps/clientes/avisos.py apps/clientes/routing.py apps/clientes/test_avisos.py && git commit -m "WebSocket: infra de Channels y consumers autenticados"`.

### Task 2: `avisar_pedido` y enganche en las vistas (back) + documentación

**Files:**
- Modify: `apps/clientes/avisos.py` (agregar `avisar_pedido`)
- Modify: `apps/clientes/views.py` (`MisPedidosView.post`, después de crear el pedido)
- Modify: `apps/clientes/repartidor.py` (`TomarView.post`, `SoltarView.post`, `EstadoPedidoView.post`, después del cambio exitoso)
- Modify: `apps/core/views.py` (`PedidosView.put`, después de `pedido.save()`)
- Modify: `API.md` (sección "Avisos en tiempo real (WebSocket)")
- Test: `apps/clientes/test_avisos.py`

**Interfaces:**
- Consumes: grupos y consumers de la Task 1.
- Produces: `avisar_pedido(pedido: Pedidos, *, repartidores: bool = False) -> None` — síncrono (usa `asgiref.sync.async_to_sync(get_channel_layer().group_send)`); manda `{'type': 'pedido', 'id_pedido': pedido.id_pedido}` a `grupo_cliente(pedido.id_usuario_id)` (si hay), `grupo_restaurante(pedido.id_restaurante_id)` (si hay), `grupo_repartidor(pedido.id_repartidor_id)` (si hay) y, si `repartidores=True`, a `GRUPO_REPARTIDORES`. Todo envuelto en `try/except Exception` con `logging.getLogger(__name__).exception(...)`: nunca propaga.
- Llamadas: crear pedido → `repartidores=True`; panel PUT estado → `repartidores=True`; tomar → `repartidores=True` (después del UPDATE, con el pedido recargado para tener `id_repartidor`); soltar → `repartidores=True` y además avisar a `grupo_repartidor` del que lo soltó (recargar el pedido antes del UPDATE o pasar el id viejo: el que soltó tiene que enterarse); en camino / entregado → `repartidores=False`.

- [ ] **Step 1: Escribir los tests de ruteo** (cada uno conecta los communicators que correspondan, hace la acción por HTTP con `APIClient` y verifica con `receive_json_from(timeout=1)` / `receive_nothing()`):
  - `test_pedido_nuevo_avisa_al_restaurante_y_a_los_repartidores`: restaurante y repartidor reciben `{'tipo': 'pedido', 'id_pedido': id}`; el cliente que pidió también.
  - `test_panel_cambia_estado_avisa_al_cliente_y_repartidores`.
  - `test_tomar_avisa_al_cliente_y_a_los_repartidores`.
  - `test_soltar_avisa_al_que_lo_solto_y_a_los_repartidores`.
  - `test_en_camino_y_entregado_avisan_al_cliente_restaurante_y_repartidor_asignado_pero_no_a_los_demas_repartidores`: un segundo repartidor conectado no recibe nada (`receive_nothing()`).
  - `test_cliente_no_recibe_avisos_de_pedidos_ajenos`: cliente B conectado; pedido de A cambia → B `receive_nothing()`.
  - `test_si_falla_el_aviso_la_request_igual_responde`: `patch('apps.clientes.avisos.get_channel_layer', side_effect=RuntimeError)` → `POST /api/cliente/pedidos/` responde 201.
- [ ] **Step 2: Correr y ver que fallan** (no llega ningún aviso).
- [ ] **Step 3: Implementar `avisar_pedido` y las cinco llamadas** según Interfaces.
- [ ] **Step 4: Documentar en `API.md`**: rutas, `?token=`, cierre 4401, formato del aviso, tabla evento → quién recibe (la de Interfaces), "el socket solo avisa: recargá por REST", "un solo proceso (InMemoryChannelLayer); con varios procesos usar Redis".
- [ ] **Step 5: Correr tests nuevos y suite completa** — verdes.
- [ ] **Step 6: Commit** — `git commit -am "WebSocket: avisos de pedido desde las vistas"` (más `git add` de archivos nuevos si los hay).

### Task 3: Cliente WebSocket en la app y recarga al recibir avisos

**Files (repo de la app, sin git):**
- Modify: `gradle/libs.versions.toml` (`okhttp = "4.12.0"` y `okhttp = { group = "com.squareup.okhttp3", name = "okhttp", version.ref = "okhttp" }`)
- Modify: `app/build.gradle.kts` (`implementation(libs.okhttp)`)
- Create: `app/src/main/java/com/admin_gym/mobile_shendo/data/Avisos.kt`
- Modify: `data/ApiClient.kt` (exponer `suspend fun renovarAccess(): String?` público que reusa `renovar()` con el access actual, para que `Avisos` renueve ante 4401)
- Modify: `ui/MainViewModel.kt`, `ui/PedidosScreen.kt`, `ui/RepartidorApp.kt`

**Interfaces:**
- Produces:
  - `object Avisos` en `data/Avisos.kt` con `fun avisos(ruta: String): kotlinx.coroutines.flow.Flow<Long>` — `ruta` es `"cliente/"` o `"repartidor/"`. Es un `callbackFlow` que abre `ws://10.0.2.2:8000/ws/<ruta>?token=<ApiClient.accessActual>` (base derivada de `ApiClient.BASE_URL` cambiando `http`→`ws` y `/api/`→`/ws/`), emite el `id_pedido` de cada mensaje `{"tipo":"pedido",...}`, y ante cierre/falla reintenta con espera 1 s, 2 s, 4 s… hasta 30 s. Si el cierre es `4401`, llama a `ApiClient.renovarAccess()` antes de reintentar; si devuelve null, deja de reintentar (la sesión venció: la próxima llamada REST hará el logout). Al cancelarse el flow, cierra el socket (`close(1000, null)`).
  - `MainViewModel.escucharAvisos(ruta: String, alRecibir: () -> Unit): Job` — lanza en `viewModelScope` la recolección de `Avisos.avisos(ruta)` y llama `alRecibir()` por cada aviso; devuelve el `Job` para cancelarlo.
- Consumes: `vm.cargarMisPedidos(silencioso = true)`, `vm.cargarRepartidor(silencioso = true)` (existentes).

- [ ] **Step 1: Agregar OkHttp** al catálogo y al módulo; `./gradlew :app:compileDebugKotlin -q` compila.
- [ ] **Step 2: Implementar `Avisos.avisos(ruta)` y `ApiClient.renovarAccess()`** según Interfaces. Parseo con `org.json.JSONObject`; ignorar mensajes que no tengan `tipo == "pedido"`.
- [ ] **Step 3: Implementar `escucharAvisos`** en el ViewModel.
- [ ] **Step 4: Conectar las pantallas**:
  - `PedidosScreen.kt` (`MisPedidosScreen`): `DisposableEffect(Unit) { val job = vm.escucharAvisos("cliente/") { vm.cargarMisPedidos(silencioso = true) }; onDispose { job.cancel() } }`; el `delay(15_000)` del polling pasa a `delay(60_000)`.
  - `RepartidorApp.kt`: lo mismo con `"repartidor/"` y `vm.cargarRepartidor(silencioso = true)`; polling a 60 s.
- [ ] **Step 5: Compilar** — `./gradlew :app:assembleDebug -q` sin errores.

### Task 4: Verificación de punta a punta

**Files:** ninguno nuevo (solo verificación; si aparece un bug, se corrige en el archivo de la task que lo originó).

- [ ] **Step 1: Back corriendo con ASGI** — `runserver 0.0.0.0:8000 --noreload` (línea `Starting ASGI/Daphne`).
- [ ] **Step 2: Repartidor en el emulador** — instalar el APK, entrar con la cuenta de repartidor de `datos_prueba.md`, estar en "Disponibles" con disponibilidad activa. En el log de runserver aparece `WSCONNECT /ws/repartidor/`.
- [ ] **Step 3: El cliente pide por la API** (curl con la cuenta de cliente de `datos_prueba.md`) → el pedido aparece en "Disponibles" en menos de 3 s (sin esperar los 60 s del polling). Tomar → en camino → entregado desde la app.
- [ ] **Step 4: Cliente en el emulador** — entrar con la cuenta de cliente en "Mis pedidos"; cambiar el estado por la API (repartidor por curl) → la tarjeta cambia en menos de 3 s.
- [ ] **Step 5: Reconexión** — con la app en "Mis pedidos", reiniciar runserver; en menos de ~30 s aparece de nuevo `WSCONNECT` y un cambio de estado vuelve a reflejarse al instante.
- [ ] **Step 6: Limpieza** — borrar los pedidos de prueba (detalle, pago, pedido) y reponer stock, como en `e2e_vivo`.
- [ ] **Step 7: Suite y e2e** — suite del back verde y `manage.py e2e_vivo --confirmar` OK (corre con daphne).
