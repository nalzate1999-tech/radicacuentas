"""Vincula expedientes historicos a cuentas sin reescribir datos originales."""
from alembic import op
from sqlalchemy.orm import Session
from app.models import UserAlias, CaseAssignment
from app.identity import rebuild_assignments

revision = '0007_identity_matching'
down_revision = '0006_delete_mig_001305'
branch_labels = None
depends_on = None

def upgrade():
    connection = op.get_bind()
    UserAlias.__table__.create(bind=connection, checkfirst=True)
    CaseAssignment.__table__.create(bind=connection, checkfirst=True)
    with Session(bind=connection, autoflush=False) as db:
        summary = rebuild_assignments(db)
        db.flush()
        print('VINCULACION_IDENTIDADES: casos={cases} gestiones={assigned} '
              'ambiguas={ambiguous}'.format(**summary), flush=True)

def downgrade():
    raise RuntimeError('No se permite borrar los vinculos de identidad con expedientes.')
