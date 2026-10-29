"""initial schema

Revision ID: 0001
Revises: 
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')  # ix_change_field_path_trgm
    op.create_table('asset',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('path', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('type', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('path')
    )
    op.create_table('build',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('label', sa.Text(), nullable=False),
    sa.Column('prev_build_id', sa.Integer(), nullable=True),
    sa.Column('steam_buildid', sa.Text(), nullable=True),
    sa.Column('manifest_fingerprint', sa.Text(), nullable=True),
    sa.Column('released_at', sa.Date(), nullable=True),
    sa.Column('ingested_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['prev_build_id'], ['build.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('label')
    )
    op.create_table('asset_alias',
    sa.Column('old_path', sa.Text(), nullable=False),
    sa.Column('build_id', sa.Integer(), nullable=False),
    sa.Column('asset_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['asset_id'], ['asset.id'], ),
    sa.ForeignKeyConstraint(['build_id'], ['build.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('old_path', 'build_id')
    )
    op.create_table('asset_status',
    sa.Column('build_id', sa.Integer(), nullable=False),
    sa.Column('asset_id', sa.Integer(), nullable=False),
    sa.Column('area', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('paired_by', sa.Text(), nullable=True),
    sa.Column('exports', sa.Integer(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('noise', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.CheckConstraint("status IN ('added', 'removed', 'modified', 'moved')", name='status_valid'),
    sa.ForeignKeyConstraint(['asset_id'], ['asset.id'], ),
    sa.ForeignKeyConstraint(['build_id'], ['build.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('build_id', 'asset_id')
    )
    op.create_table('build_area',
    sa.Column('build_id', sa.Integer(), nullable=False),
    sa.Column('area', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('counts', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.ForeignKeyConstraint(['build_id'], ['build.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('build_id', 'area')
    )
    op.create_table('change',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('build_id', sa.Integer(), nullable=False),
    sa.Column('asset_id', sa.Integer(), nullable=False),
    sa.Column('field_path', sa.Text(), nullable=False),
    sa.Column('op', sa.Text(), nullable=False),
    sa.Column('before', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('after', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.CheckConstraint("op IN ('added', 'removed', 'changed', 'resequenced')", name='op_valid'),
    sa.ForeignKeyConstraint(['asset_id'], ['asset.id'], ),
    sa.ForeignKeyConstraint(['build_id'], ['build.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_change_build', 'change', ['build_id'], unique=False)
    op.create_index('ix_change_field_path_trgm', 'change', ['field_path'], unique=False, postgresql_using='gin', postgresql_ops={'field_path': 'gin_trgm_ops'})
    op.create_index('ix_change_history', 'change', ['asset_id', 'field_path', 'build_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_change_history', table_name='change')
    op.drop_index('ix_change_field_path_trgm', table_name='change', postgresql_using='gin', postgresql_ops={'field_path': 'gin_trgm_ops'})
    op.drop_index('ix_change_build', table_name='change')
    op.drop_table('change')
    op.drop_table('build_area')
    op.drop_table('asset_status')
    op.drop_table('asset_alias')
    op.drop_table('build')
    op.drop_table('asset')
