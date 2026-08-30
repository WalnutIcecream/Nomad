"""initial schema

Revision ID: 5d46be5c7038
Revises: 
Create Date: 2026-08-17 10:25:00.860543

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '5d46be5c7038'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('username')
    )
    op.create_table('worlds',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('owner_id', sa.Uuid(), nullable=False),
    sa.Column('latest_version_id', sa.Uuid(), nullable=True),
    sa.Column('current_host_id', sa.Uuid(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('minecraft_version', sa.String(length=32), nullable=False),
    sa.Column('server_software', sa.String(length=32), nullable=False),
    sa.Column('configuration', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('storage_backend', sa.String(length=32), nullable=False),
    sa.Column('storage_config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['current_host_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('world_versions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('world_id', sa.Uuid(), nullable=False),
    sa.Column('version_number', sa.BigInteger(), nullable=False),
    sa.Column('storage_key', sa.Text(), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=False),
    sa.Column('minecraft_version', sa.String(length=32), nullable=False),
    sa.Column('server_version', sa.String(length=64), nullable=True),
    sa.Column('sha256', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['world_id'], ['worlds.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_foreign_key(
        'fk_worlds_latest_version',
        'worlds',
        'world_versions',
        ['latest_version_id'],
        ['id'],
    )
    op.create_table('host_leases',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('world_id', sa.Uuid(), nullable=False),
    sa.Column('host_user_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['host_user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['world_id'], ['worlds.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('one_active_lease_per_world', 'host_leases', ['world_id'], unique=True, postgresql_where=sa.text("status = 'active'"))
    op.create_table('sessions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('token_hash', sa.Text(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_table('world_members',
    sa.Column('world_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['world_id'], ['worlds.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('world_id', 'user_id')
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('world_members')
    op.drop_table('sessions')
    op.drop_index('one_active_lease_per_world', table_name='host_leases', postgresql_where=sa.text("status = 'active'"))
    op.drop_table('host_leases')
    op.drop_constraint('fk_worlds_latest_version', 'worlds', type_='foreignkey')
    op.drop_table('worlds')
    op.drop_table('world_versions')
    op.drop_table('users')
