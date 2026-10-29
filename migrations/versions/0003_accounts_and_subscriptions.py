"""accounts and subscriptions

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('app_user',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('discord_id', sa.Text(), nullable=False),
    sa.Column('username', sa.Text(), nullable=False),
    sa.Column('avatar', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('discord_id')
    )
    op.create_table('session',
    sa.Column('token_hash', sa.Text(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('token_hash')
    )
    op.create_index(op.f('ix_session_user_id'), 'session', ['user_id'], unique=False)
    op.create_table('subscription',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('asset_id', sa.Integer(), nullable=True),
    sa.Column('target', sa.Text(), nullable=True),
    sa.Column('webhook_url', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('disabled_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(kind = 'asset') = (asset_id IS NOT NULL) AND (kind = 'asset') = (target IS NULL)", name='kind_target'),
    sa.CheckConstraint("kind IN ('asset', 'area', 'field')", name='kind_valid'),
    sa.ForeignKeyConstraint(['asset_id'], ['asset.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'kind', 'asset_id', 'target', 'webhook_url', name='subscription_unique', postgresql_nulls_not_distinct=True)
    )
    op.create_index(op.f('ix_subscription_user_id'), 'subscription', ['user_id'], unique=False)
    op.create_table('delivery',
    sa.Column('subscription_id', sa.Integer(), nullable=False),
    sa.Column('build_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('sent', 'failed')", name='status_valid'),
    sa.ForeignKeyConstraint(['build_id'], ['build.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['subscription_id'], ['subscription.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('subscription_id', 'build_id')
    )


def downgrade() -> None:
    op.drop_table('delivery')
    op.drop_index(op.f('ix_subscription_user_id'), table_name='subscription')
    op.drop_table('subscription')
    op.drop_index(op.f('ix_session_user_id'), table_name='session')
    op.drop_table('session')
    op.drop_table('app_user')
