# RadicaCuentas — Sistema integrado de radicación y seguimiento

**Versión funcional inicial · 17 de noviembre de 2026**

Aplicativo web para reemplazar las hojas como motor operativo. Implementado con **FastAPI, SQLAlchemy, PostgreSQL, HTML/Jinja y Docker**, sin requerir Google Cloud. Diseñado con base en el Apps Script aportado, la fuente `Rev cuentas.xlsx` y las capturas PDF de los cuatro formularios. Su despliegue definitivo requiere revisión de seguridad, homologación funcional y autorización de tratamiento de datos.

## Funciones implementadas

- Inicio de sesión con contraseña Argon2, límite de intentos de acceso y cambio de contraseña por usuario; roles administrador, seguimiento, revisor, abogados y consulta; cookies de sesión HTTPS y formularios con CSRF.
- Registro de cuenta con ID_CASO nuevo, controles de duplicidad, contratista, contrato, período, valor, responsables, tipo de revisión, subtareas y SLA. **No pide ni admite cargar el archivo de la cuenta de cobro.**
- Formulario 2: Supervisor/Revisor (Requiere ajustes, Segunda revisión, Firma Directora, Financiera).
- Formulario 3: Seguimiento con 11 acciones, cierres financieros, frentes paralelos y observaciones.
- Formulario 4: Revisión técnica/jurídica con proveedor y resultado.
- Bandeja de casos SQL con búsqueda, filtros, paginación, trazabilidad, subtareas, semáforo y exportación CSV.
- Radicación mensual por contrato/año con marcación manual y sincronización automática para nuevos registros cuyo contrato se identifica sin ambigüedad.
- Importación histórica íntegra: las 13 pestañas del Excel, filas originales, valores calculados, fórmulas y destinos de hipervínculos; además se crean tablas operativas SQL.
- Panel básico, informe por responsable, bandeja de anomalías, auditoría, catálogo de personal, administración de festivos y cola SMTP de notificaciones optativa.
- Scripts de integridad y migraciones de esquema con Alembic.

**No implementado todavía como función productiva homologada:** SSO/MFA institucional, permisos finos por expediente, alertas regulatorias por contrato/mes, reconciliación automática de filas ambiguas, controles legales específicos de tratamiento de datos y pruebas de carga de producción. La salida oficial de Google Sheets debe esperar su aceptación y las pruebas finales.

## Estructura del proyecto

| Ruta | Función | ¿Subir a servidor? |
|---|---|---|
| `app/` | Lógica web, plantillas, estilos, importador | Sí |
| `requirements.txt` | Librerías Python | Sí |
| `Dockerfile` | Construcción de la aplicación | Sí |
| `render.yaml` | Despliegue de aplicación + PostgreSQL en Render | Sí, si usa Render |
| `railway.json` | Detección de Docker en Railway | Sí, si usa Railway |
| `docker-compose.yml` | Instalación en servidor propio con PostgreSQL | Sí, si usa Docker Compose |
| `migrations/`, `alembic.ini` | Crear tablas e índices SQL | Sí |
| `.env.example` | Guía de configuración, **sin secretos reales** | Sí |
| `scripts/verificar_migracion.py` | Comprobación de filas migradas | Sí |
| `tests/`, `requirements-dev.txt` | QA opcional | No necesario para ejecución |
| `Rev cuentas.xlsx` | Datos históricos, contiene información personal | **NO subir a GitHub ni a repositorios de código.** Cargar desde la administración privada tras autorización. |
| PDFs de los formularios | Referencia funcional | No; los formularios ya están implementados. |

## Alternativa A: servidor propio o institucional (preferible para datos sensibles)

Requisitos: Docker Engine + Docker Compose, almacenamiento persistente, copia de seguridad y un proxy HTTPS institucional. En un servidor Linux autorizado:

1. Extraiga el ZIP del código completo, entre en la carpeta del proyecto.
2. Copie `.env.example` a `.env` y cambie `POSTGRES_PASSWORD` y `SECRET_KEY` por secretos generados localmente. Ejemplo de secreto: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
3. Deje `APP_ENV=development`, `SESSION_HTTPS_ONLY=0` y `ALLOWED_HOSTS=localhost,127.0.0.1` **solo para pruebas locales**.
4. Ejecute `docker compose up -d --build`. La primera puesta en marcha aplica `alembic upgrade head`.
5. Cree el administrador: `docker compose exec web python -m app.cli admin --email administrador@dominio.autorizado --name "Administrador"`. El sistema solicita la contraseña sin imprimirla.
6. Abra `http://127.0.0.1:8000`, inicie sesión y entre a **Migración**. Cargue el **Excel original**, sin modificarlo. El servicio no envía correos hasta habilitar SMTP.
7. Verifique las cifras de la pantalla de Migración. Para ejecutar un control formal desde el mismo equipo que dispone del archivo: `docker compose cp "Rev cuentas.xlsx" web:/tmp/rev_cuentas.xlsx` y `docker compose exec web python scripts/verificar_migracion.py /tmp/rev_cuentas.xlsx`. Después elimine la copia temporal: `docker compose exec -u root web rm -f /tmp/rev_cuentas.xlsx`.
8. Para abrir el aplicativo a usuarios fuera del equipo, configure primero **HTTPS real** (Nginx/Caddy/Traefik, certificados válidos), `APP_ENV=production`, `SESSION_HTTPS_ONLY=1`, `ALLOWED_HOSTS=dominio.institucional.gov.co` y `APP_URL=https://dominio.institucional.gov.co`. El Docker Compose suministrado expone el puerto 8000 **solo en 127.0.0.1**, deliberadamente.
9. Configure backups automáticos de PostgreSQL con retención y ejercicios de restauración. El volumen `postgres_data` no sustituye una copia externa.

