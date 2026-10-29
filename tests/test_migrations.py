import sqlalchemy
from alembic import command
from alembic.config import Config

from conftest import ROOT, URL


def test_migration_env_disposes_its_engine(engine, monkeypatch):
    made = []
    real = sqlalchemy.create_engine

    def spy(*args, **kwargs):
        made.append(real(*args, **kwargs))
        return made[-1]

    monkeypatch.setattr(sqlalchemy, "create_engine", spy)
    config = Config(ROOT / "alembic.ini")
    config.attributes["url"] = URL.render_as_string(hide_password=False)
    command.upgrade(config, "head")  # already at head: env.py still runs and connects
    assert len(made) == 1 and made[0].pool.checkedin() == 0 and made[0].pool.checkedout() == 0
