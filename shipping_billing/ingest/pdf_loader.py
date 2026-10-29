"""PDF 解析：文本层优先，扫描件转 OCR。

为什么不是"PDF 一律走 OCR"？
* 文本层是原始矢量文字，准确率 100%，且不受网络/服务波动影响；
* 扫描件（文本层字符数过少）才需要 OCR；
* 通过 ``--ocr-mode api`` 可以强制所有 PDF 走 OCR，用于验收"调用 OCR API"。
"""

from __future__ import annotations

from pathlib import Path

from ..io_utils import get_logger, normalize_text, sha256_bytes
from ..models import RawDocument, RawTable
from .ocr_client import OcrClient

logger = get_logger("pdf")


def _extract_with_pypdf(raw_bytes: bytes) -> tuple[list[str], str | None]:
    try:
        import io

        import pypdf
    except ImportError:  # pragma: no cover
        return [], "未安装 pypdf，无法读取 PDF 文本层"

    try:
        reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
        pages: list[str] = []
        for page in reader.pages:
            try:
                pages.append(normalize_text(page.extract_text() or ""))
            except Exception as exc:  # noqa: BLE001
                pages.append("")
                logger.debug("单页解析失败: %s", exc)
        return pages, None
    except Exception as exc:  # noqa: BLE001
        return [], f"pypdf 解析失败: {type(exc).__name__}: {exc}"


def parse_pdf(path: Path, *, ocr_client: OcrClient, role: str = "primary") -> RawDocument:
    settings = ocr_client.settings
    raw_bytes = path.read_bytes()
    sha = sha256_bytes(raw_bytes)
    warnings: list[str] = []

    if settings.ocr_mode == "api":
        pages, err = [], "强制 OCR 模式"
    else:
        pages, err = _extract_with_pypdf(raw_bytes)
        if err:
            warnings.append(err)

    text = "\n\n".join(p for p in pages if p)
    ocr_source: str | None = "pdf_text_layer" if text else None
    ocr_confidence: float | None = 1.0 if text else None
    tables: list[RawTable] = []

    need_ocr = settings.ocr_mode == "api" or len(text) < settings.pdf_text_layer_min_chars
    if need_ocr:
        outcome = ocr_client.process_file(path)
        if outcome.text or outcome.tables:
            ocr_text = normalize_text(outcome.text)
            if ocr_text:
                text = ocr_text
                pages = [ocr_text]
                ocr_source = outcome.source
                ocr_confidence = outcome.confidence
            tables.extend(outcome.tables)
            warnings.extend(outcome.warnings)
        else:
            warnings.extend(outcome.warnings or ["OCR 未返回内容"])
            if text:
                warnings.append("OCR 未返回内容，使用 PDF 文本层结果")
            else:
                warnings.append("PDF 无可读取文本，且 OCR 未返回内容")
    elif settings.ocr_mode in {"cache", "auto"}:
        # 文本层足够时，仍读取 OCR 缓存里的表格（若曾经识别过），用于结构化补强
        cached = ocr_client._load_cache(sha)  # noqa: SLF001 - 同一模块族的受控访问
        if cached and cached.tables:
            tables.extend(cached.tables)

    return RawDocument(
        doc_id=sha[:12],
        path=str(path),
        filename=path.name,
        kind="pdf",
        sha256=sha,
        size=len(raw_bytes),
        text=text,
        pages=pages,
        tables=tables,
        metadata={
            "page_count": len(pages),
            "ocr_source": ocr_source,
            "has_text_layer": bool(err is None and text),
        },
        warnings=warnings,
        ocr_source=ocr_source,
        ocr_confidence=ocr_confidence,
    )


__all__ = ["parse_pdf"]
