import io
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin

import httpx

ARDIS_MENU_URL = "https://www.ardis.fvg.it/contenuti.php?id=214&view=page"
ROME_TZ = timezone(timedelta(hours=2))
DEFAULT_TRANSLATE_URL = "https://libretranslate.com/translate"

logger = logging.getLogger(__name__)


def rome_today() -> date:
    return datetime.now(ROME_TZ).date()
TELEGRAM_MAX_LEN = 4000

EXCLUDE_CITIES = ["udine", "gorizia", "pordenone", "gemona", "sacile"]

TRIESTE_KEYWORDS = [
    "trieste", "centrale", "self service", "self-service", "pizzeria", "insalatone"
]

MENU_KEYWORDS = ["menù", "menu", "men-", "men_"]

WEEKDAYS = [
    "Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"
]
WEEKDAY_KEYS = [
    "LUNEDÌ", "MARTEDÌ", "MERCOLEDÌ", "GIOVEDÌ", "VENERDÌ", "SABATO", "DOMENICA"
]

MONTHS_IT = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4,
    "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8,
    "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}

ROW_LABELS = {
    "primo": ("PRIMO DELLO CHEF",),
    "secondo": ("SECONDO DELLO CHEF",),
    "contorno": ("1 CONTORNO CALDO",),
}

LINE_CONTINUATIONS = {
    "CHEF", "CALDO", "FREDDO", "FISSA", "VEGETARIANO", "PIATTO", "CONTORNO", "CONTORNI",
}

MONTH_NAMES_IT = [
    "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
    "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre",
]

WEEKDAYS_EN = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
]

MONTH_NAMES_EN = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

HTTP_HEADERS = {"user-agent": "TriesteMensaBot/2.0"}

SINGLE_DATE_PATTERNS = [
    re.compile(
        r"(?P<weekday>Lunedì|Martedì|Mercoledì|Giovedì|Venerdì|Sabato|Domenica)\s+"
        r"(?P<day>\d{1,2})\s+(?P<month>[A-Za-zÀ-ú]+)(?:\s+(?P<year>\d{4}))?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<!\d)(?P<day>\d{1,2})\s+(?P<month>[A-Za-zÀ-ú]+)(?:\s+(?P<year>\d{4}))?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<!\d)(?P<day>\d{1,2})[/.\-](?P<month>\d{1,2})(?:[/.\-](?P<year>\d{2,4}))?",
    ),
]


def _resolve_month(name: str) -> int | None:
    cleaned = re.sub(r"[^a-zà-ú]", "", name.lower())
    if not cleaned:
        return None
    if cleaned in MONTHS_IT:
        return MONTHS_IT[cleaned]
    for month_name, month_num in MONTHS_IT.items():
        if cleaned.startswith(month_name[:4]) or month_name.startswith(cleaned[:4]):
            return month_num
    return None


