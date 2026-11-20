"""Creación inicial del modelo completo (registro, eventos, migración, auditoría)."""
from alembic import op
from app.database import Base
from app import models  # noqa: F401: registra las tablas
revision='0001_base'
down_revision=None
branch_labels=None
depends_on=None

def upgrade():
    # Se crea el conjunto íntegro de tablas con sus índices y restricciones.
    Base.metadata.create_all(bind=op.get_bind())

def downgrade():
    # No se permite un downgrade destructivo accidental de un sistema con datos.
    raise RuntimeError('No se permite borrar el histórico desde Alembic.')
