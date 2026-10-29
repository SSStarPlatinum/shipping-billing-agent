"""多格式文档解析层。"""

from __future__ import annotations

from .dispatcher import SUPPORTED_EXTS, collect_input_files, load_document
from .ocr_client import OcrClient, OcrError, OcrOutcome

__all__ = [
    "OcrClient",
    "OcrError",
    "OcrOutcome",
    "SUPPORTED_EXTS",
    "collect_input_files",
    "load_document",
]
