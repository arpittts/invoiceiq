"""Where uploaded file bytes live.

Two backends, picked by STORAGE_BACKEND:
- "local": files on disk under UPLOAD_DIR (development).
- "db": bytes in the file_blobs table (production hosts whose disk is wiped on restart).
- "bundle:" (read-only): sample files shipped inside the app (app/demo/), shared by every
  demo account, so a demo visitor's invoices take no storage of their own.

storage_path records which one was used, so files stay readable if the setting changes later.
"""

from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import FileBlob, InvoiceFile

DB_PREFIX = "db:"
BUNDLE_PREFIX = "bundle:"
BUNDLE_DIR = (Path(__file__).resolve().parent.parent / "demo").resolve()

EXTENSIONS = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg"}


def save_file(db: Session, file: InvoiceFile, content: bytes) -> str:
    """Store the bytes for an InvoiceFile that has already been flushed (so it has an id).

    Returns the storage_path to save on the record. The caller commits the session.
    """
    settings = get_settings()
    if settings.storage_backend == "db":
        db.add(FileBlob(file_id=file.id, data=content))
        return f"{DB_PREFIX}{file.id}"

    # Name files by id, not by the user's filename, so names can't escape the folder.
    relative = Path(str(file.user_id)) / f"{file.id}{EXTENSIONS[file.mime_type]}"
    target = Path(settings.upload_dir) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return str(relative)


def bundled(relative: str) -> str:
    """The storage_path of a sample file shipped in app/demo/."""
    return f"{BUNDLE_PREFIX}{relative}"


def load_file(db: Session, file: InvoiceFile) -> bytes:
    if file.storage_path.startswith(BUNDLE_PREFIX):
        path = (BUNDLE_DIR / file.storage_path.removeprefix(BUNDLE_PREFIX)).resolve()
        if not path.is_relative_to(BUNDLE_DIR):  # never read outside the bundle
            raise FileNotFoundError(file.storage_path)
        return path.read_bytes()
    if file.storage_path.startswith(DB_PREFIX):
        blob = db.get(FileBlob, file.id)
        if blob is None:
            raise FileNotFoundError(f"No stored bytes for file {file.id}")
        return blob.data
    return (Path(get_settings().upload_dir) / file.storage_path).read_bytes()
