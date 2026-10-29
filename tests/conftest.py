import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text

ROOT = Path(__file__).parent.parent
URL = make_url(os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://tracker:tracker@127.0.0.1:5433/tracker_test"))


@pytest.fixture(scope="session")
def engine():
    """A fresh database built by the migrations, so the tests also prove the migrations."""
    with create_engine(URL.set(database="postgres"), isolation_level="AUTOCOMMIT").connect() as admin:
        admin.execute(text(f'DROP DATABASE IF EXISTS "{URL.database}" WITH (FORCE)'))
        admin.execute(text(f'CREATE DATABASE "{URL.database}"'))
    config = Config(ROOT / "alembic.ini")
    config.attributes["url"] = URL.render_as_string(hide_password=False)
    command.upgrade(config, "head")
    engine = create_engine(URL)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine):
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE build, asset, asset_alias, asset_status, change, build_area RESTART IDENTITY CASCADE"))
    return engine


@pytest.fixture
def fixtures():
    return Path(__file__).parent / "fixtures"
