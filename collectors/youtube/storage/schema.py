"""The database namespace this collector owns.

origin: service/yt-scrapper/src/tubedepth/database.py -- ported for #8. One constant so the Table
objects and the DDL diff check cannot spell the boundary two different ways (collectors/commerce's
storage/schema.py does the same for `trend_radar`).
"""

from __future__ import annotations

SERVICE_SCHEMA = "tubedepth"
