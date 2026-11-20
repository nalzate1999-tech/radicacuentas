"""Remove empty migration placeholders, never valid historical cases.

One-time data correction for user-identified MIG-CASOS-FILA artifacts.
Original imported source rows remain preserved in the archival tables.
"""
import re
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = '0005_cleanup_mig_cases'
down_revision = '0004_remove_selected_mig_cases'
branch_labels = None
depends_on = None

PLACEHOLDER = re.compile(r'^MIG-CASOS-FILA-[0-9]{6}$')


def empty(value):
    return value is None or (isinstance(value, str) and not value.strip())


def upgrade():
    bind = op.get_bind()
    cases = sa.table(
        'cases',
        sa.column('id_caso', sa.String(length=65)),
        sa.column('source', sa.String(length=20)),
        sa.column('needs_reconciliation', sa.Boolean()),
        sa.column('contractor_name', sa.String(length=240)),
        sa.column('document', sa.String(length=90)),
        sa.column('contract_no', sa.String(length=100)),
        sa.column('contractor_email', sa.String(length=250)),
        sa.column('period', sa.String(length=160)),
        sa.column('registered_at', sa.DateTime(timezone=True)),
        sa.column('received_at', sa.DateTime(timezone=True)),
        sa.column('amount', sa.Numeric(18, 2)),
        sa.column('original_values', sa.JSON()),
    )
    children = (
        sa.table('events', sa.column('case_id', sa.String(length=65))),
        sa.table('subtasks', sa.column('case_id', sa.String(length=65))),
        sa.table('form_responses', sa.column('case_id', sa.String(length=65))),
    )
    notifications = sa.table(
        'notifications',
        sa.column('case_id', sa.String(length=65)),
    )
    audits = sa.table(
        'audits',
        sa.column('created_at', sa.DateTime(timezone=True)),
        sa.column('actor', sa.String(length=260)),
        sa.column('action', sa.String(length=100)),
        sa.column('target', sa.String(length=130)),
        sa.column('detail', sa.JSON()),
    )

    all_rows = bind.execute(
        sa.select(cases)
        .where(cases.c.id_caso.like('MIG-CASOS-FILA-%'))
        .order_by(cases.c.id_caso)
    ).mappings().all()
    candidates = [row for row in all_rows if PLACEHOLDER.fullmatch(row['id_caso'])]
    removed = []
    skipped = {}

    for row in candidates:
        case_id = row['id_caso']
        # Preserve records linked to an identifiable contractor or actual registration.
        required_empty = (
            'contractor_name', 'document', 'contract_no', 'contractor_email', 'period'
        )
        if (
            row['source'] != 'import'
            or row['needs_reconciliation'] is not True
            or any(not empty(row[field]) for field in required_empty)
            or row['registered_at'] is not None
            or row['received_at'] is not None
            or row['amount'] not in (None, 0)
        ):
            skipped[case_id] = 'CON_DATOS_O_PROTEGIDO'
            continue

        # Never delete a case that has movements, forms or subtasks linked to it.
        linked = {}
        for child in children:
            linked[child.name] = bind.execute(
                sa.select(sa.func.count()).select_from(child)
                .where(child.c.case_id == case_id)
            ).scalar_one()
        if any(linked.values()):
            skipped[case_id] = 'CON_TRAZABILIDAD_VINCULADA'
            continue

        # An audit snapshot permits tracing/restoration from original imported rows.
        # All changes run in Alembic's single PostgreSQL transaction.
        bind.execute(
            sa.insert(audits).values(
                actor='system-migration',
                action='RETIRAR_CASO_TEMPORAL_VACIO',
                target=case_id,
                created_at=datetime.now(timezone.utc),
                detail={
                    'reason': 'Temporary blank case generated from historical sheet',
                    'original_values': row['original_values'],
                    'linked_records': linked,
                },
            )
        )
        canceled = bind.execute(
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
            raise RuntimeError('Could not remove exactly one blank placeholder: ' + case_id)
        removed.append(case_id)
        print(f'RETIRO_MIG_VACIO: {case_id}: ELIMINADO '
              f'(notificaciones_canceladas={canceled})', flush=True)

    remaining = bind.execute(
        sa.select(cases.c.id_caso).where(cases.c.id_caso.like('MIG-CASOS-FILA-%'))
    ).scalars().all()
    unexpected = [
        case_id for case_id in remaining
        if PLACEHOLDER.fullmatch(case_id) and case_id not in skipped
    ]
    if unexpected:
        raise RuntimeError('Unexpected temporary cases still present: ' + str(len(unexpected)))

    for case_id, reason in skipped.items():
        print(f'RETIRO_MIG_VACIO: {case_id}: CONSERVADO_{reason}', flush=True)
    print(
        f'RETIRO_MIG_VACIO_RESUMEN: candidatos={len(candidates)} '
        f'eliminados={len(removed)} protegidos={len(skipped)} '
        f'restantes={len(remaining)}',
        flush=True,
    )


def downgrade():
    raise RuntimeError('Deleted cases require recovery from the retained import source.')
