# services/parser.py
"""
Улучшенный парсер объявлений недвижимости из Telegram.
Извлекает структурированные данные и чистое описание.
"""
import re
from typing import Optional, List, Dict, Any, Tuple


# ============================================
# REGEX PATTERNS
# ============================================

PHONE_RE = re.compile(r"(?:\+?\d[\s\-\(\)]*){7,15}")

# Формат: комнаты/этаж/этажность (например: 2/5/9)
TRIPLE_ROOMS_FLOORS = re.compile(r"\b(\d+)\s*/\s*(\d+)\s*/\s*(\d+)\b")

# Комнаты
ROOMS_PATTERNS = [
    re.compile(r"кол[–\-]?во\s+комнат\s*[:\-–]?\s*(\d+)", re.I),
    re.compile(r"комнат\s*[:\-–]?\s*(\d+)", re.I),
    re.compile(r"(\d+)\s*[–\-]?\s*комн", re.I),
    re.compile(r"(\d+)\s*хонали", re.I),  # узбекский
    re.compile(r"(\d+)\s*xonali", re.I),
]

# Этаж
FLOOR_PATTERNS = [
    re.compile(r"этаж\s*[:\-–]?\s*(\d+)", re.I),
    re.compile(r"(\d+)\s*[–\-]?\s*этаж(?!н)", re.I),
    re.compile(r"qavat\s*[:\-–]?\s*(\d+)", re.I),
]

# Этажность
TOTAL_FLOORS_PATTERNS = [
    re.compile(r"этажность\s*[:\-–]?\s*(\d+)", re.I),
    re.compile(r"этажей\s+в\s+доме\s*[:\-–]?\s*(\d+)", re.I),
    re.compile(r"(\d+)\s*[–\-]?\s*этажн", re.I),
]

# Площадь
AREA_PATTERNS = [
    re.compile(r"площад[ьия]\s*[:\-–]?\s*(\d+(?:[.,]\d+)?)\s*(?:кв\.?\s*м|м[²2]?)", re.I),
    re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:кв\.?\s*м|м[²2])", re.I),
    re.compile(r"площад[ьия][–\-]?\s*(\d+)", re.I),
]

# Цена
PRICE_PATTERNS = [
    re.compile(r"цена\s*[:\-–]?\s*(\d[\d\s]*\d|\d)\s*(\$|уе|у\.е|y\.e|долл|сум|sum|so'm|сўм)?", re.I),
    re.compile(r"narx\s*[:\-–]?\s*(\d[\d\s]*\d|\d)\s*(\$|уе|у\.е|y\.e|долл|сум|sum|so'm|сўм)?", re.I),
    re.compile(r"(\d[\d\s]*\d|\d)\s*\$\s*(?:/\s*мес|в\s*мес)?", re.I),
    re.compile(r"(\d[\d\s]*\d)\s*(сум|sum|so'm|сўм)", re.I),
]

# Депозит
DEPOSIT_PATTERNS = [
    re.compile(r"(?:без\s+)?депозит[аы]?\s*[:\-–]?\s*(\d[\d\s]*)?", re.I),
    re.compile(r"залог\s*[:\-–]?\s*(\d[\d\s]*)?", re.I),
    re.compile(r"предоплат[аы]?\s*[:\-–]?\s*(\d[\d\s]*)?", re.I),
]

# Район
DISTRICT_PATTERNS = [
    re.compile(r"#?([А-ЯЁA-Z][а-яёa-z]+(?:ский|ий)?)\s*(?:район|р-н)", re.I),
    re.compile(r"район\s*[:\-–]?\s*([А-ЯЁа-яё\s]+?)(?:\s*[,\n◆]|$)", re.I),
    re.compile(r"#(Юнусабад|Чиланзар|Мирабад|Яккасарай|Мирзо.?Улугбек|Сергели|Шайхантахур|Алмазар|Бектемир|Яшнабад|Учтепа)", re.I),
]

# Метро
METRO_PATTERNS = [
    re.compile(r"(?:м\.|метро|metro)\s*[:\-–]?\s*([А-ЯЁа-яёA-Za-z\s]+?)(?:\s*[,\n◆]|$)", re.I),
    re.compile(r"#?метро[_\s]?([А-ЯЁа-яё]+)", re.I),
]

