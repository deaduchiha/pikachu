from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from menu import (
    ARDIS_MENU_URL,
    extract_trieste_menus,
    fetch_html,
    format_menu_message,
)

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Ciao! 👋 I'm the Trieste Mensa Bot.\n\n"
        "I fetch weekly menu files for:\n"
        "📍 Mensa Centrale Trieste\n\n"
        "Commands:\n"
        "/menus — latest Trieste menu files\n"
        "/today — same as /menus\n"
        "/help  — this message",
        disable_web_page_preview=True,
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "This bot only covers Trieste (no Udine, Gorizia, etc.).\n\n"
        "It scrapes the public ARDiS ristorazione page and returns\n"
        "menu links for Mensa Centrale Trieste.\n\n"
        "Use /menus to get the latest files.",
        disable_web_page_preview=True,
    )


@router.message(Command("menus", "menu", "today"))
async def cmd_menus(message: Message, ardis_url: str = ARDIS_MENU_URL) -> None:
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
        "Unknown command. Use /menus or /help.",
        disable_web_page_preview=True,
    )
