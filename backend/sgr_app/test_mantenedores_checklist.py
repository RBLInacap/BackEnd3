from datetime import date, timedelta

from django.core import mail
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from sgr_app.forms import DelegacionForm, MantenedorPeriodoForm
from sgr_app.models import (
    Area,
    Cargo,
    CatalogoServicio,
    Delegacion,
    ItemMedicion,
    PeriodoMedicion,
    RegistroAuditoria,
    Rol,
    Solicitante,
    Usuario,
)


class MantenedoresChecklistTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin.checklist',
            email='admin.checklist@example.com',
            password='TestPassword123!',
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        self.client.force_login(self.admin)
        self.delegacion = Delegacion.objects.create(
            nombre='Delegación para checklist',
            codigo='CHK',
        )
        self.area = Area.objects.create(nombre='Área para checklist')
        self.cargo = Cargo.objects.create(nombre='Cargo para checklist')
        self.periodo = PeriodoMedicion.objects.create(
            nombre='Período base checklist',
            fecha_inicio=date(2026, 10, 1),
            fecha_termino=date(2026, 12, 31),
        )

    def test_delegation_phone_only_accepts_digits(self):
        form_data = {
            'nombre': 'Delegación prueba',
            'codigo': 'NUM',
            'direccion': '',
            'email': '',
            'ambito': 'Urbano',
            'telefono': '512206518',
        }
        valid_form = DelegacionForm(data=form_data)
        self.assertTrue(valid_form.is_valid(), valid_form.errors)

        invalid_form = DelegacionForm(data={**form_data, 'telefono': 'sjdfasdf'})
        self.assertFalse(invalid_form.is_valid())
        self.assertIn('telefono', invalid_form.errors)
        self.assertIn('solo números', invalid_form.errors['telefono'][0])

    def assert_crud_flow(
        self,
        *,
        list_url,
        create_url,
        update_url,
        delete_url,
        create_data,
        update_data,
        find_object,
        active_field,
    ):
        self.assertEqual(self.client.get(list_url).status_code, 200)

        response = self.client.post(create_url, create_data)
        self.assertEqual(response.status_code, 302, response.content.decode())
        self.assertEqual(response.url, list_url)

        obj = find_object()
        self.assertIsNotNone(obj)

        response = self.client.post(update_url.format(pk=obj.pk), update_data)
        self.assertEqual(response.status_code, 302, response.content.decode())
        self.assertEqual(response.url, list_url)
        obj.refresh_from_db()

        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn(obj.pk, {
            entry.pk for entry in response.context['object_list']
        })

        response = self.client.post(delete_url.format(pk=obj.pk))
        self.assertEqual(response.status_code, 302, response.content.decode())
        self.assertEqual(response.url, list_url)
        obj.refresh_from_db()
        self.assertFalse(getattr(obj, active_field))
        self.assertTrue(type(obj).objects.filter(pk=obj.pk).exists())

        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(obj.pk, {
            entry.pk for entry in response.context['object_list']
        })

    def test_all_eight_maintenance_screens_support_crud(self):
        flows = (
            {
                'name': 'delegaciones',
                'list_url': '/mantenedores/delegaciones/',
                'create_url': '/mantenedores/delegaciones/crear/',
                'update_url': '/mantenedores/delegaciones/{pk}/modificar/',
                'delete_url': '/mantenedores/delegaciones/{pk}/desactivar/',
                'create_data': {
                    'nombre': 'Delegación CRUD',
                    'codigo': 'CRUD',
                    'direccion': 'Calle Uno 123',
                    'telefono': '',
                    'email': 'delegacion.crud@example.com',
                    'ambito': 'Urbano',
                },
                'update_data': {
                    'nombre': 'Delegación CRUD actualizada',
                    'codigo': 'CRUD',
                    'direccion': 'Calle Dos 456',
                    'telefono': '',
                    'email': 'delegacion.crud@example.com',
                    'ambito': 'Urbano',
                },
                'find_object': lambda: Delegacion.objects.filter(codigo='CRUD').first(),
                'active_field': 'activo',
            },
            {
                'name': 'cargos',
                'list_url': '/mantenedores/cargos/',
                'create_url': '/mantenedores/cargos/crear/',
                'update_url': '/mantenedores/cargos/{pk}/modificar/',
                'delete_url': '/mantenedores/cargos/{pk}/desactivar/',
                'create_data': {
                    'nombre': 'Cargo CRUD',
                    'descripcion': 'Descripción de prueba',
                    'area': str(self.area.pk),
                },
                'update_data': {
                    'nombre': 'Cargo CRUD actualizado',
                    'descripcion': 'Descripción actualizada',
                    'area': str(self.area.pk),
                },
                'find_object': lambda: Cargo.objects.filter(nombre='Cargo CRUD').first(),
                'active_field': 'activo',
            },
            {
                'name': 'items',
                'list_url': '/mantenedores/items/',
                'create_url': '/mantenedores/items/crear/',
                'update_url': '/mantenedores/items/{pk}/modificar/',
                'delete_url': '/mantenedores/items/{pk}/desactivar/',
                'create_data': {
                    'cargo': str(self.cargo.pk),
                    'periodo': str(self.periodo.pk),
                    'nombre_item': 'Ítem CRUD',
                    'descripcion': 'Descripción de prueba',
                    'tipo_unidad': 'UNIDAD',
                    'meta_cuantitativa': '1',
                    'ponderador': '50',
                    'unidad_medida': 'Actividades',
                    'activo': 'on',
                },
                'update_data': {
                    'cargo': str(self.cargo.pk),
                    'periodo': str(self.periodo.pk),
                    'nombre_item': 'Ítem CRUD actualizado',
                    'descripcion': 'Descripción actualizada',
                    'tipo_unidad': 'UNIDAD',
                    'meta_cuantitativa': '1',
                    'ponderador': '50',
                    'unidad_medida': 'Actividades',
                    'activo': 'on',
                },
                'find_object': lambda: ItemMedicion.objects.filter(
                    nombre_item='Ítem CRUD',
                ).first(),
                'active_field': 'activo',
            },
            {
                'name': 'periodos',
                'list_url': '/mantenedores/periodos/',
                'create_url': '/mantenedores/periodos/crear/',
                'update_url': '/mantenedores/periodos/{pk}/modificar/',
                'delete_url': '/mantenedores/periodos/{pk}/desactivar/',
                'create_data': {
                    **self.period_form_data(timezone.localdate(), timezone.localdate()),
                    'nombre': 'Período CRUD',
                },
                'update_data': {
                    **self.period_form_data(timezone.localdate(), timezone.localdate()),
                    'nombre': 'Período CRUD actualizado',
                },
                'find_object': lambda: PeriodoMedicion.objects.filter(
                    nombre='Período CRUD',
                ).first(),
                'active_field': 'estado',
            },
            {
                'name': 'solicitantes',
                'list_url': '/mantenedores/solicitantes/',
                'create_url': '/mantenedores/solicitantes/crear/',
                'update_url': '/mantenedores/solicitantes/{pk}/modificar/',
                'delete_url': '/mantenedores/solicitantes/{pk}/desactivar/',
                'create_data': {
                    'run': '11.111.111-1',
                    'nombre': 'Persona CRUD',
                    'apellido_paterno': 'Apellido',
                    'apellido_materno': 'Prueba',
                    'tipo_solicitante': 'VECINO',
                    'telefono': '',
                    'correo': 'solicitante.crud@example.com',
                    'estado': 'on',
                },
                'update_data': {
                    'run': '11.111.111-1',
                    'nombre': 'Persona CRUD actualizada',
                    'apellido_paterno': 'Apellido',
                    'apellido_materno': 'Prueba',
                    'tipo_solicitante': 'VECINO',
                    'telefono': '',
                    'correo': 'solicitante.crud@example.com',
                    'estado': 'on',
                },
                'find_object': lambda: Solicitante.objects.filter(
                    run='11.111.111-1',
                ).first(),
                'active_field': 'estado',
            },
            {
                'name': 'funcionarios',
                'list_url': '/mantenedores/funcionarios/',
                'create_url': '/mantenedores/funcionarios/crear/',
                'update_url': '/mantenedores/funcionarios/{pk}/modificar/',
                'delete_url': '/mantenedores/funcionarios/{pk}/desactivar/',
                'create_data': {
                    'run': '11.111.111-1',
                    'nombre': 'Funcionario CRUD',
                    'apellidoPaterno': 'Apellido',
                    'apellidoMaterno': 'Prueba',
                    'correo': 'funcionario.crud@example.com',
                    'rol': 'FUNCIONARIO',
                    'area_entidad': str(self.area.pk),
                    'estado': 'on',
                },
                'update_data': {
                    'run': '11.111.111-1',
                    'nombre': 'Funcionario CRUD actualizado',
                    'apellidoPaterno': 'Apellido',
                    'apellidoMaterno': 'Prueba',
                    'correo': 'funcionario.crud@example.com',
                    'rol': 'FUNCIONARIO',
                    'area_entidad': str(self.area.pk),
                    'estado': 'on',
                },
                'find_object': lambda: Usuario.objects.filter(
                    email='funcionario.crud@example.com',
                ).first(),
                'active_field': 'is_active',
            },
            {
                'name': 'roles',
                'list_url': '/mantenedores/roles/',
                'create_url': '/mantenedores/roles/crear/',
                'update_url': '/mantenedores/roles/{pk}/modificar/',
                'delete_url': '/mantenedores/roles/{pk}/desactivar/',
                'create_data': {
                    'nombre_rol': 'Rol CRUD',
                    'descripcion': 'Descripción de prueba',
                    'estado': 'on',
                },
                'update_data': {
                    'nombre_rol': 'Rol CRUD actualizado',
                    'descripcion': 'Descripción actualizada',
                    'estado': 'on',
                },
                'find_object': lambda: Rol.objects.filter(
                    nombre_rol='Rol CRUD',
                ).first(),
                'active_field': 'estado',
            },
            {
                'name': 'areas',
                'list_url': '/mantenedores/areas/',
                'create_url': '/mantenedores/areas/crear/',
                'update_url': '/mantenedores/areas/{pk}/modificar/',
                'delete_url': '/mantenedores/areas/{pk}/desactivar/',
                'create_data': {
                    'nombre': 'Área CRUD',
                    'descripcion': 'Descripción de prueba',
                    'estado': 'on',
                },
                'update_data': {
                    'nombre': 'Área CRUD actualizada',
                    'descripcion': 'Descripción actualizada',
                    'estado': 'on',
                },
                'find_object': lambda: Area.objects.filter(
                    nombre='Área CRUD',
                ).first(),
                'active_field': 'estado',
            },
        )

        for flow in flows:
            with self.subTest(mantenedor=flow['name']):
                self.assert_crud_flow(**{
                    key: value
                    for key, value in flow.items()
                    if key != 'name'
                })

    def test_service_catalog_crud_is_available_from_items_template(self):
        list_url = '/mantenedores/items/'
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)

        response = self.client.post(list_url, {
            'action': 'guardar_catalogo',
            'codigo': 'SVC-CRUD',
            'nombre': 'Servicio CRUD',
            'area': 'Territorio',
        })
        self.assertEqual(response.status_code, 302, response.content.decode())
        servicio = CatalogoServicio.objects.get(codigo='SVC-CRUD')

        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn(servicio, response.context['catalogo_servicios'])

        response = self.client.post(list_url, {
            'action': 'guardar_catalogo',
            'catalogo_id': str(servicio.pk),
            'codigo': 'SVC-CRUD',
            'nombre': 'Servicio CRUD actualizado',
            'area': 'Operaciones',
        })
        self.assertEqual(response.status_code, 302, response.content.decode())
        servicio.refresh_from_db()
        self.assertEqual(servicio.nombre, 'Servicio CRUD actualizado')

        response = self.client.post(list_url, {
            'action': 'desactivar_catalogo',
            'catalogo_id': str(servicio.pk),
        })
        self.assertEqual(response.status_code, 302, response.content.decode())
        servicio.refresh_from_db()
        self.assertFalse(servicio.activo)
        self.assertTrue(CatalogoServicio.objects.filter(pk=servicio.pk).exists())

    def test_maintenance_dropdown_uses_activity_dropdown_style(self):
        response = self.client.get('/mantenedores/delegaciones/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'dropdown-menu nav-dropdown-menu')
        self.assertContains(response, 'dropdown-item nav-dropdown-item active')
        self.assertContains(response, 'bi-geo-alt')

    def test_cargo_maintainer_assigns_area_and_generates_code(self):
        response = self.client.post('/mantenedores/cargos/crear/', {
            'nombre': 'Cargo con área',
            'descripcion': 'Cargo asociado a un área',
            'area': str(self.area.pk),
        })
        self.assertEqual(response.status_code, 302)

        cargo = Cargo.objects.get(nombre='Cargo con área')
        self.assertEqual(cargo.area, self.area)
        self.assertTrue(cargo.codigo.startswith('CAR-'))
        self.assertEqual(len(cargo.codigo), 12)

        response = self.client.get('/mantenedores/cargos/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Área')
        self.assertContains(response, cargo.codigo)
        self.assertContains(response, self.area.nombre)
        self.assertNotContains(response, 'Departamento')

        response = self.client.get('/mantenedores/delegaciones/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Teléfono')
        self.assertNotContains(response, 'Departamento')

    def test_create_cargo_shows_active_areas_in_dropdown(self):
        inactive_area = Area.objects.create(
            nombre='Área inactiva para prueba',
            estado=False,
        )

        response = self.client.get('/mantenedores/cargos/crear/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<select name="area"', html=False)
        self.assertContains(response, f'value="{self.area.pk}"', html=False)
        self.assertContains(response, self.area.nombre)
        self.assertNotContains(response, inactive_area.nombre)
        self.assertContains(response, 'Selecciona un área')

    def test_functionary_role_dropdown_has_four_choices_and_maps_delegate(self):
        response = self.client.get('/mantenedores/funcionarios/crear/')

        self.assertEqual(response.status_code, 200)
        role_field = response.context['form'].fields['rol']
        self.assertTrue(response.context['form'].fields['estado'].initial)
        self.assertEqual(
            list(role_field.choices),
            [
                ('ADMINISTRADOR', 'Administrador'),
                ('COORDINADOR', 'Coordinador'),
                ('DELEGADO', 'Delegado'),
                ('FUNCIONARIO', 'Funcionario'),
            ],
        )
        self.assertNotIn('es_delegado', response.context['form'].fields)

        response = self.client.post('/mantenedores/funcionarios/crear/', {
            'run': '12.345.678-5',
            'nombre': 'Persona',
            'apellidoPaterno': 'Delegada',
            'apellidoMaterno': 'Prueba',
            'correo': 'delegada.prueba@example.com',
            'rol': 'DELEGADO',
            'area_entidad': str(self.area.pk),
            'delegacion': str(self.delegacion.pk),
            'estado': 'on',
        })
        self.assertEqual(response.status_code, 302)

        funcionario = Usuario.objects.get(email='delegada.prueba@example.com')
        self.assertEqual(funcionario.rol, Usuario.Rol.SUPERVISOR)
        self.assertTrue(funcionario.es_delegado)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_new_functionary_does_not_use_run_as_password_and_can_reset_it(self):
        response = self.client.post('/mantenedores/funcionarios/crear/', {
            'run': '12.345.678-5',
            'nombre': 'Persona',
            'apellidoPaterno': 'Clave',
            'apellidoMaterno': 'Segura',
            'correo': 'clave.segura@example.com',
            'rol': 'FUNCIONARIO',
            'area_entidad': str(self.area.pk),
            'estado': 'on',
        })
        self.assertEqual(response.status_code, 302)

        funcionario = Usuario.objects.get(email='clave.segura@example.com')
        self.assertFalse(funcionario.check_password(funcionario.rut_anonimizado))
        client = Client()
        self.assertFalse(client.login(
            username=funcionario.username,
            password=funcionario.rut_anonimizado,
        ))

        response = client.post(
            '/password-reset/',
            {'email': funcionario.email},
        )
        self.assertRedirects(response, '/password-reset/done/')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [funcionario.email])

    def test_functionary_first_login_requires_matching_new_password(self):
        response = self.client.post('/mantenedores/funcionarios/crear/', {
            'run': '12.345.678-5',
            'nombre': 'Persona',
            'apellidoPaterno': 'Clave',
            'apellidoMaterno': 'Segura',
            'correo': 'clave.segura@example.com',
            'rol': 'FUNCIONARIO',
            'area_entidad': str(self.area.pk),
            'estado': 'on',
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Funcionario creado correctamente.')
        self.assertContains(response, 'RUN sin puntos ni guion')

        funcionario = Usuario.objects.get(email='clave.segura@example.com')
        self.assertEqual(funcionario.username, funcionario.email)
        self.assertTrue(funcionario.must_change_password)
        self.assertGreater(funcionario.initial_login_expires_at, timezone.now())
        self.assertTrue(funcionario.check_password('123456785'))

        client = Client()
        response = client.post('/login/', {
            'username': 'clave.segura@example.com',
            'password': '123456785',
        })
        self.assertRedirects(response, '/crear-contrasena/')
        self.assertNotIn('_auth_user_id', client.session)

        response = client.post('/crear-contrasena/', {
            'new_password1': 'Faro.Cobre-2031!',
            'new_password2': 'Faro.Cobre-2032!',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Las contraseñas no coinciden.')
        funcionario.refresh_from_db()
        self.assertTrue(funcionario.must_change_password)
        self.assertTrue(funcionario.check_password('123456785'))

        response = client.post('/crear-contrasena/', {
            'new_password1': 'Faro.Cobre-2031!',
            'new_password2': 'Faro.Cobre-2031!',
        })
        self.assertRedirects(response, '/dashboard/')
        funcionario.refresh_from_db()
        self.assertFalse(funcionario.must_change_password)
        self.assertIsNone(funcionario.initial_login_expires_at)
        self.assertTrue(client.session.get('_auth_user_id'))
        self.assertTrue(funcionario.check_password('Faro.Cobre-2031!'))
        self.assertFalse(funcionario.check_password('123456785'))

    def test_functionary_first_login_expires_after_ten_minutes(self):
        self.client.post('/mantenedores/funcionarios/crear/', {
            'run': '12.345.678-5',
            'nombre': 'Persona',
            'apellidoPaterno': 'Clave',
            'apellidoMaterno': 'Vencida',
            'correo': 'clave.vencida@example.com',
            'rol': 'FUNCIONARIO',
            'area_entidad': str(self.area.pk),
            'estado': 'on',
        })
        funcionario = Usuario.objects.get(email='clave.vencida@example.com')
        funcionario.initial_login_expires_at = timezone.now() - timedelta(seconds=1)
        funcionario.save(update_fields=['initial_login_expires_at'])

        client = Client()
        response = client.post('/login/', {
            'username': funcionario.email,
            'password': '123456785',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'El plazo de 10 minutos')
        funcionario.refresh_from_db()
        self.assertFalse(funcionario.is_active)

        response = self.client.post(
            f'/mantenedores/funcionarios/{funcionario.pk}/renovar-acceso/',
        )
        self.assertRedirects(response, '/mantenedores/funcionarios/')
        funcionario.refresh_from_db()
        self.assertTrue(funcionario.is_active)
        self.assertTrue(funcionario.must_change_password)
        self.assertGreater(funcionario.initial_login_expires_at, timezone.now())
        self.assertTrue(funcionario.check_password('123456785'))

    def test_functionary_requires_area(self):
        response = self.client.post('/mantenedores/funcionarios/crear/', {
            'run': '12.345.678-5',
            'nombre': 'Persona',
            'apellidoPaterno': 'Sin',
            'apellidoMaterno': 'Área',
            'correo': 'sin.area@example.com',
            'rol': 'FUNCIONARIO',
            'estado': 'on',
        })

        self.assertEqual(response.status_code, 200)
        self.assertIn('area_entidad', response.context['form'].errors)
        self.assertFalse(Usuario.objects.filter(email='sin.area@example.com').exists())

    def test_new_role_name_does_not_default_to_operativo(self):
        response = self.client.get('/mantenedores/roles/crear/')

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'value="OPERATIVO"')
        self.assertEqual(response.context['form']['nombre_rol'].value(), None)

    def test_inactive_maintainer_records_can_be_reactivated(self):
        self.delegacion.activo = False
        self.delegacion.save(update_fields=['activo'])
        self.cargo.activo = False
        self.cargo.save(update_fields=['activo'])
        self.periodo.estado = False
        self.periodo.save(update_fields=['estado'])
        solicitante = Solicitante.objects.create(
            nombre='Solicitante inactivo',
            apellido='Prueba',
            apellido_paterno='Prueba',
            apellido_materno='Inactivo',
            tipo_solicitante='VECINO',
            estado=False,
        )
        funcionario = Usuario.objects.create_user(
            username='funcionario.inactivo',
            password='TestPassword123!',
            email='funcionario.inactivo@example.com',
            rol=Usuario.Rol.ADMINISTRATIVO,
            is_active=False,
        )
        rol = Rol.objects.create(nombre_rol='Rol inactivo', estado=False)
        area = Area.objects.create(nombre='Área inactiva', estado=False)
        servicio = CatalogoServicio.objects.create(
            codigo='SVC-INACTIVO',
            nombre='Servicio inactivo',
            activo=False,
        )
        item = ItemMedicion.objects.create(
            cargo=self.cargo,
            periodo=self.periodo,
            nombre_item='Ítem inactivo',
            meta_cuantitativa=1,
            ponderador=100,
            activo=False,
        )
        records = (
            ('delegaciones', self.delegacion, 'activo', '/mantenedores/delegaciones/'),
            ('cargos', self.cargo, 'activo', '/mantenedores/cargos/'),
            ('items', item, 'activo', '/mantenedores/items/'),
            ('periodos', self.periodo, 'estado', '/mantenedores/periodos/'),
            ('solicitantes', solicitante, 'estado', '/mantenedores/solicitantes/'),
            ('funcionarios', funcionario, 'is_active', '/mantenedores/funcionarios/'),
            ('roles', rol, 'estado', '/mantenedores/roles/'),
            ('areas', area, 'estado', '/mantenedores/areas/'),
            ('catalogos', servicio, 'activo', '/mantenedores/items/'),
        )

        for entity, record, active_field, list_url in records:
            with self.subTest(entity=entity):
                inactive_response = self.client.get(f'{list_url}?estado=inactivas')
                self.assertEqual(inactive_response.status_code, 200)
                self.assertContains(inactive_response, 'Activar')
                response = self.client.post(
                    f'/mantenedores/{entity}/{record.pk}/reactivar/',
                )
                self.assertEqual(
                    response.status_code,
                    302,
                    response.content.decode(),
                )
                record.refresh_from_db()
                self.assertTrue(getattr(record, active_field))

    def test_period_form_limits_dates_to_today_through_one_year(self):
        today = timezone.localdate()
        try:
            maximum_date = today.replace(year=today.year + 1)
        except ValueError:
            maximum_date = today.replace(year=today.year + 1, day=28)

        valid_form = MantenedorPeriodoForm(data=self.period_form_data(today, today))
        self.assertTrue(valid_form.is_valid(), valid_form.errors)
        self.assertEqual(
            valid_form.fields['fecha_inicio'].widget.attrs['min'],
            today.isoformat(),
        )
        self.assertEqual(
            valid_form.fields['fecha_termino'].widget.attrs['max'],
            maximum_date.isoformat(),
        )

        too_early = today - timedelta(days=1)
        invalid_form = MantenedorPeriodoForm(
            data=self.period_form_data(too_early, too_early),
        )
        self.assertFalse(invalid_form.is_valid())
        self.assertIn('fecha_inicio', invalid_form.errors)
        self.assertIn('fecha_termino', invalid_form.errors)

        too_late = maximum_date + timedelta(days=1)
        invalid_form = MantenedorPeriodoForm(
            data=self.period_form_data(too_late, too_late),
        )
        self.assertFalse(invalid_form.is_valid())
        self.assertIn('fecha_inicio', invalid_form.errors)
        self.assertIn('fecha_termino', invalid_form.errors)

    def period_form_data(self, start, end):
        return {
            'nombre': 'Período de validación',
            'anio': str(start.year),
            'trimestre': str((start.month - 1) // 3 + 1),
            'fecha_inicio': start.isoformat(),
            'fecha_termino': end.isoformat(),
            'dias_computables': str((end - start).days + 1),
            'umbral_verde': '80',
            'umbral_ambar': '50',
            'umbral_semaforo_verde': '100',
            'umbral_semaforo_ambar': '60',
            'umbral_colectivo': '80',
            'tope_cumplimiento': '150',
            'cerrado': '',
            'estado': 'on',
        }

    def test_inactive_delegation_can_be_reactivated_from_same_maintainer(self):
        delegacion = self.delegacion
        delegacion.activo = False
        delegacion.save(update_fields=['activo'])

        response = self.client.get('/mantenedores/delegaciones/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, delegacion.nombre)
        self.assertContains(response, 'Ver delegaciones inactivas')

        response = self.client.get('/mantenedores/delegaciones/?estado=inactivas')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, delegacion.nombre)
        self.assertContains(response, 'Activar')

        response = self.client.post(
            f'/mantenedores/delegaciones/{delegacion.pk}/reactivar/',
        )
        self.assertRedirects(
            response,
            '/mantenedores/delegaciones/?estado=inactivas',
            fetch_redirect_response=False,
        )
        delegacion.refresh_from_db()
        self.assertTrue(delegacion.activo)
        response = self.client.get('/mantenedores/delegaciones/?estado=inactivas')
        self.assertNotContains(response, delegacion.nombre)
        response = self.client.get('/mantenedores/delegaciones/')
        self.assertContains(response, delegacion.nombre)
        self.assertTrue(RegistroAuditoria.objects.filter(
            entidad='Delegacion',
            identificador=str(delegacion.pk),
            accion='REACTIVACION',
        ).exists())

    def test_supervisor_can_read_maintainers_and_manage_requesters_and_catalog(self):
        supervisor = Usuario.objects.create_user(
            username='supervisor.checklist',
            password='TestPassword123!',
            rol=Usuario.Rol.SUPERVISOR,
            delegacion=self.delegacion,
        )
        client = self.client
        client.force_login(supervisor)

        list_urls = (
            '/mantenedores/delegaciones/',
            '/mantenedores/cargos/',
            '/mantenedores/items/',
            '/mantenedores/periodos/',
            '/mantenedores/solicitantes/',
            '/mantenedores/funcionarios/',
            '/mantenedores/roles/',
            '/mantenedores/areas/',
        )
        for url in list_urls:
            with self.subTest(url=url):
                response = client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Mantenedores')
                if url == '/mantenedores/solicitantes/':
                    self.assertTrue(response.context['can_manage'])
                else:
                    self.assertFalse(response.context['can_manage'])
                if url == '/mantenedores/items/':
                    self.assertFalse(response.context['can_manage_items'])
                    self.assertTrue(response.context['can_manage_catalog'])

        restricted_objects = (
            ('delegaciones', self.delegacion),
            ('cargos', self.cargo),
            (
                'items',
                ItemMedicion.objects.create(
                    cargo=self.cargo,
                    periodo=self.periodo,
                    nombre_item='Ítem restringido',
                    ponderador=10,
                ),
            ),
            ('periodos', self.periodo),
            ('funcionarios', self.admin),
            ('roles', Rol.objects.create(nombre_rol='Rol restringido')),
            ('areas', Area.objects.create(nombre='Área restringida')),
        )
        for name, obj in restricted_objects:
            with self.subTest(mantenedor=name):
                base_url = f'/mantenedores/{name}/'
                self.assertEqual(
                    client.post(f'{base_url}crear/', {}).status_code,
                    403,
                )
                self.assertEqual(
                    client.post(f'{base_url}{obj.pk}/modificar/', {}).status_code,
                    403,
                )
                self.assertEqual(
                    client.post(f'{base_url}{obj.pk}/desactivar/').status_code,
                    403,
                )

        requester_data = {
            'run': '11.111.111-1',
            'nombre': 'Solicitante Supervisor',
            'apellido_paterno': 'Apellido',
            'apellido_materno': 'Prueba',
            'tipo_solicitante': 'VECINO',
            'telefono': '',
            'correo': 'solicitante.supervisor@example.com',
            'estado': 'on',
        }
        response = client.post(
            '/mantenedores/solicitantes/crear/',
            requester_data,
        )
        self.assertRedirects(response, '/mantenedores/solicitantes/')
        requester = Solicitante.objects.get(run='11.111.111-1')
        response = client.get('/mantenedores/solicitantes/')
        self.assertContains(response, 'Crear solicitante')
        self.assertContains(response, 'Modificar')

        requester_data['nombre'] = 'Solicitante actualizado'
        response = client.post(
            f'/mantenedores/solicitantes/{requester.pk}/modificar/',
            requester_data,
        )
        self.assertRedirects(response, '/mantenedores/solicitantes/')
        requester.refresh_from_db()
        self.assertEqual(requester.nombre, 'Solicitante actualizado')

        response = client.post(
            f'/mantenedores/solicitantes/{requester.pk}/desactivar/',
        )
        self.assertRedirects(response, '/mantenedores/solicitantes/')
        requester.refresh_from_db()
        self.assertFalse(requester.estado)

        catalog_url = '/mantenedores/items/'
        response = client.get(catalog_url)
        self.assertContains(response, 'Agregar servicio')
        self.assertNotContains(response, 'Crear ítem')
        response = client.post(catalog_url, {
            'action': 'guardar_catalogo',
            'codigo': 'SVC-SUPERVISOR',
            'nombre': 'Servicio Supervisor',
            'area': 'Territorio',
        })
        self.assertRedirects(response, catalog_url)
        service = CatalogoServicio.objects.get(codigo='SVC-SUPERVISOR')
        response = client.post(catalog_url, {
            'action': 'guardar_catalogo',
            'catalogo_id': str(service.pk),
            'codigo': 'SVC-SUPERVISOR',
            'nombre': 'Servicio Supervisor actualizado',
            'area': 'Operaciones',
        })
        self.assertRedirects(response, catalog_url)
        service.refresh_from_db()
        self.assertEqual(service.nombre, 'Servicio Supervisor actualizado')
        response = client.post(catalog_url, {
            'action': 'desactivar_catalogo',
            'catalogo_id': str(service.pk),
        })
        self.assertRedirects(response, catalog_url)
        service.refresh_from_db()
        self.assertFalse(service.activo)

    def test_maintenance_api_allows_reads_but_rejects_all_writes(self):
        servicio = CatalogoServicio.objects.create(
            codigo='SVC-API',
            nombre='Servicio API',
        )
        item = ItemMedicion.objects.create(
            cargo=self.cargo,
            periodo=self.periodo,
            nombre_item='Ítem API',
            ponderador=10,
        )
        resources = (
            ('delegaciones', self.delegacion),
            ('cargos', self.cargo),
            ('usuarios', self.admin),
            ('periodos', self.periodo),
            ('catalogos', servicio),
            ('items-medicion', item),
        )

        for endpoint, obj in resources:
            with self.subTest(recurso=endpoint):
                collection_url = f'/api/{endpoint}/'
                detail_url = f'{collection_url}{obj.pk}/'
                self.assertEqual(self.client.get(collection_url).status_code, 200)
                self.assertEqual(self.client.get(detail_url).status_code, 200)
                self.assertEqual(
                    self.client.post(collection_url, {}).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.put(detail_url, {}).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.patch(detail_url, {}).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.delete(detail_url).status_code,
                    403,
                )

    def test_api_does_not_render_browsable_management_interface(self):
        response = self.client.get(
            '/api/delegaciones/',
            HTTP_ACCEPT='text/html',
        )
        self.assertEqual(response.status_code, 406)

        response = self.client.get(
            '/api/delegaciones/',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'].split(';')[0],
            'application/json',
        )
