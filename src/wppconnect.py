import base64
import json
import re
from typing import Any
from urllib.parse import quote

import httpx

DEFAULT_CHANNEL_INVITE = "0029Vb5cElw5a23zVecmn70P"
DEFAULT_WPPCONNECT_SESSION = "default"

PDF_URL_RE = re.compile(r"https?://[^\s)>\"']+\.pdf", re.IGNORECASE)
IMAGE_MIMETYPES = ("image/jpeg", "image/jpg", "image/png", "image/webp")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
MENU_TEXT_KEYWORDS = ("primo", "secondo", "menù", "menu", "mensa", "contorno")
DOCUMENT_TYPES = ("document", "ptt", "audio", "video")
IMAGE_TYPES = ("image", "sticker")

_token_cache: dict[tuple[str, str, str], str] = {}


class WppConnectError(Exception):
    pass


def channel_invite_from_url(url: str) -> str:
    url = url.rstrip("/")
    if "/" in url:
        return url.rsplit("/", 1)[-1]
    return url


def normalize_channel_id(channel_id: str) -> str:
    channel_id = channel_id.strip()
    if not channel_id:
        return ""
    if "@newsletter" in channel_id:
        return channel_id
    return f"{channel_id}@newsletter"


def _message_text(msg: dict) -> str:
    for key in ("body", "caption", "content"):
        value = msg.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _message_mimetype(msg: dict) -> str:
    mimetype = msg.get("mimetype") or ""
    if isinstance(mimetype, str):
        return mimetype.lower()
    return ""


def _is_image_message(msg: dict, mimetype: str) -> bool:
    msg_type = str(msg.get("type") or "").lower()
    if msg_type in IMAGE_TYPES:
        return True
    return mimetype.startswith("image/") or any(
        ext in mimetype for ext in ("jpeg", "jpg", "png", "webp")
    )


def _is_document_message(msg: dict, mimetype: str) -> bool:
    msg_type = str(msg.get("type") or "").lower()
    if msg_type in DOCUMENT_TYPES:
        return True
    return "pdf" in mimetype or mimetype.startswith("application/")


async def _generate_token(
    base_url: str,
    session: str,
    secret_key: str,
) -> str:
    if not secret_key:
        raise WppConnectError(
            "WPPConnect authentication failed. Set WPPCONNECT_SECRET_KEY in .env "
            "to the same value as the wppconnect service, then restart the stack."
        )

    cache_key = (base_url, session, secret_key)
    cached = _token_cache.get(cache_key)
    if cached:
        return cached

    url = f"{base_url}/api/{session}/{secret_key}/generate-token"
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, headers={"accept": "application/json"})

    raw = response.text
    if response.status_code == 400 and "SECRET_KEY" in raw:
        raise WppConnectError(
            "WPPConnect authentication failed. Set WPPCONNECT_SECRET_KEY in .env "
            "to the same value as the wppconnect service, then restart the stack."
        )
    if response.status_code < 200 or response.status_code >= 300:
        raise WppConnectError(
            f"WPPConnect token request failed ({response.status_code}): {raw[:300]}"
        )

    data = json.loads(raw)
    token = data.get("full") or data.get("token")
    if not token:
        raise WppConnectError("Unexpected WPPConnect token response.")

    _token_cache[cache_key] = token
    return token


async def fetch_wppconnect_json(
    url: str,
    *,
    session: str,
    base_url: str,
    secret_key: str,
    method: str = "GET",
    body: dict | None = None,
) -> object:
    token = await _generate_token(base_url, session, secret_key)
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.request(method, url, headers=headers, json=body)

    raw = response.text
    if response.status_code in {401, 403}:
        _token_cache.pop((base_url.rstrip("/"), session, secret_key), None)
        raise WppConnectError(
            "WPPConnect authentication failed. Check WPPCONNECT_SECRET_KEY and that "
            "the WhatsApp session is connected, then restart the stack."
        )
    if response.status_code < 200 or response.status_code >= 300:
        raise WppConnectError(
            f"WPPConnect request failed ({response.status_code}): {raw[:300]}"
        )

    if not raw.strip():
        return {}

    return json.loads(raw)


async def ensure_session_started(
    base_url: str,
    session: str,
    secret_key: str,
) -> None:
    base = base_url.rstrip("/")
    check_url = f"{base}/api/{session}/check-connection-session"
    try:
        data = await fetch_wppconnect_json(
            check_url,
            session=session,
            base_url=base,
            secret_key=secret_key,
        )
        if isinstance(data, dict) and data.get("status") is True:
            return
    except WppConnectError:
        pass

    url = f"{base}/api/{session}/start-session"
    await fetch_wppconnect_json(
        url,
        session=session,
        base_url=base,
        secret_key=secret_key,
        method="POST",
        body={"waitQrCode": False},
    )


