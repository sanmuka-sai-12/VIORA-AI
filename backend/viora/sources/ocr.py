"""Optional CPU-based OCR adapter and bounded raster rendering helpers.

RapidOCR (Apache-2.0) + ONNX Runtime (MIT) is an optional runtime/worker dependency:
- Importing this module never requires rapidocr or onnxruntime to be installed.
- Production HTTP upload routes keep OCR disabled until durable background jobs in Batch 2.4.
- Handwriting and complex diagram understanding remain explicit extension points; OCR
  alone never fabricates visual understanding or handwriting interpretation.
"""

from __future__ import annotations

import hashlib
import io
import os
from dataclasses import dataclass
from typing import Any, Protocol

from PIL import Image, ImageEnhance, ImageOps

RAPIDOCR_EXTRACTOR_ID = "viora.sources.rapidocr_onnx"
RAPIDOCR_EXTRACTOR_VERSION = "3.10"
OCR_CONFIDENCE_PREPROCESS_THRESHOLD = 0.78
OCR_LOW_CONFIDENCE_THRESHOLD = 0.70
OCR_UNSUPPORTED_OR_HANDWRITING_THRESHOLD = 0.50


class OcrUnavailableError(RuntimeError):
    """Raised when an OCR backend is requested but not installed or initialized."""


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class OcrPageResult:
    lines: tuple[OcrLine, ...]
    image_width: int
    image_height: int
    engine_id: str
    engine_version: str
    mean_confidence: float
    preprocess_applied: bool = False


class OcrBackend(Protocol):
    """Extractor-independent OCR port for printed text on rasterized pages/images."""

    @property
    def engine_id(self) -> str: ...

    @property
    def engine_version(self) -> str: ...

    def is_available(self) -> bool: ...

    def recognize_image(
        self,
        image: Image.Image,
        *,
        preprocess: bool = False,
    ) -> OcrPageResult: ...


def preprocess_pil_for_ocr(image: Image.Image) -> Image.Image:
    """Lightweight deterministic PIL preprocessing (grayscale + autocontrast + sharpness)."""
    gray = ImageOps.grayscale(image)
    contrasted = ImageOps.autocontrast(gray, cutoff=1)
    sharpened = ImageEnhance.Sharpness(contrasted).enhance(1.5)
    return sharpened.convert("RGB")


