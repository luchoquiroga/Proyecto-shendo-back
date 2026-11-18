from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from .models import Productos, Usuarios, Pedidos, Categoria, Restaurantes
from django.contrib.auth.hashers import check_password, identify_hasher, make_password
from django.core.files.storage import default_storage
from rest_framework.permissions import IsAuthenticated
import math
import uuid

from PIL import Image

from .cuerpo import CuerpoObjetoMixin
from .permissions import IsRestaurantStaffOrReadOnly, IsOrderParticipantOnly

FORMATOS_IMAGEN = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp', 'GIF': '.gif', 'AVIF': '.avif'}


def numero_valido(valor):
    """float finito a partir de un número o un texto numérico; None si no lo es (bool, nan, lista...)."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float, str)):
        return None
    try:
        n = float(valor)
    except (ValueError, OverflowError):
        return None
    return n if math.isfinite(n) else None


#este DEF lo que hace es leer el token JWT del header de la peticion
#y devuelve el id_restaurante que esta guardado dentro del token. 
# Si el token es invalido o no existe, devuelve None.
def get_restaurante_from_token(request):
    
    auth_header = request.headers.get('Authorization', '')
    # lee el token del header Authorization, que tiene el formato "Bearer <token>" para
    # evitar problemas de seguridad. 
    if not auth_header.startswith('Bearer '): #bearer: formato estandar para enviar tokens en el header
        return None                           # para evitar problemas de seguridad.
    
    partes = auth_header.split(' ')
    if len(partes) != 2:  # "Bearer <token> basura" no es válido
        return None
    token_str = partes[1] #extrae el token del header, que viene después de "Bearer "
    try:
        from rest_framework_simplejwt.tokens import AccessToken 
        #importamos AccessToken para decodificar el token y obtener el id_restaurante
        token = AccessToken(token_str)
        return token.get('id_restaurante')
    except (InvalidToken, TokenError):
        return None


def contrasena_valida(usuario, contrasena):
    """Acepta hash de Django y, para las cuentas viejas, texto plano: si coincide, lo reemplaza por el hash."""
    guardada = usuario.contrasena or ''
    try:
        identify_hasher(guardada)
    except ValueError:
        # No es un hash: contraseña vieja en texto plano.
        if guardada and guardada == contrasena:
            usuario.contrasena = make_password(contrasena)
            usuario.save(update_fields=['contrasena'])
            return True
        return False
    return check_password(contrasena, guardada)


class LoginView(CuerpoObjetoMixin, APIView):
    throttle_scope = 'login'

    def post(self, request):
        nombre = request.data.get('nombre')
        contrasena = request.data.get('contrasena')

        if not nombre or not isinstance(contrasena, str) or not contrasena:
            return Response(
                {'error': 'Usuario y contraseña son requeridos'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # No hay UNIQUE en nombre: se prueba cada usuario con ese nombre.
        usuario = next(
            (u for u in Usuarios.objects.filter(nombre=nombre).order_by('id_usuario') if contrasena_valida(u, contrasena)),
            None,
        )
        if usuario is None:
            return Response(
                {'error': 'Credenciales inválidas'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        if not usuario.id_restaurante:
            return Response(
                {'error': 'Este usuario no tiene un restaurante asignado'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Generamos el token y metemos el id_restaurante adentro.
        token = RefreshToken()
        token['id_usuario'] = usuario.id_usuario
        token['nombre'] = usuario.nombre
        token['id_restaurante'] = usuario.id_restaurante.id_restaurante

        return Response({
            'access': str(token.access_token),
            'refresh': str(token),
            'id_usuario': usuario.id_usuario,
            'nombre': usuario.nombre,
            'email': usuario.email,
            'rol': usuario.rol,
            'id_restaurante': usuario.id_restaurante.id_restaurante,
            'nombre_restaurante': usuario.id_restaurante.nombre,
        })


# PRODUCTOS: solo se puede acceder a este panel si el token válido, para que muestre
# los productos del restaurante que corresponde al token. 
# No se puede acceder a productos de otros restaurantes.
class ProductosView(CuerpoObjetoMixin, APIView):

    def get(self, request):
        #se lee el token y se obtiene el id_restaurante para filtrar los productos de ese restaurante
        id_restaurante = get_restaurante_from_token(request)

        if not id_restaurante:
            return Response(
                {'error': 'Token inválido o no enviado'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        productos = Productos.objects.filter(id_restaurante=id_restaurante)

        data = [
            {
                "id_producto": p.id_producto,
                "nombre": p.nombre,
                "descripcion": p.descripcion,
                "precio": float(p.precio) if p.precio else 0,
                "stock": p.stock,
                "imagen_url": request.build_absolute_uri(p.imagen_url) if p.imagen_url else "",
                "disponible": p.disponible,
            }
            for p in productos
        ]
        return Response(data)

    def post(self, request):
        # El restaurante viene del token, no del body — así no puede falsificarlo
        id_restaurante = get_restaurante_from_token(request)

        if not id_restaurante:
            return Response(
                {'error': 'Token inválido o no enviado'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        nombre = request.data.get('nombre')
        precio = request.data.get('precio')

        if not all([nombre, precio]):
            return Response(
                {'error': 'nombre y precio son requeridos'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not isinstance(nombre, str) or not nombre.strip() or len(nombre.strip()) > 100:
            return Response({'error': 'nombre es requerido (hasta 100 caracteres)'}, status=status.HTTP_400_BAD_REQUEST)
        nombre = nombre.strip()

        precio = numero_valido(precio)
        if precio is None or not 0 < precio <= 10_000_000:
            return Response({'error': 'precio tiene que ser un número mayor a 0'}, status=status.HTTP_400_BAD_REQUEST)

        stock = request.data.get('stock', 0)
        if stock is not None:
            stock = numero_valido(stock)
            # 32767 = límite de smallint en la base.
            if stock is None or stock != int(stock) or not 0 <= stock <= 32767:
                return Response({'error': 'stock tiene que ser un entero entre 0 y 32767'}, status=status.HTTP_400_BAD_REQUEST)
            stock = int(stock)

        descripcion = request.data.get('descripcion', '')
        if not isinstance(descripcion, str):
            return Response({'error': 'descripcion tiene que ser texto'}, status=status.HTTP_400_BAD_REQUEST)

        disponible =request.data.get('disponible', True)
        
        if isinstance(disponible, str):
            disponible = disponible.lower() == 'true'

        # Antes estaba dentro del if de arriba: con JSON (disponible booleano) imagen_url no existía y daba error 500.
        imagen_url = ''

        if 'imagen' in request.FILES:
            imagen = request.FILES['imagen']
            # Solo imágenes reales (se sirven desde /media/ en el mismo origen que la API: un .html/.svg sería XSS).
            # El nombre se genera: ni la extensión ni el nombre del usuario sirven.
            try:
                with Image.open(imagen) as img:
                    formato = img.format
                    img.verify()
            except Exception:  # Pillow lanza de todo con archivos rotos
                formato = None
            if formato not in FORMATOS_IMAGEN:
                return Response({'error': 'imagen tiene que ser JPEG, PNG, WEBP, GIF o AVIF'}, status=status.HTTP_400_BAD_REQUEST)
            imagen.seek(0)
            ruta_imagen = default_storage.save(f'productos/{uuid.uuid4().hex}{FORMATOS_IMAGEN[formato]}', imagen)
            imagen_url = f'/media/{ruta_imagen}'

        producto = Productos.objects.create(
            nombre=nombre,
            precio=precio,
            descripcion=descripcion,
            stock=stock,
            imagen_url=imagen_url,
            disponible=disponible,
            id_restaurante_id=id_restaurante,  # Viene del token, no del usuario
        )

        return Response({
            'id_producto': producto.id_producto,
            'nombre': producto.nombre,
            'precio': float(producto.precio),
            'imagen_url': request.build_absolute_uri(producto.imagen_url) if producto.imagen_url else "",

        }, status=status.HTTP_201_CREATED)

# PEDIDOS... Lo mismoq que productos
class PedidosView(CuerpoObjetoMixin, APIView):

    def get(self, request):
        id_restaurante = get_restaurante_from_token(request)

        if not id_restaurante:
            return Response(
                {'error': 'Token inválido o no enviado'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        pedidos = Pedidos.objects.filter(id_restaurante=id_restaurante).select_related('id_repartidor')

        data = [
            {
                "id_pedido": p.id_pedido,
                "fecha": p.fecha,
                "estado": p.estado if p.estado is not None else 0,
                "total": float(p.total) if p.total else 0,
                "direccion_entega": p.direccion_entrega,  # (sic) clave que ya consume el panel
                "id_usuario": p.id_usuario_id,
                "tipo_entrega": p.tipo_entrega,
                "repartidor": p.id_repartidor.nombre if p.id_repartidor else None,
            }
            for p in pedidos
        ]
        return Response(data)

    def put(self, request, pedido_id):
        id_restaurante = get_restaurante_from_token(request)

        if not id_restaurante:
            return Response(
                {'error': 'Token inválido o no enviado'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        try:
            # Buscamos el pedido Y verificamos que pertenezca al restaurante del token
            pedido = Pedidos.objects.get(pk=pedido_id, id_restaurante=id_restaurante)
        except Pedidos.DoesNotExist:
            return Response(
                {'error': 'Pedido no encontrado o no pertenece a tu restaurante'},
                status=status.HTTP_404_NOT_FOUND
            )

        estado = request.data.get('estado')
        # shortcut: solo se valida que sea texto corto; si el panel fija una lista de estados, validar contra esa lista.
        if not isinstance(estado, str) or not estado.strip() or len(estado) > 50:
            return Response(
                {'error': 'estado es requerido (texto de hasta 50 caracteres)'},
                status=status.HTTP_400_BAD_REQUEST
            )

        pedido.estado = estado.strip()
        pedido.save()
        from apps.clientes.avisos import avisar_pedido  # acá: evita import circular core <-> clientes
        avisar_pedido(pedido, repartidores=pedido.tipo_entrega == 'domicilio')
        return Response({'id_pedido': pedido.id_pedido, 'estado': pedido.estado})


def nombre_categoria(request):
    """Nombre limpio de la categoría del body, o None si no es válido."""
    nombre = request.data.get('nombre')
    if not isinstance(nombre, str) or not nombre.strip() or len(nombre) > 100:
        return None
    return nombre.strip()


TOKEN_INVALIDO = {'error': 'Token inválido o no enviado'}
NOMBRE_INVALIDO = {'error': 'nombre es requerido (hasta 100 caracteres)'}


# CATEGORÍAS: igual que productos, todo filtrado por el restaurante del token.
class CategoriasView(CuerpoObjetoMixin, APIView):

    def get(self, request):
        id_restaurante = get_restaurante_from_token(request)
        if not id_restaurante:
            return Response(TOKEN_INVALIDO, status=status.HTTP_401_UNAUTHORIZED)

        categorias = Categoria.objects.filter(id_restaurante=id_restaurante)
        return Response([
            {"id_categoria": c.id_categoria, "nombre": c.nombre}
            for c in categorias
        ])

    def post(self, request):
        id_restaurante = get_restaurante_from_token(request)
        if not id_restaurante:
            return Response(TOKEN_INVALIDO, status=status.HTTP_401_UNAUTHORIZED)

        nombre = nombre_categoria(request)
        if nombre is None:
            return Response(NOMBRE_INVALIDO, status=status.HTTP_400_BAD_REQUEST)

        categoria = Categoria.objects.create(nombre=nombre, id_restaurante_id=id_restaurante)
        return Response(
            {"id_categoria": categoria.id_categoria, "nombre": categoria.nombre},
            status=status.HTTP_201_CREATED
        )


class CategoriaDetailView(CuerpoObjetoMixin, APIView):

    def categoria(self, request, categoria_id):
        """(categoria, None) si es del restaurante del token; si no, (None, respuesta de error)."""
        id_restaurante = get_restaurante_from_token(request)
        if not id_restaurante:
            return None, Response(TOKEN_INVALIDO, status=status.HTTP_401_UNAUTHORIZED)
        categoria = Categoria.objects.filter(pk=categoria_id, id_restaurante=id_restaurante).first()
        if categoria is None:
            return None, Response({"error": "Categoría no encontrada"}, status=status.HTTP_404_NOT_FOUND)
        return categoria, None

    def put(self, request, categoria_id):
        categoria, respuesta = self.categoria(request, categoria_id)
        if respuesta:
            return respuesta

        nombre = nombre_categoria(request)
        if nombre is None:
            return Response(NOMBRE_INVALIDO, status=status.HTTP_400_BAD_REQUEST)

        categoria.nombre = nombre
        categoria.save()
        return Response({"id_categoria": categoria.id_categoria, "nombre": categoria.nombre})

    def delete(self, request, categoria_id):
        categoria, respuesta = self.categoria(request, categoria_id)
        if respuesta:
            return respuesta

        # La FK de productos no tiene cascada: borrar una categoría en uso rompería en la base.
        if Productos.objects.filter(id_categoria=categoria).exists():
            return Response(
                {"error": "La categoría tiene productos; cambiales la categoría antes de borrarla"},
                status=status.HTTP_409_CONFLICT
            )

        categoria.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# PERFIL DEL LOCAL: el restaurante del token ve y edita sus datos (envío, mínimo, abierto/cerrado).
# Los horarios se cargan desde el admin de Django.
CAMPOS_TEXTO_LOCAL = {'nombre': 100, 'direccion': 255, 'telefono': 30, 'estado': 30}
CAMPOS_NUMERO_LOCAL = ('costo_envio', 'pedido_minimo')


def restaurante_json(r):
    return {
        'id_restaurante': r.id_restaurante,
        'nombre': r.nombre,
        'direccion': r.direccion,
        'telefono': r.telefono,
        'costo_envio': float(r.costo_envio) if r.costo_envio is not None else None,
        'pedido_minimo': float(r.pedido_minimo) if r.pedido_minimo is not None else None,
        'estado': r.estado,
    }


class RestauranteView(CuerpoObjetoMixin, APIView):

    def restaurante(self, request):
        id_restaurante = get_restaurante_from_token(request)
        return Restaurantes.objects.filter(pk=id_restaurante).first() if id_restaurante else None

    def get(self, request):
        r = self.restaurante(request)
        if r is None:
            return Response(TOKEN_INVALIDO, status=status.HTTP_401_UNAUTHORIZED)
        return Response(restaurante_json(r))

    def patch(self, request):
        r = self.restaurante(request)
        if r is None:
            return Response(TOKEN_INVALIDO, status=status.HTTP_401_UNAUTHORIZED)

        for campo, largo in CAMPOS_TEXTO_LOCAL.items():
            if campo in request.data:
                valor = request.data[campo]
                if not isinstance(valor, str) or len(valor.strip()) > largo or (campo == 'nombre' and not valor.strip()):
                    return Response({'error': f'{campo} no es válido (texto de hasta {largo} caracteres)'},
                                    status=status.HTTP_400_BAD_REQUEST)
                setattr(r, campo, valor.strip())

        for campo in CAMPOS_NUMERO_LOCAL:
            if campo in request.data:
                valor = request.data[campo]
                if valor is not None:
                    try:
                        valor = float(valor)
                    except (TypeError, ValueError):
                        valor = -1
                    if isinstance(request.data[campo], bool) or not 0 <= valor <= 10_000_000:
                        return Response({'error': f'{campo} tiene que ser un número mayor o igual a 0'},
                                        status=status.HTTP_400_BAD_REQUEST)
                setattr(r, campo, valor)

        r.save()
        return Response(restaurante_json(r))
