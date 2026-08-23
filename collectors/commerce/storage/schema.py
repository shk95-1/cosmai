"""The database namespace this collector owns.

origin: service/trend-radar/src/trend_radar/storage/schema.py -- ported for #7. One constant so SQLAlchemy
metadata and the DDL diff check cannot spell the boundary two different ways.
"""

from __future__ import annotations

SERVICE_SCHEMA = "trend_radar"
