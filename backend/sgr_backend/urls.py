"""
URL configuration for sgr_backend project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.urls import path, include, reverse_lazy
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth.decorators import login_not_required, login_required
from django.contrib.auth import views as auth_views
from sgr_app import mantenedores, web_views

urlpatterns = [
    # Web Views (HTML + Bootstrap 5)
    path('', web_views.web_login, name='home'),
    path('login/', web_views.web_login, name='web_login'),
    path('logout/', web_views.web_logout, name='web_logout'),
    path('verificar-2fa/', web_views.web_verificar_2fa, name='web_verificar_2fa'),
    path('mantenedores/delegaciones/', mantenedores.DelegacionListView.as_view(), name='mantenedor_delegaciones_list'),
    path('mantenedores/delegaciones/crear/', mantenedores.DelegacionCreateView.as_view(), name='mantenedor_delegaciones_create'),
    path('mantenedores/delegaciones/<int:pk>/modificar/', mantenedores.DelegacionUpdateView.as_view(), name='mantenedor_delegaciones_update'),
    path('mantenedores/delegaciones/<int:pk>/desactivar/', mantenedores.DelegacionDeleteView.as_view(), name='mantenedor_delegaciones_delete'),
    path('mantenedores/cargos/', mantenedores.CargoListView.as_view(), name='mantenedor_cargos_list'),
    path('mantenedores/cargos/crear/', mantenedores.CargoCreateView.as_view(), name='mantenedor_cargos_create'),
    path('mantenedores/cargos/<int:pk>/modificar/', mantenedores.CargoUpdateView.as_view(), name='mantenedor_cargos_update'),
    path('mantenedores/cargos/<int:pk>/desactivar/', mantenedores.CargoDeleteView.as_view(), name='mantenedor_cargos_delete'),
    path('mantenedores/items/', mantenedores.ItemListView.as_view(), name='mantenedor_items_list'),
    path('mantenedores/items/crear/', mantenedores.ItemCreateView.as_view(), name='mantenedor_items_create'),
    path('mantenedores/items/<int:pk>/modificar/', mantenedores.ItemUpdateView.as_view(), name='mantenedor_items_update'),
    path('mantenedores/items/<int:pk>/desactivar/', mantenedores.ItemDeleteView.as_view(), name='mantenedor_items_delete'),
    path('mantenedores/periodos/', mantenedores.PeriodoListView.as_view(), name='mantenedor_periodos_list'),
    path('mantenedores/periodos/crear/', mantenedores.PeriodoCreateView.as_view(), name='mantenedor_periodos_create'),
    path('mantenedores/periodos/<int:pk>/modificar/', mantenedores.PeriodoUpdateView.as_view(), name='mantenedor_periodos_update'),
    path('mantenedores/periodos/<int:pk>/desactivar/', mantenedores.PeriodoDeleteView.as_view(), name='mantenedor_periodos_delete'),
    path('mantenedores/solicitantes/', mantenedores.SolicitanteListView.as_view(), name='mantenedor_solicitantes_list'),
    path('mantenedores/solicitantes/crear/', mantenedores.SolicitanteCreateView.as_view(), name='mantenedor_solicitantes_create'),
    path('mantenedores/solicitantes/<int:pk>/modificar/', mantenedores.SolicitanteUpdateView.as_view(), name='mantenedor_solicitantes_update'),
    path('mantenedores/solicitantes/<int:pk>/desactivar/', mantenedores.SolicitanteDeleteView.as_view(), name='mantenedor_solicitantes_delete'),
    path('mantenedores/funcionarios/', mantenedores.FuncionarioListView.as_view(), name='mantenedor_funcionarios_list'),
    path('mantenedores/funcionarios/crear/', mantenedores.FuncionarioCreateView.as_view(), name='mantenedor_funcionarios_create'),
    path('mantenedores/funcionarios/<int:pk>/modificar/', mantenedores.FuncionarioUpdateView.as_view(), name='mantenedor_funcionarios_update'),
    path('mantenedores/funcionarios/<int:pk>/desactivar/', mantenedores.FuncionarioDeleteView.as_view(), name='mantenedor_funcionarios_delete'),
    path('mantenedores/roles/', mantenedores.RolListView.as_view(), name='mantenedor_roles_list'),
    path('mantenedores/roles/crear/', mantenedores.RolCreateView.as_view(), name='mantenedor_roles_create'),
    path('mantenedores/roles/<int:pk>/modificar/', mantenedores.RolUpdateView.as_view(), name='mantenedor_roles_update'),
    path('mantenedores/roles/<int:pk>/desactivar/', mantenedores.RolDeleteView.as_view(), name='mantenedor_roles_delete'),
    path('mantenedores/areas/', mantenedores.AreaListView.as_view(), name='mantenedor_areas_list'),
    path('mantenedores/areas/crear/', mantenedores.AreaCreateView.as_view(), name='mantenedor_areas_create'),
    path('mantenedores/areas/<int:pk>/modificar/', mantenedores.AreaUpdateView.as_view(), name='mantenedor_areas_update'),
    path('mantenedores/areas/<int:pk>/desactivar/', mantenedores.AreaDeleteView.as_view(), name='mantenedor_areas_delete'),
    path(
        'password-reset/',
        login_not_required(auth_views.PasswordResetView.as_view(
            template_name='registration/password_reset_form.html',
            email_template_name='registration/password_reset_email.txt',
            subject_template_name='registration/password_reset_subject.txt',
            html_email_template_name='registration/password_reset_email.html',
            success_url=reverse_lazy('password_reset_done'),
        )),
        name='password_reset',
    ),
    path(
        'password-reset/done/',
        login_not_required(auth_views.PasswordResetDoneView.as_view(
            template_name='registration/password_reset_done.html',
        )),
        name='password_reset_done',
    ),
    path(
        'reset/<uidb64>/<token>/',
        login_not_required(auth_views.PasswordResetConfirmView.as_view(
            template_name='registration/password_reset_confirm.html',
            success_url=reverse_lazy('password_reset_complete'),
        )),
        name='password_reset_confirm',
    ),
    path(
        'reset/complete/',
        login_not_required(auth_views.PasswordResetCompleteView.as_view(
            template_name='registration/password_reset_complete.html',
        )),
        name='password_reset_complete',
    ),
    path('dashboard/', web_views.web_dashboard, name='web_dashboard'),
    path('semaforo/', web_views.web_semaforo, name='web_semaforo'),
    path('alertas/', web_views.web_alertas_operativas, name='web_alertas_operativas'),
    path('informes/', web_views.web_reportes, name='web_reportes'),
    path('ajustes/', web_views.web_ajustes_incentivo, name='web_ajustes_incentivo'),
    path(
        'ajustes/<int:ajuste_id>/decidir/',
        web_views.web_decidir_ajuste_incentivo,
        name='web_decidir_ajuste_incentivo',
    ),
    path('personal/', web_views.web_personal, name='web_personal'),
    path('mi-ficha/', web_views.web_mi_ficha, name='web_mi_ficha'),
    path('personal/registrar-actividad/', web_views.web_registrar_actividad_personal, name='web_registrar_actividad_personal'),
    path('actividad-diaria/registrar-solicitante/', web_views.web_registrar_solicitante, name='web_registrar_solicitante'),
    path('actividad-diaria/registrar-actividad/', web_views.web_registrar_actividad_personal, name='web_registrar_actividad_diaria'),
    path('personal/actividades/<int:actividad_id>/evidencias/', web_views.web_agregar_evidencia, name='web_agregar_evidencia'),
    path('verificacion/', web_views.web_verificacion, name='web_verificacion'),
    path('evidencias/', web_views.web_verificacion, name='web_evidencias'),
    path('verificacion/<int:evidencia_id>/validar/', web_views.web_validar_evidencia, name='web_validar_evidencia'),
    path('tubo/', web_views.web_tubo, name='web_tubo'),
    path('tubo/<int:compromiso_id>/cambiar-estado/', web_views.web_cambiar_estado_compromiso, name='web_cambiar_estado_compromiso'),
    path('tubo/<int:compromiso_id>/editar/', web_views.CompromisoUpdateView.as_view(), name='web_editar_compromiso'),
    path('tubo/<int:compromiso_id>/eliminar/', web_views.web_eliminar_compromiso, name='web_eliminar_compromiso'),
    path('tubo/crear/', web_views.CompromisoCreateView.as_view(), name='web_crear_compromiso'),
    path('terreno/', web_views.web_terreno, name='web_terreno'),
    path('casos-sociales/', web_views.web_casos_sociales, name='web_casos_sociales'),
    path('auditoria/', web_views.web_auditoria, name='web_auditoria'),
    path('api/geocodificar/', web_views.api_geocodificar_direccion, name='api_geocodificar'),

    # API REST
    path('api/', include('sgr_app.urls')),
]

if settings.DEBUG:
    static_patterns = static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
    for pattern in static_patterns:
        pattern.callback = login_not_required(pattern.callback)
    urlpatterns += static_patterns
    media_patterns = static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    for pattern in media_patterns:
        pattern.callback = login_required(pattern.callback)
    urlpatterns += media_patterns
