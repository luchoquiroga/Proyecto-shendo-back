from collections.abc import Mapping

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response


class CuerpoInvalido(APIException):
    status_code = status.HTTP_400_BAD_REQUEST


def tiene_nulo(valor):
    if isinstance(valor, str):
        return '\x00' in valor
    if isinstance(valor, Mapping):
        valor = valor.values()
    if isinstance(valor, (list, tuple, type({}.values()))):
        return any(tiene_nulo(v) for v in valor)
    return False


class CuerpoObjetoMixin:
    """Para las vistas que leen request.data: el body tiene que ser un objeto JSON (o vacío) y sin '\x00'
    (Postgres lo rechaza en un varchar y daría 500). Un solo lugar para todos los endpoints."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)  # primero autenticación, después el body
        if request.method in ('POST', 'PUT', 'PATCH'):
            if not isinstance(request.data, Mapping):
                raise CuerpoInvalido('El cuerpo tiene que ser un objeto JSON')
            try:
                nulo = tiene_nulo(request.data)
            except RecursionError:  # JSON anidado hasta el límite del parser
                nulo = True
            if nulo:
                raise CuerpoInvalido('El texto no puede tener caracteres nulos')

    def handle_exception(self, exc):
        if isinstance(exc, CuerpoInvalido):
            return Response({'error': str(exc.detail)}, status=exc.status_code)
        return super().handle_exception(exc)
