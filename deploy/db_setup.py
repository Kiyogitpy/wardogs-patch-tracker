"""One-shot database setup, run by the `migrate` service before the app starts.

1. `alembic upgrade head` as the owner role (MIGRATE_DATABASE_URL).
2. Create or update the restricted role named in DATABASE_URL (default `tracker_app`) and grant it
   DML only: SELECT/INSERT/UPDATE/DELETE on tables, USAGE on sequences, same for future tables
   (the owner's default privileges). No DDL, no superuser, no access to alembic_version.

Idempotent: safe to run on every deploy. The owner's credentials never reach the `app` service.
"""
import os
import subprocess
import sys

import psycopg
from psycopg import sql
from sqlalchemy import make_url


def conn_args(url):
    return dict(host=url.host, port=url.port or 5432, user=url.username, password=url.password, dbname=url.database)


def main() -> int:
    owner_raw, app_raw = os.environ.get("MIGRATE_DATABASE_URL"), os.environ.get("DATABASE_URL")
    if not owner_raw or not app_raw:
        print("MIGRATE_DATABASE_URL and DATABASE_URL must both be set", file=sys.stderr)
        return 2
    if "change-me" in owner_raw + app_raw:
        print("refusing to run with the 'change-me' placeholder password", file=sys.stderr)
        return 2
    owner, app = make_url(owner_raw), make_url(app_raw)
    if owner.username == app.username or not app.password or (owner.host, owner.database) != (app.host, app.database):
        print("DATABASE_URL must be a different role with a password, on the same host and database", file=sys.stderr)
        return 2

    subprocess.run(["alembic", "upgrade", "head"], check=True, env={**os.environ, "DATABASE_URL": owner_raw})

    role, db = sql.Identifier(app.username), sql.Identifier(app.database)
    with psycopg.connect(**conn_args(owner), autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (app.username,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        conn.execute(sql.SQL(f"{verb} ROLE {{}} LOGIN PASSWORD {{}} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION")
                     .format(role, sql.Literal(app.password)))
        for stmt in (
            "GRANT CONNECT ON DATABASE {db} TO {role}",
            "REVOKE CREATE ON SCHEMA public FROM {role}",
            "GRANT USAGE ON SCHEMA public TO {role}",
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}",
            "GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {role}",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE ON SEQUENCES TO {role}",
            "REVOKE ALL ON alembic_version FROM {role}",
        ):
            conn.execute(sql.SQL(stmt).format(role=role, db=db))
    print(f"migrations applied; role {app.username} has DML-only access", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
