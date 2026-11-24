"""Photo checks and storage (REQ-7).

A photo is accepted only if it really is a JPEG or PNG image (checked by opening it with Pillow,
not just by its file name) and is no larger than PHOTO_MAX_BYTES.

In production the photo is uploaded to Cloudinary and only its link is saved in the database.
In development, when CLOUDINARY_URL is empty, it is saved in instance/uploads instead.
"""
import os
import uuid

from flask import current_app, url_for
from PIL import Image, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

ALLOWED_FORMATS = {"JPEG": "jpg", "PNG": "png"}


class PhotoError(Exception):
    """The photo was rejected or could not be stored. The message is safe to show to the user."""


def validate_photo(file: FileStorage) -> str:
    """Check size and real image type. Returns the file extension to use."""
    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(0)
    if size == 0:
        raise PhotoError("The photo file is empty.")
    if size > current_app.config["PHOTO_MAX_BYTES"]:
        raise PhotoError("That photo is too large. Please choose a JPEG or PNG under 5 MB.")
    try:
        with Image.open(file.stream) as img:
            image_format = img.format
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise PhotoError("Only JPEG or PNG photos are allowed.") from None
    finally:
        file.stream.seek(0)
    if image_format not in ALLOWED_FORMATS:
        raise PhotoError("Only JPEG or PNG photos are allowed.")
    return ALLOWED_FORMATS[image_format]


def store_photo(file: FileStorage) -> str:
    """Validate and store the photo. Returns the URL to save in the database."""
    extension = validate_photo(file)
    if current_app.config.get("CLOUDINARY_URL"):
        return _upload_to_cloudinary(file)
    return _save_locally(file, extension)


def _upload_to_cloudinary(file: FileStorage) -> str:
    import cloudinary
    import cloudinary.uploader

    cloudinary.config(cloudinary_url=current_app.config["CLOUDINARY_URL"], secure=True)
    try:
        result = cloudinary.uploader.upload(file.stream, folder="mtaafix", resource_type="image", timeout=20)
    except Exception:  # noqa: BLE001 - network or service error
        current_app.logger.exception("Cloudinary upload failed")
        raise PhotoError("The photo could not be uploaded. Try again, or submit the report without it.") from None
    return result["secure_url"]


def _save_locally(file: FileStorage, extension: str) -> str:
    folder = os.path.join(current_app.instance_path, "uploads")
    os.makedirs(folder, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{extension}"
    file.save(os.path.join(folder, name))
    return url_for("main.uploaded_photo", filename=name)
