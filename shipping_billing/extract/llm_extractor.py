"""LangChain LLM 抽取器（可选增强，缺省关闭）。

职责边界（很重要）：
* **LLM 不做最终决策**。它只负责"补充规则抓不到的语义字段"（例如合同措辞里
  的签发方、模糊的费用名），以及和规则结果做交叉校验；
* 输出强制 Pydantic 结构 + JSON 解析失败自动修复重试；
* 温度 0、结果缓存、超时与重试，保证同一输入可复现。

未配置 API Key 时整个模块自动降级为空实现，不影响主链路。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..config import Settings
from ..io_utils import get_logger, read_json, truncate, write_json

logger = get_logger("llm")


class LlmCharge(BaseModel):
    raw_name: str = Field(description="单据上原始写法的费用名称")
    amount: float | None = Field(default=None, description="金额，纯数字；无法确定填 null")
    currency: str | None = Field(default=None, description="三位 ISO 4217 币种代码")
    quantity: float | None = None
    unit_rate: float | None = None
    payment_term: str | None = Field(default=None, description="PP 或 CC 或 COD")
    evidence: str | None = Field(default=None, description="支持该条费用的原文片段")


class LlmDocument(BaseModel):
    document_type: str | None = Field(default=None, description="Debit Note / Credit Note / Invoice / Arrival Notice / Pre-Alert")
    reference_no: str | None = None
    document_date: str | None = Field(default=None, description="YYYY-MM-DD")
    issuer: str | None = None
    recipient: str | None = None
    currency: str | None = None
    payment_term: str | None = None
    total_amount: float | None = None
    charges: list[LlmCharge] = Field(default_factory=list)
    reasoning: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, value: str | None) -> str | None:
        return value.upper() if isinstance(value, str) else value


SYSTEM_PROMPT = """你是国际货运代理行业的账单信息抽取专家。
只根据给定的单据文本抽取信息，绝对不要编造：文本里没有的字段一律返回 null。
金额只保留数字（不要千分位、不要货币符号），币种必须是三位 ISO 4217 代码。
付款方式只允许 PP（预付）、CC（到付）、COD 三种。
每个费用项目单独输出一条 charges 记录。"""

USER_PROMPT = """请从下面的单据文本中抽取结构化费用信息。

===== 文件信息 =====
文件名: {filename}
来源类型: {kind}

===== 单据文本（可能来自 OCR，存在识别噪声）=====
{text}

===== 输出要求 =====
严格按以下 JSON Schema 输出**一个 JSON 对象**，不要输出任何解释性文字：
{schema}
"""


class LlmExtractor:
    """封装"提示词 + 结构化解析 + 重试 + 缓存"的 LangChain 调用。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.enabled = bool(settings.llm_enabled and settings.llm_api_key)
        self._chain = None
        self._parser = None
        self.cache_dir = Path(settings.llm_cache_dir or ".llm_cache")
        self.stats = {"calls": 0, "cache_hits": 0, "failures": 0, "repairs": 0}
        if settings.llm_enabled and not settings.llm_api_key:
            logger.warning("LLM 已启用但未提供 API Key，自动降级为纯规则模式")

    # ---------------------------------------------------------------- #
    def _build(self):
        if self._chain is not None:
            return self._chain
        from langchain_core.output_parsers import PydanticOutputParser
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_openai import ChatOpenAI

        parser = PydanticOutputParser(pydantic_object=LlmDocument)
        self._parser = parser
        prompt = ChatPromptTemplate.from_messages(
            [("system", SYSTEM_PROMPT), ("human", USER_PROMPT)]
        )
        model = ChatOpenAI(
            model=self.settings.llm_model,
            base_url=self.settings.llm_base_url,
            api_key=self.settings.llm_api_key,
            temperature=self.settings.llm_temperature,
            timeout=self.settings.llm_timeout,
            max_retries=self.settings.llm_max_retries,
        )
        self._chain = prompt | model | parser
        return self._chain

    def extract(self, text: str, *, filename: str, kind: str) -> LlmDocument | None:
        if not self.enabled:
            return None
        if not text or not text.strip():
            return None

        schema_text = json.dumps(
            LlmDocument.model_json_schema(), ensure_ascii=False, separators=(",", ":")
        )
        # 控制上下文长度，避免超长 PDF 把费用表挤掉
        max_chars = 12000
        clipped = text if len(text) <= max_chars else text[:max_chars] + "\n...[truncated]"
        key = hashlib.sha256(
            f"{self.settings.llm_model}|{filename}|{clipped}".encode("utf-8")
        ).hexdigest()

        cache_path = self.cache_dir / f"{key}.json"
        cached = read_json(cache_path, None)
        if cached:
            self.stats["cache_hits"] += 1
            try:
                return LlmDocument.model_validate(cached)
            except Exception:  # noqa: BLE001 - 缓存结构变化时忽略
                pass

        chain = self._build()
        last_error: Exception | None = None
        for attempt in range(1, self.settings.llm_max_retries + 2):
            try:
                self.stats["calls"] += 1
                started = time.time()
                result: LlmDocument = chain.invoke(
                    {
                        "filename": filename,
                        "kind": kind,
                        "text": clipped,
                        "schema": schema_text,
                    }
                )
                elapsed = time.time() - started
                logger.info("LLM 抽取成功 %s (%.2fs, %.0f%%)", filename, elapsed, result.confidence * 100)
                self._save_cache(cache_path, result)
                return result
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                self.stats["repairs"] += 1
                logger.warning("LLM 第 %d 次调用/解析失败: %s", attempt, truncate(str(exc), 200))
                time.sleep(min(4.0, 1.2 * attempt))
        self.stats["failures"] += 1
        logger.warning("LLM 抽取最终失败 %s: %s", filename, last_error)
        return None

    def _save_cache(self, path: Path, result: LlmDocument) -> None:
        try:
            write_json(path, result.model_dump(mode="json"))
        except Exception as exc:  # pragma: no cover
            logger.debug("LLM 缓存写入失败: %s", exc)

    def __call__(self, payload: dict[str, Any]) -> LlmDocument | None:
        """作为 LangChain Runnable 的可调用节点使用。"""
        return self.extract(
            payload.get("text", ""), filename=payload.get("filename", ""), kind=payload.get("kind", "")
        )


__all__ = ["LlmCharge", "LlmDocument", "LlmExtractor"]
