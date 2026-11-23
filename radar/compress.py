"""Make attachments light enough to send by the hundred.

Scanned documents (transcripts, certificates) are usually stored as page photos of several
thousand pixels.  Each image is re-saved at about 200 dpi for its printed size — sharp on
screen and on paper — as JPEG.  Text, vector drawings, links and page layout are untouched,
and the result is only kept when it is noticeably smaller than the original.
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, NumberObject

Image.MAX_IMAGE_PIXELS = None  # scans are large on purpose; they come from your own files

TARGET_DPI = 200
JPEG_QUALITY = 72
WORTH_IT = 0.85          # keep the result only when it saves at least 15 %


def _page_size_inches(page: Any) -> tuple[float, float]:
    box = page.mediabox
    return float(box.width) / 72, float(box.height) / 72


def _open_raw(xobj: Any) -> Image.Image | None:
    """Decode an image XObject with Pillow directly (pypdf refuses very large scans)."""
    filters = xobj.get("/Filter")
    filters = [str(item) for item in filters] if isinstance(filters, list) else [str(filters)] if filters else []
    try:
        if filters and filters[-1] in ("/DCTDecode", "/JPXDecode"):
            return Image.open(io.BytesIO(xobj.get_data() if len(filters) > 1 else xobj._data))
        width, height = int(xobj["/Width"]), int(xobj["/Height"])
        if int(xobj.get("/BitsPerComponent", 8)) != 8:
            return None
        space = str(xobj.get("/ColorSpace", "/DeviceRGB"))
        mode = {"/DeviceRGB": "RGB", "/DeviceGray": "L", "/DeviceCMYK": "CMYK"}.get(space)
        if mode is None:
            return None
        return Image.frombytes(mode, (width, height), xobj.get_data())
    except Exception:  # an image Pillow cannot read is simply left as it is
        return None


def compress_pdf(data: bytes) -> bytes:
    reader = PdfReader(io.BytesIO(data))
    writer = PdfWriter(clone_from=reader)
    done: set[int] = set()
    for page in writer.pages:
        width_in, height_in = _page_size_inches(page)
        resources = page.get("/Resources")
        xobjects = resources.get_object().get("/XObject") if resources else None
        if not xobjects:
            continue
        for name in list(xobjects.get_object().keys()):
            reference = xobjects.get_object().raw_get(name)
            xobj = reference.get_object()
            key = getattr(reference, "idnum", id(xobj))
            if key in done or xobj.get("/Subtype") != "/Image" or "/SMask" in xobj or "/Mask" in xobj or xobj.get("/ImageMask"):
                continue
            done.add(key)
            image = _open_raw(xobj)
            if image is None:
                continue
            # Never more pixels than 200 dpi over the whole page needs.
            limit_w, limit_h = int(width_in * TARGET_DPI), int(height_in * TARGET_DPI)
            scale = min(1.0, limit_w / image.width, limit_h / image.height) if limit_w and limit_h else 1.0
            if image.mode == "CMYK":
                image = image.convert("RGB")
            elif image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            if scale < 0.98:
                image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
            encoded = buffer.getvalue()
            if len(encoded) >= len(xobj._data) and scale >= 0.98:
                continue  # already as small as it gets
            xobj._data = encoded
            xobj[NameObject("/Filter")] = NameObject("/DCTDecode")
            xobj[NameObject("/Width")] = NumberObject(image.width)
            xobj[NameObject("/Height")] = NumberObject(image.height)
            xobj[NameObject("/BitsPerComponent")] = NumberObject(8)
            xobj[NameObject("/ColorSpace")] = NameObject("/DeviceGray" if image.mode == "L" else "/DeviceRGB")
            for stale in ("/DecodeParms", "/Decode", "/Length1"):
                if stale in xobj:
                    del xobj[stale]
    for page in writer.pages:
        page.compress_content_streams()
    writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def compress_image(data: bytes, max_side: int = 2200) -> bytes:
    image = Image.open(io.BytesIO(data))
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    if max(image.size) > max_side:
        scale = max_side / max(image.size)
        image = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    return buffer.getvalue()


def compress(data: bytes, extension: str) -> bytes | None:
    """The smaller version of a file, or None when compressing would not help."""
    extension = extension.lower()
    if extension == ".pdf":
        smaller = compress_pdf(data)
    elif extension in (".jpg", ".jpeg"):  # PNG stays PNG: re-encoding it as JPEG would change its type
        smaller = compress_image(data)
    else:
        return None
    return smaller if len(smaller) <= len(data) * WORTH_IT else None
