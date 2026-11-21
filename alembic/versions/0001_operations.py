"""Создаёт журнал операций создания кампаний."""

import sqlalchemy as sa

from alembic import op

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Создаёт таблицу с ключом идемпотентности и ID ресурсов Keitaro."""
    op.create_table(
        'operations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('country_code', sa.String(2), nullable=False),
        sa.Column('offer_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('campaign_id', sa.Integer(), nullable=True),
        sa.Column('google_stream_id', sa.Integer(), nullable=True),
        sa.Column('offer_stream_id', sa.Integer(), nullable=True),
        sa.Column('url', sa.String(1024), nullable=True),
        sa.Column('error', sa.String(2048), nullable=True),
    )


def downgrade() -> None:
    """Удаляет журнал операций при явном откате миграции."""
    op.drop_table('operations')
