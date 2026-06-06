import asyncio
import io
import logging

from PIL import Image, ImageEnhance, ImageFilter
import pytesseract

logger = logging.getLogger(__name__)


def _extract_text_from_image_sync(image_bytes: bytes) -> str:
    image = Image.open(io.BytesIO(image_bytes))
    if image.mode != "L":
        image = image.convert("L")
    image = ImageEnhance.Contrast(image).enhance(1.8)
    image = image.filter(ImageFilter.SHARPEN)
    text = pytesseract.image_to_string(image, lang="ita")
    return text.strip()


async def extract_text_from_image(image_bytes: bytes) -> str:
    text = await asyncio.to_thread(_extract_text_from_image_sync, image_bytes)
    if not text:
        logger.warning("OCR returned empty text for menu image")
    return text
