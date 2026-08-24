"""The database namespace this collector's tables live in -- `needs`, not a schema of its own
(contracts/ddl/needs/004_naver.sql's header explains why). One constant so `tables.py` and
`db.py` cannot spell it two different ways, matching collectors/commerce's own schema.py."""

from __future__ import annotations

SERVICE_SCHEMA = "needs"
