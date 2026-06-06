import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .menu import (
    MenuLoadError,
    _serialize_rows,
    filter_menu,
    format_menu_table,
    load_menu_rows,
    translate_to_english,
)

logger = logging.getLogger(__name__)

MENU_MODES = ("today", "week", "month")


class MenuCache:
    def __init__(
        self,
        cache_dir: str,
        loader_kwargs: dict,
        translate_url: str,
        translate_api_key: str = "",
        ttl_seconds: int = 1800,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.loader_kwargs = loader_kwargs
        self.translate_url = translate_url
        self.translate_api_key = translate_api_key
        self.ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def rows_path(self) -> Path:
        return self.cache_dir / "menu_rows.json"

    @property
    def formatted_path(self) -> Path:
        return self.cache_dir / "formatted_en.json"

    @property
    def meta_path(self) -> Path:
        return self.cache_dir / "source_meta.json"

    def _read_json(self, path: Path) -> dict | list | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read cache file %s: %s", path, exc)
            return None

    def _write_json(self, path: Path, data: object) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _meta_age_seconds(self, meta: dict) -> float | None:
        fetched_at = meta.get("fetched_at")
        if not fetched_at:
            return None
        try:
            fetched = datetime.fromisoformat(fetched_at)
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - fetched).total_seconds()
        except ValueError:
            return None

    def is_fresh(self) -> bool:
        meta = self._read_json(self.meta_path)
        if not isinstance(meta, dict):
            return False
        age = self._meta_age_seconds(meta)
        return age is not None and age <= self.ttl_seconds

    def get_formatted(self, mode: str) -> str | None:
        formatted = self._read_json(self.formatted_path)
        if not isinstance(formatted, dict):
            return None
        text = formatted.get(mode)
        return text if isinstance(text, str) and text.strip() else None

    async def refresh(self) -> None:
        async with self._lock:
            logger.info("Refreshing menu cache")
            rows, source = await load_menu_rows(**self.loader_kwargs)
            serialized_rows = _serialize_rows(rows)
            content_hash = hashlib.sha256(
                json.dumps(serialized_rows, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()

            formatted: dict[str, str] = {}
            for mode in MENU_MODES:
                filtered = filter_menu(rows, mode)
                if not filtered and mode != "month":
                    formatted[mode] = ""
                    continue
                table = format_menu_table(filtered if filtered else rows)
                formatted[mode] = await translate_to_english(
                    table,
                    translate_url=self.translate_url,
                    translate_api_key=self.translate_api_key,
                )

            self._write_json(self.rows_path, _serialize_rows(rows))
            self._write_json(self.formatted_path, formatted)
            self._write_json(
                self.meta_path,
                {
                    "source": source,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "content_hash": content_hash,
                    "row_count": len(rows),
                },
            )
            logger.info("Menu cache refreshed (%s rows, source=%s)", len(rows), source)

    async def get_or_refresh(self, mode: str, allow_stale: bool = True) -> str:
        if self.is_fresh():
            cached = self.get_formatted(mode)
            if cached is not None:
                return cached

        stale = self.get_formatted(mode) if allow_stale else None
        try:
            await self.refresh()
            fresh = self.get_formatted(mode)
            if fresh:
                return fresh
        except MenuLoadError:
            if stale:
                return stale
            raise

        if stale:
            return stale
        return ""
