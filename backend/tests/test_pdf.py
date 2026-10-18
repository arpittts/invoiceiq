import pymupdf

from app.services.pdf import render_pages


def make_pdf(pages: int) -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page(width=595, height=842)  # A4 in points
    return doc.tobytes()


def make_image(width: int, height: int, fmt: str, alpha: bool = False) -> bytes:
    colorspace = pymupdf.csRGB
    pixmap = pymupdf.Pixmap(colorspace, pymupdf.IRect(0, 0, width, height), alpha)
    pixmap.clear_with(200)
    return pixmap.tobytes(fmt)


def test_pdf_renders_one_png_per_page_with_long_edge_capped():
    pages = render_pages(make_pdf(3), "application/pdf", max_edge=1000)
    assert len(pages) == 3
    for page in pages:
        assert page.media_type == "image/png"
        assert page.data.startswith(b"\x89PNG")
        assert max(page.width, page.height) == 1000  # A4 is portrait, so height is the long edge
        assert abs(page.width - 595 * 1000 / 842) <= 1  # aspect ratio kept


def test_large_photo_is_downscaled_and_stays_jpeg():
    [page] = render_pages(make_image(4000, 3000, "jpeg"), "image/jpeg", max_edge=2000)
    assert page.media_type == "image/jpeg"
    assert page.data.startswith(b"\xff\xd8\xff")
    assert (page.width, page.height) == (2000, 1500)


def test_small_image_is_not_upscaled():
    [page] = render_pages(make_image(800, 600, "png"), "image/png", max_edge=2000)
    assert (page.width, page.height) == (800, 600)


def test_transparent_png_is_flattened():
    [page] = render_pages(make_image(100, 100, "png", alpha=True), "image/png", max_edge=2000)
    assert pymupdf.Pixmap(page.data).alpha == 0
