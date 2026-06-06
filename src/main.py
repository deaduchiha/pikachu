import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from .bot_handlers import create_router
from .cache import MenuCache
from .config import Settings
from .menu import MenuLoadError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def health_handler(_request: web.Request) -> web.Response:
    return web.Response(text="OK")


async def refresh_loop(menu_cache: MenuCache, interval_seconds: int) -> None:
    while True:
        try:
            await menu_cache.refresh()
        except MenuLoadError as exc:
            logger.warning("Background menu refresh failed: %s", exc)
        except Exception:
            logger.exception("Unexpected error during menu cache refresh")
        await asyncio.sleep(interval_seconds)


async def on_startup(app: web.Application) -> None:
    settings: Settings = app["settings"]
    bot: Bot = app["bot"]
    menu_cache: MenuCache = app["menu_cache"]

    if settings.webhook_url:
        await bot.set_webhook(
            url=settings.webhook_url,
            drop_pending_updates=True,
            allowed_updates=["message"],
        )
        logger.info("Webhook registered at %s", settings.webhook_url)
    else:
        logger.warning("WEBHOOK_HOST is not set; webhook was not registered")

    try:
        await menu_cache.refresh()
    except MenuLoadError as exc:
        logger.warning("Initial menu cache warm-up failed: %s", exc)

    app["refresh_task"] = asyncio.create_task(
        refresh_loop(menu_cache, settings.refresh_interval_seconds)
    )


async def on_shutdown(app: web.Application) -> None:
    refresh_task: asyncio.Task | None = app.get("refresh_task")
    if refresh_task:
        refresh_task.cancel()
        try:
            await refresh_task
        except asyncio.CancelledError:
            pass

    bot: Bot = app["bot"]
    await bot.session.close()


def create_app(settings: Settings) -> web.Application:
    bot = Bot(token=settings.telegram_bot_token)
    menu_cache = MenuCache(
        cache_dir=settings.cache_dir,
        loader_kwargs=settings.menu_loader_kwargs(),
        translate_url=settings.translate_url,
        ttl_seconds=settings.cache_ttl_seconds,
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router(settings, menu_cache))

    app = web.Application()
    app["settings"] = settings
    app["bot"] = bot
    app["menu_cache"] = menu_cache

    app.router.add_get("/health", health_handler)

    webhook_handler = SimpleRequestHandler(
        dispatcher=dispatcher,
        bot=bot,
        secret_token=None,
    )
    webhook_handler.register(app, path=settings.webhook_path)

    setup_application(app, dispatcher, bot=bot)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app


def main() -> None:
    settings = Settings.from_env()
    app = create_app(settings)
    web.run_app(app, host="0.0.0.0", port=settings.webhook_port)


if __name__ == "__main__":
    main()