# Ориентир / адрес
LANDMARK_PATTERNS = [
    re.compile(r"(?:ор[–\-]р|ориентир)\s*[:\-–]?\s*(.+?)(?:\s*[◆\n]|$)", re.I),
    re.compile(r"(?:рядом\s+с|возле|около|близко\s+к)\s+(.+?)(?:\s*[,\n◆]|$)", re.I),
]

# Мин. срок аренды
MIN_PERIOD_PATTERNS = [
    re.compile(r"(?:мин\.?\s*)?срок\s*[:\-–]?\s*(?:от\s+)?(\d+)\s*мес", re.I),
    re.compile(r"от\s+(\d+)\s*мес", re.I),
    re.compile(r"(\d+)\s*мес(?:яц)?\s*(?:минимум|мин)", re.I),
]

# Коммуналка
UTILITIES_PATTERNS = [
    re.compile(r"коммунал(?:ка|ьные)?\s*[:\-–]?\s*(включен|отдельно|входит|не\s*входит)", re.I),
    re.compile(r"комм?\.?\s*услуги\s*[:\-–]?\s*(включен|отдельно)", re.I),
]

# Состояние/ремонт
CONDITION_PATTERNS = [
    re.compile(r"состояние\s*[:\-–]?\s*([А-Яа-яЁё\s]+?)(?:\s*[◆\n,]|$)", re.I),
    re.compile(r"ремонт\s*[:\-–]?\s*([А-Яа-яЁё\s]+?)(?:\s*[◆\n,]|$)", re.I),
    re.compile(r"(евро\s*ремонт|новый\s*ремонт|косметический|без\s*ремонта)", re.I),
]

# Тип дома
HOUSE_TYPE_PATTERNS = [
    re.compile(r"тип\s*дома\s*[:\-–]?\s*([А-Яа-яЁё\s]+?)(?:\s*[◆\n,]|$)", re.I),
    re.compile(r"(вторичн|новостройка|панельн|кирпичн|монолит)", re.I),
]

# Удобства
AMENITIES_KEYWORDS = {
    'furniture': [
        r'мебел[ьия]', r'меблирован', r'с\s+мебелью', r'мебель\s+есть',
        r'mebel', r'furnished'
    ],
    'conditioner': [
        r'кондиц', r'сплит', r'konditsioner', r'air\s*condition'
    ],
    'washing_machine': [
        r'стирал', r'стир\.?\s*маш', r'washing'
    ],
    'refrigerator': [
        r'холодильник', r'хол-к', r'fridge', r'refrigerator'
    ],
    'internet': [
        r'интернет', r'wi-?fi', r'wifi', r'internet'
    ],
    'parking': [
        r'парковка', r'паркинг', r'машиноместо', r'parking', r'гараж'
    ],
    'balcony': [
        r'балкон', r'лоджия', r'balkon'
    ],
    'pets_allowed': [
        r'можно\s+с\s+животн', r'животные\s+разрешен', r'pets\s+allowed',
        r'с\s+питомц'
    ],
    'kids_allowed': [
        r'можно\s+с\s+детьми', r'дети\s+разрешен', r'семь[ея]\s+с\s+детьми'
    ],
}

# Технические строки для удаления из описания
TECHNICAL_PATTERNS = [
    r'#\S+',  # хештеги
    r'◆\s*',  # маркеры
    r'[◇◈●•]\s*',
    r'тип\s*дома\s*[:\-–]?\s*[^\n◆]+',
    r'кол[–\-]?во\s+комнат\s*[:\-–]?\s*\d+',
    r'этаж\s*[:\-–]?\s*\d+',
    r'этажность\s*[:\-–]?\s*\d+',
    r'площад[ьия]\s*[:\-–]?\s*[\d.,]+\s*(?:кв\.?\s*м|м[²2]?)?',
    r'площад[ьия][\-–]?\s*\d+',
    r'цена\s*[:\-–]?\s*[^\n◆]+',
    r'narx\s*[:\-–]?\s*[^\n◆]+',
    r'депозит\s*[:\-–]?\s*[^\n◆]*',
    r'предоплат[аы]?\s*[:\-–]?\s*[^\n◆]*',
    r'залог\s*[:\-–]?\s*[^\n◆]*',
    r'состояние\s*[:\-–]?\s*[^\n◆]+',
    r'комиссионные\s*[^\n◆]*',
    r'комиссия\s*[^\n◆]*',
    r'maklerskiy\s*[^\n◆]*',
    r'ID\s*[:\-–]?\s*\d+',
    r'\d+\s*/\s*\d+\s*/\s*\d+',  # формат комнаты/этаж/этажность
    r'(\+?\d[\s\-\(\)]*){7,15}',  # телефоны
    r't\.me/\S+',  # telegram ссылки
    r'@\S+',  # telegram юзернеймы
]


