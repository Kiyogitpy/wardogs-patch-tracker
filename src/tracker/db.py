from sqlalchemy import (
    BigInteger, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer,
    MetaData, Table, Text,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

ASSET_STATUSES = ("added", "removed", "modified", "moved")
CHANGE_OPS = ("added", "removed", "changed", "resequenced")


def _in(column, values):
    return CheckConstraint(f"{column} IN ({', '.join(repr(v) for v in values)})", name=f"{column}_valid")


build = Table(
    "build", metadata,
    Column("id", Integer, primary_key=True),
    Column("label", Text, nullable=False, unique=True),
    # The build this one was diffed against; NULL for a base build with no ingested diff.
    Column("prev_build_id", Integer, ForeignKey("build.id")),
    Column("steam_buildid", Text),
    Column("manifest_fingerprint", Text),
    Column("released_at", Date),
    Column("ingested_at", DateTime(timezone=True)),
)

# One row per game asset, keyed by its current path. Moves rename the row, so history follows the asset.
asset = Table(
    "asset", metadata,
    Column("id", Integer, primary_key=True),
    Column("path", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("type", Text, nullable=False),
)

asset_alias = Table(
    "asset_alias", metadata,
    Column("old_path", Text, primary_key=True),
    Column("build_id", Integer, ForeignKey("build.id", ondelete="CASCADE"), primary_key=True),
    Column("asset_id", Integer, ForeignKey("asset.id"), nullable=False),
)

asset_status = Table(
    "asset_status", metadata,
    Column("build_id", Integer, ForeignKey("build.id", ondelete="CASCADE"), primary_key=True),
    Column("asset_id", Integer, ForeignKey("asset.id"), primary_key=True),
    Column("area", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("paired_by", Text),
    Column("exports", Integer),
    Column("note", Text),
    Column("noise", JSONB, nullable=False),
    _in("status", ASSET_STATUSES),
)

change = Table(
    "change", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("build_id", Integer, ForeignKey("build.id", ondelete="CASCADE"), nullable=False),
    Column("asset_id", Integer, ForeignKey("asset.id"), nullable=False),
    Column("field_path", Text, nullable=False),
    Column("op", Text, nullable=False),
    Column("before", JSONB),
    Column("after", JSONB),
    _in("op", CHANGE_OPS),
    Index("ix_change_history", "asset_id", "field_path", "build_id"),
    Index("ix_change_build", "build_id"),
    Index("ix_change_field_path_trgm", "field_path",
          postgresql_using="gin", postgresql_ops={"field_path": "gin_trgm_ops"}),
)

build_area = Table(
    "build_area", metadata,
    Column("build_id", Integer, ForeignKey("build.id", ondelete="CASCADE"), primary_key=True),
    Column("area", Text, primary_key=True),
    Column("position", Integer, nullable=False),  # order in index.json
    Column("title", Text, nullable=False),
    Column("counts", JSONB, nullable=False),
)
