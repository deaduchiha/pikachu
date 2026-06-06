import os
from dataclasses import dataclass

from dotenv import load_dotenv

from .menu import ARDIS_MENU_URL, DEFAULT_TRANSLATE_URL
from .waha import DEFAULT_CHANNEL_INVITE, DEFAULT_WAHA_SESSION

load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    webhook_host: str
    webhook_path: str
    webhook_port: int
    waha_url: str
    waha_session: str
    waha_api_key: str
    whatsapp_channel_invite: str
    ardis_menu_url: str
    ardis_fallback: bool
    translate_url: str
    translate_api_key: str
    cache_dir: str
    cache_ttl_seconds: int
    refresh_interval_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

        waha_url = os.getenv("WAHA_URL", "http://waha:3000").strip().rstrip("/")
        translate_url = os.getenv("TRANSLATE_URL", DEFAULT_TRANSLATE_URL).strip()

        return cls(
            telegram_bot_token=token,
            webhook_host=os.getenv("WEBHOOK_HOST", "").strip().rstrip("/"),
            webhook_path=os.getenv("WEBHOOK_PATH", "/webhook").strip() or "/webhook",
            webhook_port=int(os.getenv("WEBHOOK_PORT", "8080")),
            waha_url=waha_url,
            waha_session=os.getenv("WAHA_SESSION", DEFAULT_WAHA_SESSION).strip(),
            waha_api_key=os.getenv("WAHA_API_KEY", "").strip(),
            whatsapp_channel_invite=os.getenv(
                "WHATSAPP_CHANNEL_INVITE", DEFAULT_CHANNEL_INVITE
            ).strip(),
            ardis_menu_url=os.getenv("ARDIS_MENU_URL", ARDIS_MENU_URL).strip(),
            ardis_fallback=_env_bool("ARDIS_FALLBACK", default=False),
            translate_url=translate_url,
            translate_api_key=os.getenv("TRANSLATE_API_KEY", "").strip(),
            cache_dir=os.getenv("CACHE_DIR", "/data/cache").strip(),
            cache_ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "1800")),
            refresh_interval_seconds=int(os.getenv("REFRESH_INTERVAL_SECONDS", "900")),
        )

    @property
    def webhook_url(self) -> str:
        if not self.webhook_host:
            return ""
        return f"{self.webhook_host}{self.webhook_path}"

    def menu_loader_kwargs(self) -> dict:
        return {
            "waha_url": self.waha_url,
            "waha_session": self.waha_session,
            "channel_invite": self.whatsapp_channel_invite,
            "waha_api_key": self.waha_api_key,
            "ardis_url": self.ardis_menu_url,
            "prefer_waha": bool(self.waha_url),
            "ardis_fallback": self.ardis_fallback,
        }
