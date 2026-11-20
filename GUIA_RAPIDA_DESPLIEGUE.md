# RadicaCuentas v2 — acceso completamente web

**Tecnología:** FastAPI + PostgreSQL + Docker desplegado en Railway. Diseño basado en Material Design 3, sin dependencias externas de frontend.

## URL del sistema

- Aplicación: `https://radicacuentas-web-production.up.railway.app/ingresar`
- Configuración del primer administrador (solo si la tabla `users` está vacía): `https://radicacuentas-web-production.up.railway.app/configuracion-inicial`
- Prueba de disponibilidad: `/health`

## Primera configuración desde el navegador

1. En Railway, configurar las variables `BOOTSTRAP_EMAIL` (correo único del administrador) y `BOOTSTRAP_TOKEN` (código criptográficamente aleatorio, conocido solo por el administrador).
2. Abrir la ruta `/configuracion-inicial` con HTTPS e ingresar nombre, correo autorizado, código y contraseña fuerte de 12 o más caracteres.
3. El sistema impide repetir la operación cuando ya existe la primera cuenta. No se cargan contraseñas por la consola.
4. Iniciar sesión en `/ingresar`. Desde **Usuarios y permisos**, crear usuarios introduciendo **nombre, correo y casillas para los cuatro formularios** y herramientas adicionales.
5. El sistema entrega un enlace de activación privado de un solo uso, válido durante 72 horas. El administrador lo comparte por un canal seguro; el usuario activa su propia contraseña. No se envían correos automáticos hasta configurar SMTP institucional.
6. Para modificar permisos, abrir la tarjeta del usuario en **Usuarios y permisos → Administrar permisos**. Para revocar el acceso, utilizar **Desactivar**.

## Formulario y permisos

- Formulario 01 Registro de cuentas: `registro`
- Formulario 02 Revisión supervisor: `revision`
- Formulario 03 Gestión y seguimiento: `seguimiento`
- Formulario 04 Jurídica / técnica: `juridica`
- Permisos extra: consultar expedientes, radicación mensual, informes, exportar CSV.
- Todos los permisos se validan en el servidor, incluso cuando se entra directamente por una URL.
- La edición de usuarios, el histórico y la auditoría son exclusivos del administrador.
- El **registro de cuentas no solicita cargar archivos**. Los enlaces anteriores se conservan exclusivamente al migrar los datos.

## Migrar los 13 conjuntos de datos desde el aplicativo

1. Es indispensable hacerlo **antes de registrar nuevas cuentas** en la nueva base; el importador rechaza una mezcla insegura de histórico y casos nuevos.
2. Descarga la fuente original `Rev cuentas.xlsx` de la ubicación institucional autorizada.
3. Accede con el administrador al menú **Migración histórica → Importar base histórica**.
4. Selecciona `Rev cuentas.xlsx` y pulsa **Migrar información**. Puede tomar más de un minuto, según el tamaño y el servidor. No cierres la pestaña ni pulses dos veces.
5. Verifica el lote con estado `COMPLETADA` y los indicadores del aplicativo. Los valores comprobados en el entorno local con la fuente proporcionada fueron:

| Resultado de conciliación local | Valor |
| --- | ---: |
| Hojas | 13 |
| Filas originales preservadas | 43.800 |
| Expedientes importados | 1.292 |
| Eventos | 8.555 |
| Subtareas | 157 |
| Contratos de radicación | 112 |
| Respuestas históricas de formularios | 8.371 |
| Registros de bitácora | 23.288 |
| Incidencias de conciliación | 1.371 |

Los datos con identificadores incompletos se preservan como origen y se muestran para revisión. Los archivos vinculados de Google Drive **no cambian de permisos** ni se suben de manera automática.

## Precauciones de producción

- Antes de cargar datos reales se requiere autorización institucional para alojarlos en Railway.
- Falta homologar políticas de control de acceso **por expediente, contrato o dependencia**, así como SSO/MFA y pruebas concurrentes. Los permisos actuales controlan **herramientas**; no sustituyen el aislamiento entre expedientes.
- El calendario de festivos de la fuente original está vacío. Debe completarse desde **Festivos y plazos** antes de confiar en los cálculos SLA.
- Configurar respaldos diarios de PostgreSQL, revisar HTTPS, auditar cuentas y no distribuir enlaces de activación en público.
- No subir `Rev cuentas.xlsx`, contraseñas, `.env` ni códigos de configuración a GitHub.
