# services/competitor_scraper/parser.py
"""
Парсер заголовков joymi.uz и нормализация полей.

Заголовки разнобойные, например:
  "🏠Юнусобод 15 мавзе 4/6/9"
  "Sergeli Qumariqdan 10.9 sotixli uchastka sotiladi!"
  "Sagban 2x Komnatnaya euro kvartira"
  "Яшнабадский, Боткина 2/6/9 61м2"
  "ДОМ Яккасарайский район на: Ракат махалля 350кв.м 3 сотки"
  "Янги Тошкент, Дача сотилади 6 сотых"

Нам надо вытащить: rooms, floor, total_floors, area, object_type, deal_type.
И нормализовать district из address_line ("Toshkent shahri, Yunusobod tumani").
"""
import re
from typing import Optional, Dict, Any, Tuple
from decimal import Decimal


# ============================================
# CURRENCY MAPPING
# ============================================
# joymi.uz: currency=1 → UZS (sum), currency=2 → USD
# Это не задокументировано, но из примеров очевидно:
#   currency=2, price=700 → USD (аренда квартиры)
#   currency=1, price=1000000 → UZS (1млн сум)
CURRENCY_MAP = {1: "uzs", 2: "usd"}


# ============================================
# DISTRICT MAPPING (joymi → наш формат)
# ============================================
# joymi пишет районы как "Yunusobod tumani", у нас в основной БД "Юнусабадский".
# Этот словарь — мост.
DISTRICT_NORMALIZE = {
    # Tashkent city
    "olmazor":          "Алмазарский",
    "bektemir":         "Бектемирский",
    "mirobod":          "Мирабадский",
    "mirzo ulug'bek":   "Мирзо-Улугбекский",
    "mirzo ulugbek":    "Мирзо-Улугбекский",
    "sergeli":          "Сергелийский",
    "uchtepa":          "Учтепинский",
    "chilonzor":        "Чиланзарский",
    "shayxontohur":     "Шайхантахурский",
    "yunusobod":        "Юнусабадский",
    "yakkasaroy":       "Яккасарайский",
    "yashnobod":        "Яшнабадский",
    # Tashkent oblast and other regions kept as-is in district_norm = None
    # (мы парсим только Ташкент-шахри для пересечений)
}


# ============================================
# PATTERNS
# ============================================

# Тройная нотация: 4/6/9 (комнаты/этаж/этажность). Может быть "2-3/4/8".
TRIPLE_RE = re.compile(r"(\d+)(?:\s*[\-–]\s*\d+)?\s*/\s*(\d+)\s*/\s*(\d+)")

# "2x Komnatnaya", "2-комнатная", "1 хонали"
ROOMS_RE = [
    re.compile(r"(\d+)\s*[xх]\s*komnat", re.I),
    re.compile(r"(\d+)\s*[\-–]?\s*комнат", re.I),
    re.compile(r"(\d+)\s*xonali", re.I),
    re.compile(r"(\d+)\s*хонали", re.I),
    re.compile(r"(\d+)\s*xona\b", re.I),
]

# Площадь м²: "350кв.м", "61м2", "120 m²"
AREA_M2_RE = [
    re.compile(r"(\d+(?:[.,]\d+)?)\s*кв\.?\s*м", re.I),
    re.compile(r"(\d+(?:[.,]\d+)?)\s*m[²2]", re.I),
    re.compile(r"(\d+(?:[.,]\d+)?)\s*м[²2]", re.I),
    re.compile(r"(\d+(?:[.,]\d+)?)\s*кв\b", re.I),
]

# Сотки: "3 сотки", "6 сотых", "10.9 sotix", "6 сотых"
SOTOK_RE = [
    re.compile(r"(\d+(?:[.,]\d+)?)\s*сот(?:ок|ки|ых|ыx|ка)?", re.I),
    re.compile(r"(\d+(?:[.,]\d+)?)\s*sot(?:ix|ixli|ka|ok)?", re.I),
]

# Sale keywords (latin + cyrillic, ru + uz)
SALE_KW = re.compile(
    r"\b(сотилади|sotiladi|sotuv|продаётся|продается|продам|продаю|на\s*продажу|prodaetsia|prodajа|prodaja)\b",
    re.I,
)

# Rent keywords
RENT_KW = re.compile(
    r"\b(ижара|ijara|аренда|сдаётся|сдается|сдам|kvartira\s*ijara|arenda|rent)\b",
    re.I,
)

