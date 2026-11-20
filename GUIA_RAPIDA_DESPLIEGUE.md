# Qué subir y dónde — Guía rápida

## Opción recomendada: infraestructura autorizada por la entidad

1. **Subir**: extraer `radicacion_cuentas_app.zip` en un servidor Linux institucional con Docker; conservar juntos `app/`, `migrations/`, `Dockerfile`, `requirements.txt`, `docker-compose.yml`, `alembic.ini` y `.env.example`.
2. **Configurar**: crear `.env` privado con `SECRET_KEY` y `POSTGRES_PASSWORD` aleatorios y largos. Consultar `README.md`.
3. **Iniciar**: `docker compose up -d --build`.
4. **Crear primer acceso**: `docker compose exec web python -m app.cli admin --email correo@dominio --name "Administrador"`.
5. **Entrar**: `http://localhost:8000/ingresar` (solo prueba interna). Para acceso remoto se requiere un proxy HTTPS con dominio institucional y ajustes de producción.
6. **Migrar datos**: en el menú **Migración** del aplicativo, cargar el archivo original `Rev cuentas.xlsx` desde el equipo autorizado. No subirlo a GitHub.
7. **Verificar**: la pantalla de migración debe reflejar las 13 pestañas y sus conteos de filas, sin pérdidas; revisar anomalías.

## Opción administrada: Render

1. Crear repositorio **privado** y subir **solamente los archivos del código fuente**, no el Excel ni PDFs ni secretos.
2. Conectar GitHub en `render.com` → **New Blueprint**. El archivo `render.yaml` despliega web + PostgreSQL de pago.
3. Configurar `ALLOWED_HOSTS` y `APP_URL` según el dominio asignado. La base se crea con acceso restringido; la aplicación usa TLS/HTTPS.
4. Crear administrador desde Shell (`python -m app.cli admin ...`). Ingresar y cargar Excel por el menú **Migración**.

**No hay que subir** los cuatro PDF de Google Forms: las opciones ya están codificadas. El formulario de registro nuevo NO permite cargar el archivo de la cuenta.

**Antes de empezar**: validar hosting con la entidad, copias de seguridad y protección de los datos contractuales/personales.
