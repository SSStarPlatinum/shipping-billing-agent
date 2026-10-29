"""图片解析：必须走 OCR。"""

from __future__ import annotations

from pathlib import Path

from ..io_utils import get_logger, normalize_text, sha256_bytes
from ..models import RawDocument
from .ocr_client import OcrClient

logger = get_logger("image")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}


def parse_image(path: Path, *, ocr_client: OcrClient, role: str = "primary") -> RawDocument:
    raw_bytes = path.read_bytes()
    sha = sha256_bytes(raw_bytes)
    warnings: list[str] = []
    meta: dict[str, object] = {}

    try:  # 可选：记录尺寸，便于排查 OCR 分辨率不足
        from PIL import Image

        with Image.open(path) as img:
            meta["width"], meta["height"] = img.size
            meta["mode"] = img.mode
            if img.width < 900:
                warnings.append(f"图片宽度仅 {img.width}px，OCR 准确率可能受影响")
    except Exception:  # noqa: BLE001 - 没有 Pillow 不影响主流程
        pass

    outcome = ocr_client.process_file(path)
    text = normalize_text(outcome.text)
    warnings.extend(outcome.warnings)
    if not text and not outcome.tables:
        warnings.append("OCR 未识别出文字")

    return RawDocument(
        doc_id=sha[:12],
        path=str(path),
        filename=path.name,
        kind="image",
        sha256=sha,
        size=len(raw_bytes),
        text=text,
        pages=[text] if text else [],
        tables=outcome.tables,
        metadata={**meta, "ocr_source": outcome.source},
        warnings=warnings,
        ocr_source=outcome.source,
        ocr_confidence=outcome.confidence,
    )


__all__ = ["parse_image", "IMAGE_EXTS"]
