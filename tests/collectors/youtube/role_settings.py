"""Read the runtime limits that the test harness built from the declared source bootstrap."""

from __future__ import annotations

import sqlalchemy as sa


def runtime_role_settings(database_url: str, role: str) -> dict[str, str]:
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as conn:
            values = (
                conn.execute(
                    sa.text(
                        "SELECT unnest(s.setconfig) FROM pg_db_role_setting s "
                        "JOIN pg_roles r ON r.oid = s.setrole "
                        "JOIN pg_database d ON d.oid = s.setdatabase "
                        "WHERE r.rolname = :role AND d.datname = current_database()"
                    ),
                    {"role": role},
                )
                .scalars()
                .all()
            )
    finally:
        engine.dispose()
    assert values, f"no database-scoped settings for {role}"
    return dict(value.split("=", 1) for value in values)


def timeout_seconds(value: str) -> float:
    assert value.endswith("s"), f"unexpected timeout unit: {value}"
    return float(value[:-1])