# Object type hints
HOUSE_KW = re.compile(r"\b(дом|дача|hovli|uy|kottedj|коттедж|hujra|dacha)\b", re.I)
LAND_KW = re.compile(r"\b(участок|uchastka|yer|земля|tomorqa)\b", re.I)
COMMERCIAL_KW = re.compile(r"\b(офис|office|магазин|do['']kon|склад|sklad|коммерч|kommert)\b", re.I)
ROOM_KW = re.compile(r"\b(комнат[уа]|xona\s*ijara|комната\s)\b", re.I)


# ============================================
# HELPERS
# ============================================

def _to_float(s: str) -> Optional[float]:
    if not s:
        return None
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def _to_int(s: str) -> Optional[int]:
    f = _to_float(s)
    return int(f) if f is not None else None


# ============================================
# PARSERS
# ============================================

def parse_address(address_line: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    "Toshkent shahri, Yunusobod tumani" → (region, district_raw, district_norm)
    "Toshkent viloyati, Quyichirchiq tumani" → (region, district_raw, None)
    """
    if not address_line:
        return None, None, None

    parts = [p.strip() for p in address_line.split(",")]
    region = parts[0] if parts else None
    district_raw = None
    district_norm = None

    if len(parts) >= 2:
        # Find part with "tumani" / "район"
        for p in parts[1:]:
            low = p.lower()
            if "tumani" in low or "район" in low or "tuman" in low:
                district_raw = p
                # Strip "tumani" / "район" suffix to lookup
                key = re.sub(r"\s*(tumani|tuman|район)\s*$", "", low).strip()
                # Remove apostrophe variants for matching
                key_norm = key.replace("'", "'").replace("`", "'")
                district_norm = DISTRICT_NORMALIZE.get(key_norm)
                if not district_norm:
                    # try without apostrophe
                    key_alt = key.replace("'", "").replace("'", "").replace("`", "")
                    for k, v in DISTRICT_NORMALIZE.items():
                        if k.replace("'", "") == key_alt:
                            district_norm = v
                            break
                break

    return region, district_raw, district_norm


def parse_triple(title: str) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """Парсит '4/6/9' формат: rooms/floor/total_floors."""
    if not title:
        return None, None, None
    m = TRIPLE_RE.search(title)
    if not m:
        return None, None, None
    try:
        rooms = int(m.group(1))
        floor = int(m.group(2))
        total = int(m.group(3))
        # sanity
        if not (0 < rooms <= 10):
            rooms = None
        if floor and total and floor > total:
            # Подозрительно — возможно это что-то другое
            return None, None, None
        if total and total > 60:
            total = None
        if floor and floor > 60:
            floor = None
        return rooms, floor, total
    except (ValueError, IndexError):
        return None, None, None


def parse_rooms_only(title: str) -> Optional[int]:
    if not title:
        return None
    for pat in ROOMS_RE:
        m = pat.search(title)
        if m:
            n = _to_int(m.group(1))
            if n and 0 < n <= 10:
                return n
    return None


def parse_area_m2(title: str) -> Optional[float]:
    if not title:
        return None
    for pat in AREA_M2_RE:
        m = pat.search(title)
        if m:
            v = _to_float(m.group(1))
            if v and 5 <= v <= 5000:
                return v
    return None


def parse_area_sotok(title: str) -> Optional[float]:
    if not title:
        return None
    for pat in SOTOK_RE:
        m = pat.search(title)
        if m:
            v = _to_float(m.group(1))
            if v and 0.5 <= v <= 500:
                return v
    return None


def detect_object_type(title: str, area_m2: Optional[float], area_sotok: Optional[float]) -> str:
    """Определяет тип объекта."""
    if not title:
        return "unknown"
    t = title.lower()

    if COMMERCIAL_KW.search(t):
        return "commercial"
    if LAND_KW.search(t) or (area_sotok and not area_m2 and not re.search(r"квартир|kvartira|комнат", t, re.I)):
        return "land"
    if HOUSE_KW.search(t) and not re.search(r"квартир|kvartira", t, re.I):
        return "house"
    if ROOM_KW.search(t):
        return "room"
    # Default — flat (т.к. большинство listings)
    if re.search(r"квартир|kvartira", t, re.I) or "/" in t:
        return "flat"

    return "flat"


def detect_deal_type(title: str, price_usd: Optional[float], object_type: str) -> str:
    """
    rent_long / sale / unknown.
    
    Эвристика: явные слова > price-based.
    Аренда обычно USD: 200..3000, продажа: 30000..500000.
    """
    if title:
        if SALE_KW.search(title):
            return "sale"
        if RENT_KW.search(title):
            return "rent_long"

    # Price heuristic (only if currency is USD-equivalent)
    if price_usd is not None:
        if price_usd < 5000:
            return "rent_long"
        if price_usd >= 10000:
            return "sale"

    # Land/house без цены — обычно sale
    if object_type in ("land", "house", "commercial"):
        return "sale"

    return "unknown"


def normalize_currency(currency_code: Optional[int]) -> Optional[str]:
    if currency_code is None:
        return None
    return CURRENCY_MAP.get(currency_code)


def price_to_usd_estimate(price: Optional[float], currency: Optional[str]) -> Optional[float]:
    """Грубая оценка в USD для классификации deal_type. 1 USD ≈ 12500 UZS."""
    if price is None or currency is None:
        return None
    if currency == "usd":
        return float(price)
    if currency == "uzs":
        return float(price) / 12500.0
    return None


# ============================================
# MAIN
# ============================================

def parse_listing(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Главная функция: берёт raw item из API и возвращает поля для БД.
    
    Возвращает только то, что мы напарсили. Сырые поля (price_raw, currency_raw,
    address_line, etc.) сохраняются отдельно в scraper.py.
    """
    title = item.get("title") or ""
    address_line = item.get("address_line") or ""

    # Address
    region, district_raw, district_norm = parse_address(address_line)

    # Title parsing
    rooms, floor, total_floors = parse_triple(title)
    if rooms is None:
        rooms = parse_rooms_only(title)

    area_m2 = parse_area_m2(title)
    area_sotok = parse_area_sotok(title)

    object_type = detect_object_type(title, area_m2, area_sotok)

    # Currency / price
    currency = normalize_currency(item.get("currency"))
    raw_price = item.get("price")
    price_usd = price_to_usd_estimate(raw_price, currency)
    deal_type = detect_deal_type(title, price_usd, object_type)

    # Parse score (heuristic for needs_review)
    score = 0
    if rooms is not None: score += 2
    if floor is not None: score += 1
    if total_floors is not None: score += 1
    if area_m2 is not None or area_sotok is not None: score += 1
    if district_norm is not None: score += 2
    if deal_type != "unknown": score += 2

    return {
        "rooms": rooms,
        "floor": floor,
        "total_floors": total_floors,
        "area_m2": area_m2,
        "area_sotok": area_sotok,
        "object_type": object_type,
        "deal_type": deal_type,
        "currency": currency,
        "region": region,
        "district_raw": district_raw,
        "district_norm": district_norm,
        "parse_score": score,
        "needs_review": score < 4,
    }


# ============================================
# Self-test
# ============================================

if __name__ == "__main__":
    samples = [
        {
            "title": "🏠Юнусобод 15 мавзе 4/6/9",
            "address_line": "Toshkent shahri, Yunusobod tumani",
            "price": 95500.0, "currency": 2,
        },
        {
            "title": "Sergeli Qumariqdan 10.9 sotixli uchastka sotiladi!",
            "address_line": "Toshkent shahri, Sergeli tumani",
            "price": 29000.0, "currency": 2,
        },
        {
            "title": "Sagban 2x Komnatnaya euro kvartira",
            "address_line": "Toshkent shahri, Olmazor tumani",
            "price": 700.0, "currency": 2,
        },
        {
            "title": "Яшнабадский, Боткина 2/6/9 61м2",
            "address_line": "Toshkent shahri, Yashnobod tumani",
            "price": 450.0, "currency": 2,
        },
        {
            "title": "ДОМ Яккасарайский район на: Ракат махалля 350кв.м 3 сотки",
            "address_line": "Toshkent shahri, Yakkasaroy tumani",
            "price": 550000.0, "currency": 2,
        },
        {
            "title": "Янги Тошкент, Дача сотилади 6 сотых",
            "address_line": "Toshkent viloyati, Quyichirchiq tumani",
            "price": 80000.0, "currency": 2,
        },
        {
            "title": "🏠Юнусобод 13 мавзе 2-3/4/8 НОВОСТРОЙКА",
            "address_line": "Toshkent shahri, Yunusobod tumani",
            "price": 87500.0, "currency": 2,
        },
    ]

    for s in samples:
        result = parse_listing(s)
        print(f"\n>>> {s['title']!r}")
        for k, v in result.items():
            if v is not None and v is not False:
                print(f"    {k}: {v}")