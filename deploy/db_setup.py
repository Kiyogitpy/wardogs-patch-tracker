"""One-shot database setup, run by the `migrate` service before the app starts.

1. `alembic upgrade head` as the owner role (MIGRATE_DATABASE_URL).
2. Create or update the restricted role named in DATABASE_URL (default `tracker_app`) and grant it
   DML only: SELECT/INSERT/UPDATE/DELETE on tables, USAGE on sequences, same for future tables
   (the owner's default privileges). No DDL, no superuser, no access to alembic_version, no TEMP,
   at most 20 connections, statement_timeout 5s.

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


PLACEHOLDERS = {"", "change-me", "changeme", "password"}
MIN_PASSWORD_LENGTH = 16  # same rule as check_environment in src/tracker/server.py
# -1 = no per-role limit. A limit was tried and dropped: on Postgres 16.15 a burst past a role's limit
# crash-restarted the whole server, so the limit itself was a DoS. Load is bounded by the pools and timeouts.
MAX_CONNECTIONS = -1


def bad_password(password) -> bool:
    return password is None or password.strip().lower() in PLACEHOLDERS


def main() -> int:
    owner_raw, app_raw = os.environ.get("MIGRATE_DATABASE_URL"), os.environ.get("DATABASE_URL")
    if not owner_raw or not app_raw:
        print("MIGRATE_DATABASE_URL and DATABASE_URL must both be set", file=sys.stderr)
        return 2
    owner, app = make_url(owner_raw), make_url(app_raw)
    if "change-me" in (owner_raw + app_raw).lower() or bad_password(owner.password) or bad_password(app.password):
        print("refusing to run with an empty, whitespace-only or placeholder password (change-me, changeme, password)",
              file=sys.stderr)
        return 2
    if len(owner.password) < MIN_PASSWORD_LENGTH or len(app.password) < MIN_PASSWORD_LENGTH:
        print(f"both database passwords must be at least {MIN_PASSWORD_LENGTH} characters (openssl rand -hex 24)",
              file=sys.stderr)
        return 2
    if owner.password == app.password:
        print("DATABASE_URL and MIGRATE_DATABASE_URL must use different passwords", file=sys.stderr)
        return 2
    name = (app.username or "").lower()
    if not name or name in (owner.username.lower(), "postgres") or name.startswith("pg_"):
        print("DATABASE_URL must name a dedicated role: not the owner, postgres or a pg_* role", file=sys.stderr)
        return 2
    if (owner.host, owner.database) != (app.host, app.database):
        print("DATABASE_URL must be on the same host and database as MIGRATE_DATABASE_URL", file=sys.stderr)
        return 2

    subprocess.run(["alembic", "upgrade", "head"], check=True, env={**os.environ, "DATABASE_URL": owner_raw})

    role, db = sql.Identifier(app.username), sql.Identifier(app.database)
    with psycopg.connect(**conn_args(owner), autocommit=True) as conn:
        # Keep the password out of the server log if CREATE/ALTER ROLE fails: log no statement text for
        # this session (superuser-only settings, skipped for a plain CREATEROLE owner), and send a SCRAM
        # verifier instead of the plaintext, so the log could never hold the password anyway.
        for setting in ("log_min_error_statement = panic", "log_statement = none", "log_min_duration_statement = -1"):
            try:
                conn.execute(sql.SQL("SET " + setting))
            except psycopg.errors.InsufficientPrivilege:
                pass
        verifier = conn.pgconn.encrypt_password(app.password.encode(), app.username.encode(), b"scram-sha-256").decode()
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (app.username,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        conn.execute(sql.SQL(f"{verb} ROLE {{}} LOGIN PASSWORD {{}} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
                             f"CONNECTION LIMIT {MAX_CONNECTIONS}").format(role, sql.Literal(verifier)))
        for stmt in (
            "GRANT CONNECT ON DATABASE {db} TO {role}",
            "REVOKE TEMP ON DATABASE {db} FROM PUBLIC",
            "REVOKE TEMP ON DATABASE {db} FROM {role}",
            "ALTER ROLE {role} SET statement_timeout = '5s'",
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
