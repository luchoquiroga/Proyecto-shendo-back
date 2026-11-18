from django.contrib import admin
from .models import Categoria, HorariosRestaurante, Productos, Repartidor, Restaurantes, Usuarios, Pedidos

admin.site.register(Repartidor)

admin.site.register(Productos)


# Las categorías (rubros) se cargan dentro del restaurante: una fila por rubro.
class CategoriaInline(admin.TabularInline):
    model = Categoria
    extra = 1


class HorarioInline(admin.TabularInline):
    model = HorariosRestaurante
    extra = 1


@admin.register(Restaurantes)
class RestaurantesAdmin(admin.ModelAdmin):
    list_display = ('id_restaurante', 'nombre', 'rubros', 'estado')
    search_fields = ('nombre', 'categorias__nombre')
    inlines = [CategoriaInline, HorarioInline]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('categorias')

    @admin.display(description='Categorías')
    def rubros(self, obj):
        return ', '.join(c.nombre for c in obj.categorias.all())


admin.site.register(Pedidos)  # ← agregá esta línea

@admin.register(Usuarios)
class UsuariosAdmin(admin.ModelAdmin):
   list_display = ('id_usuario', 'nombre', 'email', 'rol')
   search_fields = ('nombre', 'email')