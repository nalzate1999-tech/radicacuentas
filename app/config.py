import os
from functools import lru_cache
from urllib.parse import urlsplit

@lru_cache
def get_settings():
    class Settings:
        env = os.getenv('APP_ENV', 'development').lower()
        secret = os.getenv('SECRET_KEY', 'dev-only-local-secret-not-for-publication')
        database_url = os.getenv('DATABASE_URL', 'sqlite:///./local.sqlite3')
        allowed_hosts = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver')
        session_https = os.getenv('SESSION_HTTPS_ONLY', '0') == '1'
        smtp_host = os.getenv('SMTP_HOST', '').strip()
        smtp_port = int(os.getenv('SMTP_PORT', '587'))
        smtp_user = os.getenv('SMTP_USER', '')
        smtp_password = os.getenv('SMTP_PASSWORD', '')
        smtp_from = os.getenv('SMTP_FROM', '')
        smtp_tls = os.getenv('SMTP_STARTTLS', '1') == '1'
        app_url = os.getenv('APP_URL', 'http://127.0.0.1:8000').rstrip('/')
        notifications_enabled = os.getenv('NOTIFICATIONS_ENABLED', '0') == '1'
    s = Settings()
    if s.env == 'production':
        if len(s.secret) < 32 or 'dev-only' in s.secret or 'cambiar' in s.secret.lower():
            raise RuntimeError('Configure un SECRET_KEY aleatorio y seguro (32+ caracteres).')
        if not s.session_https:
            raise RuntimeError('En producción SESSION_HTTPS_ONLY=1 y HTTPS son obligatorios.')
        if not s.database_url.startswith(('postgresql+psycopg://','postgresql://')):
            raise RuntimeError('En producción se requiere PostgreSQL.')
        if not s.allowed_hosts.strip() or '*' in [x.strip() for x in s.allowed_hosts.split(',')]:
            raise RuntimeError('Defina ALLOWED_HOSTS de forma restrictiva.')
    return s
