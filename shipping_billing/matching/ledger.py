"""已有账单台账（Ledger）。

以"一行一条费用"的扁平结构存储，便于按题目要求的 4 类规则做匹配，
写回时也能精确定位被修改的那一行。
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..io_utils import get_logger, read_json, write_json

logger = get_logger("ledger")


def norm_key(value: str | None) -> str:
    """编号归一化：忽略空格/横线/大小写，'DC-108478' == 'DC108478'。"""
    if not value:
        return ""
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


class BillLine(BaseModel):
    """台账中的一条费用行。"""

    model_config = ConfigDict(extra="allow")

    bill_id: str
    line_id: str | None = None
    charge_item: str
    amount: float | None = None
    currency: str | None = None
    payment_term: str | None = None

    document_type: str | None = None
    reference_no: str | None = None
    document_date: str | None = None
    issuer: str | None = None
    recipient: str | None = None

    mbl_no: str | None = None
    hbl_no: str | None = None
    bl_no: str | None = None
    booking_no: str | None = None
    vessel: str | None = None
    containers: list[str] = Field(default_factory=list)

    posting_type: str | None = None
    accounting_entity: str | None = None
    source: str | None = None
    updated_at: str | None = None
    note: str | None = None

    def identity(self) -> str:
        return self.line_id or self.bill_id

    def key_text(self) -> str:
        return f"{self.bill_id}/{self.charge_item}"

    def apply_changes(self, changes: dict[str, Any]) -> None:
        for field_name, value in changes.items():
            if hasattr(self, field_name):
                setattr(self, field_name, value)
            else:  # 未知字段进 extra
                self.__pydantic_extra__ = {
                    **(self.__pydantic_extra__ or {}),
                    field_name: value,
                }
        self.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")


class Ledger(BaseModel):
    entity: str = ""
    version: int = 1
    updated_at: str | None = None
    lines: list[BillLine] = Field(default_factory=list)

    # ---------------------------------------------------------------- #
    @classmethod
    def load(cls, path: Path | str, *, entity: str | None = None) -> "Ledger":
        data = read_json(path, None)
        if data is None:
            logger.warning("台账文件不存在，使用空台账: %s", path)
            return cls(entity=entity or "")
        if isinstance(data, list):  # 兼容纯数组格式
            data = {"lines": data}
        ledger = cls.model_validate(data)
        if entity and not ledger.entity:
            ledger.entity = entity
        return ledger

    def save(self, path: Path | str) -> Path:
        self.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        return write_json(path, self.model_dump(mode="json"))

    # ---------------------------------------------------------------- #
    def scope_lines(self, entity: str | None) -> list[BillLine]:
        """按记账主体隔离台账：只返回"属于该主体"或"未标注主体"的行。

        同一个平台可能替多家公司管账，跨主体匹配会造成张冠李戴。
        """
        if not entity:
            return self.lines
        key = entity.strip().lower()
        return [
            line
            for line in self.lines
            if not line.accounting_entity or line.accounting_entity.strip().lower() == key
        ]

    def by_reference(self, reference: str | None) -> list[BillLine]:
        key = norm_key(reference)
        if not key:
            return []
        return [line for line in self.lines if norm_key(line.reference_no) == key]

    def candidate_lines(
        self,
        *,
        reference_no: str | None = None,
        mbl_no: str | None = None,
        hbl_no: str | None = None,
        bl_no: str | None = None,
        booking_no: str | None = None,
        charge_item: str | None = None,
        entity: str | None = None,
    ) -> list[BillLine]:
        candidates = self.scope_lines(entity)
        keys = {
            "reference_no": norm_key(reference_no),
            "mbl_no": norm_key(mbl_no),
            "hbl_no": norm_key(hbl_no),
            "bl_no": norm_key(bl_no),
            "booking_no": norm_key(booking_no),
        }
        for field_name, key in keys.items():
            if not key:
                continue
            subset = [line for line in candidates if norm_key(getattr(line, field_name)) == key]
            if subset:
                candidates = subset
        if charge_item:
            filtered = [
                line
                for line in candidates
                if (line.charge_item or "").strip().lower() == charge_item.strip().lower()
            ]
            candidates = filtered
        return candidates

    def add(self, line: BillLine) -> None:
        self.lines.append(line)

    def next_bill_id(self, prefix: str = "BILL") -> str:
        numbers = []
        for line in self.lines:
            match = re.match(rf"{prefix}-(\d+)", line.bill_id or "")
            if match:
                numbers.append(int(match.group(1)))
        return f"{prefix}-{(max(numbers) + 1) if numbers else 1001:04d}"

    def stats(self) -> dict[str, int]:
        return {
            "lines": len(self.lines),
            "bills": len({line.bill_id for line in self.lines}),
        }


__all__ = ["BillLine", "Ledger", "norm_key"]
