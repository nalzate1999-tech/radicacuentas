"""Permisos por formulario y activación individual sin exposición de contraseñas."""
from alembic import op
import sqlalchemy as sa
revision = '0002_permissions'
down_revision = '0001_base'
branch_labels = None
depends_on = None

def upgrade():
    # 0001 usa metadata completa: en instalaciones nuevas estas columnas pueden
    # existir ya. En bases antiguas se añaden solo las que falten.
    insp = sa.inspect(op.get_bind())
    columns = {col['name'] for col in insp.get_columns('users')}
    if 'form_permissions' not in columns:
        op.add_column('users', sa.Column('form_permissions', sa.JSON(), nullable=True))
    if 'invite_token_hash' not in columns:
        op.add_column('users', sa.Column('invite_token_hash', sa.String(length=64), nullable=True))
    if 'invite_expires_at' not in columns:
        op.add_column('users', sa.Column('invite_expires_at', sa.DateTime(timezone=True), nullable=True))
    indexes = {idx['name'] for idx in sa.inspect(op.get_bind()).get_indexes('users')}
    if 'ix_users_invite_token_hash' not in indexes:
        op.create_index('ix_users_invite_token_hash', 'users', ['invite_token_hash'], unique=False)

def downgrade():
    raise RuntimeError('No se permite eliminación destructiva de permisos o invitaciones.')
