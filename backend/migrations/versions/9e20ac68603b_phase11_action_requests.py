"""phase11 action requests

Revision ID: 9e20ac68603b
Revises: 8822524ef06f
Create Date: 2026-09-15 13:58:08.914271
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '9e20ac68603b'
down_revision: str | None = '8822524ef06f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('action_requests',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=False),
    sa.Column('conversation_id', sa.Uuid(), nullable=True),
    sa.Column('tool_id', sa.String(length=200), nullable=False),
    sa.Column('autonomy', sa.Enum('automatic', 'policy', 'explicit_action', name='agent_tool_autonomy', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('execution_class', sa.Enum('local', 'remote', name='tool_execution_class', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('side_effect_class', sa.Enum('read_only', 'creates_derived_result', 'domain_mutation', 'external_action', name='tool_side_effect_class', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('arguments', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('arguments_digest', sa.String(length=64), nullable=False),
    sa.Column('status', sa.Enum('pending', 'executing', 'succeeded', 'failed', 'ambiguous', 'rejected', name='action_request_status', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('status_reason', sa.String(length=500), nullable=True),
    sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('result_run_id', sa.Uuid(), nullable=True),
    sa.Column('result_decision_id', sa.Uuid(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['actors.actor_id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['conversation_id'], ['project_conversations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['result_decision_id'], ['decisions.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['result_run_id'], ['global_resource_registry.resource_id'], ondelete='SET NULL'),
    sa.CheckConstraint("status <> 'succeeded' OR result_run_id IS NOT NULL OR result_decision_id IS NOT NULL", name='ck_action_succeeded_has_result'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_action_requests_actor_id'), 'action_requests', ['actor_id'], unique=False)
    op.create_index('ix_action_requests_actor_project', 'action_requests', ['actor_id', 'project_id'], unique=False)
    op.create_index(op.f('ix_action_requests_conversation_id'), 'action_requests', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_action_requests_project_id'), 'action_requests', ['project_id'], unique=False)
    op.create_index(op.f('ix_action_requests_tool_id'), 'action_requests', ['tool_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_action_requests_tool_id'), table_name='action_requests')
    op.drop_index(op.f('ix_action_requests_project_id'), table_name='action_requests')
    op.drop_index(op.f('ix_action_requests_conversation_id'), table_name='action_requests')
    op.drop_index('ix_action_requests_actor_project', table_name='action_requests')
    op.drop_index(op.f('ix_action_requests_actor_id'), table_name='action_requests')
    op.drop_table('action_requests')
