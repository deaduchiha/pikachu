import json
import re

import httpx

DEFAULT_CHANNEL_INVITE = "0029Vb5cElw5a23zVecmn70P"
DEFAULT_WAHA_SESSION = "default"

PDF_URL_RE = re.compile(r"https?://[^\s)>\"']+\.pdf", re.IGNORECASE)
IMAGE_MIMETYPES = ("image/jpeg", "image/jpg", "image/png", "image/webp")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


class WahaError(Exception):
    pass


def channel_invite_from_url(url: str) -> str:
    url = url.rstrip("/")
    if "/" in url:
        return url.rsplit("/", 1)[-1]
    return url


def _waha_headers(api_key: str = "") -> dict[str, str]:
    headers = {"accept": "application/json"}
    if api_key:
        headers["X-Api-Key"] = api_key
    return headers


async def fetch_waha_json(
    url: str,
    api_key: str = "",
    method: str = "GET",
    body: dict | None = None,
) -> object:
    headers = _waha_headers(api_key)
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.request(
            method,
            url,
            headers=headers,
            json=body,
        )

    raw = response.text
    if response.status_code < 200 or response.status_code >= 300:
        raise WahaError(f"WAHA request failed ({response.status_code}): {raw[:300]}")

    if not raw.strip():
        return []

    return json.loads(raw)


async def fetch_channel_messages_preview(
    waha_url: str,
    session: str,
    invite: str,
    api_key: str = "",
    limit: int = 20,
    download_media: bool = True,
) -> list:
    base = waha_url.rstrip("/")
    query = f"downloadMedia={'true' if download_media else 'false'}&limit={limit}"
    url = f"{base}/api/{session}/channels/{invite}/messages/preview?{query}"
    data = await fetch_waha_json(url, api_key=api_key)
    if not isinstance(data, list):
        raise WahaError("Unexpected WAHA response for channel preview.")
    return data


def _normalize_waha_media_url(media_url: str, waha_url: str) -> str:
    if media_url.startswith("http://") or media_url.startswith("https://"):
        return media_url
    return f"{waha_url.rstrip('/')}/{media_url.lstrip('/')}"


def _is_image_media(mimetype: str, url: str) -> bool:
    lowered = url.lower()
    return mimetype.startswith("image/") or any(lowered.endswith(ext) for ext in IMAGE_EXTENSIONS)


def extract_menu_asset(
    messages: list,
    waha_url: str,
) -> tuple[str, str, str]:
    """Return (asset_url, source_kind, title) where kind is pdf, image, or text."""

    pdf_candidate: tuple[str, str, str] | None = None
    image_candidate: tuple[str, str, str] | None = None
    text_candidate: tuple[str, str, str] | None = None

    for item in messages:
        msg = item.get("message") if isinstance(item, dict) else None
        if not isinstance(msg, dict):
            continue

        body = (msg.get("body") or "").strip()
        media = msg.get("media") or {}
        media_url = media.get("url") or msg.get("mediaUrl") or ""
        mimetype = (media.get("mimetype") or "").lower()

        if media_url:
            full_url = _normalize_waha_media_url(media_url, waha_url)
            if "pdf" in mimetype or full_url.lower().endswith(".pdf"):
                title = body or "LAMensa WhatsApp PDF"
                return full_url, "pdf", title
            if _is_image_media(mimetype, full_url) and image_candidate is None:
                title = body or "LAMensa WhatsApp image menu"
                image_candidate = (full_url, "image", title)

        for match in PDF_URL_RE.finditer(body):
            if pdf_candidate is None:
                pdf_candidate = (
                    match.group(0),
                    "pdf",
                    body[:200] or "LAMensa WhatsApp link",
                )

        if body and any(kw in body.lower() for kw in ("primo", "secondo", "menù", "menu", "mensa")):
            if text_candidate is None:
                text_candidate = (body, "text", body[:80])

    if pdf_candidate:
        return pdf_candidate
    if image_candidate:
        return image_candidate
    if text_candidate:
        return text_candidate

    raise WahaError("No menu PDF, image, or text found in WhatsApp channel messages.")
