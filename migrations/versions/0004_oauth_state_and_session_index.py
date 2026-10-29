"""server-side single-use OAuth state; index on session.expires_at

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('oauth_state',
    sa.Column('state_hash', sa.Text(), nullable=False),
    sa.Column('next', sa.Text(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('state_hash')
    )
    op.create_index('ix_oauth_state_expires_at', 'oauth_state', ['expires_at'], unique=False)
    op.create_index('ix_session_expires_at', 'session', ['expires_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_session_expires_at', table_name='session')
    op.drop_index('ix_oauth_state_expires_at', table_name='oauth_state')
    op.drop_table('oauth_state')
