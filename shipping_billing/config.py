"""运行配置。

所有可调参数集中在此：OCR 网关、LLM、记账主体、台账路径、缓存目录等。
配置优先级：CLI 参数 > 环境变量(.env) > 默认值。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

try:  # 可选依赖：没有 python-dotenv 也能跑
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - 环境缺失时静默降级
    pass


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError:
        return default


#: 题目给定的 OCR 网关
DEFAULT_OCR_URL = "https://gateway.pltplt.com/ai/process"
DEFAULT_OCR_TASK = "orderChange"


@dataclass(slots=True)
class Settings:
    """一次运行所需的全部配置。"""

    # ---------- OCR ----------
    ocr_url: str = DEFAULT_OCR_URL
    ocr_task: str = DEFAULT_OCR_TASK
    #: auto | api | cache | text
    #:   api   —— 强制调用 OCR API（file_url 不可用时报错，避免静默造假）
    #:   cache —— 只读本地 OCR 结果回放（离线/CI/复现）
    #:   auto  —— 有公网地址则调 API，失败回落缓存，再回落 PDF 文本层
    #:   text  —— 不使用 OCR，仅用 PDF 文本层（纯文本件）
    ocr_mode: str = "auto"
    ocr_connect_timeout: float = 10.0
    ocr_read_timeout: float = 300.0
    ocr_max_retries: int = 3
    ocr_backoff_base: float = 1.5
    #: OCR 服务需要能下载到文件，因此必须给出一个公网可访问的地址前缀，
    #: 例如 https://files.example.com/bills ；会拼成 {base}/{文件名}
    ocr_public_base_url: str | None = None
    #: 若已有公网 URL 映射文件（json: {文件名: url}），优先使用
    ocr_url_map_path: Path | None = None
    #: 文本层字符数低于该阈值时视为扫描件，转 OCR
    pdf_text_layer_min_chars: int = 120

    # ---------- LLM（可选增强，非必需） ----------
    llm_enabled: bool = False
    llm_model: str = "deepseek-chat"
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str | None = None
    llm_temperature: float = 0.0
    llm_max_retries: int = 2
    llm_timeout: float = 60.0

    # ---------- 业务 ----------
    #: 当前记账主体（我司）。AR/AP 判定必须以它为中心。
    accounting_entity: str = "NEPTUNE SHIPPING LIMITED"
    #: 记账主体的别名，用于与单据上的签发方/接收方做模糊比对
    entity_aliases: tuple[str, ...] = ()
    #: 金额比对容差（绝对差额）
    amount_tolerance: float = 0.02
    #: 明细合计与单据总计允许的偏差（比例）
    total_tolerance_ratio: float = 0.005
    #: 低于该置信度的记录进入 PENDING_REVIEW
    review_confidence_threshold: float = 0.55

    # ---------- 路径 ----------
    data_dir: Path = DEFAULT_DATA_DIR
    ledger_path: Path | None = None
    ocr_cache_dir: Path | None = None
    llm_cache_dir: Path | None = None
    output_dir: Path | None = None
    #: 运行时落盘（附件解包、中间产物）
    work_dir: Path | None = None

    # ---------- 杂项 ----------
    verbose: bool = False
    #: 是否把解析后的原始文本写进报告（可能较大）
    include_raw_text_in_report: bool = False

    def __post_init__(self) -> None:
        if self.ledger_path is None:
            self.ledger_path = self.data_dir / "ledger" / "existing_bills.json"
        if self.ocr_cache_dir is None:
            self.ocr_cache_dir = self.data_dir / "ocr_cache"
        if self.llm_cache_dir is None:
            self.llm_cache_dir = self.data_dir / "llm_cache"
        if self.output_dir is None:
            self.output_dir = PROJECT_ROOT / "output"
        if self.work_dir is None:
            self.work_dir = PROJECT_ROOT / ".work"
        self.ocr_mode = (self.ocr_mode or "auto").lower()
        if self.ocr_mode not in {"auto", "api", "cache", "text"}:
            raise ValueError(f"未知 ocr_mode: {self.ocr_mode}")

    # ------------------------------------------------------------------ #
    @property
    def entity_aliases_all(self) -> tuple[str, ...]:
        aliases = [self.accounting_entity, *self.entity_aliases]
        return tuple(a for a in aliases if a)

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        """从环境变量构造，再叠加显式覆盖项。"""
        settings = cls(
            ocr_url=_env("OCR_API_URL", DEFAULT_OCR_URL) or DEFAULT_OCR_URL,
            ocr_task=_env("OCR_TASK", DEFAULT_OCR_TASK) or DEFAULT_OCR_TASK,
            ocr_mode=_env("OCR_MODE", "auto") or "auto",
            ocr_public_base_url=_env("OCR_PUBLIC_BASE_URL"),
            ocr_url_map_path=(
                Path(_env("OCR_URL_MAP", "")) if _env("OCR_URL_MAP") else None
            ),
            llm_enabled=_env_bool("LLM_ENABLED", bool(_env("LLM_API_KEY"))),
            llm_model=_env("LLM_MODEL", "deepseek-chat") or "deepseek-chat",
            llm_base_url=_env("LLM_BASE_URL", "https://api.deepseek.com/v1")
            or "https://api.deepseek.com/v1",
            llm_api_key=_env("LLM_API_KEY") or _env("DEEPSEEK_API_KEY") or _env("OPENAI_API_KEY"),
            accounting_entity=_env(
                "ACCOUNTING_ENTITY", "NEPTUNE SHIPPING LIMITED"
            )
            or "NEPTUNE SHIPPING LIMITED",
            ledger_path=Path(_env("LEDGER_PATH")) if _env("LEDGER_PATH") else None,
            review_confidence_threshold=_env_float("REVIEW_CONFIDENCE", 0.55),
            verbose=_env_bool("VERBOSE", False),
        )
        if overrides:
            settings = replace(settings, **{k: v for k, v in overrides.items() if v is not None})
        return settings

    def with_overrides(self, **overrides) -> "Settings":
        clean = {k: v for k, v in overrides.items() if v is not None}
        return replace(self, **clean) if clean else self


__all__ = ["Settings", "PROJECT_ROOT", "DEFAULT_DATA_DIR"]
