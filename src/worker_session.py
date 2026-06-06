from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any, cast

from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods.base import TelegramType
from js import Headers, Request, fetch as js_fetch

if TYPE_CHECKING:
    from aiogram.client.bot import Bot
    from aiogram.methods import TelegramMethod


class WorkersFetchSession(BaseSession):
    """aiogram HTTP session backed by the Workers runtime fetch API."""

    async def close(self) -> None:
        return None

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,
    ) -> TelegramType:
        del timeout

        url = self.api.api_url(token=bot.token, method=method.__api_method__)
        files: dict[str, Any] = {}
        payload: dict[str, Any] = {}

        for key, value in method.model_dump(warnings=False).items():
            prepared = self.prepare_value(value, bot=bot, files=files)
            if prepared is not None and prepared != "":
                payload[key] = prepared

        if files:
            msg = "File uploads are not supported in WorkersFetchSession"
            raise NotImplementedError(msg)

        headers = Headers.new()
        headers.set("content-type", "application/json")
        req = Request.new(
            url,
            method="POST",
            headers=headers,
            body=self.json_dumps(payload),
        )

        try:
            resp = await js_fetch(req)
            raw_result = await resp.text()
        except Exception as exc:
            raise TelegramNetworkError(
                method=method,
                message=f"{type(exc).__name__}: {exc}",
            ) from exc

        response = self.check_response(
            bot=bot,
            method=method,
            status_code=resp.status,
            content=raw_result,
        )
        return cast(TelegramType, response.result)

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        del timeout, chunk_size, raise_for_status

        js_headers = Headers.new()
        if headers:
            for key, value in headers.items():
                js_headers.set(key, str(value))

        req = Request.new(url, headers=js_headers)
        resp = await js_fetch(req)
        body = await resp.arrayBuffer()
        yield bytes(body)
