from sqlalchemy import Connection, Engine, create_engine

from alembic import context
from keitaro_wrapper.config import Settings
from keitaro_wrapper.infrastructure.editor import EditorRow  # noqa: F401
from keitaro_wrapper.infrastructure.persistence import Base

config: Settings = Settings()


def run_migrations() -> None:
    """Применяет миграции к URL окружения онлайн либо генерирует SQL офлайн."""
    if context.is_offline_mode():
        context.configure(
            url=config.database_url,
            target_metadata=Base.metadata,
            literal_binds=True,
            dialect_opts={'paramstyle': 'named'},
        )
        with context.begin_transaction():
            context.run_migrations()
    else:
        engine: Engine = create_engine(config.database_url)
        with engine.connect() as connection:
            connection: Connection
            context.configure(connection=connection, target_metadata=Base.metadata)
            with context.begin_transaction():
                context.run_migrations()
        engine.dispose()


run_migrations()
