# services/olx_scraper/parser.py
"""
HTML parsers for olx.uz.

Two main parts:
1) parse_list_page(html)  → list[ListCard]   from arenda-dolgosrochnaya page
2) parse_detail_page(html) → DetailData      from individual /d/obyavlenie/... page

Strategy: try Next.js __NEXT_DATA__ JSON first (if OLX exposes it — they do, but
schema can shift), fall back to DOM selectors with multiple alternatives, then
to regex on plain text. This keeps the scraper alive even if OLX changes class
names or adds wrapper components.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag


# ============================================================
# Data classes
# ============================================================

@dataclass
class ListCard:
    source_id: str                      # URL slug, e.g. "4mpFL"
    url: str                            # absolute URL
    title: Optional[str] = None
    price: Optional[Decimal] = None
    currency: Optional[str] = None      # "uzs" / "usd"
    region: Optional[str] = None        # "Ташкент" / "Самарканд" / ...
    district_raw: Optional[str] = None  # "Юнусабадский район"
    district_norm: Optional[str] = None # "Юнусабадский"
    posted_text: Optional[str] = None   # raw "Сегодня в 07:19" / "08 марта 2026 г."
    preview_url: Optional[str] = None
    is_promoted: bool = False           # search_reason=promoted

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d["price"] is not None:
            d["price"] = float(d["price"])
        return d


@dataclass
class DetailData:
    # Identity
    source_id: str = ""
    numeric_id: Optional[int] = None
    url: str = ""
    title: Optional[str] = None

    # Pricing
    price: Optional[Decimal] = None
    currency: Optional[str] = None
    is_negotiable: bool = False

    # Object params (from structured fields)
    rooms: Optional[int] = None
    floor: Optional[int] = None
    total_floors: Optional[int] = None
    area_m2: Optional[Decimal] = None

    building_type: Optional[str] = None    # "Кирпичный"
    layout: Optional[str] = None           # "Раздельная"
    bathroom: Optional[str] = None         # "Совмещенный"
    has_furniture: Optional[bool] = None
    condition: Optional[str] = None        # "Авторский проект"
    has_commission: Optional[bool] = None
    seller_type: Optional[str] = None      # "Бизнес" / "Частное"

    # Location
    region: Optional[str] = None
    district_raw: Optional[str] = None
    district_norm: Optional[str] = None
    address_line: Optional[str] = None     # full breadcrumb-derived

    # Description / contact
    description: Optional[str] = None
    phones_in_text: List[str] = field(default_factory=list)

    # Seller (subdomain profile)
    seller_name: Optional[str] = None
    seller_url: Optional[str] = None

    # Posted
    posted_text: Optional[str] = None
    created_at_remote: Optional[datetime] = None

    # Media
    photo_urls: List[str] = field(default_factory=list)

    # Quality
    parse_score: int = 0
    needs_review: bool = True

    def metadata(self) -> Dict[str, Any]:
        """Source-specific extras — stored as JSONB."""
        return {
            k: v for k, v in {
                "building_type": self.building_type,
                "layout": self.layout,
                "bathroom": self.bathroom,
                "has_commission": self.has_commission,
                "seller_type": self.seller_type,
                "is_negotiable": self.is_negotiable,
                "numeric_id": self.numeric_id,
                "seller_name": self.seller_name,
                "seller_url": self.seller_url,
            }.items() if v is not None
        }


# ============================================================
# District/region normalization (mirrors joymi parser)
# ============================================================

DISTRICT_NORMALIZE_RU = {
    # OLX writes districts in Russian — direct mapping
    "алмазарский":      "Алмазарский",
    "бектемирский":     "Бектемирский",
    "мирабадский":      "Мирабадский",
    "мирзо-улугбекский":"Мирзо-Улугбекский",
    "мирзо улугбекский":"Мирзо-Улугбекский",
    "сергелийский":     "Сергелийский",
    "учтепинский":      "Учтепинский",
    "чиланзарский":     "Чиланзарский",
    "шайхантахурский":  "Шайхантахурский",
    "шайхонтохурский":  "Шайхантахурский",
    "юнусабадский":     "Юнусабадский",
    "яккасарайский":    "Яккасарайский",
    "яшнабадский":      "Яшнабадский",
}


def _normalize_district(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    cleaned = raw.lower().strip()
    cleaned = re.sub(r"\s+район\s*$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return DISTRICT_NORMALIZE_RU.get(cleaned)


# ============================================================
# Regex helpers
# ============================================================

# Match "ID4mpFL" before .html
RE_ID_FROM_URL = re.compile(r"-ID([A-Za-z0-9]+)\.html")
RE_NUMERIC_ID = re.compile(r"^ID:\s*(\d+)\s*$", re.M)
RE_PROMOTED = re.compile(r"search_reason=search%7Cpromoted")

# Price: "5 465 925 сум" or "1 200 у.е." (currencies have NBSP variants)
RE_PRICE = re.compile(
    r"(\d{1,3}(?:[\s\u00a0\u202f]\d{3})*|\d+)\s*(сум|у\.\s*е\.?|у\.е|y\.e\.?|usd|UZS|\$)",
    re.IGNORECASE
)
RE_NEGOTIABLE = re.compile(r"договорн", re.IGNORECASE)


# ============================================================
# Helpers
# ============================================================

def _parse_price_and_currency(text: str) -> Tuple[Optional[Decimal], Optional[str], bool]:
    if not text:
        return None, None, False
    is_neg = bool(RE_NEGOTIABLE.search(text))
    m = RE_PRICE.search(text)
    if not m:
        return None, None, is_neg
    digits = re.sub(r"[\s\u00a0\u202f]", "", m.group(1))
    try:
        amount = Decimal(digits)
    except Exception:
        return None, None, is_neg
    cur_raw = m.group(2).lower().replace(" ", "").replace(".", "")
    if cur_raw in ("сум", "uzs"):
        currency = "uzs"
    else:
        currency = "usd"
    return amount, currency, is_neg


def _parse_location(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    "Ташкент, Юнусабадский район - 08 марта 2026 г."  → ("Ташкент", "Юнусабадский район")
    "Самарканд - Сегодня в 07:19"                      → ("Самарканд", None)
    "Мирабад - Сегодня в 07:18"                        → ("Мирабад", None)
    """
    if not text:
        return None, None
    # Strip date suffix after the last " - " — but careful: "г. Ташкент, X район - дата"
    # split on " - " (with spaces) and take first half
    core = text.split(" - ")[0].strip() if " - " in text else text.strip()
    # Then split by comma → region, district
    parts = [p.strip() for p in core.split(",")]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return parts[0], None


