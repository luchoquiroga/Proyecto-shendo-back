# Settings para correr los tests sin tocar la base real (Supabase):
#   python manage.py test apps.clientes apps.core.test_seguridad --settings=config.settings_test
#   (apps.clientes incluye test_repartidor.py)
from .settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

# core no tiene migraciones: así sus tablas se crean directo desde los modelos.
MIGRATION_MODULES = {'core': None, 'clientes': None}

PASSWORD_HASHERS = ['apps.clientes.test_support.PBKDF2Rapido']

TEST_RUNNER = 'apps.clientes.test_support.Runner'

# SQLite no soporta comentarios de tabla (los modelos de core los tienen).
SILENCED_SYSTEM_CHECKS = ['models.W046']

# Sin límite de intentos: los tests hacen muchos logins seguidos. El throttling se prueba aparte con patch.
REST_FRAMEWORK = {**REST_FRAMEWORK, 'DEFAULT_THROTTLE_RATES': {'login': None}}  # noqa: F405
