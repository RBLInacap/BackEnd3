from django.urls import path, include
from django.contrib.auth.decorators import login_not_required
from rest_framework.routers import DefaultRouter
from sgr_app.views import (
    AuthLoginView, Verificar2FAView, MetricasAPIView,
    DelegacionViewSet, CargoViewSet, UsuarioViewSet,
    PeriodoMedicionViewSet, CatalogoServicioViewSet,
    ItemMedicionViewSet, CompromisoAgendaViewSet,
    ActividadViewSet, EvidenciaViewSet,
    CasoSocialViewSet, AtencionSocialViewSet,
    RegistroAuditoriaViewSet
)

router = DefaultRouter()
router.register(r'delegaciones', DelegacionViewSet)
router.register(r'cargos', CargoViewSet)
router.register(r'usuarios', UsuarioViewSet)
router.register(r'periodos', PeriodoMedicionViewSet)
router.register(r'catalogos', CatalogoServicioViewSet)
router.register(r'items-medicion', ItemMedicionViewSet)
router.register(r'compromisos', CompromisoAgendaViewSet)
router.register(r'actividades', ActividadViewSet)
router.register(r'evidencias', EvidenciaViewSet)
router.register(r'casos-sociales', CasoSocialViewSet)
router.register(r'atenciones-sociales', AtencionSocialViewSet)
router.register(r'auditoria', RegistroAuditoriaViewSet)

urlpatterns = [
    path('auth/login/', login_not_required(AuthLoginView.as_view()), name='auth_login'),
    path('auth/verificar-2fa/', login_not_required(Verificar2FAView.as_view()), name='auth_verificar_2fa'),
    path('metricas/', MetricasAPIView.as_view(), name='metricas'),
    path('', include(router.urls)),
]