async def resolve_channel_id(
    base_url: str,
    session: str,
    secret_key: str,
    channel_id: str = "",
) -> str:
    normalized = normalize_channel_id(channel_id)
    if normalized:
        return normalized

    url = f"{base_url.rstrip('/')}/api/{session}/list-chats"
    data = await fetch_wppconnect_json(
        url,
        session=session,
        base_url=base_url.rstrip("/"),
        secret_key=secret_key,
    )
    if not isinstance(data, dict):
        raise WppConnectError("Unexpected WPPConnect response for list-chats.")

    chats = data.get("response") or []
    newsletters = []
    for chat in chats:
        if not isinstance(chat, dict):
            continue
        chat_id = chat.get("id")
        serialized = ""
        if isinstance(chat_id, dict):
            serialized = str(chat_id.get("_serialized") or "")
        elif isinstance(chat_id, str):
            serialized = chat_id
        if "@newsletter" in serialized:
            newsletters.append(serialized)

    if len(newsletters) == 1:
        return newsletters[0]

    if not newsletters:
        raise WppConnectError(
            "No WhatsApp channel found. Follow the LAMensa channel in WhatsApp, "
            "then set WHATSAPP_CHANNEL_ID to the channel JID (e.g. 120363...@newsletter)."
        )

    raise WppConnectError(
        "Multiple WhatsApp channels found. Set WHATSAPP_CHANNEL_ID to the LAMensa "
        "channel JID (numeric id with @newsletter suffix)."
    )


async def fetch_channel_messages(
    base_url: str,
    session: str,
    channel_id: str,
    secret_key: str = "",
    limit: int = 100,
) -> list:
    base = base_url.rstrip("/")
    await ensure_session_started(base, session, secret_key)
    resolved_id = await resolve_channel_id(base, session, secret_key, channel_id)
    encoded_id = quote(resolved_id, safe="")
    query = f"count={limit}&direction=before"
    url = f"{base}/api/{session}/get-messages/{encoded_id}?{query}"
    data = await fetch_wppconnect_json(
        url,
        session=session,
        base_url=base,
        secret_key=secret_key,
    )
    if not isinstance(data, dict):
        raise WppConnectError("Unexpected WPPConnect response for channel messages.")

    messages = data.get("response")
    if not isinstance(messages, list):
        raise WppConnectError("Unexpected WPPConnect response for channel messages.")
    return messages


async def download_message_media(
    base_url: str,
    session: str,
    secret_key: str,
    message: dict,
) -> tuple[bytes, str]:
    url = f"{base_url.rstrip('/')}/api/{session}/download-media-by-message"
    data = await fetch_wppconnect_json(
        url,
        session=session,
        base_url=base_url.rstrip("/"),
        secret_key=secret_key,
        method="POST",
        body={"messageId": message},
    )
    if not isinstance(data, dict):
        raise WppConnectError("Unexpected WPPConnect media download response.")

    encoded = data.get("base64")
    if not encoded:
        raise WppConnectError("WPPConnect did not return media bytes for the message.")

    mimetype = str(data.get("mimetype") or _message_mimetype(message))
    return base64.b64decode(encoded), mimetype


def _menu_asset_from_message(msg: dict) -> tuple[Any, str, str] | None:
    body = _message_text(msg)
    mimetype = _message_mimetype(msg)
    has_media = bool(msg.get("isMedia") or msg.get("isMMS") or msg.get("mediaKey"))

    if has_media or _is_document_message(msg, mimetype) or _is_image_message(msg, mimetype):
        if "pdf" in mimetype or _is_document_message(msg, mimetype):
            title = body or "LAMensa WhatsApp PDF"
            return msg, "pdf", title
        if _is_image_message(msg, mimetype):
            title = body or "LAMensa WhatsApp image menu"
            return msg, "image", title

    for match in PDF_URL_RE.finditer(body):
        return match.group(0), "pdf", body[:200] or "LAMensa WhatsApp link"

    if body and any(kw in body.lower() for kw in MENU_TEXT_KEYWORDS):
        return body, "text", body[:80]

    if has_media and _is_image_message(msg, mimetype):
        return msg, "image", body or "LAMensa WhatsApp image menu"

    return None


def extract_menu_asset(messages: list) -> tuple[Any, str, str]:
    """Return the menu asset from the newest message that contains one."""

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        asset = _menu_asset_from_message(msg)
        if asset:
            return asset

    raise WppConnectError("No menu PDF, image, or text found in WhatsApp channel messages.")