# ============================================
# HELPER FUNCTIONS
# ============================================

def normalize_text(text: str) -> str:
    """Нормализует текст: убирает лишние пробелы, заменяет спецсимволы."""
    if not text:
        return ""
    t = text.replace("\u00a0", " ")  # non-breaking space
    t = t.replace("–", "-").replace("—", "-")
    t = re.sub(r'\s+', ' ', t)
    return t.strip()


def extract_number(text: str) -> Optional[int]:
    """Извлекает число из строки."""
    if not text:
        return None
    digits = re.sub(r'\D', '', text)
    if digits:
        try:
            return int(digits)
        except ValueError:
            pass
    return None


def extract_float(text: str) -> Optional[float]:
    """Извлекает дробное число из строки."""
    if not text:
        return None
    # Заменяем запятую на точку
    text = text.replace(',', '.')
    match = re.search(r'(\d+(?:\.\d+)?)', text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return None


def match_first(patterns: List[re.Pattern], text: str) -> Optional[re.Match]:
    """Возвращает первое совпадение из списка паттернов."""
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match
    return None


def check_keywords(text: str, keywords: List[str]) -> bool:
    """Проверяет наличие ключевых слов в тексте."""
    text_lower = text.lower()
    for kw in keywords:
        if re.search(kw, text_lower, re.I):
            return True
    return False


# ============================================
# PARSING FUNCTIONS
# ============================================

def detect_is_real_estate(text: str, hashtags: List[str]) -> bool:
    """Определяет, является ли пост объявлением о недвижимости."""
    t = text.lower()
    tags = " ".join(h.lower() for h in (hashtags or []))
    
    keywords = [
        "квартира", "квартиру", "кв ", "комнат", "участок", "недвижим",
        "аренда", "сдается", "сдаётся", "сдам", "сниму", "снять",
        "посуточно", "риелтор", "риэлтор", "депозит", "комиссионные",
        "maklerskiy", "narx", "ijara", "xona", "kvartira"
    ]
    
    if any(k in t for k in keywords):
        return True
    
    tag_keywords = ["аренда", "квартира", "дом", "недвиж", "rent", "flat"]
    if any(k in tags for k in tag_keywords):
        return True
    
    # Проверяем наличие цены в долларах
    if re.search(r'\d+\s*\$', t):
        return True
    
    return False


def detect_deal_type(text: str, hashtags: List[str]) -> str:
    """Определяет тип сделки: аренда долгосрочная/посуточная, продажа, поиск."""
    t = text.lower()
    tags = " ".join(h.lower() for h in (hashtags or []))
    
    # Поиск (человек ищет квартиру)
    if any(x in t for x in ["сниму", "ищу квартиру", "ищу дом", "ищу комнату", "нужна квартира"]):
        return "wanted_rent"
    if "куплю" in t:
        return "wanted_buy"
    
    # Посуточная аренда
    if any(x in t for x in ["посуточно", "сутки", "по суткам", "на сутки", "sutkalik"]):
        return "rent_daily"
    
    # Продажа
    if any(x in t for x in ["продам", "продаю", "продажа", "на продажу", "sotiladi"]) or "продажа" in tags:
        return "sale"
    
    # Долгосрочная аренда (по умолчанию для объявлений о сдаче)
    if any(x in t for x in ["аренда", "сдается", "сдаётся", "сдам", "в аренду", "ijara"]) or "аренда" in tags:
        return "rent_long"
    
    # Если есть признаки аренды
    if any(x in t for x in ["депозит", "комиссионные", "maklerskiy", "риелтор", "/мес", "в месяц"]):
        return "rent_long"
    
    return "rent_long"  # по умолчанию


def detect_object_type(text: str, hashtags: List[str]) -> str:
    """Определяет тип объекта: квартира, комната, дом и т.д."""
    t = text.lower()
    tags = " ".join(h.lower() for h in (hashtags or []))
    
    # Студия - проверяем первой
    if any(x in t for x in ["студия", "студию"]):
        return "studio"
    
    # Комната
    if any(x in t for x in ["комната", "комнату", "койко-место"]) and "комнат" not in t.replace("комнату", "").replace("комната", ""):
        return "room"
    
    # Квартира - если есть слово "квартира" или признаки квартиры (комнаты, этаж)
    if any(x in t for x in ["квартир", " кв ", "кв.", "kvartira"]) or "квартира" in tags:
        return "flat"
    
    # Если есть этаж/этажность - скорее всего квартира
    if re.search(r'этаж|qavat', t):
        return "flat"
    
    # Дом
    if any(x in t for x in ["частный дом", "дом ", "дом,", "дом.", "hovli", "коттедж"]) or "дом" in tags:
        return "house"
    
    # Участок
    if any(x in t for x in ["участок", "соток", "сотки", "земля", "yer"]):
        return "land"
    
    # Коммерческая
    if any(x in t for x in ["офис", "коммерческ", "торговая площадь", "помещение"]):
        return "commercial"
    
    # Квартира по умолчанию
    return "flat"


def parse_rooms_floor_triple(text: str) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """Парсит формат комнаты/этаж/этажность."""
    match = TRIPLE_ROOMS_FLOORS.search(text)
    if match:
        try:
            return int(match.group(1)), int(match.group(2)), int(match.group(3))
        except (ValueError, IndexError):
            pass
    return None, None, None


def parse_rooms(text: str) -> Optional[int]:
    """Извлекает количество комнат."""
    match = match_first(ROOMS_PATTERNS, text)
    if match:
        return extract_number(match.group(1))
    return None


def parse_floor(text: str) -> Tuple[Optional[int], Optional[int]]:
    """Извлекает этаж и этажность."""
    floor = None
    total_floors = None
    
    floor_match = match_first(FLOOR_PATTERNS, text)
    if floor_match:
        floor = extract_number(floor_match.group(1))
    
    total_match = match_first(TOTAL_FLOORS_PATTERNS, text)
    if total_match:
        total_floors = extract_number(total_match.group(1))
    
    return floor, total_floors


def parse_area(text: str) -> Optional[float]:
    """Извлекает площадь в м²."""
    match = match_first(AREA_PATTERNS, text)
    if match:
        return extract_float(match.group(1))
    return None


def parse_price(text: str, deal_type: str) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """Извлекает цену, валюту и период."""
    price = None
    currency = None
    period = None
    
    for pattern in PRICE_PATTERNS:
        match = pattern.search(text)
        if match:
            price = extract_number(match.group(1))
            
            # Определяем валюту
            cur_match = match.group(2) if match.lastindex >= 2 else None
            if cur_match:
                cur_lower = cur_match.lower()
                if any(x in cur_lower for x in ["сум", "sum", "so'm", "сўм"]):
                    currency = "uzs"
                else:
                    currency = "usd"
            elif "$" in match.group(0):
                currency = "usd"
            else:
                # Эвристика: большие числа скорее всего в сумах
                if price and price > 10000:
                    currency = "uzs"
                else:
                    currency = "usd"
            
            break
    
    if price is None:
        return None, None, None
    
    # Определяем период
    text_lower = text.lower()
    if any(x in text_lower for x in ["в месяц", "месяц", "ежемесячно", "/мес", "oyiga", "ойига"]):
        period = "month"
    elif any(x in text_lower for x in ["в сутки", "сутки", "посуточно", "/сут", "sutkasiga"]):
        period = "day"
    elif deal_type == "sale":
        period = "total"
    else:
        period = "month"  # по умолчанию для аренды
    
    return price, currency, period


def parse_deposit(text: str) -> Tuple[Optional[int], bool]:
    """Извлекает депозит и флаг его отсутствия."""
    text_lower = text.lower()
    
    # Проверяем "без депозита/залога"
    if re.search(r'без\s+(?:депозит|залог)', text_lower):
        return None, True  # no_deposit = True
    
    for pattern in DEPOSIT_PATTERNS:
        match = pattern.search(text)
        if match and match.group(1):
            deposit = extract_number(match.group(1))
            if deposit:
                return deposit, False
    
    return None, False


def parse_district(text: str) -> Optional[str]:
    """Извлекает район."""
    match = match_first(DISTRICT_PATTERNS, text)
    if match:
        district = match.group(1).strip()
        # Очищаем от лишних символов
        district = re.sub(r'[#_]', ' ', district).strip()
        return district
    return None


def parse_metro(text: str) -> Optional[str]:
    """Извлекает станцию метро."""
    match = match_first(METRO_PATTERNS, text)
    if match:
        metro = match.group(1).strip()
        metro = re.sub(r'[#_]', ' ', metro).strip()
        # Убираем слова типа "рядом", "около" в конце
        metro = re.sub(r'\s+(рядом|около|близко)$', '', metro, flags=re.I)
        return metro
    return None


def parse_landmark(text: str) -> Optional[str]:
    """Извлекает ориентир/адрес."""
    match = match_first(LANDMARK_PATTERNS, text)
    if match:
        return match.group(1).strip()
    return None


def parse_min_period(text: str) -> Optional[int]:
    """Извлекает минимальный срок аренды в месяцах."""
    match = match_first(MIN_PERIOD_PATTERNS, text)
    if match:
        return extract_number(match.group(1))
    return None


def parse_utilities_included(text: str) -> Optional[bool]:
    """Определяет, включена ли коммуналка."""
    match = match_first(UTILITIES_PATTERNS, text)
    if match:
        value = match.group(1).lower()
        if any(x in value for x in ["включен", "входит"]):
            return True
        if any(x in value for x in ["отдельно", "не входит", "не включен"]):
            return False
    return None


def parse_condition(text: str) -> Optional[str]:
    """Извлекает состояние/ремонт."""
    match = match_first(CONDITION_PATTERNS, text)
    if match:
        return match.group(1).strip()
    return None


def parse_house_type(text: str) -> Optional[str]:
    """Извлекает тип дома."""
    match = match_first(HOUSE_TYPE_PATTERNS, text)
    if match:
        return match.group(1).strip()
    return None


def parse_amenities(text: str) -> Dict[str, bool]:
    """Извлекает удобства."""
    amenities = {}
    for key, keywords in AMENITIES_KEYWORDS.items():
        amenities[key] = check_keywords(text, keywords)
    return amenities


def extract_phones(text: str) -> List[str]:
    """Извлекает телефонные номера."""
    if not text:
        return []
    phones = PHONE_RE.findall(text)
    # Нормализуем и убираем дубликаты
    normalized = set()
    for phone in phones:
        clean = re.sub(r'[\s\-\(\)]', '', phone)
        if len(clean) >= 9:  # минимальная длина номера
            normalized.add(clean)
    return list(normalized)


def clean_description(text: str) -> str:
    """
    Создает чистое описание, убирая технические данные.
    Оставляет только человекочитаемый текст.
    """
    if not text:
        return ""
    
    result = text
    
    # Убираем технические паттерны
    for pattern in TECHNICAL_PATTERNS:
        result = re.sub(pattern, ' ', result, flags=re.I)
    
    # Дополнительные паттерны для очистки
    extra_patterns = [
        r'район\s*[,:]?',
        r'метро\s+[А-Яа-яЁё]+\s*',
        r'ор-р\s+[^\n◆,]+',
        r'ориентир\s*[:\-–]?\s*[^\n◆,]+',
        r'площадь\s+кв\.?метр\s*[:\-–]?\s*\d+',
        r'[А-Яа-яЁё]+ский\s+район',
        r'вторичн\w*\s*фонд',
        r'новостройка',
    ]
    for pattern in extra_patterns:
        result = re.sub(pattern, ' ', result, flags=re.I)
    
    # Убираем строки, состоящие только из цифр и знаков препинания
    lines = result.split('\n')
    clean_lines = []
    for line in lines:
        line = line.strip()
        # Пропускаем пустые строки и строки только с цифрами/символами
        if not line:
            continue
        if re.match(r'^[\d\s\-\+\(\)\.,:;/\\◆◇●•]+$', line):
            continue
        # Пропускаем очень короткие строки (менее 15 символов)
        if len(line) < 15:
            continue
        # Пропускаем строки, которые выглядят как технические данные
        if re.match(r'^[А-Яа-яЁё\s]+\s*[:\-–]\s*[\d\w]+$', line):
            continue
        clean_lines.append(line)
    
    result = '\n'.join(clean_lines)
    
    # Финальная очистка
    result = re.sub(r'[ \t]+', ' ', result)  # множественные пробелы
    result = re.sub(r'\n\s*\n+', '\n', result)  # множественные переносы
    result = result.strip()
    
    # Если осталось слишком мало текста, возвращаем пустую строку
    if len(result) < 20:
        return ""
    
    return result


# ============================================
# MAIN PARSING FUNCTION
# ============================================

def parse_real_estate(text: str, hashtags: List[str] = None) -> Dict[str, Any]:
    """
    Главная функция парсинга объявления о недвижимости.
    
    Возвращает словарь со всеми извлеченными данными.
    """
    text = text or ""
    hashtags = hashtags or []
    text_norm = normalize_text(text)
    
    if not text_norm:
        return {"is_real_estate": False}
    
    # Проверяем, является ли это объявлением о недвижимости
    if not detect_is_real_estate(text_norm, hashtags):
        return {"is_real_estate": False}
    
    # Определяем тип сделки и объекта
    deal_type = detect_deal_type(text_norm, hashtags)
    object_type = detect_object_type(text_norm, hashtags)
    
    # Парсим комнаты/этаж/этажность из формата X/X/X
    rooms, floor, total_floors = parse_rooms_floor_triple(text_norm)
    
    # Если не нашли в тройном формате, парсим отдельно
    if rooms is None:
        rooms = parse_rooms(text_norm)
    
    if floor is None or total_floors is None:
        parsed_floor, parsed_total = parse_floor(text_norm)
        if floor is None:
            floor = parsed_floor
        if total_floors is None:
            total_floors = parsed_total
    
    # Площадь
    area = parse_area(text_norm)
    
    # Цена
    price, currency, price_period = parse_price(text_norm, deal_type)
    
    # Депозит
    deposit, no_deposit = parse_deposit(text_norm)
    
    # Локация
    district = parse_district(text_norm)
    metro = parse_metro(text_norm)
    landmark = parse_landmark(text_norm)
    
    # Условия аренды
    min_period = parse_min_period(text_norm)
    utilities_included = parse_utilities_included(text_norm)
    
    # Состояние
    condition = parse_condition(text_norm)
    house_type = parse_house_type(text_norm)
    
    # Удобства
    amenities = parse_amenities(text_norm)
    
    # Телефоны
    phones = extract_phones(text)
    
    # Чистое описание
    description_clean = clean_description(text)
    
    # Комиссия
    has_commission = any(x in text_norm.lower() for x in [
        "комиссионные", "комиссия", "maklerskiy", "риелтор", "риэлтор", "агент"
    ])
    
    return {
        "is_real_estate": True,
        
        # Тип сделки и объекта
        "deal_type": deal_type,
        "object_type": object_type,
        
        # Основные параметры
        "rooms": rooms,
        "floor": floor,
        "total_floors": total_floors,
        "area_m2": area,
        
        # Цена
        "price": price,
        "currency": currency,
        "price_period": price_period,
        
        # Депозит
        "deposit": deposit,
        "no_deposit": no_deposit,
        
        # Локация
        "district_raw": district,
        "metro_raw": metro,
        "landmark": landmark,
        
        # Условия
        "min_period_months": min_period,
        "utilities_included": utilities_included,
        "has_commission": has_commission,
        
        # Состояние
        "condition": condition,
        "house_type": house_type,
        
        # Удобства
        "has_furniture": amenities.get('furniture', False),
        "has_conditioner": amenities.get('conditioner', False),
        "has_washing_machine": amenities.get('washing_machine', False),
        "has_refrigerator": amenities.get('refrigerator', False),
        "has_internet": amenities.get('internet', False),
        "has_parking": amenities.get('parking', False),
        "has_balcony": amenities.get('balcony', False),
        "pets_allowed": amenities.get('pets_allowed', False),
        "kids_allowed": amenities.get('kids_allowed', False),
        
        # Контакты
        "phones": phones,
        
        # Чистое описание
        "description_clean": description_clean,
    }


# ============================================
# TEST
# ============================================

if __name__ == "__main__":
    # Тестовый текст из скриншота
    test_text = """#Мирабадский район,Метро Ойбек Ор-р Точка вкуса ◆ Тип дома:Вторичный фонд ◆ Кол-во комнат: 1
◆ Этаж: 3 ◆ Этажность: 4 ◆ Площадь кв.метр:35 ◆ Цена: 650$|Депозит|Предоплата ◆ Состояние:Новый
ремонт ID:11355 Комиссионные 50% от первого месяца

Светлая уютная квартира в центре города. Рядом метро, магазины, кафе.
Полностью меблирована, есть кондиционер и стиральная машина.
Подходит для семейной пары или одного человека.

+998 90 123 45 67
@landlord_tashkent"""

    result = parse_real_estate(test_text, ["#аренда", "#квартира", "#мирабад"])
    
    print("=" * 60)
    print("РЕЗУЛЬТАТ ПАРСИНГА:")
    print("=" * 60)
    for key, value in result.items():
        if value is not None and value != "" and value != False:
            print(f"{key}: {value}")
    print("=" * 60)