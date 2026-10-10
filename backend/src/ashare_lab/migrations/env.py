from alembic import context
from ashare_lab.config import Settings
from ashare_lab.db import Database, metadata
from sqlalchemy.engine import Connection


def run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


provided = context.config.attributes.get("connection")
if provided is not None:
    run(provided)
else:
    database = Database(Settings().database_path)
    with database.engine.connect() as connection:
        run(connection)
