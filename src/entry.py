import json

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.methods import SetWebhook
from aiogram.types import Update
from bot_handlers import router
from menu import ARDIS_MENU_URL, DEFAULT_TRANSLATE_URL
from worker_session import WorkersFetchSession
from workers import WorkerEntrypoint, Response


def json_response(data, status=200):
    return Response(
        json.dumps(data, ensure_ascii=False),
        status=status,
        headers={"content-type": "application/json; charset=utf-8"},
    )


def text_response(text, status=200):
    return Response(
        text,
        status=status,
        headers={"content-type": "text/plain; charset=utf-8"},
    )


dispatcher = Dispatcher()
dispatcher.include_router(router)


def create_bot(token: str) -> Bot:
    return Bot(token=token, session=WorkersFetchSession())


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        url = str(request.url)
        method = str(request.method)

        if method == "GET" and "setWebhook" in url:
            bot = create_bot(self.env.TELEGRAM_BOT_TOKEN)
            base = url.split("setWebhook")[0].rstrip("/?")
            webhook_url = base + "/webhook"

            result = await bot(
                SetWebhook(
                    url=webhook_url,
                    drop_pending_updates=True,
                    allowed_updates=["message"],
                )
            )

            return json_response({
                "ok": True,
                "webhook_url": webhook_url,
                "telegram_result": result.model_dump(),
            })

        if method == "GET" and "health" in url:
            return text_response("OK")

        if method == "GET":
            return text_response(
                "Trieste Mensa Bot is running.\n\n"
                "After deployment, open once:\n"
                "https://<your-worker>.workers.dev/setWebhook"
            )

        if method == "POST":
            try:
                bot = create_bot(self.env.TELEGRAM_BOT_TOKEN)
                update = Update.model_validate(
                    await request.json(),
                    context={"bot": bot},
                )
                ardis_url = getattr(self.env, "ARDIS_MENU_URL", ARDIS_MENU_URL)
                translate_url = getattr(
                    self.env, "TRANSLATE_URL", DEFAULT_TRANSLATE_URL
                )
                await dispatcher.feed_update(
                    bot,
                    update,
                    ardis_url=ardis_url,
                    translate_url=translate_url,
                )
                return json_response({"ok": True})
            except TelegramAPIError as e:
                return json_response({"ok": True, "telegram_error": str(e)})
            except Exception as e:
                return json_response({"ok": False, "error": str(e)}, status=500)

        return text_response("Method not allowed", status=405)
