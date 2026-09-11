"""phase9 persistent project conversations

Revision ID: 6c88c6688930
Revises: c9a41f2d3e8b
Create Date: 2026-09-10 16:03:13.059069
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '6c88c6688930'
down_revision: str | None = 'c9a41f2d3e8b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'project_conversations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('actor_id', sa.Uuid(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['actor_id'], ['actors.actor_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_project_conversations_actor_id'), 'project_conversations', ['actor_id'], unique=False)
    op.create_index(op.f('ix_project_conversations_project_id'), 'project_conversations', ['project_id'], unique=False)

    op.create_table(
        'conversation_messages',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('conversation_id', sa.Uuid(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('role', sa.Enum('user', 'assistant', name='conversation_role', native_enum=False, create_constraint=True), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('termination_reason', sa.Enum('final_response', 'max_model_turns', 'max_tool_calls', 'total_duration', 'model_unavailable', name='agent_termination_reason', native_enum=False, create_constraint=True), nullable=True),
        sa.Column('tool_trace_summary', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['project_conversations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('conversation_id', 'seq', name='uq_conversation_message_seq'),
    )
    op.create_index(op.f('ix_conversation_messages_conversation_id'), 'conversation_messages', ['conversation_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_conversation_messages_conversation_id'), table_name='conversation_messages')
    op.drop_table('conversation_messages')
    op.drop_index(op.f('ix_project_conversations_project_id'), table_name='project_conversations')
    op.drop_index(op.f('ix_project_conversations_actor_id'), table_name='project_conversations')
    op.drop_table('project_conversations')
