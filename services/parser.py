"""
services/parser.py
==================
Парсер объявлений недвижимости из Telegram-постов (Ташкент).

Экспортирует:
    parse_listing(text, hashtags=None) -> dict   — основная функция парсинга
    extract_phones(text) -> list[str]            — извлечение номеров телефонов

Импортируется в services/tg_listener.py:
    from services.parser import parse_listing, extract_phones
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# UZBEK PHONE EXTRACTION
# ============================================================

# Реальные коды операторов Узбекистана (мобильные + Ташкент-городской).
# Если появится новый код — добавь сюда, регулярка автоматически его подхватит.
UZ_OPERATOR_CODES = frozenset({
    "90", "91", "93", "94", "95", "97", "98", "99",  # мобильные основные
    "88", "33", "77",                                  # новые мобильные
    "71", "78",                                        # Ташкент городской
})

# Кандидаты на телефонные номера. Структура:
#   [+998 / 998 / 8]?  XX  XXX  XX  XX   (с разделителями или без)
# Разделители: пробел, тире, точка, скобки.
_PHONE_CANDIDATE = re.compile(
    r"(?:\+?\s*9{0,2}\s*8[\s\-\.\(\)]*)?"   # необязательный префикс страны
    r"\(?\s*(\d{2})\s*\)?"                  # код оператора (2 цифры)
    r"[\s\-\.\)]*"
    r"(\d{3})"                              # 3 цифры
    r"[\s\-\.]*"
    r"(\d{2})"                              # 2 цифры
    r"[\s\-\.]*"
    r"(\d{2})"                              # 2 цифры
)


def extract_phones(text: str) -> List[str]:
    """
    Извлекает узбекские номера телефонов из текста.

    Возвращает отсортированный список нормализованных номеров вида
    ['+998901234567', '+998712345678'] — без пробелов и разделителей.

    Поддерживает форматы:
        +998 90 123 45 67
        +998(90)123-45-67
        998901234567
        8 998 90 123 45 67     (с лидирующей 8)
        90 123 45 67           (без префикса, но с известным кодом оператора)

    Отбрасывает:
        2026-04-28      (даты)
        ID:11355        (айдишники)
        100 000 сум     (цены без structure 2-3-2-2)
    """
    if not text:
        return []

    found: set[str] = set()

    for m in _PHONE_CANDIDATE.finditer(text):
        # Берём весь матч и достаём только цифры
        raw = m.group(0)
        digits = re.sub(r"\D", "", raw)

        # Нормализация: целевой формат — 12 цифр, начинающихся с 998
        if len(digits) == 13 and digits.startswith("8998"):
            digits = digits[1:]  # уберём лидирующую 8

        if len(digits) == 12 and digits.startswith("998"):
            op_code = digits[3:5]
            if op_code in UZ_OPERATOR_CODES:
                found.add("+" + digits)
                continue

        if len(digits) == 9:
            op_code = digits[:2]
            if op_code in UZ_OPERATOR_CODES:
                found.add("+998" + digits)

    return sorted(found)


# ============================================================
# REAL-ESTATE PARSING
# ============================================================

# Тройная нотация: комнаты/этаж/этажность (например, 2/5/9)
_TRIPLE_RFE = re.compile(r"\b(\d+)\s*/\s*(\d+)\s*/\s*(\d+)\b")

# Комнаты (несколько вариантов написания, RU + UZ)
_ROOMS_PATTERNS = [
    re.compile(r"кол[–\-]?во\s+комнат\s*[:\-–]?\s*(\d+)", re.I),
    re.compile(r"комнат(?:[ыа])?\s*[:\-–]?\s*(\d+)", re.I),
    re.compile(r"(\d+)\s*[–\-]?\s*комн", re.I),
    re.compile(r"(\d+)\s*хонали", re.I),
    re.compile(r"(\d+)\s*xonali", re.I),
    re.compile(r"(\d+)\s*xona\b", re.I),
]

# Этаж
_FLOOR_PATTERNS = [
    re.compile(r"этаж\s*[:\-–]?\s*(\d+)\s*(?![\d/])", re.I),
    re.compile(r"(\d+)\s*[–\-]?\s*этаж(?!н)", re.I),
    re.compile(r"qavat\s*[:\-–]?\s*(\d+)", re.I),
]

# Этажность дома
_TOTAL_FLOORS_PATTERNS = [
    re.compile(r"этажность\s*[:\-–]?\s*(\d+)", re.I),
    re.compile(r"этажей\s+в\s+доме\s*[:\-–]?\s*(\d+)", re.I),
    re.compile(r"(\d+)\s*[–\-]?\s*этажн", re.I),
]

# Площадь м²
_AREA_PATTERNS = [
    re.compile(r"площад[ьия]\s*[:\-–]?\s*(\d+(?:[.,]\d+)?)\s*(?:кв\.?\s*м|м[²2]?)", re.I),
    re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:кв\.?\s*м|м[²2])", re.I),
    re.compile(r"площад[ьия][–\-]?\s*(\d+)", re.I),
]

# Цена + валюта. Возвращаем ВСЕ матчи, чтобы потом приоритизировать USD.
_PRICE_USD_PATTERNS = [
    re.compile(r"(\d[\d\s\u00a0]*\d|\d)\s*\$", re.I),
    re.compile(r"(\d[\d\s\u00a0]*\d|\d)\s*(?:у\.\s*е\.?|у\.е|y\.e\.?|у\.?е\.?|usd|долл)", re.I),
]
_PRICE_UZS_PATTERNS = [
    re.compile(r"(\d[\d\s\u00a0]*\d)\s*(?:сум|so'm|сўм|sum|uzs)", re.I),
]
_PRICE_LABEL_PATTERN = re.compile(
    r"(?:цена|narx)\s*[:\-–]?\s*(\d[\d\s\u00a0]*\d|\d)\s*(\$|у\.?е\.?|y\.?e\.?|долл|сум|sum|so'm|сўм)?",
    re.I,
)

# Депозит
_DEPOSIT_PATTERNS = [
    re.compile(r"депозит\s*[:\-–]?\s*(\d[\d\s\u00a0]*)", re.I),
    re.compile(r"залог\s*[:\-–]?\s*(\d[\d\s\u00a0]*)", re.I),
    re.compile(r"предоплат[аы]?\s*[:\-–]?\s*(\d[\d\s\u00a0]*)", re.I),
]
_NO_DEPOSIT_PATTERN = re.compile(r"без\s+(?:депозит|залог|предоплат)", re.I)

# Район
_DISTRICT_NAMES = [
    "Алмазар", "Бектемир", "Мирабад", "Мирзо.?Улугбек", "Сергели",
    "Шайхантахур", "Юнусабад", "Яккасарай", "Яшнабад", "Чиланзар",
    "Учтепа", "Янгихаёт",
]
_DISTRICT_NAMES_RE = "|".join(_DISTRICT_NAMES)
_DISTRICT_PATTERNS = [
    re.compile(r"#?(" + _DISTRICT_NAMES_RE + r")(?:ский|ий|ская)?(?:\s*район)?", re.I),
    re.compile(r"район\s*[:\-–]?\s*([А-ЯЁа-яё]+?)(?:\s*[,\n◆]|$)", re.I),
]

# Метро
_METRO_PATTERNS = [
    re.compile(r"(?:м\.|метро|metro)\s*[:\-–]?\s*([А-ЯЁа-яёA-Za-z][А-ЯЁа-яёA-Za-z\s]{2,30}?)(?:\s*[,\n◆]|$)", re.I),
    re.compile(r"#метро[_\s]?([А-ЯЁа-яё]+)", re.I),
]

# Срок аренды
_MIN_PERIOD_PATTERN = re.compile(r"(?:мин\.?\s*)?срок\s*[:\-–]?\s*(?:от\s+)?(\d+)\s*мес", re.I)

# Состояние / ремонт
_CONDITION_PATTERNS = [
    re.compile(r"состояние\s*[:\-–]?\s*([А-Яа-яЁё\s]+?)(?:\s*[◆\n,]|$)", re.I),
    re.compile(r"ремонт\s*[:\-–]?\s*([А-Яа-яЁё\s]+?)(?:\s*[◆\n,]|$)", re.I),
    re.compile(r"(евро\s*ремонт|новый\s*ремонт|косметический|без\s*ремонта)", re.I),
]

# Тип дома
_HOUSE_TYPE_PATTERNS = [
    re.compile(r"тип\s*дома\s*[:\-–]?\s*([А-Яа-яЁё\s]+?)(?:\s*[◆\n,]|$)", re.I),
    re.compile(r"(вторичн\w*|новостройка|панельн\w*|кирпичн\w*|монолит\w*)", re.I),
]

# Коммуналка
_UTILITIES_PATTERN = re.compile(
    r"коммунал(?:ка|ьные|ьн)\w*\s*[:\-–]?\s*(включен|отдельно|входит|не\s*входит)",
    re.I,
)

# Удобства — bilingual keyword lists
_AMENITY_KEYWORDS = {
    "furniture":       [r"мебел[ьия]", r"меблирован", r"с\s+мебелью", r"\bmebel\b", r"furnished"],
    "conditioner":     [r"кондиц", r"\bсплит\b", r"konditsioner", r"air\s*condition"],
    "washing_machine": [r"стирал", r"стир\.?\s*маш", r"washing\s*mach"],
    "refrigerator":    [r"холодильник", r"хол-к", r"fridge", r"refrigerator"],
    "internet":        [r"интернет", r"wi[-\s]?fi", r"internet"],
    "parking":         [r"парковка", r"паркинг", r"машиноместо", r"parking", r"\bгараж\b"],
    "balcony":         [r"балкон", r"лоджия", r"\bbalkon\b"],
    "pets_allowed":    [r"можно\s+с\s+животн", r"животные\s+разрешен", r"pets\s+allowed", r"с\s+питомц"],
    "kids_allowed":    [r"можно\s+с\s+детьми", r"дети\s+разрешен", r"семь[ея]\s+с\s+детьми"],
}

# Технические паттерны для очистки описания
_TECHNICAL_CLEAN_PATTERNS = [
    r"#\S+",
    r"[◆◇◈●•▪]\s*",
    r"тип\s*дома\s*[:\-–]?\s*[^\n◆]+",
    r"кол[–\-]?во\s+комнат\s*[:\-–]?\s*\d+",
    r"этаж\s*[:\-–]?\s*\d+",
    r"этажность\s*[:\-–]?\s*\d+",
    r"площад[ьия]\s*[:\-–]?\s*[\d.,]+\s*(?:кв\.?\s*м|м[²2]?)?",
    r"площад[ьия][\-–]?\s*\d+",
    r"цена\s*[:\-–]?\s*[^\n◆]+",
    r"narx\s*[:\-–]?\s*[^\n◆]+",
    r"депозит\s*[:\-–]?\s*[^\n◆]*",
    r"предоплат[аы]?\s*[:\-–]?\s*[^\n◆]*",
    r"залог\s*[:\-–]?\s*[^\n◆]*",
    r"состояние\s*[:\-–]?\s*[^\n◆]+",
    r"комиссионные\s*[^\n◆]*",
    r"комиссия\s*[^\n◆]*",
    r"maklerskiy\s*[^\n◆]*",
    r"ID\s*[:\-–]?\s*\d+",
    r"\d+\s*/\s*\d+\s*/\s*\d+",
    r"(\+?\d[\s\-\(\)]*){7,15}",
    r"t\.me/\S+",
    r"@[A-Za-z0-9_]+",
]


# ============================================================
# HELPERS
# ============================================================

def _normalize_text(text: str) -> str:
    if not text:
        return ""
    t = text.replace("\u00a0", " ").replace("–", "-").replace("—", "-")
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _to_int(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _to_float(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    s = s.replace(",", ".")
    m = re.search(r"\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _match_first(patterns: List[re.Pattern], text: str) -> Optional[re.Match]:
    for pat in patterns:
        m = pat.search(text)
        if m:
            return m
    return None


def _has_kw(text: str, kws: List[str]) -> bool:
    for kw in kws:
        if re.search(kw, text, re.I):
            return True
    return False


# ============================================================
# DETECTION
# ============================================================

_REALTY_KEYWORDS = (
    "квартир", "комнат", "участок", "недвижим",
    "аренда", "сдается", "сдаётся", "сдам", "сниму", "снять", "посуточно",
    "риелтор", "риэлтор", "депозит", "комиссионные",
    "maklerskiy", "narx", "ijara", "xonali", "kvartira",
)

_REALTY_TAG_KEYWORDS = ("аренда", "квартира", "дом", "недвиж", "rent", "flat")


def _detect_is_real_estate(text: str, hashtags: List[str]) -> bool:
    t = text.lower()
    if any(k in t for k in _REALTY_KEYWORDS):
        return True
    tags = " ".join(h.lower() for h in (hashtags or []))
    if any(k in tags for k in _REALTY_TAG_KEYWORDS):
        return True
    if re.search(r"\d+\s*\$|\d+\s*у\.?е\.?", t):
        return True
    return False


def _detect_deal_type(text: str, hashtags: List[str]) -> str:
    t = text.lower()
    tags = " ".join(h.lower() for h in (hashtags or []))

    if any(x in t for x in ("сниму", "ищу квартиру", "ищу дом", "ищу комнату", "нужна квартира")):
        return "wanted_rent"
    if "куплю" in t:
        return "wanted_buy"
    if any(x in t for x in ("посуточно", "сутки", "по суткам", "на сутки", "sutkalik")):
        return "rent_daily"
    if any(x in t for x in ("продам", "продаю", "продажа", "на продажу", "sotiladi", "sotilyapti")):
        return "sale"
    if any(x in t for x in ("аренда", "сдается", "сдаётся", "сдам", "в аренду", "ijara")):
        return "rent_long"
    if "аренда" in tags or "rent" in tags:
        return "rent_long"
    if any(x in t for x in ("депозит", "комиссионные", "maklerskiy", "/мес", "в месяц")):
        return "rent_long"
    return "rent_long"


def _detect_object_type(text: str, hashtags: List[str]) -> str:
    t = text.lower()
    tags = " ".join(h.lower() for h in (hashtags or []))

    if "студи" in t:
        return "studio"
    if re.search(r"\bкомнат[уа]\b|койко[-\s]место", t) and "квартир" not in t:
        return "room"
    if "квартир" in t or "kvartira" in t or "квартира" in tags:
        return "flat"
    if re.search(r"\bэтаж\b|qavat", t):
        return "flat"
    if any(x in t for x in ("частный дом", "коттедж", "hovli")) or "дом" in tags:
        return "house"
    if any(x in t for x in ("участок", "соток", "сотки", "земля", "yer")):
        return "land"
    if any(x in t for x in ("офис", "коммерческ", "торговая площадь", "помещение")):
        return "commercial"
    return "flat"


def _parse_rooms_floor_triple(text: str) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """Парсит '2/5/9' → (комнаты, этаж, этажность). С sanity-чеками."""
    for m in _TRIPLE_RFE.finditer(text):
        try:
            rooms, floor, total = int(m.group(1)), int(m.group(2)), int(m.group(3))
        except (ValueError, IndexError):
            continue
        # Sanity: rooms 1-10, floor ≤ total, total ≤ 60
        if not (1 <= rooms <= 10):
            continue
        if floor > total or total > 60 or floor > 60:
            continue
        return rooms, floor, total
    return None, None, None


def _parse_rooms(text: str) -> Optional[int]:
    m = _match_first(_ROOMS_PATTERNS, text)
    if m:
        n = _to_int(m.group(1))
        if n and 1 <= n <= 10:
            return n
    return None


def _parse_floor(text: str) -> Tuple[Optional[int], Optional[int]]:
    floor = None
    total = None

    fm = _match_first(_FLOOR_PATTERNS, text)
    if fm:
        n = _to_int(fm.group(1))
        if n and 1 <= n <= 60:
            floor = n

    tm = _match_first(_TOTAL_FLOORS_PATTERNS, text)
    if tm:
        n = _to_int(tm.group(1))
        if n and 1 <= n <= 60:
            total = n

    return floor, total


def _parse_area(text: str) -> Optional[float]:
    m = _match_first(_AREA_PATTERNS, text)
    if m:
        v = _to_float(m.group(1))
        if v and 5 <= v <= 5000:
            return v
    return None


def _parse_price(text: str, deal_type: str) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """
    Возвращает (price, currency, period).
    
    При двойной валюте (например '7 300 000 сум (600 у.е.)') ПРИОРИТЕТ USD —
    долларовая цена надёжнее, в сумах часто прыгают цифры на курсе.
    """
    # Сначала ищем все USD-цены
    usd_prices: List[int] = []
    for pat in _PRICE_USD_PATTERNS:
        for m in pat.finditer(text):
            v = _to_int(m.group(1))
            if v and 50 <= v <= 5_000_000:  # разумный диапазон
                usd_prices.append(v)

    # Потом UZS
    uzs_prices: List[int] = []
    for pat in _PRICE_UZS_PATTERNS:
        for m in pat.finditer(text):
            v = _to_int(m.group(1))
            if v and 10_000 <= v <= 100_000_000_000:
                uzs_prices.append(v)

    # И на всякий — паттерн с явной меткой "Цена:"
    label_m = _PRICE_LABEL_PATTERN.search(text)
    if label_m and not usd_prices and not uzs_prices:
        v = _to_int(label_m.group(1))
        cur = (label_m.group(2) or "").lower()
        if v:
            if any(x in cur for x in ("сум", "sum", "so'm", "сўм", "uzs")):
                uzs_prices.append(v)
            else:
                usd_prices.append(v)

    # Приоритет USD
    if usd_prices:
        price, currency = min(usd_prices) if len(usd_prices) > 1 else usd_prices[0], "usd"
        # Если разброс — берём самый частый. Но для простоты берём первый.
        price = usd_prices[0]
    elif uzs_prices:
        price, currency = uzs_prices[0], "uzs"
    else:
        return None, None, None

    # Период
    tl = text.lower()
    if any(x in tl for x in ("в месяц", "/мес", "ежемесячно", "oyiga")):
        period = "month"
    elif any(x in tl for x in ("в сутки", "посуточно", "/сут", "sutkasiga")):
        period = "day"
    elif deal_type == "sale":
        period = "total"
    else:
        period = "month"

    return price, currency, period


def _parse_deposit(text: str) -> Tuple[Optional[int], bool]:
    if _NO_DEPOSIT_PATTERN.search(text):
        return None, True
    for pat in _DEPOSIT_PATTERNS:
        m = pat.search(text)
        if m and m.group(1):
            v = _to_int(m.group(1))
            if v:
                return v, False
    return None, False


def _parse_district(text: str) -> Optional[str]:
    m = _match_first(_DISTRICT_PATTERNS, text)
    if m:
        d = m.group(1).strip()
        d = re.sub(r"[#_]", " ", d).strip()
        return d
    return None


def _parse_metro(text: str) -> Optional[str]:
    m = _match_first(_METRO_PATTERNS, text)
    if m:
        s = m.group(1).strip()
        s = re.sub(r"[#_]", " ", s).strip()
        s = re.sub(r"\s+(рядом|около|близко)$", "", s, flags=re.I)
        if 2 < len(s) < 50:
            return s
    return None


def _parse_min_period(text: str) -> Optional[int]:
    m = _MIN_PERIOD_PATTERN.search(text)
    if m:
        return _to_int(m.group(1))
    return None


def _parse_utilities_included(text: str) -> Optional[bool]:
    m = _UTILITIES_PATTERN.search(text)
    if m:
        v = m.group(1).lower()
        if any(x in v for x in ("включен", "входит")):
            return True
        if any(x in v for x in ("отдельно", "не входит", "не включен")):
            return False
    return None


def _parse_condition(text: str) -> Optional[str]:
    m = _match_first(_CONDITION_PATTERNS, text)
    if m:
        return m.group(1).strip()
    return None


def _parse_house_type(text: str) -> Optional[str]:
    m = _match_first(_HOUSE_TYPE_PATTERNS, text)
    if m:
        return m.group(1).strip()
    return None


def _parse_amenities(text: str) -> Dict[str, bool]:
    return {key: _has_kw(text, kws) for key, kws in _AMENITY_KEYWORDS.items()}


def _clean_description(text: str) -> str:
    """Убирает технические данные, оставляет человекочитаемое описание."""
    if not text:
        return ""

    result = text
    for pat in _TECHNICAL_CLEAN_PATTERNS:
        result = re.sub(pat, " ", result, flags=re.I)

    lines = []
    for line in result.split("\n"):
        line = line.strip()
        if not line or len(line) < 15:
            continue
        if re.match(r"^[\d\s\-+\(\)\.,:;/\\◆◇●•▪]+$", line):
            continue
        lines.append(line)

    result = "\n".join(lines)
    result = re.sub(r"[ \t]+", " ", result)
    result = re.sub(r"\n\s*\n+", "\n", result)
    result = result.strip()

    return result if len(result) >= 20 else ""


# ============================================================
# MAIN ENTRY POINT
# ============================================================

def parse_listing(text: str, hashtags: List[str] = None) -> Dict[str, Any]:
    """
    Главная функция: парсит Telegram-пост и возвращает структурированные данные.

    Args:
        text: сырой текст поста
        hashtags: список хештегов (опционально)

    Returns:
        dict с ключами is_real_estate, deal_type, object_type, rooms, floor, ...
        Если пост не про недвижимость — возвращает {"is_real_estate": False}.
    """
    text = text or ""
    hashtags = hashtags or []

    text_norm = _normalize_text(text)
    if not text_norm:
        return {"is_real_estate": False}

    if not _detect_is_real_estate(text_norm, hashtags):
        return {"is_real_estate": False}

    deal_type = _detect_deal_type(text_norm, hashtags)
    object_type = _detect_object_type(text_norm, hashtags)

    # Сначала пробуем тройной формат 2/5/9
    rooms, floor, total_floors = _parse_rooms_floor_triple(text_norm)
    # Если не нашли — парсим поля по отдельности
    if rooms is None:
        rooms = _parse_rooms(text_norm)
    if floor is None or total_floors is None:
        f, tf = _parse_floor(text_norm)
        floor = floor or f
        total_floors = total_floors or tf

    area = _parse_area(text_norm)
    price, currency, price_period = _parse_price(text_norm, deal_type)
    deposit, no_deposit = _parse_deposit(text_norm)

    district = _parse_district(text_norm)
    metro = _parse_metro(text_norm)

    min_period = _parse_min_period(text_norm)
    utilities_included = _parse_utilities_included(text_norm)

    condition = _parse_condition(text_norm)
    house_type = _parse_house_type(text_norm)

    amenities = _parse_amenities(text_norm)
    phones = extract_phones(text)
    description_clean = _clean_description(text)

    has_commission = any(x in text_norm.lower() for x in (
        "комиссионные", "комиссия", "maklerskiy", "риелтор", "риэлтор", "агент"
    ))

    return {
        "is_real_estate": True,

        "deal_type": deal_type,
        "object_type": object_type,

        "rooms": rooms,
        "floor": floor,
        "total_floors": total_floors,
        "area_m2": area,

        "price": price,
        "currency": currency,
        "price_period": price_period,

        "deposit": deposit,
        "no_deposit": no_deposit,

        "district_raw": district,
        "metro_raw": metro,
        "landmark": None,

        "min_period_months": min_period,
        "utilities_included": utilities_included,
        "has_commission": has_commission,

        "condition": condition,
        "house_type": house_type,

        "has_furniture":       amenities.get("furniture", False),
        "has_conditioner":     amenities.get("conditioner", False),
        "has_washing_machine": amenities.get("washing_machine", False),
        "has_refrigerator":    amenities.get("refrigerator", False),
        "has_internet":        amenities.get("internet", False),
        "has_parking":         amenities.get("parking", False),
        "has_balcony":         amenities.get("balcony", False),
        "pets_allowed":        amenities.get("pets_allowed", False),
        "kids_allowed":        amenities.get("kids_allowed", False),

        "phones": phones,
        "description_clean": description_clean,
    }


# ============================================================
# SELF-TEST
# ============================================================

if __name__ == "__main__":
    # Тесты на extract_phones
    phone_tests = [
        ("+998 90 123 45 67",          ["+998901234567"]),
        ("+998(90)123-45-67",          ["+998901234567"]),
        ("998901234567",               ["+998901234567"]),
        ("8 998 90 123 45 67",         ["+998901234567"]),
        ("Звоните: 90 123 45 67",      ["+998901234567"]),
        ("Тел: 901234567",             ["+998901234567"]),
        ("Тел.: 71 123-45-67",         ["+998711234567"]),
        ("2026-04-28 опубликовано",    []),
        ("ID:11355",                   []),
        ("100 000 сум депозит",        []),
        ("два номера: +998901112233 и 935554455", ["+998901112233", "+998935554455"]),
    ]

    print("=== Тесты extract_phones ===")
    ok_count = 0
    for text, expected in phone_tests:
        result = extract_phones(text)
        status = "✓" if result == expected else "✗"
        if result == expected:
            ok_count += 1
        print(f"  {status} {text!r:50s} → {result}")
        if result != expected:
            print(f"      ожидалось: {expected}")
    print(f"  {ok_count}/{len(phone_tests)} прошло")

    # Тест на полный парсинг
    sample = """#Мирабадский район, Метро Ойбек, Ор-р Точка вкуса
◆ Тип дома: Вторичный фонд
◆ Кол-во комнат: 1
◆ 2/3/5
◆ Площадь: 35 м²
◆ Цена: 650$/мес
◆ Депозит: 1300$
◆ Состояние: Евро ремонт

Светлая уютная квартира в центре города. Рядом метро, магазины, кафе.
Полностью меблирована, есть кондиционер и стиральная машина.

+998 90 123 45 67
@landlord_tashkent"""

    print("\n=== Тест parse_listing ===")
    result = parse_listing(sample, ["#аренда", "#квартира", "#мирабад"])
    for key, value in result.items():
        if value is not None and value is not False and value != "":
            print(f"  {key}: {value}")