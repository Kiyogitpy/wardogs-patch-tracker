import os

from alembic import context
from sqlalchemy import create_engine

from tracker.db import metadata

config = context.config
# Tests pass their own URL through the config; everything else reads DATABASE_URL.
url = config.attributes.get("url") or os.environ["DATABASE_URL"]

with create_engine(url).connect() as conn:
    context.configure(connection=conn, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()
