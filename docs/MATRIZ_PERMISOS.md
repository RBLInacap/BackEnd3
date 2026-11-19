# Matriz de permisos (RNF-005)

Describe quién puede hacer qué en el SGR, con mínimo privilegio y segregación de funciones. La matriz se verifica automáticamente en `backend/sgr_app/test_matriz_permisos.py`: si un cambio de código la altera, las pruebas fallan.

## Perfiles

| Perfil | Cómo se asigna | Ámbito de datos |
|---|---|---|
| **Administrador** | Rol `ADMINISTRADOR` o superusuario | Toda la comuna |
| **Coordinador** | Rol `COORDINADOR` | Toda la comuna |
| **Delegado** | Rol `DELEGADO` con una delegación asignada | Solo su delegación |
| **Funcionario** | Rol `FUNCIONARIO` | Solo sus propios registros |
| **Usuario de consulta** | Rol `CONSULTA` | Su ámbito, solo lectura (CU-38) |

Los roles adicionales configurados en el mantenedor de roles pueden sumar permisos (`APROBAR_EVIDENCIA`, `REGISTRAR_ACTIVIDAD`, etc.).

## Operaciones

Leyenda: ✅ permitido · 🔸 solo dentro de su ámbito · ❌ denegado (403) · — no aplica

### Registros operativos

| Recurso / operación | Administrador | Coordinador | Delegado | Funcionario |
|---|---|---|---|---|
| Actividades: ver | ✅ | ✅ | 🔸 delegación | 🔸 propias |
| Actividades: registrar | ✅ | ✅ | 🔸 | 🔸 propias |
| Actividades: modificar / eliminar (API) | ✅ | ✅ | ❌ | ❌ |
| Actividades: anular (web) | ✅ | ✅ | 🔸 | ❌ |
| Evidencias: ver | ✅ | ✅ | 🔸 delegación | 🔸 propias |
| Evidencias: agregar | — | — | — | 🔸 a sus actividades |
| Evidencias: aprobar / rechazar | ✅ ¹ | ✅ ¹ | 🔸 ¹ | ❌ |
| Compromisos (tubo de trabajo): gestionar | ✅ | ✅ | 🔸 delegación | 🔸 asignados |
| Casos y atenciones sociales | ✅ | ✅ | 🔸 delegación | ❌ |
| Ajustes de incentivo: registrar | ✅ | ✅ | ❌ | ❌ |
| Ajustes de incentivo: aprobar | ✅ ² | ✅ ² | ❌ | ❌ |

### Tableros, informes y auditoría

| Recurso / operación | Administrador | Coordinador | Delegado | Funcionario |
|---|---|---|---|---|
| Tablero principal | Panel Administrador | Panel Coordinador | **Panel Territorial** (su delegación) | Panel Funcionario |
| Comparación territorial entre delegaciones | ✅ | ✅ | ❌ | ❌ |
| Semáforo y alertas | ✅ | ✅ | 🔸 delegación | 🔸 alertas propias |
| Tablero personal | ✅ | ✅ | ✅ | 🔸 propio |
| Informes y exportación CSV | ✅ | ✅ | 🔸 delegación | ❌ |
| Auditoría (web y `/api/auditoria/`) | ✅ | ✅ | ❌ | ❌ |

### Mantenedores (configuración)

| Recurso / operación | Administrador | Coordinador | Delegado | Funcionario |
|---|---|---|---|---|
| Delegaciones, cargos, roles, áreas, ítems, períodos, funcionarios (web) | ✅ | ❌ ³ | ❌ | ❌ |
| Solicitantes (web) | ✅ | ✅ | ❌ | ❌ |
| Mantenedores por API: leer | ✅ | ✅ | ✅ ⁴ | ✅ ⁴ |
| Mantenedores por API: crear / modificar / eliminar | ❌ ⁵ | ❌ ⁵ | ❌ | ❌ |
| Usuarios por API: leer | ✅ | ✅ | 🔸 delegación | 🔸 solo su ficha |

## Notas

1. **Segregación de funciones:** nadie, ni siquiera un administrador, puede aprobar o rechazar evidencias de **sus propias actividades** o que **subió él mismo**. Se aplica en la web y en la API (`Evidencia.es_autovalidacion`).
2. El **funcionario beneficiado** no puede aprobar ni rechazar su propio ajuste de incentivo. **Decisión pendiente del equipo:** hoy quien *registra* un ajuste sí puede aprobarlo (las pruebas `test_incentive_adjustment_workflow_*` lo establecen así). Para segregación completa ("maker-checker") habría que exigir que decida otra persona.
3. Algunos listados admiten consulta del supervisor (`allow_supervisor`), sin edición.
4. Delegaciones, cargos, períodos, catálogo e ítems son **datos de referencia sin información personal**, necesarios para los formularios de registro. Por eso pueden leerlos todos los usuarios autenticados. Los usuarios sí se filtran por ámbito.
5. La configuración se modifica solo desde los mantenedores web, que registran auditoría y control de concurrencia. La API de mantenedores es deliberadamente de solo lectura.

## Dónde se implementa

| Capa | Archivo |
|---|---|
| Autorización de la API por rol y operación | `sgr_app/permissions.py` (`AuthenticatedRolePermission`, `AuditAccessPermission`) |
| Filtro de datos por ámbito | `get_queryset()` de cada ViewSet en `sgr_app/views.py` y vistas web en `sgr_app/web_views.py` |
| Mantenedores web | `AdministradorRequiredMixin` en `sgr_app/mantenedores.py` |
| Segregación de funciones | `Evidencia.es_autovalidacion` y validación de ajustes en `sgr_app/models.py` |
| 2FA obligatoria por rol | `TWO_FACTOR_REQUIRED_ROLES` y `sgr_app/dos_factores.py` |
