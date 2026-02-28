from __future__ import annotations

import base64
import io
from urllib.parse import quote


def build_qr_data_uri(data: str) -> str | None:
    try:
        import qrcode  # type: ignore[import-not-found]
    except ImportError:
        return None

    image = qrcode.make(data)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def join_link_with_key(base_join_url: str, join_key: str) -> str:
    return f"{base_join_url}?key={quote(join_key)}"
