from django.apps import apps
from django.contrib.auth.hashers import PBKDF2PasswordHasher
from django.test.runner import DiscoverRunner


class PBKDF2Rapido(PBKDF2PasswordHasher):
    """Mismo formato pbkdf2_sha256 de Django, con pocas iteraciones para que los tests no tarden."""
    iterations = 1000


class Runner(DiscoverRunner):
    """Los modelos de core son managed=False (la base la administra el equipo).
    En la base de test (SQLite en memoria) hay que crear esas tablas."""

    def setup_databases(self, **kwargs):
        for model in apps.get_app_config('core').get_models():
            if not model._meta.db_table.startswith(('auth_', 'django_')):
                model._meta.managed = True
        return super().setup_databases(**kwargs)
