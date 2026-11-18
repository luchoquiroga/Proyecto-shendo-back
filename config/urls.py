from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/cliente/', include('apps.clientes.urls')),  # app mobile de clientes
    path('api/repartidor/', include('apps.clientes.urls_repartidor')),  # misma app, cara de repartidor
    path('api/', include('apps.core.urls')),  
    # cambio debido a que en core.urls.py ya no tenemos 
    #el prefijo 'api/' en las rutas

]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)