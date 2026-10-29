"""MySQL-only canonical persistence. No implicit SQLite/in-memory fallback."""

import os
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.engine import URL

metadata = MetaData()
DocumentText = Text().with_variant(LONGTEXT(), "mysql")
opts = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci", "mysql_engine": "InnoDB"}


def table(name, *columns, **kw):
    return Table(name, metadata, *columns, **opts, **kw)


def ident():
    return Column("id", String(64), primary_key=True)


schema = table(
    "schema_versions",
    Column("version", Integer, primary_key=True),
    Column("applied_at", Float(53), nullable=False),
)
locks = table(
    "system_locks",
    Column("name", String(64), primary_key=True),
    Column("version", Integer, nullable=False, default=1),
)
users = table(
    "users",
    ident(),
    Column("phone", String(32), unique=True),
    Column("email", String(254), unique=True),
    Column("login_alias", String(64), unique=True),
    Column("display_name", String(120), nullable=False),
    Column("password_hash", String(255), nullable=False),
    Column("roles", JSON, nullable=False),
    Column("status", String(24), nullable=False, default="active"),
    Column("version", Integer, nullable=False, default=1),
    Column("auth_version", Integer, nullable=False, default=1),
    Column("must_change_password", Boolean, nullable=False, default=False),
    Column("created_at", Float(53), nullable=False),
)
flower_names = table(
    "flower_names",
    ident(),
    Column("batch", Integer, nullable=False, index=True),
    Column("name", String(20), nullable=False),
    Column("used_at", Float(53), index=True),
    Column("created_at", Float(53), nullable=False),
    UniqueConstraint("batch", "name"),
)
workspaces = table(
    "workspaces",
    ident(),
    Column("owner_user_id", String(64), ForeignKey("users.id"), index=True),
    Column("name", String(160), nullable=False),
    Column("state", String(32), nullable=False, default="active"),
    Column("legacy_id", String(128), unique=True),
    Column("created_at", Float(53), nullable=False),
)
auth_sessions = table(
    "auth_sessions",
    Column("digest", String(64), primary_key=True),
    Column("user_id", String(64), ForeignKey("users.id"), index=True, nullable=False),
    Column("csrf", String(64), nullable=False),
    Column("auth_version", Integer, nullable=False),
    Column("expires_at", Float(53), nullable=False),
)
auth_limits = table(
    "auth_limits",
    Column("key", String(64), primary_key=True),
    Column("window", Integer, nullable=False),
    Column("count", Integer, nullable=False),
)
audit = table(
    "audit_events",
    ident(),
    Column("actor_user_id", String(64)),
    Column("action", String(80), nullable=False),
    Column("target_id", String(128)),
    Column("payload", JSON, nullable=False),
    Column("created_at", Float(53), nullable=False),
)
threads = table(
    "threads",
    ident(),
    Column("workspace_id", String(64), ForeignKey("workspaces.id"), index=True, nullable=False),
    Column("title", String(160), nullable=False),
    Column("profile", String(40), nullable=False, default="meeting_assistant"),
    Column("next_seq", Integer, nullable=False, default=1),
    Column("lease_run_id", String(64)),
    Column("lease_until", Float(53)),
    Column("created_at", Float(53), nullable=False),
)
records = table(
    "conversation_records",
    ident(),
    Column("thread_id", String(64), ForeignKey("threads.id"), index=True, nullable=False),
    Column("seq", Integer, nullable=False),
    Column("actor_user_id", String(64)),
    Column("role", String(24), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("text", DocumentText, nullable=False),
    Column("visibility", String(32), nullable=False, default="user_shared"),
    Column("revision", Integer, nullable=False, default=1),
    Column("withdrawn", Boolean, nullable=False, default=False),
    Column("source_record_id", String(64)),
    Column("metadata", JSON, nullable=False),
    Column("created_at", Float(53), nullable=False),
    UniqueConstraint("thread_id", "seq"),
)
revisions = table(
    "record_revisions",
    Column("record_id", String(64), ForeignKey("conversation_records.id"), primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("text", DocumentText, nullable=False),
    Column("actor_user_id", String(64)),
    Column("created_at", Float(53), nullable=False),
)
runs = table(
    "agent_runs",
    ident(),
    Column("kind", String(24), nullable=False, server_default="interactive", index=True),
    Column("parent_run_id", String(64)),
    Column("source_revision", Integer, nullable=False, server_default="1"),
    Column("thread_id", String(64), ForeignKey("threads.id"), index=True, nullable=False),
    Column("record_id", String(64), ForeignKey("conversation_records.id"), nullable=False),
    Column("actor_user_id", String(64), ForeignKey("users.id"), nullable=False),
    Column("owner_user_id", String(64), ForeignKey("users.id"), nullable=False),
    Column("auth_version", Integer, nullable=False),
    Column("status", String(32), nullable=False, index=True),
    Column("attempt", Integer, nullable=False, default=0),
    Column("lease_token", String(64)),
    Column("lease_until", Float(53)),
    Column("cancel_requested", Boolean, nullable=False, default=False),
    Column("next_event_seq", Integer, nullable=False, default=1),
    Column("error_code", String(80)),
    Column("created_at", Float(53), nullable=False),
    Column("updated_at", Float(53), nullable=False),
)
events = table(
    "run_events",
    ident(),
    Column("run_id", String(64), ForeignKey("agent_runs.id"), index=True, nullable=False),
    Column("thread_id", String(64), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("type", String(80), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("source_record_ids", JSON, nullable=False),
    Column("created_at", Float(53), nullable=False),
    UniqueConstraint("run_id", "seq"),
)
assistance_requests = table(
    "hinter_assistance_requests",
    ident(),
    Column("thread_id", String(64), ForeignKey("threads.id"), nullable=False, index=True),
    Column("run_id", String(64), ForeignKey("agent_runs.id"), nullable=False, unique=True),
    Column("source_record_id", String(64), ForeignKey("conversation_records.id"), nullable=False),
    Column("source_revision", Integer, nullable=False),
    Column("summary", String(500), nullable=False),
    Column("needed_information", DocumentText, nullable=False),
    Column("reason", String(40), nullable=False),
    Column("status", String(24), nullable=False, default="pending", index=True),
    Column("version", Integer, nullable=False, default=1),
    Column("resolved_by_user_id", String(64), ForeignKey("users.id")),
    Column("guidance_record_id", String(64), ForeignKey("conversation_records.id")),
    Column("continuation_run_id", String(64), ForeignKey("agent_runs.id")),
    Column("created_at", Float(53), nullable=False),
    Column("updated_at", Float(53), nullable=False),
)
commands = table(
    "idempotency_commands",
    Column("owner_user_id", String(64), primary_key=True),
    Column("operation", String(128), primary_key=True),
    Column("key", String(100), primary_key=True),
    Column("request_hash", String(64), nullable=False),
    Column("response", JSON, nullable=False),
)
memories = table(
    "memory_documents",
    ident(),
    Column("workspace_id", String(64), ForeignKey("workspaces.id"), index=True, nullable=False),
    Column("name", String(160), nullable=False),
    Column("visibility", String(32), nullable=False),
    Column("text", DocumentText, nullable=False),
    Column("revision", Integer, nullable=False, default=1),
    Column("metadata", JSON, nullable=False),
    Column("created_at", Float(53), nullable=False),
    UniqueConstraint("workspace_id", "name"),
)
projections = table(
    "record_projections",
    Column("record_id", String(64), ForeignKey("conversation_records.id"), primary_key=True),
    Column("kind", String(32), primary_key=True),
    Column("source_revision", Integer, nullable=False),
    Column("items", JSON, nullable=False),
    Column("status", String(24), nullable=False),
)
artifacts = table(
    "artifacts",
    ident(),
    Column("thread_id", String(64), ForeignKey("threads.id"), index=True, nullable=False),
    Column("path", String(512), nullable=False),
    Column("media_type", String(100), nullable=False),
    Column("checksum", String(64), nullable=False),
    Column("size", Integer, nullable=False),
    Column("created_at", Float(53), nullable=False),
)
legacy_maps = table(
    "legacy_imports",
    Column("source_key", String(191), primary_key=True),
    Column("target_id", String(64), nullable=False),
    Column("checksum", String(64), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_at", Float(53), nullable=False),
)
# Full typed checkpoint serialization, including channel versions and pending writes.
checkpoints = table(
    "deerflow_checkpoints",
    Column("thread_id", String(128), primary_key=True),
    Column("namespace", String(191), primary_key=True),
    Column("checkpoint_id", String(64), primary_key=True),
    Column("parent_id", String(64)),
    Column("type", String(32), nullable=False),
    Column("data", LargeBinary(length=4294967295), nullable=False),
    Column("metadata_type", String(32), nullable=False),
    Column("metadata_data", LargeBinary(length=4294967295), nullable=False),
)
writes = table(
    "deerflow_checkpoint_writes",
    Column("thread_id", String(128), primary_key=True),
    Column("namespace", String(191), primary_key=True),
    Column("checkpoint_id", String(64), primary_key=True),
    Column("task_id", String(128), primary_key=True),
    Column("idx", Integer, primary_key=True),
    Column("channel", String(128), nullable=False),
    Column("task_path", DocumentText, nullable=False),
    Column("type", String(32), nullable=False),
    Column("data", LargeBinary(length=4294967295), nullable=False),
)
kv = table(
    "deerflow_store",
    Column("namespace", String(191), primary_key=True),
    Column("key", String(191), primary_key=True),
    Column("value", JSON, nullable=False),
    Column("created_at", Float(53), nullable=False),
    Column("updated_at", Float(53), nullable=False),
)


def uid(prefix):
    return f"{prefix}_{uuid.uuid4().hex}"


def now():
    return datetime.now(UTC).timestamp()


@lru_cache
def engine():
    value = os.getenv("DATABASE_URL")
    if value:
        if not value.startswith("mysql+pymysql://"):
            raise RuntimeError("DATABASE_URL must use mysql+pymysql; fallback is forbidden")
    else:
        secret = Path(os.getenv("MYSQL_PASSWORD_FILE", ".runtime/secrets/mysql-password"))
        if not secret.exists():
            raise RuntimeError("MySQL credentials missing; run scripts/setup-employee.py")
        value = URL.create(
            "mysql+pymysql",
            username=os.getenv("MYSQL_USER", "duet"),
            password=secret.read_text().strip(),
            host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            port=int(os.getenv("MYSQL_PORT", "13316")),
            database=os.getenv("MYSQL_DATABASE", "duet"),
            query={"charset": "utf8mb4"},
        )
    return create_engine(
        value,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_size=10,
        connect_args={"init_command": "SET time_zone = '+00:00'"},
        hide_parameters=True,
    )


@contextmanager
def transaction():
    with engine().begin() as connection:
        yield connection


def row(result):
    return dict(result.mappings().one())
