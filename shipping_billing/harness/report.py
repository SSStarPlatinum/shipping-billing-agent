"""运行报告（Markdown）：给人看的可复核产物。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

OPERATION_LABELS = {
    "CREATE": "新增账单",
    "UPDATE": "更新账单",
    "NO_CHANGE": "无需变更",
    "PENDING_REVIEW": "转人工复核",
}


def _fmt_amount(value: float | None) -> str:
    return f"{value:,.2f}" if isinstance(value, (int, float)) else "null"


def build_markdown(
    *,
    records: list[Any],
    summary: dict[str, Any],
    settings: Any,
    run_log: dict[str, Any] | None = None,
    documents: list[Any] | None = None,
    deterministic: bool = True,
    extra_markdown: str = "",
) -> str:
    lines: list[str] = []
    lines.append("# 航运智能账单助手 · 运行报告")
    lines.append("")
    lines.append("## 1. 概览")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---|")
    lines.append(f"| 记账主体 | {settings.accounting_entity} |")
    lines.append(f"| OCR 模式 | {settings.ocr_mode} |")
    lines.append(f"| LLM 增强 | {'开启：' + settings.llm_model if settings.llm_enabled else '关闭（纯规则，确定性）'} |")
    lines.append(f"| 处理文件数 | {summary.get('files', 0)} |")
    lines.append(f"| 解析单据数 | {summary.get('documents', 0)} |")
    lines.append(f"| 输出记录数 | {summary.get('records', 0)} |")
    lines.append(f"| 总耗时 | {summary.get('duration_s', 0):.2f}s |")
    ops = summary.get("operations", {})
    for key in ("CREATE", "UPDATE", "NO_CHANGE", "PENDING_REVIEW"):
        lines.append(f"| {OPERATION_LABELS.get(key, key)} | {ops.get(key, 0)} |")
    postings = summary.get("posting", {})
    lines.append(f"| AR（应收） | {postings.get('AR', 0)} |")
    lines.append(f"| AP（应付） | {postings.get('AP', 0)} |")
    lines.append(f"| 方向待定 | {postings.get('unknown', 0)} |")
    lines.append("")

    lines.append("## 2. 输出记录明细")
    lines.append("")
    lines.append("| # | 操作 | 单据类型 | 单据编号 | 日期 | 签发方 → 接收方 | 费用项目 | 金额 | 币种 | 付款 | AR/AP | 置信度 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for index, record in enumerate(records, start=1):
        doc = record.document_info
        party = f"{record.transaction_party.issuer or '-'} → {record.transaction_party.recipient or '-'}"
        charge = record.charge_details
        lines.append(
            f"| {index} | {record.operation} | {doc.document_type or '-'} | {doc.reference_no or '-'} | "
            f"{doc.document_date or '-'} | {party} | {charge.charge_item or '-'} | "
            f"{_fmt_amount(charge.amount)} | {charge.currency or '-'} | {charge.payment_term or '-'} | "
            f"{record.financial_attributes.posting_type or '-'} | {record.audit_trail.confidence or 0:.2f} |"
        )
    lines.append("")

    lines.append("## 3. 处理依据（审计链）")
    lines.append("")
    for index, record in enumerate(records, start=1):
        lines.append(f"### 记录 {index}：{record.charge_details.charge_item} · {_fmt_amount(record.charge_details.amount)} {record.charge_details.currency or ''} · {record.operation}")
        lines.append("")
        source = record.audit_trail.source or {}
        lines.append(
            f"- 来源文件：`{source.get('file')}`（{source.get('kind')}，{source.get('locator') or '整篇'}，"
            f"抽取方式 {source.get('extractor')}，OCR {source.get('ocr_source') or '未使用'}）"
        )
        if record.audit_trail.matched_bill_id:
            lines.append(f"- 命中台账：{record.audit_trail.matched_bill_id}（{record.audit_trail.match_rule}）")
        for reason in record.audit_trail.ai_reasoning:
            lines.append(f"- {reason}")
        if record.audit_trail.warnings:
            lines.append(f"- ⚠️ 提示：{'；'.join(record.audit_trail.warnings[:6])}")
        lines.append("")

    if documents:
        lines.append("## 4. 单据解析情况")
        lines.append("")
        lines.append("| 文件 | 类型 | 单据类型 | 费用条数 | 明细合计 | 单据总计 | OCR 来源 |")
        lines.append("|---|---|---|---|---|---|---|")
        for doc in documents:
            filename = doc.source.filename if doc.source else "-"
            kind = doc.source.kind if doc.source else "-"
            ocr = doc.source.ocr_source if doc.source else None
            lines.append(
                f"| {filename} | {kind} | {doc.document_type or '-'} | {len(doc.charges)} | "
                f"{_fmt_amount(doc.charge_total())} | {_fmt_amount(doc.total)} | {ocr or '-'} |"
            )
        lines.append("")

    issues = summary.get("issues", {})
    if any(issues.values()):
        lines.append("## 5. 校验统计")
        lines.append("")
        lines.append(f"- 错误：{issues.get('error', 0)}")
        lines.append(f"- 警告：{issues.get('warning', 0)}")
        lines.append(f"- 提示：{issues.get('info', 0)}")
        lines.append("")

    if run_log:
        lines.append("## 6. 逐文件运行日志")
        lines.append("")
        lines.append("| 文件 | 状态 | 耗时(ms) | 记录数 | 错误 |")
        lines.append("|---|---|---|---|---|")
        for entry in run_log.get("files", []):
            lines.append(
                f"| {entry.get('file')} | {entry.get('status')} | {entry.get('duration_ms')} | "
                f"{entry.get('records')} | {entry.get('error') or '-'} |"
            )
        lines.append("")

    if extra_markdown:
        lines.append(extra_markdown.rstrip())
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "> 本报告由 `python -m shipping_billing` 自动生成。"
        + ("所有记录均由确定性规则产出，可复现。" if deterministic else "本批次包含模型推理，结果可能随模型版本变化。")
    )
    lines.append("")
    return "\n".join(lines)


def write_markdown(path: Path | str, content: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


__all__ = ["build_markdown", "write_markdown"]
