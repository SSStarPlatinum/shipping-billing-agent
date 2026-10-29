"""运行日志：耗时、统计、逐文件诊断，全部结构化落盘。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..io_utils import write_json


@dataclass
class FileLog:
    file: str
    kind: str = "unknown"
    status: str = "ok"
    duration_ms: int = 0
    documents: int = 0
    records: int = 0
    operations: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    ocr_source: str | None = None


@dataclass
class RunLog:
    run_id: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_s: float = 0.0
    files: list[FileLog] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    _t0: float = field(default=0.0, repr=False)

    def start(self, run_id: str) -> None:
        self.run_id = run_id
        self._t0 = time.time()
        self.started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    def stop(self) -> None:
        self.duration_s = round(time.time() - self._t0, 3) if self._t0 else 0.0
        self.finished_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    def add_file(self, entry: FileLog) -> None:
        self.files.append(entry)

    def bump(self, name: str, value: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + value

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": self.duration_s,
            "counters": self.counters,
            "meta": self.meta,
            "files": [entry.__dict__ for entry in self.files],
        }

    def save(self, path: Path | str) -> Path:
        return write_json(path, self.to_dict())

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


__all__ = ["FileLog", "RunLog"]
