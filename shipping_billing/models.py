"""领域模型。

分三层：

1. RawDocument    —— 解析后的“原始证据”（文本、表格、来源），保留可追溯信息；
2. ExtractedDocument / ChargeLine —— 语义层，规则/LLM 抽取出的单据与费用；
3. OutputRecord   —— 题目要求的最终标准输出结构（严格 JSON）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DocumentKind = Literal["eml", "pdf", "image", "excel", "word", "json", "unknown"]
OperationType = Literal["CREATE", "UPDATE", "NO_CHANGE", "PENDING_REVIEW"]
PostingType = Literal["AR", "AP"]


# --------------------------------------------------------------------------- #
# 1. 原始证据层
# --------------------------------------------------------------------------- #
class Provenance(BaseModel):
    """字段级来源，保证每条结论都能回溯到具体文件/页码/行。"""

    model_config = ConfigDict(extra="allow")

    source_file: str
    source_role: Literal["primary", "attachment"] = "primary"
    kind: str = "unknown"
    locator: str | None = None  # "page 1" / "Sheet1!row 12" / "line 37"
    extractor: str = "rules"  # rules | llm | ocr | table
    ocr_source: str | None = None  # api | replay_cache | pdf_text_layer
    snippet: str | None = None


class RawTable(BaseModel):
    name: str | None = None
    locator: str | None = None
    rows: list[list[str]] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not any(any(cell.strip() for cell in row) for row in self.rows)


class RawDocument(BaseModel):
    """一个文件（含其附件递归）解析后的原始内容。"""

    doc_id: str
    path: str
    filename: str
    kind: DocumentKind = "unknown"
    sha256: str = ""
    size: int = 0
    text: str = ""
    pages: list[str] = Field(default_factory=list)
    tables: list[RawTable] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    attachments: list["RawDocument"] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    ocr_source: str | None = None
    ocr_confidence: float | None = None

    def iter_documents(self) -> list["RawDocument"]:
        out: list[RawDocument] = [self]
        for att in self.attachments:
            out.extend(att.iter_documents())
        return out

    def text_chars(self) -> int:
        return len(self.text.strip())


RawDocument.model_rebuild()


# --------------------------------------------------------------------------- #
# 2. 语义层
# --------------------------------------------------------------------------- #
class DocumentIdentifiers(BaseModel):
    """匹配/校验用的业务标识。"""

    model_config = ConfigDict(extra="allow")

    reference_no: str | None = None
    mbl_no: str | None = None
    hbl_no: str | None = None
    booking_no: str | None = None
    bl_no: str | None = None
    invoice_no: str | None = None
    ams_no: str | None = None
    container_nos: list[str] = Field(default_factory=list)
    vessel: str | None = None
    voyage: str | None = None
    agent_file_no: str | None = None

    def as_context(self) -> dict[str, Any]:
        data = self.model_dump(exclude_none=True)
        if not data.get("container_nos"):
            data.pop("container_nos", None)
        return data


class ChargeLine(BaseModel):
    """一条费用明细（未标准化前的原始形态 + 标准化结果）。"""

    raw_name: str
    charge_item: str
    standardized: bool = False
    amount: float | None = None
    currency: str | None = None
    quantity: float | None = None
    unit_rate: float | None = None
    payment_term: str | None = None
    #: 1 = 正数费用; -1 = Credit（冲销/红字）
    amount_sign: int = 1
    confidence: float = 0.6
    source_line: str | None = None
    provenance: Provenance | None = None
    warnings: list[str] = Field(default_factory=list)

    @property
    def signed_amount(self) -> float | None:
        if self.amount is None:
            return None
        return round(self.amount * self.amount_sign, 2)


class ExtractedDocument(BaseModel):
    """一份单据的抽取结果。"""

    document_type: str | None = None
    reference_no: str | None = None
    document_date: str | None = None
    issuer: str | None = None
    recipient: str | None = None
    currency: str | None = None
    payment_term: str | None = None
    subtotal: float | None = None
    tax: float | None = None
    total: float | None = None
    charges: list[ChargeLine] = Field(default_factory=list)
    identifiers: DocumentIdentifiers = Field(default_factory=DocumentIdentifiers)
    raw_text: str = ""
    #: 关键字段置信度，键为字段名
    confidence: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    method: str = "rules"
    #: 该单据对应的原始文件（含附件树）
    source: RawDocument | None = None
    #: 附件里携带的业务上下文（例如 B/L 号、箱号）
    attachment_context: dict[str, Any] = Field(default_factory=dict)

    def charge_total(self) -> float:
        return round(sum((c.signed_amount or 0.0) for c in self.charges), 2)

    def effective_reference(self) -> str | None:
        return (
            self.reference_no
            or self.identifiers.reference_no
            or self.identifiers.mbl_no
            or self.identifiers.hbl_no
            or self.identifiers.invoice_no
        )


# --------------------------------------------------------------------------- #
# 3. 标准输出层（严格对齐题目“五、标准输出格式”）
# --------------------------------------------------------------------------- #
class DocumentInfo(BaseModel):
    document_type: str | None = None
    reference_no: str | None = None
    document_date: str | None = None


class TransactionParty(BaseModel):
    issuer: str | None = None
    recipient: str | None = None


class ChargeDetails(BaseModel):
    charge_item: str | None = None
    amount: float | None = None
    currency: str | None = None
    payment_term: str | None = None


class FinancialAttributes(BaseModel):
    accounting_entity: str | None = None
    posting_type: PostingType | None = None


class AuditTrail(BaseModel):
    """处理依据（题目只要求 ai_reasoning，这里附加可复核的证据字段）。"""

    ai_reasoning: list[str] = Field(default_factory=list)
    source: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = None
    warnings: list[str] = Field(default_factory=list)
    matched_bill_id: str | None = None
    match_rule: str | None = None
    field_changes: list[dict[str, Any]] = Field(default_factory=list)


class OutputRecord(BaseModel):
    operation: OperationType
    document_info: DocumentInfo
    transaction_party: TransactionParty
    charge_details: ChargeDetails
    financial_attributes: FinancialAttributes
    audit_trail: AuditTrail

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


__all__ = [
    "AuditTrail",
    "ChargeDetails",
    "ChargeLine",
    "DocumentIdentifiers",
    "DocumentInfo",
    "ExtractedDocument",
    "FinancialAttributes",
    "OperationType",
    "OutputRecord",
    "PostingType",
    "Provenance",
    "RawDocument",
    "RawTable",
    "TransactionParty",
]
