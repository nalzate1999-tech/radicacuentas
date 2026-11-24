"""Especialidades editables del directorio de usuarios."""
from alembic import op
import sqlalchemy as sa
revision = '0008_review_types'
down_revision = '0007_identity_matching'
branch_labels = None
depends_on = None

def upgrade():
    columns={column['name'] for column in sa.inspect(op.get_bind()).get_columns('users')}
    if 'review_types' not in columns:
        op.add_column('users', sa.Column('review_types', sa.JSON(), nullable=True))

def downgrade():
    op.drop_column('users', 'review_types')