def render_pdf_page_to_rgb(
    raw_pdf_bytes: bytes,
    page_index: int,
    *,
    dpi: int = 150,
    max_pixels: int = 16_000_000,
) -> Image.Image:
    """Render a single PDF page bounded by DPI and pixel count using pypdfium2."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(raw_pdf_bytes)
    try:
        if page_index < 0 or page_index >= len(doc):
            raise IndexError(f"PDF page index {page_index} out of bounds")
        page = doc[page_index]
        try:
            scale = max(72, min(dpi, 300)) / 72.0
            w_pt, h_pt = page.get_size()
            est_pixels = int(w_pt * scale) * int(h_pt * scale)
            if est_pixels > max_pixels and est_pixels > 0:
                scale *= (max_pixels / est_pixels) ** 0.5
            bitmap = page.render(scale=scale)
            try:
                pil_img = bitmap.to_pil().convert("RGB")
                return pil_img
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        doc.close()


def decode_bounded_image(
    raw_bytes: bytes,
    *,
    max_pixels: int = 16_000_000,
) -> Image.Image:
    """Safely decode PNG/JPEG bytes with decompression-bomb protection."""
    prev_max = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            img.load()
            w, h = img.size
            if w <= 0 or h <= 0 or (w * h) > max_pixels:
                raise ValueError(f"Image dimensions {w}x{h} exceed pixel limit {max_pixels}")
            return ImageOps.exif_transpose(img).convert("RGB")
    finally:
        Image.MAX_IMAGE_PIXELS = prev_max


def hash_pil_image(image: Image.Image) -> str:
    """Compute a deterministic SHA-256 digest of normalized RGB pixel dimensions and bytes."""
    rgb = image.convert("RGB")
    h = hashlib.sha256()
    h.update(f"{rgb.width}x{rgb.height}:".encode("ascii"))
    h.update(rgb.tobytes())
    return h.hexdigest()


class RapidOcrBackend:
    """Optional RapidOCR + ONNX Runtime CPU adapter (Apache-2.0 / MIT)."""

    def __init__(self) -> None:
        self._engine: Any = None
        self._available: bool | None = None

    @property
    def engine_id(self) -> str:
        return RAPIDOCR_EXTRACTOR_ID

    @property
    def engine_version(self) -> str:
        return RAPIDOCR_EXTRACTOR_VERSION

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            import onnxruntime  # noqa: F401
            import rapidocr  # noqa: F401

            self._available = True
        except ImportError:
            self._available = False
        return self._available

    def _get_engine(self) -> Any:
        if not self.is_available():
            raise OcrUnavailableError(
                "RapidOCR/ONNX Runtime is not installed in this environment"
            )
        if self._engine is None:
            from rapidocr import RapidOCR

            self._engine = RapidOCR()
        return self._engine

    def warm_up(self) -> bool:
        """Initialize the OCR engine once per worker process if available."""
        if not self.is_available():
            return False
        self._get_engine()
        return True

    def recognize_image(
        self,
        image: Image.Image,
        *,
        preprocess: bool = False,
    ) -> OcrPageResult:
        engine = self._get_engine()
        import numpy as np

        work_img = preprocess_pil_for_ocr(image) if preprocess else image.convert("RGB")
        arr = np.asarray(work_img)
        raw_res = engine(arr)

        lines: list[OcrLine] = []
        boxes = getattr(raw_res, "boxes", None)
        txts = getattr(raw_res, "txts", None)
        scores = getattr(raw_res, "scores", None)
        if boxes is not None and txts is not None and scores is not None:
            for box, txt, score in zip(boxes, txts, scores, strict=False):
                clean = str(txt).strip()
                if not clean:
                    continue
                xs = [float(pt[0]) for pt in box]
                ys = [float(pt[1]) for pt in box]
                lines.append(
                    OcrLine(
                        text=clean,
                        confidence=max(0.0, min(1.0, float(score))),
                        x0=max(0.0, min(xs)),
                        y0=max(0.0, min(ys)),
                        x1=max(0.0, max(xs)),
                        y1=max(0.0, max(ys)),
                    )
                )

        # Sort OCR lines top-to-bottom, left-to-right within same visual row (~12px)
        lines.sort(key=lambda ln: (round(ln.y0 / 12.0), ln.x0))
        mean_conf = (
            sum(ln.confidence for ln in lines) / len(lines) if lines else 0.0
        )
        return OcrPageResult(
            lines=tuple(lines),
            image_width=work_img.width,
            image_height=work_img.height,
            engine_id=self.engine_id,
            engine_version=self.engine_version,
            mean_confidence=round(mean_conf, 4),
            preprocess_applied=preprocess,
        )

    def recognize_image_adaptive(
        self,
        image: Image.Image,
        *,
        force_preprocess: bool = False,
    ) -> OcrPageResult:
        """Run raw OCR first and apply Pillow preprocessing selectively when confidence is low."""
        if force_preprocess:
            return self.recognize_image(image, preprocess=True)
        raw_res = self.recognize_image(image, preprocess=False)
        if raw_res.lines and raw_res.mean_confidence >= OCR_CONFIDENCE_PREPROCESS_THRESHOLD:
            return raw_res
        enhanced_res = self.recognize_image(image, preprocess=True)
        raw_chars = sum(len(ln.text) for ln in raw_res.lines)
        enh_chars = sum(len(ln.text) for ln in enhanced_res.lines)
        if not raw_res.lines and enhanced_res.lines:
            return enhanced_res
        if enh_chars >= int(raw_chars * 0.85) and (
            enhanced_res.mean_confidence > raw_res.mean_confidence + 0.02
            or (enh_chars > raw_chars and enhanced_res.mean_confidence >= raw_res.mean_confidence)
        ):
            return enhanced_res
        return raw_res


_PROCESS_OCR_BACKEND: RapidOcrBackend | None = None
_PROCESS_OCR_PID: int | None = None


def get_process_ocr_backend(*, warm_up: bool = False) -> RapidOcrBackend:
    """Return a process-scoped RapidOcrBackend singleton so models load once per worker process."""
    global _PROCESS_OCR_BACKEND, _PROCESS_OCR_PID
    pid = os.getpid()
    if _PROCESS_OCR_BACKEND is None or _PROCESS_OCR_PID != pid:
        _PROCESS_OCR_BACKEND = RapidOcrBackend()
        _PROCESS_OCR_PID = pid
    if warm_up:
        _PROCESS_OCR_BACKEND.warm_up()
    return _PROCESS_OCR_BACKEND
