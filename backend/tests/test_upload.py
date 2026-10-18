import hashlib

import pymupdf
from sqlalchemy import select

from app.config import get_settings
from app.models import FileBlob, Invoice, InvoiceFile
from app.services.storage import load_file
from tests.conftest import register_and_login

UPLOAD = "/api/v1/invoices"


def make_pdf(pages: int = 1) -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"Test invoice page {i + 1}")
    return doc.tobytes()


def make_image(fmt: str) -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False)
    return pixmap.tobytes(fmt)


def upload(client, headers, content: bytes, name: str = "invoice.pdf"):
    return client.post(UPLOAD, headers=headers, files={"file": (name, content)})


def test_upload_pdf_creates_processing_invoice_and_stores_file(client, auth_headers, db):
    content = make_pdf(pages=2)
    response = upload(client, auth_headers, content)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "processing"
    assert body["file"]["mime_type"] == "application/pdf"
    assert body["file"]["page_count"] == 2
    assert body["file"]["sha256"] == hashlib.sha256(content).hexdigest()

    invoice = db.get(Invoice, body["id"])
    assert load_file(db, invoice.file) == content


def test_upload_png_and_jpeg(client, auth_headers):
    png = upload(client, auth_headers, make_image("png"), "photo.png")
    jpeg = upload(client, auth_headers, make_image("jpeg"), "photo.jpg")
    assert png.status_code == 201 and png.json()["file"]["mime_type"] == "image/png"
    assert jpeg.status_code == 201 and jpeg.json()["file"]["mime_type"] == "image/jpeg"


def test_upload_requires_login(client):
    assert upload(client, {}, make_pdf()).status_code == 401


def test_upload_checks_real_bytes_not_filename(client, auth_headers):
    response = upload(client, auth_headers, b"just some text pretending", "invoice.pdf")
    assert response.status_code == 415


def test_upload_rejects_empty_and_corrupt_files(client, auth_headers):
    assert upload(client, auth_headers, b"").status_code == 400
    assert upload(client, auth_headers, b"%PDF-1.7 this is not really a pdf").status_code == 400


def test_upload_rejects_files_over_size_limit(client, auth_headers, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    too_big = b"%PDF-" + b"0" * (1024 * 1024)
    assert upload(client, auth_headers, too_big).status_code == 413


def test_upload_rejects_too_many_pages(client, auth_headers, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_pages", 2)
    assert upload(client, auth_headers, make_pdf(pages=3)).status_code == 400


def test_db_storage_backend_keeps_bytes_in_postgres(client, auth_headers, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_backend", "db")
    content = make_pdf()
    body = upload(client, auth_headers, content).json()

    file = db.get(InvoiceFile, body["file"]["id"])
    assert file.storage_path == f"db:{file.id}"
    assert db.get(FileBlob, file.id).data == content
    assert load_file(db, file) == content


def test_uploads_belong_to_the_uploading_user(client, db):
    alice = register_and_login(client, "alice@example.com")
    bob = register_and_login(client, "bob@example.com")
    upload(client, alice, make_pdf())
    upload(client, bob, make_pdf())
    upload(client, bob, make_pdf())

    owners = db.scalars(select(Invoice.user_id).order_by(Invoice.id)).all()
    assert owners == [1, 2, 2]
