"""Permisos por formulario y activación individual sin exposición de contraseñas."""
from alembic import op
import sqlalchemy as sa
revision = '0002_permissions'
down_revision = '0001_base'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('users',sa.Column('form_permissions',sa.JSON(),nullable=True))
    op.add_column('users',sa.Column('invite_token_hash',sa.String(length=64),nullable=True))
    op.add_column('users',sa.Column('invite_expires_at',sa.DateTime(timezone=True),nullable=True))
    op.create_index('ix_users_invite_token_hash','users',['invite_token_hash'],unique=False)

def downgrade():
    raise RuntimeError('No se permite eliminación destructiva de permisos o invitaciones.')
