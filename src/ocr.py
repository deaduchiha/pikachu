import asyncio
import io
import logging

from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import pytesseract

logger = logging.getLogger(__name__)

TESSERACT_CONFIG = "--psm 6 --oem 3"


def _extract_text_from_image_sync(image_bytes: bytes) -> str:
    image = Image.open(io.BytesIO(image_bytes))
    image = ImageOps.exif_transpose(image)
    width, height = image.size
    if max(width, height) < 1800:
        scale = 1800 / max(width, height)
        image = image.resize(
            (int(width * scale), int(height * scale)),
            Image.Resampling.LANCZOS,
        )

    gray = image.convert("L") if image.mode != "L" else image
    gray = ImageEnhance.Contrast(gray).enhance(2.0)
    gray = gray.filter(ImageFilter.SHARPEN)

    text = pytesseract.image_to_string(gray, lang="ita", config=TESSERACT_CONFIG)
    if len(text.strip()) < 40:
        text = pytesseract.image_to_string(gray, lang="ita", config="--psm 4 --oem 3")
    return text.strip()


async def extract_text_from_image(image_bytes: bytes) -> str:
    text = await asyncio.to_thread(_extract_text_from_image_sync, image_bytes)
    if not text:
        logger.warning("OCR returned empty text for menu image")
    return text
