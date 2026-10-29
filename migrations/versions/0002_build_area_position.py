"""build_area.position: area order from index.json

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing rows get 0; re-ingest the build to restore the order.
    op.add_column('build_area', sa.Column('position', sa.Integer(), nullable=False, server_default='0'))
    op.alter_column('build_area', 'position', server_default=None)


def downgrade() -> None:
    op.drop_column('build_area', 'position')
