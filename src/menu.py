from urllib.parse import urljoin

from bs4 import BeautifulSoup
from js import Headers, Request, fetch as js_fetch

ARDIS_MENU_URL = "https://www.ardis.fvg.it/contenuti.php?id=214&view=page"

EXCLUDE_CITIES = ["udine", "gorizia", "pordenone", "gemona", "sacile"]

TRIESTE_KEYWORDS = [
    "trieste", "centrale", "self service", "self-service", "pizzeria", "insalatone"
]

MENU_KEYWORDS = ["menù", "menu", "men-", "men_"]


async def fetch_html(url: str) -> str:
    headers = Headers.new()
    headers.set("user-agent", "TriesteMensaBot/2.0")
    req = Request.new(url, headers=headers)
    resp = await js_fetch(req)
    return await resp.text()


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
