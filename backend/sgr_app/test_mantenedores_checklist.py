from datetime import date

from django.test import TestCase

from sgr_app.models import (
    Area,
    Cargo,
    CatalogoServicio,
    Delegacion,
    ItemMedicion,
    PeriodoMedicion,
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
        self.cargo = Cargo.objects.create(nombre='Cargo para checklist')
        self.periodo = PeriodoMedicion.objects.create(
            nombre='Período base checklist',
            fecha_inicio=date(2026, 10, 1),
            fecha_termino=date(2026, 12, 31),
        )

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
                    'departamento': '',
                },
                'update_data': {
                    'nombre': 'Cargo CRUD actualizado',
                    'descripcion': 'Descripción actualizada',
                    'departamento': '',
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
                    'nombre': 'Período CRUD',
                    'anio': '2026',
                    'trimestre': '4',
                    'fecha_inicio': '2026-10-01',
                    'fecha_termino': '2026-12-31',
                    'dias_computables': '92',
                    'umbral_verde': '80',
                    'umbral_ambar': '50',
                    'umbral_semaforo_verde': '100',
                    'umbral_semaforo_ambar': '60',
                    'umbral_colectivo': '80',
                    'tope_cumplimiento': '150',
                    'estado': 'on',
                },
                'update_data': {
                    'nombre': 'Período CRUD actualizado',
                    'anio': '2026',
                    'trimestre': '4',
                    'fecha_inicio': '2026-10-01',
                    'fecha_termino': '2026-12-31',
                    'dias_computables': '92',
                    'umbral_verde': '80',
                    'umbral_ambar': '50',
                    'umbral_semaforo_verde': '100',
                    'umbral_semaforo_ambar': '60',
                    'umbral_colectivo': '80',
                    'tope_cumplimiento': '150',
                    'estado': 'on',
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
                    'rol': Usuario.Rol.ADMINISTRATIVO,
                    'estado': 'on',
                },
                'update_data': {
                    'run': '11.111.111-1',
                    'nombre': 'Funcionario CRUD actualizado',
                    'apellidoPaterno': 'Apellido',
                    'apellidoMaterno': 'Prueba',
                    'correo': 'funcionario.crud@example.com',
                    'rol': Usuario.Rol.ADMINISTRATIVO,
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
