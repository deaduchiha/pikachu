from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from menu import (
    ARDIS_MENU_URL,
    DEFAULT_TRANSLATE_URL,
    MenuLoadError,
    extract_trieste_menus,
    fetch_html,
    filter_menu,
    format_menu_message,
    format_menu_table,
    load_menu_rows,
    split_telegram_messages,
    translate_to_english,
)

router = Router()

HELP_TEXT = (
    "This bot covers Mensa Centrale Trieste only.\n\n"
    "It downloads the latest menu PDF from ARDiS, parses it, "
    "and replies in English.\n\n"
    "Commands:\n"
    "/today — today's menu\n"
    "/week  — this week's menu\n"
    "/month — full menu from the PDF\n"
    "/menus — same as /month\n"
    "/links — raw PDF links from ARDiS\n"
    "/help  — this message"
)

EMPTY_MESSAGES = {
    "today": "No menu found for today in the current PDF.",
    "week": "No menu found for this week in the current PDF.",
    "month": "No menu rows found in the current PDF.",
}


async def _reply_menu(
    message: Message,
    mode: str,
    ardis_url: str,
    translate_url: str,
) -> None:
    try:
        rows, _pdf_url = await load_menu_rows(ardis_url)
    except MenuLoadError as exc:
        await message.answer(str(exc), disable_web_page_preview=True)
        return

    filtered = filter_menu(rows, mode)
    if not filtered:
        await message.answer(
            EMPTY_MESSAGES.get(mode, "No menu rows found."),
            disable_web_page_preview=True,
        )
        return

    table = format_menu_table(filtered)
    translated = await translate_to_english(table, translate_url=translate_url)
    for chunk in split_telegram_messages(translated):
        await message.answer(chunk, disable_web_page_preview=True)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Ciao! 👋 I'm the Trieste Mensa Bot.\n\n"
        "📍 Mensa Centrale Trieste\n\n"
        + HELP_TEXT,
        disable_web_page_preview=True,
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, disable_web_page_preview=True)


@router.message(Command("today"))
async def cmd_today(
    message: Message,
    ardis_url: str = ARDIS_MENU_URL,
    translate_url: str = DEFAULT_TRANSLATE_URL,
) -> None:
    await _reply_menu(message, "today", ardis_url, translate_url)


@router.message(Command("week"))
async def cmd_week(
    message: Message,
    ardis_url: str = ARDIS_MENU_URL,
    translate_url: str = DEFAULT_TRANSLATE_URL,
) -> None:
    await _reply_menu(message, "week", ardis_url, translate_url)


@router.message(Command("menus", "menu", "month"))
async def cmd_month(
    message: Message,
    ardis_url: str = ARDIS_MENU_URL,
    translate_url: str = DEFAULT_TRANSLATE_URL,
) -> None:
    await _reply_menu(message, "month", ardis_url, translate_url)


@router.message(Command("links"))
async def cmd_links(message: Message, ardis_url: str = ARDIS_MENU_URL) -> None:
    html = await fetch_html(ardis_url)
    links = extract_trieste_menus(html, ardis_url)
    await message.answer(
        format_menu_message(links),
        disable_web_page_preview=True,
    )


@router.message()
async def unknown_command(message: Message) -> None:
    if not message.text or not message.text.startswith("/"):
        return

    await message.answer(
        "Unknown command. Use /today, /week, /month, or /help.",
        disable_web_page_preview=True,
    )
