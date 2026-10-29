"""Versioned, serialized, additive schema migrations. DDL failures never mark success."""

from sqlalchemy import Float, Text, insert, inspect, select, text
from sqlalchemy.dialects.mysql import insert as mysql_insert

from . import names
from .db import engine, locks, metadata, now, schema


def migrate():
    with engine().connect() as c:
        acquired = c.execute(text("SELECT GET_LOCK('duet_schema_migration', 60)")).scalar()
        c.commit()
        if acquired != 1:
            raise RuntimeError("Migration lock unavailable")
        try:
            # v1 is the initial account/record/native-DeerFlow-persistence schema.
            metadata.create_all(c)
            c.commit()
            with c.begin():
                versions = list(c.execute(select(schema.c.version)).scalars())
                if any(v > 6 for v in versions):
                    raise RuntimeError("Database schema newer than application")
                if 1 not in versions:
                    c.execute(insert(schema).values(version=1, applied_at=now()))
                c.execute(
                    mysql_insert(locks)
                    .values(name="admin_membership", version=1)
                    .on_duplicate_key_update(name="admin_membership")
                )
            if 2 not in versions:
                # MySQL FLOAT stores epoch timestamps with ~128-second granularity.
                # DOUBLE is required for correct leases, expiry and record ordering.
                for table in metadata.sorted_tables:
                    for column in table.columns:
                        if isinstance(column.type, Float):
                            nullability = "NULL" if column.nullable else "NOT NULL"
                            c.execute(
                                text(
                                    f"ALTER TABLE `{table.name}` MODIFY COLUMN "
                                    f"`{column.name}` DOUBLE {nullability}"
                                )
                            )
                            c.commit()
                with c.begin():
                    c.execute(insert(schema).values(version=2, applied_at=now()))
            if 3 not in versions:
                # TEXT is only 64 KiB; meeting records and imported Wiki use UTF-8.
                for table in metadata.sorted_tables:
                    for column in table.columns:
                        if isinstance(column.type, Text):
                            nullability = "NULL" if column.nullable else "NOT NULL"
                            statement = (
                                f"ALTER TABLE `{table.name}` MODIFY COLUMN "
                                f"`{column.name}` LONGTEXT {nullability}"
                            )
                            c.execute(text(statement))
                            c.commit()
                with c.begin():
                    c.execute(insert(schema).values(version=3, applied_at=now()))
            if 4 not in versions:
                columns = {column["name"] for column in inspect(c).get_columns("agent_runs")}
                c.commit()
                additions = {
                    "kind": "VARCHAR(24) NOT NULL DEFAULT 'interactive'",
                    "parent_run_id": "VARCHAR(64) NULL",
                    "source_revision": "INTEGER NOT NULL DEFAULT 1",
                }
                for name, declaration in additions.items():
                    if name not in columns:
                        c.execute(text(f"ALTER TABLE agent_runs ADD COLUMN `{name}` {declaration}"))
                        c.commit()
                indexes = {index["name"] for index in inspect(c).get_indexes("agent_runs")}
                c.commit()
                if "ix_agent_runs_kind" not in indexes:
                    c.execute(text("CREATE INDEX ix_agent_runs_kind ON agent_runs (kind)"))
                    c.commit()
                with c.begin():
                    c.execute(insert(schema).values(version=4, applied_at=now()))
            if 5 not in versions:
                with c.begin():
                    names.seed(c)
                    c.execute(insert(schema).values(version=5, applied_at=now()))
            if 6 not in versions:
                # Additive request table was created by metadata.create_all above.
                with c.begin():
                    c.execute(insert(schema).values(version=6, applied_at=now()))
        finally:
            c.execute(text("SELECT RELEASE_LOCK('duet_schema_migration')"))
            c.commit()


if __name__ == "__main__":
    migrate()
    print("Schema v6 ready")