def _abs_url(href: str, base: str = "https://www.olx.uz") -> str:
    return urljoin(base, href)


def _extract_id_from_url(url: str) -> Optional[str]:
    m = RE_ID_FROM_URL.search(url)
    return m.group(1) if m else None


def _is_listing_link(href: str) -> bool:
    return ("/d/obyavlenie/" in href or "/d/uz/obyavlenie/" in href) and "ID" in href and href.endswith(".html") or (
        "/d/obyavlenie/" in href and ".html" in href and "ID" in href
    )


# ============================================================
# LIST page parsing
# ============================================================

def parse_list_page(html: str, base_url: str = "https://www.olx.uz") -> List[ListCard]:
    """
    Parse the long-term-rent list page. Returns one ListCard per ad.

    Strategy: locate each card by finding listing links (anchors to
    /d/obyavlenie/.../IDxxxx.html), then walk up to the smallest enclosing
    container that holds the price + location text, and extract from there.
    """
    soup = BeautifulSoup(html, "html.parser")
    cards: List[ListCard] = []
    seen_ids: set = set()

    # Try data-cy="l-card" first (typical OLX structure)
    candidates = soup.find_all("div", attrs={"data-cy": "l-card"})

    if not candidates:
        # Fallback: find <a> elements with listing href, walk up
        anchors = soup.find_all("a", href=lambda h: h and _is_listing_link(h))
        candidates = []
        seen_anchors = set()
        for a in anchors:
            # Walk up to nearest <div> or <article>
            container = a
            for _ in range(6):
                container = container.parent
                if container is None:
                    break
                if container.name in ("article", "div") and len(container.get_text(strip=True)) > 30:
                    break
            if container is not None and id(container) not in seen_anchors:
                seen_anchors.add(id(container))
                candidates.append(container)

    for card in candidates:
        try:
            anchor = card.find("a", href=lambda h: h and _is_listing_link(h))
            if anchor is None:
                continue

            href = anchor.get("href", "")
            url = _abs_url(href, base_url)
            sid = _extract_id_from_url(url)
            if not sid or sid in seen_ids:
                continue
            seen_ids.add(sid)

            # Title — try h6/h4/h3 inside card; fall back to anchor text
            title = None
            for tag_name in ("h6", "h4", "h3"):
                tag = card.find(tag_name)
                if tag and tag.get_text(strip=True):
                    title = tag.get_text(strip=True)
                    break
            if not title:
                title = anchor.get_text(strip=True) or None

            # Price
            price_el = (
                card.find(attrs={"data-testid": "ad-price"})
                or card.find(attrs={"data-cy": "ad-card-price"})
                or card.find("p", string=re.compile(r"(сум|у\.е|\$|UZS)", re.I))
            )
            price_text = price_el.get_text(" ", strip=True) if price_el else ""
            price, currency, _ = _parse_price_and_currency(price_text)

            # Location + date
            loc_el = (
                card.find(attrs={"data-testid": "location-date"})
                or card.find("p", attrs={"data-testid": re.compile(r"location", re.I)})
            )
            loc_text = loc_el.get_text(" ", strip=True) if loc_el else ""
            region, district = _parse_location(loc_text)

            # Posted text — what's after " - " in loc_text
            posted_text = None
            if " - " in loc_text:
                posted_text = loc_text.split(" - ", 1)[1].strip()

            # Preview image
            img = card.find("img")
            preview_url = None
            if img:
                src = img.get("src") or img.get("data-src") or ""
                if "frankfurt.apollo.olxcdn.com" in src or "/files/" in src:
                    preview_url = src

            cards.append(ListCard(
                source_id=sid,
                url=url,
                title=title,
                price=price,
                currency=currency,
                region=region,
                district_raw=district,
                district_norm=_normalize_district(district),
                posted_text=posted_text,
                preview_url=preview_url,
                is_promoted=bool(RE_PROMOTED.search(href)),
            ))
        except Exception as e:
            print(f"[olx-parse] card error: {e}")

    return cards