def _normalize_menu_text(text: str) -> str:
    text = text.replace("\x0c", "\n").replace("|", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _parse_single_date(text: str, link_title: str = "") -> date | None:
    today = rome_today()
    for source in (text, link_title):
        if not source:
            continue
        for pattern in SINGLE_DATE_PATTERNS:
            match = pattern.search(source)
            if not match:
                continue
            day_num = int(match.group("day"))
            month_raw = match.group("month")
            year_raw = match.groupdict().get("year")
            if month_raw.isdigit():
                month = int(month_raw)
            else:
                month = _resolve_month(month_raw)
            if not month or month < 1 or month > 12:
                continue

            candidate_years: list[int] = []
            if year_raw:
                year = int(year_raw)
                candidate_years = [year + 2000 if year < 100 else year]
            else:
                candidate_years = [today.year, today.year - 1, today.year + 1]

            best: date | None = None
            best_delta = 9999
            for year in candidate_years:
                try:
                    parsed = date(year, month, day_num)
                except ValueError:
                    continue
                delta = abs((today - parsed).days)
                if delta < best_delta:
                    best_delta = delta
                    best = parsed
            if best and best_delta <= 370:
                return best
    return None


def _is_label_only_line(line: str, label: str) -> bool:
    return bool(
        re.match(
            rf"(?i)^{label}\s*(?:dello\s+chef|piatto|del\s+chef)?\s*(?:caldo|freddo)?\s*[:\-]?$",
            line.strip(),
        )
    )


def _extract_dish_after_label(line: str, label: str) -> str:
    if _is_label_only_line(line, label):
        return ""

    inline = re.compile(
        rf"(?i){label}\s*(?:dello\s+chef|piatto|del\s+chef)?\s*[:\-]\s*(.+)"
    )
    match = inline.search(line)
    if match:
        return _normalize_cell(match.group(1))

    trailing = re.compile(
        rf"(?i){label}\s*(?:dello\s+chef|piatto|del\s+chef)?\s+(.+)"
    )
    match = trailing.search(line)
    if match:
        value = _normalize_cell(match.group(1))
        if value.upper() not in {"CHEF", "DELLO CHEF", "CALDO", "FREDDO"}:
            return value
    return ""


def _parse_daily_menu(text: str, link_title: str = "") -> list[dict]:
    """Parse a single-day menu card (typical of daily WhatsApp image posts)."""
    day = _parse_single_date(text, link_title) or rome_today()
    date_label = _date_label(day)
    meal = "Pranzo"
    current = {"primo": "", "secondo": "", "contorno": ""}

    def build_row() -> dict | None:
        if not any(current.values()):
            return None
        return {
            "date": date_label,
            "date_obj": day,
            "meal": meal,
            "primo": current["primo"],
            "secondo": current["secondo"],
            "contorno": current["contorno"],
        }

    rows: list[dict] = []
    pending_field: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        upper = line.upper()
        if upper in {"PRANZO", "CENA"}:
            row = build_row()
            if row:
                rows.append(row)
                current = {"primo": "", "secondo": "", "contorno": ""}
            meal = "Pranzo" if "PRANZO" in upper else "Cena"
            pending_field = None
            continue

        if _parse_single_date(line):
            row = build_row()
            if row:
                rows.append(row)
                current = {"primo": "", "secondo": "", "contorno": ""}
            parsed = _parse_single_date(line)
            if parsed:
                day = parsed
                date_label = _date_label(day)
            pending_field = None
            continue

        field, values = _parse_row_line(line)
        if field and values:
            dish = values[0] if len(values) == 1 else " ".join(values)
            if dish.upper() not in {"CHEF", "DELLO", "DELLO CHEF", "CALDO"}:
                current[field] = dish
            pending_field = None
            continue

        if "PRIMO" in upper:
            current["primo"] = _extract_dish_after_label(line, "primo")
            pending_field = "primo" if not current["primo"] else None
            continue
        if "SECONDO" in upper:
            current["secondo"] = _extract_dish_after_label(line, "secondo")
            pending_field = "secondo" if not current["secondo"] else None
            continue
        if "CONTORNO" in upper or upper.startswith("1 CONTORNO"):
            current["contorno"] = _extract_dish_after_label(line, "contorno")
            pending_field = "contorno" if not current["contorno"] else None
            continue

        if pending_field and not any(
            token in upper for token in ("PRIMO", "SECONDO", "CONTORNO", "PRANZO", "CENA")
        ):
            joined = _normalize_cell(f"{current[pending_field]} {line}".strip())
            current[pending_field] = joined

    row = build_row()
    if row:
        rows.append(row)
    return rows


def _serialize_rows(rows: list[dict]) -> list[dict]:
    serialized = []
    for row in rows:
        item = dict(row)
        date_obj = item.pop("date_obj", None)
        if isinstance(date_obj, date):
            item["date_iso"] = date_obj.isoformat()
        serialized.append(item)
    return serialized


def _deserialize_rows(rows: list[dict]) -> list[dict]:
    restored = []
    for row in rows:
        item = dict(row)
        date_iso = item.pop("date_iso", None)
        if date_iso:
            try:
                item["date_obj"] = date.fromisoformat(date_iso)
            except ValueError:
                pass
        restored.append(item)
    return restored


class MenuLoadError(Exception):
    pass


async def fetch_html(url: str) -> str:
    async with httpx.AsyncClient(timeout=60.0, headers=HTTP_HEADERS) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


async def fetch_bytes(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=120.0, headers=HTTP_HEADERS) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content


async def fetch_pdf_bytes(url: str) -> bytes:
    return await fetch_bytes(url)


def parse_pdf_text(pdf_bytes: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    parts = []
    for page in reader.pages:
        text = ""
        try:
            text = page.extract_text(extraction_mode="layout") or ""
        except TypeError:
            text = page.extract_text() or ""
        if not text.strip():
            text = page.extract_text() or ""
        parts.append(text)
    return "\n".join(parts)


def pick_pdf_url(links: list[dict]) -> str | None:
    for item in links:
        if item["url"].lower().endswith(".pdf"):
            return item["url"]
    return links[0]["url"] if links else None


def _normalize_cell(value: str) -> str:
    value = re.sub(r"\s+", " ", value.replace("\n", " ")).strip()
    for suffix in (" CHEF", " CALDO", " FREDDO", " FISSA", " VEGETARIANO", " PIATTO"):
        if value.upper().endswith(suffix.strip()):
            value = value[: -len(suffix)].strip()
    if value.upper() in {"CHIUSO", "CHUSO", "-", ""}:
        return ""
    return value


def _parse_date_range(text: str, link_title: str = "") -> tuple[date, date] | None:
    sources = [text, link_title]
    patterns = [
        r"DAL\s+(\d{1,2})\s+AL\s+(\d{1,2})\s+([A-ZÀ-Úa-zà-ú]+)\s+(\d{4})",
        r"(\d{1,2})\s*[-–]\s*(\d{1,2})\s+([A-ZÀ-Úa-zà-ú]+)(?:\s+(\d{4}))?",
    ]
    today = rome_today()
    for source in sources:
        for pattern in patterns:
            match = re.search(pattern, source, re.IGNORECASE)
            if not match:
                continue
            start_day = int(match.group(1))
            end_day = int(match.group(2))
            month_name = match.group(3).lower()
            year = int(match.group(4)) if match.lastindex and match.lastindex >= 4 and match.group(4) else today.year
            month = _resolve_month(month_name)
            if not month:
                continue
            start = date(year, month, start_day)
            end = date(year, month, end_day)
            if end < start:
                continue
            return start, end
    return None


def _weekday_dates(start: date, end: date) -> dict[str, date]:
    mapping: dict[str, date] = {}
    current = start
    while current <= end:
        mapping[WEEKDAY_KEYS[current.weekday()]] = current
        current += timedelta(days=1)
    return mapping


def _date_label(day: date) -> str:
    return f"{WEEKDAYS[day.weekday()]} {day.day} {MONTH_NAMES_IT[day.month - 1]}"


def _date_label_en(day: date) -> str:
    return f"{WEEKDAYS_EN[day.weekday()]}, {day.day} {MONTH_NAMES_EN[day.month - 1]} {day.year}"


def _display_date(row: dict) -> str:
    date_obj = row.get("date_obj")
    if isinstance(date_obj, date):
        return _date_label_en(date_obj)
    parsed = _parse_row_date(row.get("date", ""), rome_today())
    if parsed:
        return _date_label_en(parsed)
    return row.get("date", "")


def _merge_layout_lines(lines: list[str]) -> list[str]:
    merged: list[str] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx].strip()
        idx += 1
        while idx < len(lines):
            nxt = lines[idx].strip()
            if not nxt:
                break
            parts = [p.strip() for p in re.split(r"\s{2,}", nxt) if p.strip()]
            if parts and parts[0].upper() == "CHEF" and "SECONDO DELLO" in line.upper():
                line = line.replace("SECONDO DELLO", "SECONDO DELLO CHEF", 1)
                if len(parts) > 1:
                    line = f"{line}  {'  '.join(parts[1:])}"
                idx += 1
                continue

            if len(parts) != 1:
                break

            token = parts[0]
            token_upper = token.upper()
            if token_upper == "CALDO" and "1 CONTORNO" in line.upper():
                line = line.replace("1 CONTORNO", "1 CONTORNO CALDO", 1)
            elif token_upper in LINE_CONTINUATIONS or len(token) <= 12:
                line = f"{line} {nxt}"
            else:
                break
            idx += 1
        if line:
            merged.append(line)
    return merged


def _join_fragmented_dishes(values: list[str]) -> list[str]:
    if not values:
        return values

    joined: list[str] = []
    idx = 0
    while idx < len(values):
        current = values[idx]
        while idx + 1 < len(values) and values[idx + 1][:1].islower():
            idx += 1
            current = f"{current} {values[idx]}"
        joined.append(_normalize_cell(current))
        idx += 1
    return joined


def _parse_row_line(line: str) -> tuple[str | None, list[str]]:
    upper = line.upper()
    for field, labels in ROW_LABELS.items():
        for label in sorted(labels, key=len, reverse=True):
            if label in upper:
                start = upper.find(label)
                remainder = line[start + len(label):].strip()
                values = [
                    _normalize_cell(part)
                    for part in re.split(r"\s{2,}", remainder)
                    if _normalize_cell(part)
                ]
                return field, _join_fragmented_dishes(values)
    return None, []


def _assign_day_values(values: list[str], weekday_dates: dict[str, date]) -> dict[str, str]:
    days = [key for key in WEEKDAY_KEYS if key in weekday_dates]
    assignments: dict[str, str] = {}
    value_idx = 0

    for day in days:
        if value_idx >= len(values):
            break
        value = values[value_idx]
        value_idx += 1
        if not value or value.upper() in {"CHIUSO", "CHUSO"}:
            continue
        assignments[day] = value

    return assignments


def _parse_grid_block(
    lines: list[str],
    header_idx: int,
    meal: str,
    weekday_dates: dict[str, date],
) -> list[dict]:
    block_lines = _merge_layout_lines(lines[header_idx + 1:])
    partial_rows: list[dict] = []

    for line in block_lines:
        if sum(1 for day in WEEKDAY_KEYS if day in line.upper()) >= 4:
            break

        field, values = _parse_row_line(line)
        if not field or not values:
            continue

        for day_key, value in _assign_day_values(values, weekday_dates).items():
            day = weekday_dates[day_key]
            partial_rows.append({
                "date": _date_label(day),
                "date_obj": day,
                "meal": meal,
                "field": field,
                "value": value,
            })

    merged: dict[tuple[date, str], dict] = {}
    for row in partial_rows:
        key = (row["date_obj"], row["meal"])
        if key not in merged:
            merged[key] = {
                "date": row["date"],
                "date_obj": row["date_obj"],
                "meal": row["meal"],
                "primo": "",
                "secondo": "",
                "contorno": "",
            }
        if not merged[key][row["field"]]:
            merged[key][row["field"]] = row["value"]

    output = []
    for item in sorted(merged.values(), key=lambda r: (r["date_obj"], r["meal"])):
        output.append({
            "date": item["date"],
            "meal": item["meal"],
            "primo": item["primo"],
            "secondo": item["secondo"],
            "contorno": item["contorno"],
        })
    return output


def _parse_vertical_sections(text: str, weekday_dates: dict[str, date]) -> list[dict]:
    rows: list[dict] = []
    day_pattern = re.compile(
        r"(Lunedì|Martedì|Mercoledì|Giovedì|Venerdì|Sabato|Domenica)\s+"
        r"(\d{1,2})\s+([A-Za-zÀ-ú]+)",
        re.IGNORECASE,
    )
    meal = "Pranzo"
    current_day: str | None = None
    current: dict[str, str] = {}

    def flush():
        nonlocal current
        if not current_day or not any(current.values()):
            return
        rows.append({
            "date": current_day,
            "meal": meal,
            "primo": current.get("primo", ""),
            "secondo": current.get("secondo", ""),
            "contorno": current.get("contorno", ""),
        })
        current = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        upper = line.upper()
        if upper in {"PRANZO", "CENA"}:
            meal = "Pranzo" if "PRANZO" in upper else "Cena"
            continue

        day_match = day_pattern.match(line)
        if day_match:
            flush()
            current_day = f"{day_match.group(1).capitalize()} {int(day_match.group(2))} {day_match.group(3).capitalize()}"
            continue

        field, values = _parse_row_line(line)
        if field and values and current_day:
            dish = values[0] if len(values) == 1 else " ".join(values)
            if dish.upper() not in {"CHEF", "DELLO", "DELLO CHEF", "CALDO"}:
                current[field] = dish
            continue

        if "PRIMO" in upper:
            current["primo"] = _extract_dish_after_label(line, "primo")
        elif "SECONDO" in upper:
            current["secondo"] = _extract_dish_after_label(line, "secondo")
        elif "CONTORNO" in upper or upper.startswith("1 CONTORNO"):
            current["contorno"] = _extract_dish_after_label(line, "contorno")

    flush()
    return rows


def parse_menu_from_text(text: str, link_title: str = "") -> list[dict]:
    text = _normalize_menu_text(text)
    link_title = _normalize_menu_text(link_title)

    date_range = _parse_date_range(text, link_title)
    if not date_range:
        single = _parse_single_date(text, link_title)
        if single:
            date_range = (single, single)
        else:
            today = rome_today()
            monday = today - timedelta(days=today.weekday())
            date_range = (monday, monday + timedelta(days=6))

    start, end = date_range
    weekday_dates = _weekday_dates(start, end)
    lines = text.splitlines()

    header_indices = [
        idx for idx, line in enumerate(lines)
        if sum(1 for day in WEEKDAY_KEYS if day in line.upper()) >= 4
    ]

    results: list[dict] = []
    meal_cycle = ["Pranzo", "Cena"]
    for block_idx, header_idx in enumerate(header_indices[:2]):
        meal = meal_cycle[block_idx] if block_idx < len(meal_cycle) else "Pranzo"
        results.extend(_parse_grid_block(lines, header_idx, meal, weekday_dates))

    if not results:
        results = _parse_vertical_sections(text, weekday_dates)

    if not results:
        results = _parse_daily_menu(text, link_title)

    cleaned = []
    seen = set()
    for row in results:
        if not any([row.get("primo"), row.get("secondo"), row.get("contorno")]):
            continue
        key = (row["date"], row["meal"], row.get("primo"), row.get("secondo"), row.get("contorno"))
        if key in seen:
            continue
        seen.add(key)
        parsed_date = row.get("date_obj")
        if not isinstance(parsed_date, date):
            parsed_date = _parse_row_date(row["date"], rome_today())
        if not isinstance(parsed_date, date):
            parsed_date = _parse_single_date(row.get("date", ""), link_title)
        cleaned.append({
            "date": row["date"],
            "date_obj": parsed_date,
            "meal": row["meal"],
            "primo": row.get("primo", ""),
            "secondo": row.get("secondo", ""),
            "contorno": row.get("contorno", ""),
        })

    return cleaned


def _parse_row_date(row_date: str, today: date) -> date | None:
    match = re.match(
        r"(Lunedì|Martedì|Mercoledì|Giovedì|Venerdì|Sabato|Domenica)\s+"
        r"(\d{1,2})\s+([A-Za-zÀ-ú]+)",
        row_date,
        re.IGNORECASE,
    )
    if not match:
        return None

    day_num = int(match.group(2))
    month = _resolve_month(match.group(3))
    if not month:
        return None

    year = today.year
    parsed = date(year, month, day_num)
    if (today - parsed).days > 30:
        parsed = date(year + 1, month, day_num)
    return parsed


def filter_menu(menu: list[dict], mode: str) -> list[dict]:
    today = rome_today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    filtered = []
    for row in menu:
        row_date = row.get("date_obj")
        if not isinstance(row_date, date):
            row_date = _parse_row_date(row.get("date", ""), today)
        if not isinstance(row_date, date):
            row_date = _parse_single_date(row.get("date", ""))
        if not row_date:
            continue
        if mode == "today" and row_date != today:
            continue
        if mode == "week" and not (week_start <= row_date <= week_end):
            continue
        filtered.append(row)

    return filtered if mode != "month" else menu


def _fit_cell(text: str, width: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= width:
        return text.ljust(width)
    return text[: width - 1] + "…"


def _format_single_table(row: dict) -> str:
    label_w, value_w = 13, 30
    top = f"┌{'─' * (label_w + 2)}┬{'─' * (value_w + 2)}┐"
    mid = f"├{'─' * (label_w + 2)}┼{'─' * (value_w + 2)}┤"
    bottom = f"└{'─' * (label_w + 2)}┴{'─' * (value_w + 2)}┘"

    def line(label: str, value: str) -> str:
        return f"│ {_fit_cell(label, label_w)} │ {_fit_cell(value or '-', value_w)} │"

    meal_en = "Lunch" if row.get("meal", "").lower() == "pranzo" else row.get("meal", "")
    if meal_en.lower() == "cena":
        meal_en = "Dinner"

    return "\n".join([
        f"📅 {_display_date(row)} — {meal_en}",
        top,
        line("First course", row.get("primo", "")),
        mid,
        line("Second course", row.get("secondo", "")),
        mid,
        line("Side dish", row.get("contorno", "")),
        bottom,
    ])


def format_menu_table(rows: list[dict]) -> str:
    if not rows:
        return "No menu rows found."

    blocks = [_format_single_table(row) for row in rows]
    return "\n\n".join(blocks)


def split_telegram_messages(text: str, limit: int = TELEGRAM_MAX_LEN) -> list[str]:
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        chunk = block if not current else f"{current}\n\n{block}"
        if len(chunk) > limit and current:
            parts.append(current)
            current = block
        elif len(chunk) > limit:
            parts.append(block[:limit])
            current = block[limit:]
        else:
            current = chunk
    if current:
        parts.append(current)
    return parts


async def translate_to_english(
    text: str,
    source_lang: str = "it",
    translate_url: str = DEFAULT_TRANSLATE_URL,
    translate_api_key: str = "",
) -> str:
    if not translate_url or not text.strip():
        return text

    blocks = [block for block in text.split("\n\n") if block.strip()]
    if not blocks:
        return text

    translated_blocks: list[str] = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for block in blocks:
            payload: dict[str, str] = {
                "q": block,
                "source": source_lang,
                "target": "en",
                "format": "text",
            }
            if translate_api_key:
                payload["api_key"] = translate_api_key

            try:
                response = await client.post(translate_url, json=payload)
                if response.status_code < 200 or response.status_code >= 300:
                    logger.warning(
                        "Translation failed (%s): %s",
                        response.status_code,
                        response.text[:200],
                    )
                    translated_blocks.append(block)
                    continue
                data = response.json()
                translated = data.get("translatedText")
                translated_blocks.append(translated if translated else block)
            except Exception as exc:
                logger.warning("Translation request error: %s", exc)
                translated_blocks.append(block)

    return "\n\n".join(translated_blocks)


async def load_menu_rows_from_ardis(ardis_url: str) -> tuple[list[dict], str]:
    html = await fetch_html(ardis_url)
    links = extract_trieste_menus(html, ardis_url)
    if not links:
        raise MenuLoadError("No Trieste menu PDF found on the ARDiS page.")

    pdf_url = pick_pdf_url(links)
    if not pdf_url:
        raise MenuLoadError("No PDF link available for Trieste menus.")

    link_title = links[0].get("title", "")
    pdf_bytes = await fetch_pdf_bytes(pdf_url)
    text = parse_pdf_text(pdf_bytes)
    rows = parse_menu_from_text(text, link_title=link_title)
    if not rows:
        raise MenuLoadError("Could not parse menu rows from the PDF.")

    return rows, pdf_url


async def load_menu_rows_from_waha(
    waha_url: str,
    session: str,
    channel_invite: str,
    api_key: str = "",
) -> tuple[list[dict], str]:
    from .waha import WahaError, extract_menu_asset, fetch_channel_messages_preview

    if not waha_url:
        raise MenuLoadError(
            "WAHA_URL is not configured. Set it to your WAHA server base URL."
        )

    try:
        messages = await fetch_channel_messages_preview(
            waha_url=waha_url,
            session=session,
            invite=channel_invite,
            api_key=api_key,
        )
        asset_url, kind, title = extract_menu_asset(messages, waha_url)
    except WahaError as exc:
        raise MenuLoadError(str(exc)) from exc

    if kind == "text":
        rows = parse_menu_from_text(asset_url, link_title=title)
        if not rows:
            raise MenuLoadError("Could not parse menu rows from WhatsApp text.")
        return rows, "whatsapp:text"

    if kind == "image":
        from .ocr import extract_text_from_image

        image_bytes = await fetch_bytes(asset_url)
        text = await extract_text_from_image(image_bytes)
        if not text:
            raise MenuLoadError("Could not extract text from the WhatsApp menu image.")
        rows = parse_menu_from_text(text, link_title=title)
        if not rows:
            raise MenuLoadError("Could not parse menu rows from the WhatsApp menu image.")
        return rows, asset_url

    pdf_bytes = await fetch_pdf_bytes(asset_url)
    text = parse_pdf_text(pdf_bytes)
    rows = parse_menu_from_text(text, link_title=title)
    if not rows:
        raise MenuLoadError("Could not parse menu rows from the WhatsApp PDF.")
    return rows, asset_url


async def load_channel_message_links(
    waha_url: str,
    session: str,
    channel_invite: str,
    api_key: str = "",
) -> list[dict]:
    from .waha import WahaError, fetch_channel_messages_preview

    messages = await fetch_channel_messages_preview(
        waha_url=waha_url,
        session=session,
        invite=channel_invite,
        api_key=api_key,
    )
    links = []
    for item in messages:
        msg = item.get("message") if isinstance(item, dict) else None
        if not isinstance(msg, dict):
            continue
        body = (msg.get("body") or "").strip()
        media = msg.get("media") or {}
        media_url = media.get("url") or msg.get("mediaUrl") or ""
        title = body[:120] if body else "WhatsApp channel post"
        if media_url:
            links.append({"title": title, "url": media_url})
        for match in re.finditer(r"https?://[^\s)>\"']+", body):
            links.append({"title": title, "url": match.group(0)})
    if not links:
        raise MenuLoadError("No links found in WhatsApp channel messages.")
    return links


async def load_menu_rows(
    waha_url: str = "",
    waha_session: str = "default",
    channel_invite: str = "",
    waha_api_key: str = "",
    ardis_url: str = ARDIS_MENU_URL,
    prefer_waha: bool = True,
    ardis_fallback: bool = False,
) -> tuple[list[dict], str]:
    if prefer_waha and waha_url:
        try:
            return await load_menu_rows_from_waha(
                waha_url=waha_url,
                session=waha_session,
                channel_invite=channel_invite,
                api_key=waha_api_key,
            )
        except MenuLoadError as exc:
            msg = str(exc).lower()
            if "401" in msg or "authentication" in msg:
                raise MenuLoadError(
                    "WhatsApp channel unavailable: WAHA API key is missing or wrong. "
                    "Set WAHA_API_KEY in .env (same value for waha and bot), then "
                    "run: docker compose up -d --build"
                ) from exc
            if not ardis_fallback or not ardis_url:
                raise
            logger.warning("WAHA failed (%s), falling back to ARDiS", exc)

    return await load_menu_rows_from_ardis(ardis_url)


def is_trieste_menu_link(title: str, url: str) -> bool:
    text = f"{title} {url}".lower()

    has_menu = any(kw in text for kw in MENU_KEYWORDS)
    has_trieste = any(kw in text for kw in TRIESTE_KEYWORDS)
    is_excluded = any(city in text for city in EXCLUDE_CITIES)

    return has_menu and has_trieste and not is_excluded


def extract_trieste_menus(html: str, base_url: str) -> list[dict]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    results = []

    for tag in soup.find_all("a", href=True):
        title = tag.get_text(strip=True)
        href = tag["href"]
        full_url = urljoin(base_url, href)

        if full_url in seen:
            continue

        if is_trieste_menu_link(title, full_url):
            seen.add(full_url)
            results.append({"title": title, "url": full_url})

        if len(results) >= 20:
            break

    return results


def format_menu_message(links: list[dict], source: str = "ARDiS") -> str:
    if not links:
        return (
            "⚠️ Could not find Trieste menu files right now.\n\n"
            "Check the LAMensa WhatsApp channel:\n"
            "https://whatsapp.com/channel/0029Vb5cElw5a23zVecmn70P"
        )

    lines = [f"🍽 Mensa Centrale Trieste", f"Latest menu files from {source}:\n"]
    for i, item in enumerate(links, 1):
        title = item["title"] or f"Menu file {i}"
        lines.append(f"{i}. {title}\n{item['url']}")

    return "\n\n".join(lines)
