from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from .cache import MenuCache
from .config import Settings
from .menu import (
    ARDIS_MENU_URL,
    MenuLoadError,
    extract_trieste_menus,
    fetch_html,
    format_menu_message,
    load_channel_message_links,
    split_telegram_messages,
)
from .wppconnect import DEFAULT_WPPCONNECT_SESSION

WHATSAPP_CHANNEL_URL = "https://whatsapp.com/channel/0029Vb5cElw5a23zVecmn70P"

HELP_TEXT = (
    "This bot covers Mensa Centrale Trieste only.\n\n"
    "It reads the weekly menu from the LAMensa WhatsApp channel "
    "(via WPPConnect), parses PDFs and images, and replies in English.\n\n"
    "Commands:\n"
    "/today — today's menu\n"
    "/week  — this week's menu\n"
    "/month — full menu from the latest post\n"
    "/menus — same as /month\n"
    "/links — recent channel posts / PDF links\n"
    "/help  — this message"
)

EMPTY_MESSAGES = {
    "today": "No menu found for today in the current channel post.",
    "week": "No menu found for this week in the current channel post.",
    "month": "No menu rows found in the current channel post.",
}


def create_router(settings: Settings, menu_cache: MenuCache) -> Router:
    router = Router()
    loader_kwargs = settings.menu_loader_kwargs()

    async def reply_menu(message: Message, mode: str) -> None:
        try:
            text = await menu_cache.get_or_refresh(mode)
        except MenuLoadError as exc:
            await message.answer(str(exc), disable_web_page_preview=True)
            return

        if not text.strip():
            await message.answer(
                EMPTY_MESSAGES.get(mode, "No menu rows found."),
                disable_web_page_preview=True,
            )
            return

        for chunk in split_telegram_messages(text):
            await message.answer(chunk, disable_web_page_preview=True)

    @router.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        await message.answer(
            "Ciao! 👋 I'm the Trieste Mensa Bot.\n\n"
            "📍 Mensa Centrale Trieste\n"
            f"📱 Source: {WHATSAPP_CHANNEL_URL}\n\n"
            + HELP_TEXT,
            disable_web_page_preview=True,
        )

    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        await message.answer(HELP_TEXT, disable_web_page_preview=True)

    @router.message(Command("today"))
    async def cmd_today(message: Message) -> None:
        await reply_menu(message, "today")

    @router.message(Command("week"))
    async def cmd_week(message: Message) -> None:
        await reply_menu(message, "week")

    @router.message(Command("month", "menus", "menu"))
    async def cmd_month(message: Message) -> None:
        await reply_menu(message, "month")

    @router.message(Command("links"))
    async def cmd_links(message: Message) -> None:
        wppconnect_url = loader_kwargs.get("wppconnect_url", "")
        if wppconnect_url:
            try:
                links = await load_channel_message_links(
                    wppconnect_url=wppconnect_url,
                    session=loader_kwargs.get(
                        "wppconnect_session", DEFAULT_WPPCONNECT_SESSION
                    ),
                    whatsapp_channel_id=loader_kwargs.get("whatsapp_channel_id", ""),
                    secret_key=loader_kwargs.get("wppconnect_secret_key", ""),
                )
                await message.answer(
                    format_menu_message(links, source="LAMensa WhatsApp"),
                    disable_web_page_preview=True,
                )
            except MenuLoadError as exc:
                await message.answer(str(exc), disable_web_page_preview=True)
        else:
            html = await fetch_html(loader_kwargs.get("ardis_url", ARDIS_MENU_URL))
            links = extract_trieste_menus(
                html, loader_kwargs.get("ardis_url", ARDIS_MENU_URL)
            )
            await message.answer(
                format_menu_message(links, source="ARDiS (WPPConnect not configured)"),
                disable_web_page_preview=True,
            )

    return router
