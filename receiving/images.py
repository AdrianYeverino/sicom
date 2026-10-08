"""Turns whatever photo arrives into what the app needs.

A photo can arrive as JPEG (taken from the page, or picked from the iPhone
gallery, which Safari converts) or as HEIC (picked from the Files app or
uploaded from a computer). Every photo goes through the same path: open it,
apply its EXIF rotation, and produce
- the image for the model: JPEG with a fixed long side, so every provider
  receives the same image and input tokens stay predictable;
- the thumbnail for review: a small WebP kept in the database;
- the trace of the original: SHA-256 of the uploaded bytes, size, capture date.

The original itself is not kept: it stays on the device.
"""

import hashlib
import io
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import pillow_heif
from django.conf import settings
from PIL import Image, ImageOps, UnidentifiedImageError

pillow_heif.register_heif_opener()

THUMBNAIL_SIDE = 1400
THUMBNAIL_QUALITY = 60
MODEL_QUALITY = 85

EXIF_IFD = 0x8769
DATETIME_ORIGINAL = 36867
OFFSET_TIME_ORIGINAL = 36881


class ImageError(Exception):
    """The upload cannot be used. code is stable; the message is for the person."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass
class PreparedImage:
    sha256: str
    file_size: int
    width: int
    height: int
    captured_at: datetime | None
    model_jpeg: bytes
    model_side: int
    thumbnail_webp: bytes


def _captured_at(image):
    """Capture date from EXIF. Cameras write local time; without an offset it
    is taken as the store's time zone."""
    try:
        exif = image.getexif().get_ifd(EXIF_IFD)
        raw = exif.get(DATETIME_ORIGINAL)
        if not raw:
            return None
        moment = datetime.strptime(raw.strip("\x00 "), "%Y:%m:%d %H:%M:%S")
        offset = exif.get(OFFSET_TIME_ORIGINAL)
        if offset:
            return datetime.fromisoformat(moment.isoformat() + offset.strip("\x00 "))
        return moment.replace(tzinfo=ZoneInfo(settings.TIME_ZONE))
    except (ValueError, TypeError, KeyError):
        return None


def _resized(image, side):
    copy = image.copy()
    copy.thumbnail((side, side), Image.Resampling.LANCZOS)
    return copy


def _encode(image, fmt, **options):
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def prepare(data: bytes, max_side: int | None = None) -> PreparedImage:
    max_side = max_side or settings.READER_IMAGE_MAX_SIDE
    if not data:
        raise ImageError("empty", "El archivo está vacío.")
    if len(data) > settings.MAX_UPLOAD_BYTES:
        raise ImageError("too_large", "La foto pasa de 25 MB.")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise ImageError("not_an_image", "El archivo no es una imagen que se pueda abrir.") from e

    captured_at = _captured_at(image)
    image = ImageOps.exif_transpose(image).convert("RGB")
    model = _resized(image, max_side)

    return PreparedImage(
        sha256=hashlib.sha256(data).hexdigest(),
        file_size=len(data),
        width=image.width,
        height=image.height,
        captured_at=captured_at,
        model_jpeg=_encode(model, "JPEG", quality=MODEL_QUALITY, optimize=True),
        model_side=max(model.size),
        thumbnail_webp=_encode(_resized(image, THUMBNAIL_SIDE), "WEBP", quality=THUMBNAIL_QUALITY),
    )
