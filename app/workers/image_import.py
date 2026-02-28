from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ImageImportRequest:
    map_id: str
    filename: str
    mime_type: str


def enqueue_image_import(request: ImageImportRequest) -> dict[str, str]:
    return {
        "status": "queued",
        "map_id": request.map_id,
        "filename": request.filename,
        "mime_type": request.mime_type,
    }
