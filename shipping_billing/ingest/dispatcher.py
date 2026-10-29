"""文件分发器：按扩展名/魔数选择解析器，并递归处理附件。"""

from __future__ import annotations

from pathlib import Path

from ..io_utils import get_logger, sha256_bytes
from ..models import RawDocument
from .eml_loader import parse_eml
from .excel_loader import EXCEL_EXTS, parse_excel
from .image_loader import IMAGE_EXTS, parse_image
from .ocr_client import OcrClient
from .pdf_loader import parse_pdf
from .word_loader import WORD_EXTS, parse_word

logger = get_logger("ingest")

SUPPORTED_EXTS = {".eml", ".pdf", *IMAGE_EXTS, *EXCEL_EXTS, *WORD_EXTS, ".json"}

MAX_DEPTH = 5


def _sniff_kind(path: Path) -> str:
    """按魔数兜底判断类型（有些附件 MIME 是 application/octet-stream）。"""
    try:
        head = path.read_bytes()[:8]
    except Exception:  # noqa: BLE001
        return "unknown"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8") or head.startswith(b"II*\x00"):
        return "image"
    if head.startswith(b"PK"):
        suffix = path.suffix.lower()
        if suffix in EXCEL_EXTS:
            return "excel"
        if suffix in WORD_EXTS:
            return "word"
    return "unknown"


def load_document(
    path: Path,
    *,
    ocr_client: OcrClient,
    role: str = "primary",
    depth: int = 0,
    work_dir: Path | None = None,
) -> RawDocument:
    path = Path(path)
    if depth > MAX_DEPTH:
        raise RuntimeError(f"附件递归层级超过 {MAX_DEPTH}: {path}")

    suffix = path.suffix.lower()
    kind = "unknown"
    if suffix == ".eml":
        kind = "eml"
    elif suffix == ".pdf":
        kind = "pdf"
    elif suffix in IMAGE_EXTS:
        kind = "image"
    elif suffix in EXCEL_EXTS:
        kind = "excel"
    elif suffix in WORD_EXTS:
        kind = "word"
    elif suffix == ".json":
        kind = "json"
    else:
        kind = _sniff_kind(path)

    settings = ocr_client.settings
    work = Path(work_dir or settings.work_dir or ".work")

    if kind == "eml":
        return parse_eml(
            path, ocr_client=ocr_client, work_dir=work, role=role, depth=depth
        )
    if kind == "pdf":
        return parse_pdf(path, ocr_client=ocr_client, role=role)
    if kind == "image":
        return parse_image(path, ocr_client=ocr_client, role=role)
    if kind == "excel":
        return parse_excel(path, ocr_client=ocr_client, role=role)
    if kind == "word":
        return parse_word(path, ocr_client=ocr_client, role=role, work_dir=work)
    if kind == "json":
        return RawDocument(
            doc_id=sha256_bytes(path.read_bytes())[:12],
            path=str(path),
            filename=path.name,
            kind="json",
            sha256=sha256_bytes(path.read_bytes()),
            size=path.stat().st_size,
            text=path.read_text(encoding="utf-8-sig", errors="replace"),
            metadata={"role": "ledger-or-structured"},
            warnings=["JSON 文件按结构化账单处理，不参与费用抽取"],
        )

    # 未知类型：保留元信息，跳过内容解析
    data = path.read_bytes()
    return RawDocument(
        doc_id=sha256_bytes(data)[:12],
        path=str(path),
        filename=path.name,
        kind="unknown",
        sha256=sha256_bytes(data),
        size=len(data),
        warnings=[f"不支持的文件类型: {suffix or '(无扩展名)'}"],
    )


def collect_input_files(inputs: list[str | Path], recursive: bool = True) -> list[Path]:
    """把目录/文件列表展开成待处理文件列表（稳定排序，保证可复现）。"""
    files: list[Path] = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            pattern = "**/*" if recursive else "*"
            files.extend(sorted(x for x in p.glob(pattern) if x.is_file()))
        elif p.is_file():
            files.append(p)
        else:
            logger.warning("输入不存在，已跳过: %s", p)
    # 去掉隐藏文件与 Office 临时文件
    filtered = [
        f
        for f in files
        if not f.name.startswith(("~$", ".")) and f.suffix.lower() != ".zip"
    ]
    # 去重（同一文件被重复传入）
    seen: set[str] = set()
    unique: list[Path] = []
    for f in filtered:
        key = str(f.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)
    return unique


__all__ = ["collect_input_files", "load_document", "SUPPORTED_EXTS"]
