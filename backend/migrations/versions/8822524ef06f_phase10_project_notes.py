"""phase10 project notes

Revision ID: 8822524ef06f
Revises: 6c88c6688930
Create Date: 2026-09-14 17:54:55.183341
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '8822524ef06f'
down_revision: str | None = '6c88c6688930'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('project_notes',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('created_by_actor_id', sa.Uuid(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['created_by_actor_id'], ['actors.actor_id']),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_project_notes_created_by_actor_id'), 'project_notes', ['created_by_actor_id'], unique=False)
    op.create_index(op.f('ix_project_notes_project_id'), 'project_notes', ['project_id'], unique=False)
    op.create_table('project_note_revisions',
    sa.Column('revision_id', sa.Uuid(), nullable=False),
    sa.Column('note_id', sa.Uuid(), nullable=False),
    sa.Column('revision_seq', sa.Integer(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('created_by_actor_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['created_by_actor_id'], ['actors.actor_id']),
    sa.ForeignKeyConstraint(['note_id'], ['project_notes.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('revision_id'),
    sa.UniqueConstraint('note_id', 'revision_seq', name='uq_note_revision_seq')
    )
    op.create_index(op.f('ix_project_note_revisions_created_by_actor_id'), 'project_note_revisions', ['created_by_actor_id'], unique=False)
    op.create_index(op.f('ix_project_note_revisions_note_id'), 'project_note_revisions', ['note_id'], unique=False)
    op.create_table('note_mentions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('revision_id', sa.Uuid(), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('target_resource_id', sa.Uuid(), nullable=True),
    sa.Column('target_kind', sa.Enum('scientific_object_series', 'scientific_object_revision', 'run_reference', 'session_reference', 'artifact_reference', 'literature_reference', 'external_reference', name='resource_kind', native_enum=False), nullable=True),
    sa.Column('target_evidence_id', sa.Uuid(), nullable=True),
    sa.Column('target_decision_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint('(CASE WHEN target_resource_id IS NOT NULL THEN 1 ELSE 0 END + CASE WHEN target_evidence_id IS NOT NULL THEN 1 ELSE 0 END + CASE WHEN target_decision_id IS NOT NULL THEN 1 ELSE 0 END) = 1', name='ck_note_mention_exactly_one_target'),
    sa.ForeignKeyConstraint(['revision_id'], ['project_note_revisions.revision_id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['target_decision_id'], ['decisions.id']),
    sa.ForeignKeyConstraint(['target_evidence_id'], ['evidence.id']),
    sa.ForeignKeyConstraint(['target_resource_id'], ['global_resource_registry.resource_id']),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('revision_id', 'ordinal', name='uq_note_mention_ordinal')
    )
    op.create_index(op.f('ix_note_mentions_revision_id'), 'note_mentions', ['revision_id'], unique=False)
    op.create_index(op.f('ix_note_mentions_target_decision_id'), 'note_mentions', ['target_decision_id'], unique=False)
    op.create_index(op.f('ix_note_mentions_target_evidence_id'), 'note_mentions', ['target_evidence_id'], unique=False)
    op.create_index(op.f('ix_note_mentions_target_resource_id'), 'note_mentions', ['target_resource_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_note_mentions_target_resource_id'), table_name='note_mentions')
    op.drop_index(op.f('ix_note_mentions_target_evidence_id'), table_name='note_mentions')
    op.drop_index(op.f('ix_note_mentions_target_decision_id'), table_name='note_mentions')
    op.drop_index(op.f('ix_note_mentions_revision_id'), table_name='note_mentions')
    op.drop_table('note_mentions')
    op.drop_index(op.f('ix_project_note_revisions_note_id'), table_name='project_note_revisions')
    op.drop_index(op.f('ix_project_note_revisions_created_by_actor_id'), table_name='project_note_revisions')
    op.drop_table('project_note_revisions')
    op.drop_index(op.f('ix_project_notes_project_id'), table_name='project_notes')
    op.drop_index(op.f('ix_project_notes_created_by_actor_id'), table_name='project_notes')
    op.drop_table('project_notes')
