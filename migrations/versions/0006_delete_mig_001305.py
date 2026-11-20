"""Remove the single reconciliable placeholder requested by the administrator.

The record is backed up in audit. Linked historical records are retained
and detached so the deletion does not cascade into real event history.
"""
from datetime import date, datetime, timezone
from decimal import Decimal
from alembic import op
import sqlalchemy as sa

revision = '0006_delete_mig_001305'
down_revision = '0005_cleanup_mig_cases'
branch_labels = None
depends_on = None

TARGET = 'MIG-CASOS-FILA-001305'


def json_safe(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def upgrade():
    db = op.get_bind()
    metadata = sa.MetaData()
    # Reflect the actual production schema and retain an exact pre-deletion record.
    case_table = sa.Table('cases', metadata, autoload_with=db)
    events = sa.Table('events', metadata, autoload_with=db)
    subtasks = sa.Table('subtasks', metadata, autoload_with=db)
    responses = sa.Table('form_responses', metadata, autoload_with=db)
    notifications = sa.Table('notifications', metadata, autoload_with=db)
    audits = sa.Table('audits', metadata, autoload_with=db)

    row = db.execute(
        sa.select(case_table).where(case_table.c.id_caso == TARGET)
    ).mappings().one_or_none()
    if row is None:
        print('RETIRO_MIG_001305: YA_AUSENTE; casos_eliminados=0', flush=True)
        return
    if row['source'] != 'import':
        raise RuntimeError('Target ID no longer belongs to an imported case. No deletion performed.')

    linked = {}
    for name, table in (
        ('events', events),
        ('subtasks', subtasks),
        ('form_responses', responses),
    ):
        # Keep movement/form data; only unlink the invalid placeholder ID.
        count = db.execute(
            sa.update(table)
            .where(table.c.case_id == TARGET)
            .values(case_id=None)
        ).rowcount
        linked[name] = count

    canceled = db.execute(
        sa.delete(notifications).where(notifications.c.case_id == TARGET)
    ).rowcount

    # Full case snapshot kept exclusively in the restricted audit log.
    db.execute(
        sa.insert(audits).values(
            created_at=datetime.now(timezone.utc),
            actor='system-migration',
            action='RETIRAR_CASO_POR_SOLICITUD',
            target=TARGET,
            detail={
                'motivo': 'Eliminacion explicita del administrador del aplicativo',
                'registro_original': json_safe(dict(row)),
                'relaciones_historicas_desvinculadas': linked,
                'notificaciones_canceladas': canceled,
            },
        )
    )

    result = db.execute(
        sa.delete(case_table).where(
            case_table.c.id_caso == TARGET,
            case_table.c.source == 'import',
        )
    )
    if result.rowcount != 1:
        raise RuntimeError('Expected exactly one deletion; rolling back the transaction.')
    remains = db.execute(
        sa.select(sa.func.count())
        .select_from(case_table)
        .where(case_table.c.id_caso == TARGET)
    ).scalar_one()
    if remains != 0:
        raise RuntimeError('Target case was not completely removed.')

    print('RETIRO_MIG_001305: ELIMINADO; casos_eliminados=1; '
          f'vinculos_historicos_desvinculados={sum(linked.values())}; '
          f'notificaciones_canceladas={canceled}; restantes=0', flush=True)


def downgrade():
    raise RuntimeError('Automatic downgrade would risk restoring an invalid case.')
