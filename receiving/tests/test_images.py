import hashlib
import io
from datetime import timedelta

from django.test import SimpleTestCase, override_settings
from PIL import Image

from receiving import images


def photo(size=(3024, 4032), fmt="JPEG", exif=None):
    image = Image.new("RGB", size, "white")
    buffer = io.BytesIO()
    options = {"exif": exif} if exif is not None else {}
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def exif(orientation=None, taken=None, offset=None):
    data = Image.Exif()
    if orientation:
        data[0x0112] = orientation
    if taken:
        ifd = data.get_ifd(images.EXIF_IFD)
        ifd[images.DATETIME_ORIGINAL] = taken
        if offset:
            ifd[images.OFFSET_TIME_ORIGINAL] = offset
    return data


class PrepareTest(SimpleTestCase):
    def test_a_jpeg_is_resized_for_the_model_and_keeps_its_trace(self):
        data = photo()
        prepared = images.prepare(data, max_side=2000)
        self.assertEqual(prepared.sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(prepared.file_size, len(data))
        self.assertEqual((prepared.width, prepared.height), (3024, 4032))
        self.assertEqual(prepared.model_side, 2000)
        model = Image.open(io.BytesIO(prepared.model_jpeg))
        self.assertEqual((model.format, max(model.size)), ("JPEG", 2000))

    def test_a_heic_from_an_iphone_is_accepted(self):
        prepared = images.prepare(photo(size=(400, 300), fmt="HEIF"))
        self.assertEqual(Image.open(io.BytesIO(prepared.model_jpeg)).format, "JPEG")

    def test_the_thumbnail_is_a_small_webp(self):
        prepared = images.prepare(photo())
        thumb = Image.open(io.BytesIO(prepared.thumbnail_webp))
        self.assertEqual(thumb.format, "WEBP")
        self.assertEqual(max(thumb.size), images.THUMBNAIL_SIDE)

    def test_a_small_photo_is_not_enlarged(self):
        self.assertEqual(images.prepare(photo(size=(800, 600)), max_side=2000).model_side, 800)

    def test_the_exif_rotation_is_applied(self):
        prepared = images.prepare(photo(size=(400, 300), exif=exif(orientation=6)))
        self.assertEqual((prepared.width, prepared.height), (300, 400))

    def test_the_capture_date_uses_the_offset_when_present(self):
        prepared = images.prepare(photo(size=(40, 30), exif=exif(taken="2026:01:15 13:25:00", offset="-06:00")))
        self.assertEqual(prepared.captured_at.utcoffset(), timedelta(hours=-6))
        self.assertEqual(prepared.captured_at.hour, 13)

    def test_without_offset_the_capture_date_is_store_time(self):
        prepared = images.prepare(photo(size=(40, 30), exif=exif(taken="2026:01:15 13:25:00")))
        self.assertEqual(str(prepared.captured_at.tzinfo), "America/Monterrey")

    def test_without_exif_there_is_no_capture_date(self):
        self.assertIsNone(images.prepare(photo(size=(40, 30))).captured_at)

    def test_something_that_is_not_an_image_is_rejected(self):
        with self.assertRaises(images.ImageError) as caught:
            images.prepare(b"%PDF-1.7 not a photo")
        self.assertEqual(caught.exception.code, "not_an_image")

    def test_an_empty_file_is_rejected(self):
        with self.assertRaises(images.ImageError) as caught:
            images.prepare(b"")
        self.assertEqual(caught.exception.code, "empty")

    @override_settings(MAX_UPLOAD_BYTES=10)
    def test_a_file_too_large_is_rejected(self):
        with self.assertRaises(images.ImageError) as caught:
            images.prepare(photo(size=(40, 30)))
        self.assertEqual(caught.exception.code, "too_large")
