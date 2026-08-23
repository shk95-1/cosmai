"""Content-addressed JSON payloads on disk -- what `work` writes and `flatten` reads back.

origin: service/yt-scrapper/src/tubedepth/payload_store.py -- ported for #8, minus gzip (the archived
version compressed because harvests ran to tens of megabytes at production volume; #8 has no live
volume yet, so that is a real simplification to revisit if a live cutover shows it matters, not a
faithfulness gap in what this issue's tests exercise).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class StoredPayload:
    digest: str
    byte_count: int


class PayloadStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _path_for(self, kind: str, digest: str) -> Path:
        return self._root / kind / digest[:2] / f"{digest}.json"

    def put(self, kind: str, payload: Any) -> StoredPayload:
        raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        path = self._path_for(kind, digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return StoredPayload(digest=digest, byte_count=len(raw))

    def get(self, kind: str, digest: str) -> Any:
        path = self._path_for(kind, digest)
        return json.loads(path.read_text(encoding="utf-8"))

    def delete(self, kind: str, digest: str) -> bool:
        path = self._path_for(kind, digest)
        if not path.exists():
            return False
        path.unlink()
        return True


__all__ = ["PayloadStore", "StoredPayload"]
