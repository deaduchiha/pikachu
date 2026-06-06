import io
import json
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from js import Headers, Request, fetch as js_fetch
from pypdf import PdfReader

ARDIS_MENU_URL = "https://www.ardis.fvg.it/contenuti.php?id=214&view=page"
ROME_TZ = timezone(timedelta(hours=2))
DEFAULT_TRANSLATE_URL = "https://libretranslate.com/translate"


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


class MenuLoadError(Exception):
    pass


async def fetch_html(url: str) -> str:
    headers = Headers.new()
    headers.set("user-agent", "TriesteMensaBot/2.0")
    req = Request.new(url, headers=headers)
    resp = await js_fetch(req)
    return await resp.text()


async def fetch_pdf_bytes(url: str) -> bytes:
    headers = Headers.new()
    headers.set("user-agent", "TriesteMensaBot/2.0")
    req = Request.new(url, headers=headers)
    resp = await js_fetch(req)
    body = await resp.arrayBuffer()
    if hasattr(body, "to_py"):
        body = body.to_py()
    return bytes(body)


def parse_pdf_text(pdf_bytes: bytes) -> str:
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
            month = MONTHS_IT.get(month_name)
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

        if "PRIMO" in upper and "PIATTO" in upper:
            current["primo"] = re.sub(r"(?i)primo\s*piatto\s*", "", line).strip()
        elif "SECONDO" in upper and "PIATTO" in upper:
            current["secondo"] = re.sub(r"(?i)secondo\s*piatto\s*", "", line).strip()
        elif upper.startswith("CONTORNO"):
            current["contorno"] = re.sub(r"(?i)contorno\s*", "", line).strip()

    flush()
    return rows


def parse_menu_from_text(text: str, link_title: str = "") -> list[dict]:
    date_range = _parse_date_range(text, link_title)
    if not date_range:
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

    cleaned = []
    seen = set()
    for row in results:
        if not any([row.get("primo"), row.get("secondo"), row.get("contorno")]):
            continue
        key = (row["date"], row["meal"], row.get("primo"), row.get("secondo"), row.get("contorno"))
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({
            "date": row["date"],
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
    month = MONTHS_IT.get(match.group(3).lower())
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
        row_date = _parse_row_date(row["date"], today)
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

    return "\n".join([
        f"📅 {row['date']} — {row['meal']}",
        top,
        line("Primo", row.get("primo", "")),
        mid,
        line("Secondo", row.get("secondo", "")),
        mid,
        line("Contorno", row.get("contorno", "")),
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
) -> str:
    headers = Headers.new()
    headers.set("content-type", "application/json")
    body = json.dumps({
        "q": text,
        "source": source_lang,
        "target": "en",
        "format": "text",
    })
    req = Request.new(translate_url, method="POST", headers=headers, body=body)

    try:
        resp = await js_fetch(req)
        raw = await resp.text()
        if resp.status < 200 or resp.status >= 300:
            return text
        data = json.loads(raw)
        translated = data.get("translatedText")
        return translated if translated else text
    except Exception:
        return text


async def load_menu_rows(ardis_url: str) -> tuple[list[dict], str]:
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


def is_trieste_menu_link(title: str, url: str) -> bool:
    text = f"{title} {url}".lower()

    has_menu = any(kw in text for kw in MENU_KEYWORDS)
    has_trieste = any(kw in text for kw in TRIESTE_KEYWORDS)
    is_excluded = any(city in text for city in EXCLUDE_CITIES)

    return has_menu and has_trieste and not is_excluded


def extract_trieste_menus(html: str, base_url: str) -> list[dict]:
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


def format_menu_message(links: list[dict]) -> str:
    if not links:
        return (
            "⚠️ Could not find Trieste menu files right now.\n\n"
            "Check manually:\n"
            "https://www.ardis.fvg.it/contenuti.php?id=214&view=page\n\n"
            "Look for: Menù settimanali → Mensa centrale Trieste"
        )

    lines = ["🍽 Mensa Centrale Trieste", "Latest menu files from ARDiS:\n"]
    for i, item in enumerate(links, 1):
        title = item["title"] or f"Menu file {i}"
        lines.append(f"{i}. {title}\n{item['url']}")

    return "\n\n".join(lines)
