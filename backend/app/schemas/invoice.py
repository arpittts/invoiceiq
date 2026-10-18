from datetime import datetime

from pydantic import BaseModel, ConfigDict


class InvoiceFileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    original_name: str
    mime_type: str
    page_count: int
    sha256: str


class InvoiceUploadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    created_at: datetime
    file: InvoiceFileResponse