**No borre el volumen PostgreSQL durante una actualización**. Los datos de un redeploy permanecen en la base y no en el contenedor de la aplicación.

## Alternativa B: Render (despliegue administrado, sin Google Cloud)

Sitios oficiales: https://render.com y https://render.com/docs/blueprint-spec

**Antes de subir datos personales**: confirme con la entidad que Render es un proveedor autorizado para el alojamiento, la región y la transferencia internacional de datos. No use instancias gratuitas para información operativa crítica; el `render.yaml` crea infraestructura de pago con PostgreSQL persistente.

1. Cree un **repositorio privado** en GitHub/GitLab autorizado. Extraiga el ZIP, suba las carpetas y archivos del código fuente (incluido `render.yaml`), **excluyendo** `.env`, `Rev cuentas.xlsx`, PDFs con información personal, archivos `.db` y respaldos.
2. En https://dashboard.render.com elija **New → Blueprint** y conecte el repositorio privado. El Blueprint prepara una aplicación web y una base PostgreSQL 17 de pago con la base restringida a red privada.
3. Configure `ALLOWED_HOSTS` con el dominio real del servicio (por ejemplo `radicacion-cuentas.onrender.com`) y `APP_URL` con la URL HTTPS correspondiente. En el primer despliegue ajuste estas variables cuando Render haya confirmado el dominio. `SECRET_KEY` se genera automáticamente y `SESSION_HTTPS_ONLY=1`.
4. Espere al estado de despliegue satisfactorio y verifique `https://su-dominio/health` (debe devolver `{"status":"ok"}`). Se crean las tablas con `alembic upgrade head`.
5. Desde la función **Shell** del servicio web (plan compatible), ejecute `python -m app.cli admin --email administrador@dominio.autorizado --name "Administrador"`. Escriba una contraseña única y segura cuando se solicite.
6. Abra `https://su-dominio/ingresar`; entre como administrador y cargue `Rev cuentas.xlsx` desde **Migración** (pantalla de carga protegida; límite 35 MB). Para cargas superiores conviene ejecutar el importador dentro del contenedor, sin pasar el archivo por una petición web.
7. Revise **Migración**, **Casos**, **Radicación mensual** y **Auditoría**. Habilite copias y recuperación de PostgreSQL en Render, supervise consumo, rendimiento y errores.

**Archivos que NO se cargan a Render por Git**: Excel con datos personales, PDF con capturas de la operación, archivos `.env`, logs, secretos, respaldos y la base SQLite local. **El Excel se carga después y únicamente por el área administrativa autenticada**, tras autorización institucional.

Para evitar indisponibilidad por un dominio desconocido al crear el servicio, puede configurar temporalmente `ALLOWED_HOSTS=*.onrender.com` en un entorno privado de pruebas y luego **reemplazarlo inmediatamente por el hostname exacto** antes de operar con datos reales.

## Alternativa C: Railway

Guía oficial: https://railway.com/guides/fastapi y https://docs.railway.com/databases/postgresql

1. Suba el código (no los datos) a repositorio privado y conecte el repositorio desde **Railway → New Project → Deploy from GitHub**. Railway construye con `Dockerfile`.
2. Agregue una base **PostgreSQL** al proyecto. En el servicio web, agregue la variable de referencia `DATABASE_URL` correspondiente al servicio PostgreSQL (habitualmente `${{Postgres.DATABASE_URL}}` si ese es el nombre real del servicio).
3. Configure `SECRET_KEY` aleatorio de al menos 32 caracteres, `APP_ENV=production`, `SESSION_HTTPS_ONLY=1`, `ALLOWED_HOSTS` con el hostname de Railway, `APP_URL` y `NOTIFICATIONS_ENABLED=0`.
4. En Networking seleccione **Generate Domain** y verifique `/health`. Cree un usuario administrativo por la shell de la plataforma (`python -m app.cli admin ...`), y cargue el Excel **solo** en la pantalla privada de migración. Active backups y HTTPS.

## Migración controlada y verificación

