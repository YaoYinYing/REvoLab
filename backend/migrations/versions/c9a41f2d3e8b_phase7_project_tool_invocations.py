"""phase7 project tool invocations

Revision ID: c9a41f2d3e8b
Revises: fdd753899c9f
Create Date: 2026-09-09 20:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c9a41f2d3e8b'
down_revision: str | None = 'fdd753899c9f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'tool_invocations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('tool_id', sa.String(length=200), nullable=False),
        sa.Column('tool_version', sa.String(length=50), nullable=False),
        sa.Column('actor_id', sa.Uuid(), nullable=True),
        sa.Column('input_resource_ids', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('parameters', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('result_kind', sa.String(length=50), nullable=False),
        sa.Column('result_resource_id', sa.Uuid(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['actor_id'], ['actors.actor_id']),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['result_resource_id'], ['global_resource_registry.resource_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_tool_invocations_project_id'), 'tool_invocations', ['project_id'], unique=False)
    op.create_index(op.f('ix_tool_invocations_tool_id'), 'tool_invocations', ['tool_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_tool_invocations_tool_id'), table_name='tool_invocations')
    op.drop_index(op.f('ix_tool_invocations_project_id'), table_name='tool_invocations')
    op.drop_table('tool_invocations')
