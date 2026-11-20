"""Conserva textos originales largos y precarga festivos nacionales 2026–2030.

No modifica ni borra registros históricos existentes.
"""
from alembic import op
import sqlalchemy as sa
from app.holiday_calendar import colombia_holidays

revision = '0003_schema_holidays'
down_revision = '0002_permissions'
branch_labels = None
depends_on = None


def upgrade():
    # PostgreSQL altera solo la restricción de longitud; los índices continúan.
    # batch_alter_table permite probar con SQLite sin cambiar el SQL de producción.
    with op.batch_alter_table('cases') as batch:
        batch.alter_column('period', existing_type=sa.String(length=10),
                           type_=sa.String(length=160), existing_nullable=True)
    with op.batch_alter_table('events') as batch:
        batch.alter_column('raw_case_id', existing_type=sa.String(length=100),
                           type_=sa.String(length=1024), existing_nullable=True)
    with op.batch_alter_table('subtasks') as batch:
        batch.alter_column('raw_case_id', existing_type=sa.String(length=100),
                           type_=sa.String(length=1024), existing_nullable=True)
    with op.batch_alter_table('form_responses') as batch:
        batch.alter_column('raw_case_id', existing_type=sa.String(length=100),
                           type_=sa.String(length=1024), existing_nullable=True)
    calendar = colombia_holidays(2026, 2030)
    holidays = sa.table('holidays', sa.column('day', sa.Date()),
                       sa.column('name', sa.String(length=120)))
    conn = op.get_bind()
    inserted = 0
    for day, name in calendar.items():
        existing = conn.execute(sa.select(holidays.c.day).where(holidays.c.day == day)).scalar_one_or_none()
        if existing is None:
            conn.execute(sa.insert(holidays).values(day=day, name=name))
            inserted += 1
    print(f'CALENDARIO_COLOMBIA_2026_2030: {len(calendar)} fechas, {inserted} agregadas, {len(calendar)-inserted} preexistentes', flush=True)


def downgrade():
    raise RuntimeError('No se permite una reversión que reduzca campos o borre festivos.')