def get_total_pages(html: str) -> Optional[int]:
    """Find max page number from pagination links."""
    soup = BeautifulSoup(html, "html.parser")
    nums: List[int] = []
    for a in soup.find_all("a", href=re.compile(r"[\?&]page=\d+")):
        m = re.search(r"page=(\d+)", a.get("href", ""))
        if m:
            try:
                nums.append(int(m.group(1)))
            except ValueError:
                pass
    return max(nums) if nums else None


# ============================================================
# DETAIL page parsing
# ============================================================

# Field labels OLX uses — Russian first, then Uzbek if differs
RE_FIELD_PATTERNS = {
    "rooms":         re.compile(r"Количество комнат[:\s]+(\d+)", re.I),
    "area_m2":       re.compile(r"Общая площадь[:\s]+(\d+(?:[.,]\d+)?)", re.I),
    "floor":         re.compile(r"^Этаж[:\s]+(\d+)\s*$", re.I | re.M),
    "total_floors":  re.compile(r"Этажность дома[:\s]+(\d+)", re.I),
    "building_type": re.compile(r"Тип строения[:\s]+([^\n]+)", re.I),
    "layout":        re.compile(r"Планировка[:\s]+([^\n]+)", re.I),
    "bathroom":      re.compile(r"Санузел[:\s]+([^\n]+)", re.I),
    "furniture":     re.compile(r"Меблирована[:\s]+([^\n]+)", re.I),
    "condition":     re.compile(r"Ремонт[:\s]+([^\n]+)", re.I),
    "commission":    re.compile(r"Комиссионные[:\s]+([^\n]+)", re.I),
}

RE_PHONE = re.compile(
    r"\+?\s*9?9?8?\s*[\-(]?\s*\d{2}\s*[\-)]?\s*\d{3}\s*[\-]?\s*\d{2}\s*[\-]?\s*\d{2}"
)

# Posted date relative phrases
RE_POSTED = re.compile(
    r"Опубликовано[^\n]+",
    re.I
)

# Russian month name → number
MONTH_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

RE_DATE_FULL = re.compile(r"(\d{1,2})\s+([а-я]+)\s+(\d{4})", re.I)


