"""内容寻址缓存：同一份输入永远得到同一份结果，且可离线回放。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..io_utils import ensure_dir, read_json, write_json


class ContentCache:
    """按 (namespace, key) 存取 JSON 结果。"""

    def __init__(self, root: Path | str) -> None:
        self.root = ensure_dir(root)
        self.hits = 0
        self.misses = 0

    def _path(self, namespace: str, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.root / namespace / f"{digest}.json"

    def get(self, namespace: str, key: str) -> Any | None:
        path = self._path(namespace, key)
        if not path.exists():
            self.misses += 1
            return None
        self.hits += 1
        try:
            return read_json(path, None)
        except (json.JSONDecodeError, OSError):
            return None

    def set(self, namespace: str, key: str, value: Any) -> None:
        write_json(self._path(namespace, key), value)

    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses}


def stable_key(*parts: Any) -> str:
    return json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)


__all__ = ["ContentCache", "stable_key"]
