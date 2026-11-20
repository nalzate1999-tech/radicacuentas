"""Remove only seven user-identified erroneous migration placeholders.

Related historical events, subtasks and form responses are detached, not erased.
This is an intentional, one-time data correction approved by the user.
"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = '0004_remove_selected_mig_cases'
down_revision = '0003_schema_holidays'
branch_labels = None
depends_on = None

TARGET_IDS = (
    'MIG-CASOS-FILA-001302',
    'MIG-CASOS-FILA-001300',
    'MIG-CASOS-FILA-001288',
    'MIG-CASOS-FILA-001287',
    'MIG-CASOS-FILA-001286',
    'MIG-CASOS-FILA-001284',
    'MIG-CASOS-FILA-001277',
)


def upgrade():
    bind = op.get_bind()
    cases = sa.table(
        'cases',
        sa.column('id_caso', sa.String(length=65)),
        sa.column('source', sa.String(length=20)),
        sa.column('needs_reconciliation', sa.Boolean()),
    )
    children = (
        sa.table('events', sa.column('case_id', sa.String(length=65))),
        sa.table('subtasks', sa.column('case_id', sa.String(length=65))),
        sa.table('form_responses', sa.column('case_id', sa.String(length=65))),
    )
    notifications = sa.table('notifications', sa.column('case_id', sa.String(length=65)))
    audits = sa.table(
        'audits',
        sa.column('actor', sa.String(length=260)),
        sa.column('action', sa.String(length=100)),
        sa.column('target', sa.String(length=130)),
        sa.column('detail', sa.JSON()),
        sa.column('created_at', sa.DateTime(timezone=True)),
    )
    removed = 0
    missing = 0
    protected = 0
    total_linked = 0
    for case_id in TARGET_IDS:
        row = bind.execute(
            sa.select(cases.c.id_caso, cases.c.source, cases.c.needs_reconciliation)
            .where(cases.c.id_caso == case_id)
        ).mappings().first()
        if row is None:
            missing += 1
            print(f'RETIRO_CASOS_MIG: {case_id}: YA_AUSENTE', flush=True)
            continue
        # Preserve any non-imported case or a case reconciled by a human.
        if row['source'] != 'import' or row['needs_reconciliation'] is not True:
            protected += 1
            print(f'RETIRO_CASOS_MIG: {case_id}: PROTEGIDO', flush=True)
            continue

        # Preserve source history without retaining a link to the deleted case.
        detached = {}
        for child in children:
            result = bind.execute(
                sa.update(child).where(child.c.case_id == case_id).values(case_id=None)
            )
            detached[child.name] = result.rowcount
            total_linked += result.rowcount

        # Never send notifications for a deleted placeholder.
        deleted_notifications = bind.execute(
            sa.delete(notifications).where(notifications.c.case_id == case_id)
        ).rowcount

        result = bind.execute(
            sa.delete(cases).where(
                cases.c.id_caso == case_id,
                cases.c.source == 'import',
                cases.c.needs_reconciliation.is_(True),
            )
        )
        if result.rowcount != 1:
            raise RuntimeError('Could not remove exactly one intended placeholder: ' + case_id)
        removed += 1
        bind.execute(
            sa.insert(audits).values(
                actor='system-migration',
                action='RETIRAR_CASO_TEMPORAL',
                target=case_id,
                created_at=datetime.now(timezone.utc),
                detail={
                    'reason': 'User requested removal of incorrect migration placeholder',
                    'detached_historical_links': detached,
                    'canceled_notifications': deleted_notifications,
                },
            )
        )
        print(f'RETIRO_CASOS_MIG: {case_id}: ELIMINADO, '
              f'referencias_historicas={sum(detached.values())}, '
              f'notificaciones={deleted_notifications}', flush=True)

    remaining = bind.execute(
        sa.select(cases.c.id_caso).where(cases.c.id_caso.in_(TARGET_IDS))
    ).scalars().all()
    if len(remaining) != protected:
        raise RuntimeError('Unexpected cases remaining after targeted cleanup')
    print(f'RETIRO_CASOS_MIG_RESUMEN: solicitados=7 eliminados={removed} '
          f'ya_ausentes={missing} protegidos={protected} '
          f'referencias_historicas_desvinculadas={total_linked} '
          f'restantes={len(remaining)}', flush=True)


def downgrade():
    raise RuntimeError('Deleted cases cannot be automatically restored without backup.')