- El importador guarda el **hash SHA-256 del archivo** para impedir la doble importación del mismo corte.
- La importación está diseñada para ejecutarse en **una base vacía** y dentro de **una transacción**: si ocurre un fallo, no deja una migración marcada como completa.
- Cada fila no vacía, incluso la que no se pueda vincular a un expediente, queda en `raw_rows` asociada a su pestaña y número de fila. Se conservan fórmulas, valores cacheados, referencias de hipervínculos, valores con error y fechas.
- A partir de esa copia se alimentan `cases`, `events`, `subtasks`, `rad_contracts`, `rad_months`, `form_responses`, `legacy_logs` y `personnel`.
- **No se crean archivos ni vínculos públicos de Drive**. Los links anteriores se preservan como texto y metadatos; los documentos reales siguen en Drive con los permisos existentes.
- No se eliminan registros huérfanos; las inconsistencias se señalan en `import_issues` para conciliación humana.
- El origen Excel es un corte estático. Para la puesta en marcha real, congele el formulario/archivo origen, exporte una **copia final**, migre esa copia y verifique que no hubo nuevos movimientos durante el cambio.
- Antes de activar la plataforma: concilie filas duplicadas, identifique actores, cargue en **Festivos / SLA** el calendario oficial colombiano (la hoja original está vacía), verifique plazos SLA, confirme el contrato/período especial y valide el resultado con usuarios de cada etapa.

**Control desde un equipo con Python y conexión a PostgreSQL:**

```bash
alembic upgrade head
python -m app.cli admin --email administrador@dominio.autorizado --name "Administrador"
python -m app.cli import "/ruta/Rev cuentas.xlsx"
python scripts/verificar_migracion.py "/ruta/Rev cuentas.xlsx"
python -m app.cli verify
```

La base real se configura en `DATABASE_URL`. No use `sqlite` en producción.

## Notificaciones SMTP y tareas programadas

La recepción de un nuevo caso crea una notificación pendiente. El worker también prepara alertas del SLA para los casos vencidos o próximos a vencer, cuando existe un correo identificable del encargado. Por defecto **no se envía ningún correo**.

Si la entidad autoriza la salida por SMTP, configure `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_STARTTLS` y `NOTIFICATIONS_ENABLED=1`. Para Docker Compose active el trabajador con `docker compose --profile notifications up -d --build`. Para Render/Railway cree un servicio de tipo worker separado ejecutando `python -m app.cli worker`, con la misma conexión a la base y variables SMTP. Compruebe primero un buzón de prueba. Los fallos se reintentan hasta cuatro veces.

Google Workspace no concede automáticamente acceso SQL ni un servidor PostgreSQL. Los correos de Google pueden requerir un relay SMTP institucional autorizado u otra integración OAuth. Esta versión no depende de Google Cloud.

## Pruebas

```bash
pip install -r requirements-dev.txt
pytest -q
```

Estas pruebas verifican login, CSRF, roles, registro sin adjuntos, duplicados, los tres flujos de movimientos y exportación. Se ejecutaron pruebas reales de importación sobre la copia suministrada. **No se ha probado carga concurrente a escala ni despliegue sobre un servidor PostgreSQL real** en este entorno: debe completarse antes de producción.

## Gestión de seguridad e información

- No publique capturas, bases de datos, rutas de archivo, credenciales o el Excel en repositorios públicos.
- Solicite aprobación de seguridad, protección de datos, continuidad, retención de soportes y contratación del hosting antes de pasar datos reales a un proveedor externo.
- Los roles de esta versión habilitan módulos, pero aún requieren homologación de permisos de **acceso por expediente, área o dependencia**.
- Configure MFA/SSO, respaldo cifrado, acceso administrativo restringido, WAF/rate limiting perimetral, políticas de contraseña y pruebas de recuperación antes de operación institucional.
- Para cambios futuros de columnas o tablas utilice migraciones Alembic versionadas; no cambie a mano el esquema en producción.

## Versión 2 — Material Design 3 y administración desde el navegador

La nueva interfaz utiliza superficies tonales, tarjetas de trabajo, tipografía de alta legibilidad, diseño adaptable y navegación por capacidad. No requiere React ni depender de servicios externos de Google Fonts. El administrador puede crear usuarios **sin consola**: nombre, correo y selección independiente de los cuatro formularios, además de módulos auxiliares. Cada cuenta recibe un enlace privado de activación, de uso único y 72 horas de vigencia; el usuario establece su contraseña.

**Configuración inicial:** `BOOTSTRAP_EMAIL` y `BOOTSTRAP_TOKEN` activan `/configuracion-inicial` mientras `users` está vacía. La creación única se protege con CSRF, límite de intentos y bloqueo de transacción sobre PostgreSQL. Después de crear la primera cuenta, esta ruta deja de estar disponible.

**Migración 2026:** desde el menú administrativo, el usuario puede cargar `Rev cuentas.xlsx`. Un ensayo local sobre la fuente proporcionada confirmó **13 pestañas y 43.800 filas crudas**, además de 1.292 casos, 8.555 eventos, 8.371 respuestas históricas, 23.288 logs, 157 subtareas y 112 contratos de radicación. La migración en Railway debe ejecutarla el administrador autorizado. No incluir el archivo fuente en GitHub.

La actualización de esquema es `migrations/versions/0002_permissions_invitations.py` y se aplica automáticamente con `alembic upgrade head` en Docker.
