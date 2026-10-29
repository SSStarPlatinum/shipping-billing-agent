"""OCR 网关客户端。

题目给定的接口：

    POST https://gateway.pltplt.com/ai/process
    {"file": "<公网可下载的 PDF/图片地址>", "task": "orderChange",
     "callBack": "", "taskType": ""}

工程化要点（对应验收标准第 2、8 条）：

* 拦截器式重试 + 指数退避 + 连接/读取双超时；
* 熔断器，避免整批文档被一个坏文件拖死；
* 结果按文件内容哈希落盘缓存，可离线回放（"cache" 模式），
  保证 CI/评审环境可复现，且不会把"没识别"伪装成"识别成功"；
* 返回结构未知，故做**自适应解析**：递归寻找 text/tables 字段；
* 每次调用都记录 source（api / replay_cache），写进审计链。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..config import Settings
from ..io_utils import ensure_dir, get_logger, read_json, sha256_file, truncate, write_json
from ..models import RawTable

logger = get_logger("ocr")

_TEXT_KEYS = ("markdown", "text", "content", "ocr_text", "raw_text", "result_text", "full_text")
_TABLE_KEYS = ("tables", "table", "cells", "grid", "rows")


class OcrError(RuntimeError):
    """OCR 调用失败（区别于"识别结果为空"）。"""


@dataclass
class OcrOutcome:
    text: str = ""
    tables: list[RawTable] = field(default_factory=list)
    confidence: float | None = None
    source: str = "none"  # api | replay_cache | none
    raw: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# 自适应解析：OCR 服务商返回结构可能变化
# --------------------------------------------------------------------------- #
def _walk(node: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
    items: list[tuple[tuple[str, ...], Any]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            items.append((path + (str(key),), value))
            items.extend(_walk(value, path + (str(key),)))
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            items.append((path + (str(idx),), value))
            items.extend(_walk(value, path + (str(idx),)))
    return items


def _collect_text(payload: Any) -> str:
    """优先取语义化字段，取不到再把单词/行拼起来。"""
    if isinstance(payload, str):
        return payload
    entries = _walk(payload)
    best: str | None = None
    for path, value in entries:
        if not isinstance(value, str):
            continue
        key = path[-1].lower() if path else ""
        if len(value.strip()) < 2:
            continue
        for candidate_key in _TEXT_KEYS:
            if key == candidate_key:
                if best is None or len(value) > len(best):
                    best = value
                break
    if best:
        return best

    # words 数组: [{"text": "..."}, ...] 或 ["...", ...]
    words: list[str] = []
    for path, value in entries:
        if path and path[-1].lower() in {"words", "word", "items", "lines", "blocks"}:
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        words.append(item)
                    elif isinstance(item, dict):
                        for k in ("text", "word", "content", "value"):
                            if isinstance(item.get(k), str):
                                words.append(item[k])
                                break
    if words:
        return "\n".join(words)

    # 兜底：所有较长字符串
    strings = [v for _, v in entries if isinstance(v, str) and len(v.strip()) > 20]
    return "\n".join(strings)


def _looks_like_table(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    if not all(isinstance(row, list) for row in value):
        return False
    if len(value) < 2:
        return False
    widths = {len(row) for row in value}
    return len(widths) == 1 and widths.pop() >= 2


def _collect_tables(payload: Any, locator_prefix: str = "ocr") -> list[RawTable]:
    tables: list[RawTable] = []
    for path, value in _walk(payload):
        key = path[-1].lower() if path else ""
        if key not in _TABLE_KEYS:
            continue
        candidate = value
        if isinstance(candidate, dict):
            candidate = candidate.get("rows") or candidate.get("data") or candidate.get("cells")
        if _looks_like_table(candidate):
            rows = [
                [("" if cell is None else str(cell)).strip() for cell in row]
                for row in candidate
            ]
            tables.append(RawTable(name=f"{locator_prefix}-table-{len(tables) + 1}", rows=rows))
    return tables


def _collect_confidence(payload: Any) -> float | None:
    for path, value in _walk(payload):
        key = path[-1].lower()
        if key in {"confidence", "score", "accuracy", "avg_confidence"} and isinstance(
            value, (int, float)
        ):
            score = float(value)
            if 0 <= score <= 1:
                return score
            if 1 < score <= 100:
                return score / 100.0
    return None


# --------------------------------------------------------------------------- #
class OcrClient:
    """OCR 客户端：api / cache / auto 三态。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cache_dir = ensure_dir(settings.ocr_cache_dir or ".ocr_cache")
        self._session = None
        self._breaker_open = False
        self._consecutive_failures = 0
        self._url_map: dict[str, str] = {}
        if settings.ocr_url_map_path and Path(settings.ocr_url_map_path).exists():
            try:
                self._url_map = {
                    str(k): str(v)
                    for k, v in (read_json(settings.ocr_url_map_path, {}) or {}).items()
                }
            except Exception:  # pragma: no cover
                logger.warning("OCR URL 映射文件解析失败: %s", settings.ocr_url_map_path)
        self.stats = {"api_calls": 0, "cache_hits": 0, "failures": 0, "replays": 0}

    # ---------------------------------------------------------------- #
    def cache_path(self, sha: str) -> Path:
        return self.cache_dir / f"{sha}.json"

    def has_cache(self, sha: str) -> bool:
        return self.cache_path(sha).exists()

    def public_url(self, path: Path) -> str | None:
        if self._url_map:
            for key in (path.name, str(path), path.stem):
                if key in self._url_map:
                    return self._url_map[key]
        base = self.settings.ocr_public_base_url
        if base:
            return f"{base.rstrip('/')}/{quote(path.name)}"
        return None

    # ---------------------------------------------------------------- #
    def process_file(self, path: Path) -> OcrOutcome:
        """按配置策略识别一个 PDF/图片文件。"""
        mode = self.settings.ocr_mode
        sha = sha256_file(path)
        warnings: list[str] = []

        if mode == "cache":
            cached = self._load_cache(sha)
            if cached:
                cached.warnings.append("使用本地 OCR 回放结果（离线/复现模式）")
                self.stats["cache_hits"] += 1
                return cached
            return OcrOutcome(
                source="none",
                warnings=["未找到该文件的 OCR 缓存，且当前为 cache 模式，无法识别"],
            )

        if mode == "text":
            return OcrOutcome(source="none", warnings=["text 模式不使用 OCR"])

        url = self.public_url(path)
        if mode == "api" and not url:
            raise OcrError(
                "OCR api 模式需要公网可访问的文件地址：请通过 --ocr-public-base-url "
                "或 OCR_URL_MAP 提供，否则 OCR 服务无法下载文件。"
            )

        if url and not self._breaker_open:
            try:
                outcome = self._call_api(path, url)
                self._consecutive_failures = 0
                # 落盘缓存，供后续回放/复现
                self._save_cache(sha, path, outcome)
                return outcome
            except OcrError as exc:
                self.stats["failures"] += 1
                self._consecutive_failures += 1
                if self._consecutive_failures >= 3:
                    self._breaker_open = True
                    logger.warning("OCR 连续失败，熔断开启，本批次后续文件走缓存/文本层")
                warnings.append(f"OCR API 调用失败: {exc}")
                if mode == "api":
                    raise
                logger.warning("OCR API 失败，尝试回放缓存: %s", exc)

        cached = self._load_cache(sha)
        if cached:
            cached.warnings.extend(warnings)
            cached.warnings.append("OCR API 不可用，使用本地回放结果")
            self.stats["cache_hits"] += 1
            return cached

        return OcrOutcome(source="none", warnings=warnings + ["无 OCR 结果可用"])

    # ---------------------------------------------------------------- #
    def process_url(self, url: str) -> OcrOutcome:
        """直接识别一个公网地址（联调/冒烟测试用，不经过本地文件与缓存）。"""
        return self._call_api(Path("<remote-url>"), url)

    # ---------------------------------------------------------------- #
    def _call_api(self, path: Path, url: str) -> OcrOutcome:
        try:
            import requests
        except ImportError as exc:  # pragma: no cover
            raise OcrError(f"缺少 requests 依赖: {exc}") from exc

        if self._session is None:
            self._session = requests.Session()

        payload = {
            "file": url,
            "task": self.settings.ocr_task,
            "callBack": "",
            "taskType": "",
        }
        timeouts = (self.settings.ocr_connect_timeout, self.settings.ocr_read_timeout)
        last_error: str = ""
        for attempt in range(1, max(1, self.settings.ocr_max_retries) + 1):
            started = time.time()
            try:
                self.stats["api_calls"] += 1
                response = self._session.post(
                    self.settings.ocr_url, json=payload, timeout=timeouts
                )
                if response.status_code >= 500:
                    raise OcrError(f"HTTP {response.status_code}")
                if response.status_code >= 400:
                    raise OcrError(f"HTTP {response.status_code}: {truncate(response.text, 200)}")
                cos = time.time() - started
                body = response.json()
                outcome = self._parse_payload(body)
                outcome.source = "api"
                outcome.raw.setdefault("_meta", {})
                outcome.raw["_meta"].update(
                    {"url": url, "elapsed_s": round(cos, 3), "attempt": attempt}
                )
                if not outcome.text.strip() and not outcome.tables:
                    outcome.warnings.append("OCR 返回成功但未解析出文字")
                logger.info("OCR 成功: %s (%.2fs, %d chars)", path.name, cos, len(outcome.text))
                return outcome
            except Exception as exc:  # noqa: BLE001 - 统一转成 OcrError
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "OCR 第 %d/%d 次失败: %s", attempt, self.settings.ocr_max_retries, last_error
                )
                if attempt < self.settings.ocr_max_retries:
                    time.sleep(min(8.0, self.settings.ocr_backoff_base ** attempt))
        raise OcrError(last_error or "未知错误")

    def _parse_payload(self, body: Any) -> OcrOutcome:
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                return OcrOutcome(text=body, raw={"raw_text": body})
        outcome = OcrOutcome(raw=body if isinstance(body, dict) else {"data": body})
        outcome.text = _collect_text(body)
        outcome.tables = _collect_tables(body)
        outcome.confidence = _collect_confidence(body)
        # 业务错误码
        if isinstance(body, dict):
            code = body.get("code", body.get("status", body.get("errcode")))
            ok_values = {0, "0", 200, "200", "success", "ok", "SUCCESS", None}
            if code not in ok_values:
                message = body.get("message") or body.get("msg") or body.get("error") or ""
                outcome.warnings.append(f"OCR 业务返回异常 code={code} message={message}")
        return outcome

    # ---------------------------------------------------------------- #
    def _save_cache(self, sha: str, path: Path, outcome: OcrOutcome) -> None:
        try:
            payload = {
                "_meta": {
                    "source_file": path.name,
                    "sha256": sha,
                    "ocr_source": outcome.source,
                    "confidence": outcome.confidence,
                    "cached_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "endpoint": self.settings.ocr_url,
                    "task": self.settings.ocr_task,
                },
                "text": outcome.text,
                "tables": [t.rows for t in outcome.tables],
                "confidence": outcome.confidence,
                "raw": outcome.raw,
            }
            write_json(self.cache_path(sha), payload)
        except Exception as exc:  # pragma: no cover
            logger.warning("OCR 缓存写入失败: %s", exc)

    def _load_cache(self, sha: str) -> OcrOutcome | None:
        path = self.cache_path(sha)
        if not path.exists():
            return None
        try:
            data = read_json(path, {}) or {}
        except Exception as exc:  # pragma: no cover
            logger.warning("OCR 缓存读取失败: %s", exc)
            return None
        text = data.get("text", "") or ""
        tables = [
            RawTable(name=f"ocr-table-{i}", rows=rows)
            for i, rows in enumerate(data.get("tables") or [])
            if isinstance(rows, list)
        ]
        confidence = data.get("confidence")
        if confidence is None:
            confidence = (data.get("_meta") or {}).get("confidence")
        warnings: list[str] = []
        meta = data.get("_meta") or {}
        if meta.get("fixture"):
            warnings.append(str(meta.get("note") or "本地 OCR 回放样本"))
        return OcrOutcome(
            text=text,
            tables=tables,
            confidence=confidence,
            source="replay_cache",
            raw=data.get("raw") or {},
            warnings=warnings,
        )


__all__ = ["OcrClient", "OcrError", "OcrOutcome", "_collect_tables", "_collect_text"]