def _to_int(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    m = re.search(r"\d+", s)
    if not m:
        return None
    try:
        return int(m.group(0))
    except ValueError:
        return None


def _to_decimal(s: Optional[str]) -> Optional[Decimal]:
    if not s:
        return None
    m = re.search(r"\d+(?:[.,]\d+)?", s)
    if not m:
        return None
    try:
        return Decimal(m.group(0).replace(",", "."))
    except Exception:
        return None


def _yes_no(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    v = value.strip().lower()
    if v.startswith("да"):
        return True
    if v.startswith("нет"):
        return False
    return None


def _parse_date_text(text: str) -> Optional[datetime]:
    """
    Best-effort parse of OLX date strings:
      "Опубликовано сегодня в 06:00"      → today at 06:00
      "Опубликовано вчера в 22:30"        → yesterday at 22:30
      "Опубликовано 08 марта 2026 г."     → 2026-03-08
    """
    if not text:
        return None
    now = datetime.now(timezone.utc)

    m = RE_DATE_FULL.search(text)
    if m:
        day = int(m.group(1))
        month_name = m.group(2).lower()
        year = int(m.group(3))
        month = MONTH_RU.get(month_name)
        if month:
            try:
                return datetime(year, month, day, tzinfo=timezone.utc)
            except ValueError:
                return None

    # "сегодня в HH:MM"
    m2 = re.search(r"сегодня в (\d{1,2}):(\d{2})", text, re.I)
    if m2:
        return now.replace(hour=int(m2.group(1)), minute=int(m2.group(2)), second=0, microsecond=0)

    m3 = re.search(r"вчера в (\d{1,2}):(\d{2})", text, re.I)
    if m3:
        from datetime import timedelta
        y = (now - timedelta(days=1))
        return y.replace(hour=int(m3.group(1)), minute=int(m3.group(2)), second=0, microsecond=0)

    return None


def _extract_breadcrumbs(soup: BeautifulSoup) -> Tuple[Optional[str], Optional[str]]:
    """Pull region + district from breadcrumb nav (most reliable)."""
    # OLX puts breadcrumbs as <li> items in a nav near top
    region: Optional[str] = None
    district: Optional[str] = None

    # Pattern: "Аренда долгосрочная - Ташкент" / "Аренда долгосрочная - Мирабадский район"
    for li in soup.find_all(["li", "a"]):
        text = li.get_text(" ", strip=True)
        if not text or "Аренда долгосрочная" not in text:
            continue
        if " - " in text:
            tail = text.split(" - ", 1)[1].strip()
            if "район" in tail.lower():
                district = tail
            elif tail and tail not in ("Ташкентская область",):
                # could be a city name like "Ташкент" or "Самарканд"
                if not region:
                    region = tail

    # Fallback: any link with text containing "район"
    if district is None:
        for a in soup.find_all("a"):
            t = a.get_text(strip=True)
            if t and t.lower().endswith("район") and len(t) < 50:
                district = t
                break

    return region, district


def _extract_photos(soup: BeautifulSoup) -> List[str]:
    """Find all gallery image URLs."""
    urls: List[str] = []
    seen = set()
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src:
            continue
        # Match OLX CDN URL pattern
        if "olxcdn.com" not in src:
            continue
        if "/files/" not in src:
            continue
        # Skip thumbnails (they include `;s=216x152` etc.) — but for matching
        # we want the highest-resolution. Strip the size suffix if present.
        base = src.split(";s=")[0]
        if base in seen:
            continue
        seen.add(base)
        urls.append(src)  # keep original (may include size hint for download)
    return urls


def parse_detail_page(html: str, url: str = "") -> DetailData:
    """Parse a single olx.uz listing detail page."""
    soup = BeautifulSoup(html, "html.parser")
    body_text = soup.get_text("\n", strip=True)

    data = DetailData(url=url)
    data.source_id = _extract_id_from_url(url) or ""

    # Numeric ID
    m = RE_NUMERIC_ID.search(body_text)
    if m:
        data.numeric_id = int(m.group(1))

    # Title — try h1/h4 (h4 is what OLX uses on detail)
    for tag_name in ("h4", "h1", "h3"):
        tag = soup.find(tag_name)
        if tag and tag.get_text(strip=True):
            txt = tag.get_text(strip=True)
            # Skip "Описание" headers
            if "Описание" in txt or len(txt) < 5:
                continue
            data.title = txt
            break

    # Price
    price_el = (
        soup.find(attrs={"data-testid": "ad-price-container"})
        or soup.find("h3", string=re.compile(r"(сум|у\.е|\$)", re.I))
    )
    price_text = price_el.get_text(" ", strip=True) if price_el else ""
    if not price_text:
        # fallback: any line in body that looks like price
        for line in body_text.split("\n"):
            if RE_PRICE.search(line) and len(line) < 50:
                price_text = line
                break
    price, currency, is_neg = _parse_price_and_currency(price_text)
    data.price = price
    data.currency = currency
    data.is_negotiable = is_neg

    # Structured fields (from the labeled list at top of detail page)
    rooms_m = RE_FIELD_PATTERNS["rooms"].search(body_text)
    if rooms_m:
        data.rooms = _to_int(rooms_m.group(1))

    area_m = RE_FIELD_PATTERNS["area_m2"].search(body_text)
    if area_m:
        data.area_m2 = _to_decimal(area_m.group(1))

    floor_m = RE_FIELD_PATTERNS["floor"].search(body_text)
    if floor_m:
        data.floor = _to_int(floor_m.group(1))

    tf_m = RE_FIELD_PATTERNS["total_floors"].search(body_text)
    if tf_m:
        data.total_floors = _to_int(tf_m.group(1))

    bt_m = RE_FIELD_PATTERNS["building_type"].search(body_text)
    if bt_m:
        data.building_type = bt_m.group(1).strip()

    lay_m = RE_FIELD_PATTERNS["layout"].search(body_text)
    if lay_m:
        data.layout = lay_m.group(1).strip()

    bath_m = RE_FIELD_PATTERNS["bathroom"].search(body_text)
    if bath_m:
        data.bathroom = bath_m.group(1).strip()

    furn_m = RE_FIELD_PATTERNS["furniture"].search(body_text)
    if furn_m:
        data.has_furniture = _yes_no(furn_m.group(1))

    cond_m = RE_FIELD_PATTERNS["condition"].search(body_text)
    if cond_m:
        data.condition = cond_m.group(1).strip()

    com_m = RE_FIELD_PATTERNS["commission"].search(body_text)
    if com_m:
        data.has_commission = _yes_no(com_m.group(1))

    # Seller type ("Бизнес" / "Частное") — usually a top-level label
    if re.search(r"^Бизнес$", body_text, re.M):
        data.seller_type = "Бизнес"
    elif re.search(r"^Частное$", body_text, re.M):
        data.seller_type = "Частное"

    # Location from breadcrumbs
    region, district = _extract_breadcrumbs(soup)
    data.region = region
    data.district_raw = district
    data.district_norm = _normalize_district(district)
    if region or district:
        data.address_line = ", ".join([x for x in (region, district) if x])

    # Description: find <div data-cy="ad_description">
    desc_el = soup.find(attrs={"data-cy": "ad_description"})
    if desc_el is None:
        desc_el = soup.find("div", attrs={"data-testid": "ad_description"})
    if desc_el:
        data.description = desc_el.get_text("\n", strip=True)
    else:
        # Fallback: text between "Описание" header and the next major heading
        m = re.search(r"Описание\s*\n+(.*?)(?=\n(?:ID:|Пожаловаться|Связаться))", body_text, re.S)
        if m:
            data.description = m.group(1).strip()

    # Phones in description text (best effort, may be obscured "xxx xxx xxx")
    if data.description:
        phones: List[str] = []
        for raw in RE_PHONE.findall(data.description):
            digits = re.sub(r"\D", "", raw)
            if "x" in raw.lower() or len(digits) < 9:
                continue
            if len(digits) == 9:
                digits = "998" + digits
            if digits.startswith("998") and len(digits) == 12:
                phones.append("+" + digits)
        # dedupe preserving order
        data.phones_in_text = list(dict.fromkeys(phones))

    # Seller info — look for an anchor pointing to a *.olx.uz/home/ subdomain
    seller_link = None
    for a in soup.find_all("a", href=re.compile(r"https?://[^/]+\.olx\.uz/home/")):
        seller_link = a
        # Prefer one with text content (name)
        if a.get_text(strip=True):
            break
    if seller_link is not None:
        data.seller_url = seller_link.get("href")
        name = seller_link.get_text(strip=True)
        if name:
            # strip trailing "на OLX с..." metadata if combined
            name = name.split("\n")[0].strip()
            if 1 < len(name) < 100:
                data.seller_name = name

    # Posted date
    posted_m = RE_POSTED.search(body_text)
    if posted_m:
        data.posted_text = posted_m.group(0).strip()
        data.created_at_remote = _parse_date_text(data.posted_text)

    # Photos
    data.photo_urls = _extract_photos(soup)

    # Parse score
    score = 0
    if data.title: score += 1
    if data.price: score += 2
    if data.rooms is not None: score += 2
    if data.floor is not None: score += 1
    if data.total_floors is not None: score += 1
    if data.area_m2 is not None: score += 1
    if data.region or data.district_raw: score += 2
    if data.description: score += 1
    if data.photo_urls: score += 1
    if data.phones_in_text: score += 1
    data.parse_score = score
    data.needs_review = score < 7

    return data


# ============================================================
# Self-test
# ============================================================

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        path = sys.argv[1]
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
        if "list" in path.lower():
            cards = parse_list_page(html)
            for c in cards[:5]:
                print(c.to_dict())
            print(f"\ntotal cards: {len(cards)}")
        else:
            d = parse_detail_page(html)
            for k, v in asdict(d).items():
                if v not in (None, "", [], False):
                    print(f"  {k}: {v}")