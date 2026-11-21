"""Добавляет постоянные черновики и архив офферов."""

import sqlalchemy as sa

from alembic import op

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Создаёт хранилище редактора с версиями для конкурентных запросов."""
    op.create_table(
        'stream_drafts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('campaign_id', sa.Integer(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
    )
    op.create_index('ix_stream_drafts_campaign_id', 'stream_drafts', ['campaign_id'])


def downgrade() -> None:
    """Удаляет локальные черновики, не изменяя Keitaro."""
    op.drop_index('ix_stream_drafts_campaign_id', table_name='stream_drafts')
    op.drop_table('stream_drafts')
