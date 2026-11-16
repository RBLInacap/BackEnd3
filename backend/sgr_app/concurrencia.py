"""Control optimista de concurrencia (RNF-003 / RNF-007).

Evita la sobrescritura silenciosa cuando dos personas editan el mismo registro:
al abrir el formulario se entrega una "huella" (hash) del estado del registro;
al guardar se bloquea la fila, se recalcula la huella y, si otra persona lo
modificó entretanto, se rechaza el cambio en lugar de pisarlo.

En la API REST se usa el estándar HTTP: la respuesta incluye `ETag` y el
cliente puede enviar `If-Match`; si no coincide se responde 412.
"""

import hashlib
import json

from django.db import transaction

CAMPO_FORMULARIO = '_huella'
MENSAJE_CONFLICTO = (
    'Otra persona modificó este registro mientras lo editabas. '
    'Recarga la página para ver los datos actuales y vuelve a aplicar tus cambios.'
)


def huella(instancia):
    """Hash estable del estado persistido de una instancia (campos concretos)."""
    valores = {
        campo.attname: campo.value_from_object(instancia)
        for campo in instancia._meta.concrete_fields
    }
    texto = json.dumps(valores, sort_keys=True, default=str)
    return hashlib.sha256(texto.encode('utf-8')).hexdigest()


def huella_actual_bloqueada(modelo, pk):
    """Bloquea la fila (dentro de una transacción) y devuelve su huella actual."""
    actual = modelo._default_manager.select_for_update().get(pk=pk)
    return huella(actual)


class ConcurrenciaContextMixin:
    """
    Agrega la huella al contexto de un UpdateView y ofrece hay_conflicto().

    Las plantillas deben incluir:
        {% if huella_concurrencia %}<input type="hidden" name="_huella" value="{{ huella_concurrencia }}">{% endif %}
    """

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        objeto = getattr(self, 'object', None)
        if objeto is not None and objeto.pk:
            enviada = self.request.POST.get(CAMPO_FORMULARIO) if self.request.method == 'POST' else None
            # Al re-mostrar un formulario con errores se conserva la huella original.
            context['huella_concurrencia'] = enviada or huella(
                type(objeto)._default_manager.get(pk=objeto.pk)
            )
        return context

    def hay_conflicto(self):
        """Debe llamarse dentro de transaction.atomic(); bloquea la fila."""
        objeto = getattr(self, 'object', None)
        enviada = self.request.POST.get(CAMPO_FORMULARIO)
        if objeto is None or not objeto.pk or not enviada:
            return False
        return huella_actual_bloqueada(type(objeto), objeto.pk) != enviada

    def rechazar_por_conflicto(self, form):
        form.add_error(None, MENSAJE_CONFLICTO)
        return self.form_invalid(form)


class ConcurrenciaFormMixin(ConcurrenciaContextMixin):
    """Mixin completo para UpdateView sin auditoría propia."""

    def form_valid(self, form):
        with transaction.atomic():
            if self.hay_conflicto():
                return self.rechazar_por_conflicto(form)
            return super().form_valid(form)


class ConcurrenciaAPIMixin:
    """Mixin para ModelViewSet: ETag en lecturas e If-Match en escrituras."""

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        response['ETag'] = f'"{huella(self.get_object())}"'
        return response

    def _verificar_if_match(self, request):
        from rest_framework.exceptions import APIException

        if_match = request.headers.get('If-Match')
        if not if_match or if_match.strip() == '*':
            return
        instancia = self.get_object()
        actual = huella_actual_bloqueada(type(instancia), instancia.pk)
        esperadas = {valor.strip().strip('"') for valor in if_match.split(',')}
        if actual not in esperadas:
            error = APIException(MENSAJE_CONFLICTO)
            error.status_code = 412
            raise error

    def update(self, request, *args, **kwargs):
        with transaction.atomic():
            self._verificar_if_match(request)
            response = super().update(request, *args, **kwargs)
            instancia = self.get_queryset().model._default_manager.filter(
                pk=kwargs.get(self.lookup_url_kwarg or self.lookup_field)
            ).first()
        if instancia is not None:
            response['ETag'] = f'"{huella(instancia)}"'
        return response

    def destroy(self, request, *args, **kwargs):
        with transaction.atomic():
            self._verificar_if_match(request)
            return super().destroy(request, *args, **kwargs)
