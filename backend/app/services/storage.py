"""Where uploaded file bytes live.

Two backends, picked by STORAGE_BACKEND:
- "local": files on disk under UPLOAD_DIR (development).
- "db": bytes in the file_blobs table (production hosts whose disk is wiped on restart).

storage_path records which one was used, so files stay readable if the setting changes later.
"""

from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import FileBlob, InvoiceFile

DB_PREFIX = "db:"

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


def load_file(db: Session, file: InvoiceFile) -> bytes:
    if file.storage_path.startswith(DB_PREFIX):
        blob = db.get(FileBlob, file.id)
        if blob is None:
            raise FileNotFoundError(f"No stored bytes for file {file.id}")
        return blob.data
    return (Path(get_settings().upload_dir) / file.storage_path).read_bytes()
