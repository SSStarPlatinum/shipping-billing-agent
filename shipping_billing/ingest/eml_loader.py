"""邮件（.eml）解析：邮件头、正文（text/plain 优先，回退 HTML）、附件递归。"""

from __future__ import annotations

import email
import email.policy
import html
import re
from email.message import Message
from pathlib import Path

from ..io_utils import ensure_dir, get_logger, normalize_text, sha256_bytes
from ..models import RawDocument
from .ocr_client import OcrClient

logger = get_logger("eml")

_TAG_RE = re.compile(r"<[^>]+>")
_STYLE_RE = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")
_BLOCK_RE = re.compile(r"(?i)</?(p|div|br|tr|li|h[1-6])[^>]*>")


def html_to_text(raw_html: str) -> str:
    """轻量 HTML 转文本（不引入 bs4 依赖）。"""
    if not raw_html:
        return ""
    text = _STYLE_RE.sub(" ", raw_html)
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    return normalize_text(text)


def _decode_part(part: Message) -> str:
    try:
        return part.get_content()
    except (KeyError, LookupError):
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except (LookupError, UnicodeDecodeError):
            return payload.decode("utf-8", errors="replace")


def _header(msg: Message, name: str) -> str | None:
    value = msg.get(name)
    if value is None:
        return None
    return str(value).strip()


def parse_eml(
    path: Path,
    *,
    ocr_client: OcrClient,
    work_dir: Path,
    role: str = "primary",
    depth: int = 0,
) -> RawDocument:
    from .dispatcher import load_document  # 延迟导入避免循环

    raw_bytes = path.read_bytes()
    sha = sha256_bytes(raw_bytes)
    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)

    meta = {
        "from": _header(msg, "From"),
        "to": _header(msg, "To"),
        "cc": _header(msg, "Cc"),
        "subject": _header(msg, "Subject"),
        "date": _header(msg, "Date"),
        "message_id": _header(msg, "Message-ID"),
    }

    text_plain: str | None = None
    text_html: str | None = None
    attachments: list[RawDocument] = []
    warnings: list[str] = []
    inline_parts: list[dict[str, str]] = []

    attach_dir = ensure_dir(Path(work_dir) / "attachments" / sha[:12])

    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        filename = part.get_filename()
        content_id = part.get("Content-ID")

        if ctype == "text/plain" and text_plain is None:
            text_plain = _decode_part(part)
            continue
        if ctype == "text/html" and text_html is None:
            text_html = _decode_part(part)
            continue

        if not filename:
            continue

        payload = part.get_payload(decode=True) or b""
        if not payload:
            continue

        # 内嵌签名图（有 Content-ID 且是图片）不作为业务单据处理
        if content_id and ctype.startswith("image/"):
            inline_parts.append(
                {
                    "filename": filename,
                    "content_type": ctype,
                    "content_id": str(content_id),
                    "size": str(len(payload)),
                }
            )
            continue

        safe_name = re.sub(r"[\\/:*?\"<>|]+", "_", filename).strip() or "attachment.bin"
        target = attach_dir / safe_name
        counter = 1
        while target.exists():
            target = attach_dir / f"{target.stem}_{counter}{target.suffix}"
            counter += 1
        target.write_bytes(payload)

        try:
            child = load_document(
                target, ocr_client=ocr_client, role="attachment", depth=depth + 1
            )
            attachments.append(child)
        except Exception as exc:  # noqa: BLE001 - 单个附件失败不拖垮整封邮件
            warnings.append(f"附件解析失败 {safe_name}: {type(exc).__name__}: {exc}")
            logger.warning("附件解析失败 %s: %s", target, exc)

    if text_plain and text_plain.strip():
        body = normalize_text(text_plain)
        body_source = "text/plain"
    elif text_html:
        body = html_to_text(text_html)
        body_source = "text/html"
    else:
        body = ""
        body_source = "none"
        warnings.append("邮件正文为空")

    meta["body_source"] = body_source
    if inline_parts:
        meta["inline_parts"] = inline_parts
    meta["attachment_count"] = len(attachments)
    meta["attachment_names"] = [a.filename for a in attachments]

    if body:
        # 把附件名也拼进正文，便于"附件是否存在 B/L"这类判断
        header_block = "\n".join(
            v for v in (f"Subject: {meta['subject']}" if meta.get("subject") else "",) if v
        )
        full_text = (header_block + "\n" + body).strip()
    else:
        full_text = ""

    return RawDocument(
        doc_id=sha[:12],
        path=str(path),
        filename=path.name,
        kind="eml",
        sha256=sha,
        size=len(raw_bytes),
        text=full_text,
        pages=[full_text] if full_text else [],
        metadata=meta,
        attachments=attachments,
        warnings=warnings,
    )


__all__ = ["parse_eml", "html_to_text"]
