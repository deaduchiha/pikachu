import json
import os
from fastapi import FastAPI, Request, Response
from workers import WorkerEntrypoint
import asgi
import httpx

app = FastAPI()

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


async def send_message(token: str, chat_id: int, text: str):
    url = TELEGRAM_API.format(token=token, method="sendMessage")
    async with httpx.AsyncClient() as client:
        await client.post(url, json={"chat_id": chat_id, "text": text})


@app.post("/webhook")
async def webhook(request: Request):
    token = request.app.state.bot_token
    body = await request.json()

    message = body.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")

    if not chat_id:
        return Response("ok")

    # Handle commands
    if text == "/start":
        await send_message(token, chat_id, "👋 Hello! I'm your bot. Send me anything!")
    elif text == "/help":
        await send_message(token, chat_id, "Commands:\n/start - Welcome\n/help - This menu")
    else:
        # Echo back
        await send_message(token, chat_id, f"You said: {text}")

    return Response("ok")


@app.get("/")
async def health():
    return {"status": "running"}


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        # Inject secrets into app state
        app.state.bot_token = self.env.BOT_TOKEN
        return await asgi.fetch(app, request.js_object, self.env)
