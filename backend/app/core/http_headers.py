"""HTTP header helpers."""
from urllib.parse import quote


def content_disposition(disposition: str, filename: str) -> str:
    """RFC 6266/5987 Content-Disposition that survives ANY filename.

    HTTP headers are latin-1. A macOS screenshot name ("… at 2.37 PM.png")
    contains U+202F, so putting it raw into filename="…" makes the whole
    response 500. Send an ASCII fallback plus the UTF-8 filename* form that
    every browser prefers."""
    fallback = (filename or "file").encode("ascii", "replace").decode("ascii").replace('"', "'")
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename or 'file')}"
