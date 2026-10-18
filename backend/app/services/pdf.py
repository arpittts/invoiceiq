"""Turn an uploaded file into page images the vision model can read.

No OCR: the model reads the pixels directly. We only control size, because larger
images cost more tokens and anything past the model's limit is wasted.
"""

from dataclasses import dataclass

import pymupdf


@dataclass(frozen=True)
class PageImage:
    data: bytes
    media_type: str  # "image/png" or "image/jpeg"
    width: int
    height: int


def _fit_scale(width: float, height: float, max_edge: int) -> float:
    """Scale factor so the longer side becomes max_edge (never upscale photos)."""
    return max_edge / max(width, height)


def render_pages(content: bytes, mime_type: str, max_edge: int) -> list[PageImage]:
    if mime_type == "application/pdf":
        return _render_pdf(content, max_edge)
    return [_resize_image(content, mime_type, max_edge)]


def _render_pdf(content: bytes, max_edge: int) -> list[PageImage]:
    pages = []
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        for page in doc:
            # PDF pages are vectors, so render them at exactly the size we want.
            scale = _fit_scale(page.rect.width, page.rect.height, max_edge)
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            # PNG keeps printed text sharp and compresses well on white pages.
            pages.append(PageImage(pixmap.tobytes("png"), "image/png", pixmap.width, pixmap.height))
    return pages


def _resize_image(content: bytes, mime_type: str, max_edge: int) -> PageImage:
    pixmap = pymupdf.Pixmap(content)
    if pixmap.alpha:
        pixmap = pymupdf.Pixmap(pixmap, 0)  # drop transparency
    if max(pixmap.width, pixmap.height) > max_edge:
        scale = _fit_scale(pixmap.width, pixmap.height, max_edge)
        pixmap = pymupdf.Pixmap(
            pixmap, round(pixmap.width * scale), round(pixmap.height * scale), None
        )
    if mime_type == "image/jpeg":
        # Photos stay JPEG: as PNG they can grow past the API's per-image size limit.
        return PageImage(
            pixmap.tobytes("jpeg", jpg_quality=90), "image/jpeg", pixmap.width, pixmap.height
        )
    return PageImage(pixmap.tobytes("png"), "image/png", pixmap.width, pixmap.height)
