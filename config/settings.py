from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# En producción: SECRET_KEY propia y DEBUG=False en el .env. Los valores por defecto son solo para desarrollo.
SECRET_KEY = os.getenv('SECRET_KEY', '1234567890abcdef1234567890abcdef')

DEBUG = os.getenv('DEBUG', 'True').lower() == 'true'

# 10.0.2.2 es la PC vista desde el emulador de Android (app mobile de clientes).
# Para un celular fisico: ALLOWED_HOSTS=192.168.x.x en el .env
ALLOWED_HOSTS = ['.localhost', '127.0.0.1', '[::1]', '10.0.2.2'] + [
    h.strip() for h in os.getenv('ALLOWED_HOSTS', '').split(',') if h.strip()
]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'apps.core',
    'apps.clientes',
    'corsheaders',
    'rest_framework',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

REST_FRAMEWORK = {
    # Si se despliega detrás de un proxy, poner acá cuántos hay (si no, X-Forwarded-For evade el throttling).
    'NUM_PROXIES': 0,
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'apps.core.authentication.CustomJWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [],
    # Anti fuerza bruta: las vistas con throttle_scope = 'login' admiten 10 intentos por minuto por IP.
    'DEFAULT_THROTTLE_CLASSES': ['rest_framework.throttling.ScopedRateThrottle'],
    'DEFAULT_THROTTLE_RATES': {'login': '10/min'},
}

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME'),
        'USER': os.getenv('DB_USER'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST'),
        'PORT': os.getenv('DB_PORT'),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
# Solo el panel React puede llamar a la API desde un navegador (la app Android no usa CORS).
# En producción: CORS_ALLOWED_ORIGINS=https://panel.ejemplo.com en el .env (separados por coma).
CORS_ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv('CORS_ALLOWED_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173').split(',') if o.strip()
]
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
