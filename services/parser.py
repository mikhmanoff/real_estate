# parser_v2.py
"""
Парсер объявлений недвижимости из Telegram каналов Ташкента.
Поддерживает русский и узбекский языки, множество форматов.
"""
import re
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field, asdict


# ============================================
# DISTRICTS & METRO DATA
# ============================================

DISTRICTS = {
    # Название в нижнем регистре -> нормализованное название
    "мирзо улугбек": "Мирзо-Улугбекский",
    "мирзо-улугбек": "Мирзо-Улугбекский",
    "мирзо_улугбек": "Мирзо-Улугбекский",
    "мирзоулугбек": "Мирзо-Улугбекский",
    "мирзо улугбекский": "Мирзо-Улугбекский",
    "mirzo ulug'bek": "Мирзо-Улугбекский",
    
    "юнусабад": "Юнусабадский",
    "юнус абад": "Юнусабадский",
    "юнусабадский": "Юнусабадский",
    "yunusabad": "Юнусабадский",
    
    "чиланзар": "Чиланзарский",
    "чиланзарский": "Чиланзарский",
    "chilanzar": "Чиланзарский",
    
    "мирабад": "Мирабадский",
    "мирабадский": "Мирабадский",
    "mirabad": "Мирабадский",
    
    "яккасарай": "Яккасарайский",
    "яккасарайский": "Яккасарайский",
    "yakkasaroy": "Яккасарайский",
    
    "сергели": "Сергелийский",
    "сергелийский": "Сергелийский",
    "sergeli": "Сергелийский",
    
    "шайхантахур": "Шайхантахурский",
    "шайхонтогур": "Шайхантахурский",
    "шайхантахурский": "Шайхантахурский",
    "shayxontohur": "Шайхантахурский",
    
    "алмазар": "Алмазарский",
    "алмазарский": "Алмазарский",
    "olmazar": "Алмазарский",
    
    "бектемир": "Бектемирский",
    "бектемирский": "Бектемирский",
    "bektemir": "Бектемирский",
    
    "яшнабад": "Яшнабадский",
    "яшнобад": "Яшнабадский",
    "яшнабадский": "Яшнабадский",
    "yashnabad": "Яшнабадский",
    
    "учтепа": "Учтепинский",
    "учтепинский": "Учтепинский",
    "uchtepa": "Учтепинский",
    
    "ц-1": "Мирзо-Улугбекский",  # Ц-1 это район в Мирзо-Улугбеке
}

METRO_STATIONS = {
    "минор": "Минор",
    "minor": "Минор",
    "ойбек": "Ойбек",
    "oybek": "Ойбек",
    "пушкин": "Пушкинская",
    "космонавтов": "Космонавтлар",
    "хамид олимжон": "Хамида Олимжона",
    "буюк ипак йули": "Буюк Ипак Йўли",
    "buyuk ipak yo'li": "Буюк Ипак Йўли",
    "милий бог": "Миллий Боғ",
    "milliy bog": "Миллий Боғ",
    "тузель": "Тузел",
    "tuzel": "Тузел",
    "сергели": "Сергели",
    "sergeli": "Сергели",
    "чкалов": "Чкалов",
}

LANDMARKS = [
    "it park", "ит парк",
    "tata", "тата",
    "мегапланет", "megaplanet",
    "hi-tech", "хай-тек",
    "паркентский", "parkent",
    "ассалом сохил", "assalom sohil",
    "akay city", "акай сити",
    "imperial club", "империал клуб",
    "mirabad avenue",
    "prestige gardens",
    "solaris", "солярис",
]


# ============================================
# REGEX PATTERNS
# ============================================

# Телефоны - улучшенный паттерн
PHONE_PATTERNS = [
    re.compile(r'\+998[\s\-]?\d{2}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}'),
    re.compile(r'\+998\d{9}'),
    re.compile(r'998\d{9}'),
    re.compile(r'\d{2}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}'),  # 90 123 45 67
]

# Формат комнаты/этаж/этажность: 1/4/4, 2/3/9
TRIPLE_FORMAT = re.compile(r'[⚫🟠🔴\s]*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)[⚫🟠🔴\s]*')

# Комнаты - разные форматы
ROOMS_PATTERNS = [
    # "Комнат: 2", "Комнат-2", "🏡 Комнат: 1"
    re.compile(r'(?:🏡\s*)?комнат[аы]?\s*[:\-–]\s*(\d+)', re.I),
    # "Кол.Комнат:2", "Кол-во комнат: 1"
    re.compile(r'кол[\.\-]?\s*(?:во\s+)?комнат\s*[:\-–]\s*(\d+)', re.I),
    # "2 комнатная", "1-комнатная"
    re.compile(r'(\d+)\s*[–\-]?\s*комнат(?:ная|ка)', re.I),
    # Узбекский: "Xonalar soni: 2", "2 XONA"
    re.compile(r'xonalar?\s*(?:soni)?\s*[:\-–]?\s*(\d+)', re.I),
    re.compile(r'(\d+)\s*xona', re.I),
    # "🔹Комнат: 2", "🔸Комнат: 2"
    re.compile(r'[🔹🔸💮]\s*комнат[аы]?\s*[:\-–]?\s*(\d+)', re.I),
]

# Этаж
FLOOR_PATTERNS = [
    re.compile(r'(?:🔼|♦️|🔸|🔹|💮)?\s*этаж\s*[:\-–]?\s*(\d+)', re.I),
    re.compile(r'(\d+)\s*этаж(?!н)', re.I),
    re.compile(r'qavat\s*[:\-–]?\s*(\d+)', re.I),
]

# Этажность
TOTAL_FLOORS_PATTERNS = [
    re.compile(r'(?:⏫|🔸|🔹|💮)?\s*этажност[ьи]\s*[:\-–]?\s*(\d+)', re.I),
    re.compile(r'этажей\s*(?:в\s*доме)?\s*[:\-–]?\s*(\d+)', re.I),
    re.compile(r'(\d+)\s*[–\-]?\s*этажн(?:ый|ая|ое|ость)', re.I),
]

# Площадь
AREA_PATTERNS = [
    re.compile(r'(?:📐|🔎)?\s*(?:общая\s+)?площад[ьия]\s*[:\-–]?\s*(\d+(?:[.,]\d+)?)\s*(?:кв\.?\s*м(?:етр)?|м[²2]?)?', re.I),
    re.compile(r'(\d+(?:[.,]\d+)?)\s*(?:кв\.?\s*м|м[²2])', re.I),
    re.compile(r'площад[ьия]\s*(\d+)\s*кв', re.I),
]

# Цена - много форматов
PRICE_PATTERNS = [
    # "Цена: 700", "💸 Цена: 600", "Цена -700"
    re.compile(r'(?:💸|💰)?\s*(?:цена|narx)\s*[:\-–]?\s*(\d[\d\s]*)\s*(\$|y\.?e\.?|уе|долл|сум)?', re.I),
    # "700$", "600 $", "500y.e"
    re.compile(r'(\d{3,})\s*(\$|y\.?e\.?|уе|долл)', re.I),
    # "350$+300$ Депозит" - берём первую цену
    re.compile(r'(\d{3,})\s*\$\s*\+', re.I),
    # Узбекский "Narx: 350 $"
    re.compile(r'narx\s*[:\-–]?\s*(\d+)\s*(\$|so\'?m|сум)?', re.I),
]

# Депозит
DEPOSIT_PATTERNS = [
    # "+300$ Депозит", "Депозит 300$"
    re.compile(r'\+\s*(\d+)\s*\$?\s*депозит', re.I),
    re.compile(r'депозит\s*[:\-–]?\s*(\d+)\s*\$?', re.I),
    # "Депозит 50$", "Депозит: 250$"
    re.compile(r'депозит\s+(\d+)', re.I),
    # "|Депозит 250$|"
    re.compile(r'\|депозит\s*(\d+)', re.I),
]

# Проверка "без депозита"
NO_DEPOSIT_PATTERN = re.compile(r'без\s+депозит', re.I)

# Район - в хештегах и тексте
DISTRICT_PATTERNS = [
    # Хештеги: #Мирзо_Улугбек, #Чиланзарский
    re.compile(r'#([А-ЯЁа-яё_\-]+(?:ский|ий)?)\s*(?:район)?', re.I),
    # "📍 Район: Мирзо Улугбекский"
    re.compile(r'(?:📍)?\s*район\s*[:\-–]?\s*([А-ЯЁа-яё\s\-]+?)(?:\s*[,\n📍🎯]|$)', re.I),
    # "Мирзо-Улугбекский район"
    re.compile(r'([А-ЯЁа-яё\-]+(?:ский|ий))\s+район', re.I),
    # Узбекский: "MIRZO ULUG'BEK TUMANI"
    re.compile(r"([A-Za-z'\s]+)\s+tumani", re.I),
]

# Адрес/Ориентир
ADDRESS_PATTERNS = [
    # "🎯 Адрес: ЖК Узмахал"
    re.compile(r'(?:🎯|⛳️)?\s*(?:адрес|manzil)\s*[:\-–]?\s*(.+?)(?:\n|$)', re.I),
    # "Ор-р Точка вкуса", "Ориентир: Метро минор"
    re.compile(r'(?:ор[–\-]р|ориентир|mo\'ljal)\s*[:\-–]?\s*(.+?)(?:\n|$)', re.I),
]

# Метро
METRO_PATTERNS = [
    re.compile(r'метро\s+([А-ЯЁа-яёA-Za-z\s\']+?)(?:\s*[,\n🚊📍]|$)', re.I),
    re.compile(r'metro\s+([A-Za-z\s\']+?)(?:\s*[,\n🚊]|$)', re.I),
]

# ЖК (жилой комплекс)
JK_PATTERN = re.compile(r'(?:жк|jk)\s*["\']?([А-ЯЁа-яёA-Za-z\s\-\']+)["\']?', re.I)

# Комиссия
COMMISSION_PATTERNS = [
    re.compile(r'комисс?ионн?ы?е?\s*(\d+)?\s*%?', re.I),
    re.compile(r'maklerskiy\s*(\d+)?\s*%?', re.I),
    re.compile(r'риелтор\s*услуги?\s*(\d+)?\s*%?', re.I),
    re.compile(r'\((\d+)\s*%\s*\)', re.I),  # (50%)
]

# Без комиссии/маклера
NO_COMMISSION_PATTERNS = [
    re.compile(r'без\s+(?:маклер|комисс|посредник)', re.I),
    re.compile(r'bezmakler', re.I),
    re.compile(r'не\s+для\s+риелтор', re.I),
]

# Состояние/ремонт
CONDITION_PATTERNS = [
    re.compile(r'(?:🏷|🔷)?\s*состояние\s*[:\-–]?\s*([А-ЯЁа-яё\s]+?)(?:\s*[,\n🔹💮ID]|$)', re.I),
    re.compile(r'(евро\s*ремонт|новый\s*ремонт|хороший\s*ремонт|классический\s*ремонт)', re.I),
    re.compile(r'evro\s*ta\'?mir', re.I),
]

# Тип дома
HOUSE_TYPE_PATTERNS = [
    re.compile(r'тип\s*дома\s*[:\-–]?\s*([А-ЯЁа-яё\s]+?)(?:\s*[,\n🔹]|$)', re.I),
    re.compile(r'(новостройка|вторичн\w*(?:\s*фонд)?)', re.I),
    re.compile(r'(?:☑️)?\s*(новостройка|вторичн\w*)', re.I),
]

# Удобства - ключевые слова
AMENITIES = {
    'has_furniture': [
        r'мебел[ьия]', r'меблирован', r'с\s+мебелью',
        r'mebel', r'диван', r'кровать', r'divan'
    ],
    'has_conditioner': [
        r'кондиц', r'сплит', r'konditsioner', r'konditsoner'
    ],
    'has_washing_machine': [
        r'стирал', r'стир\.?\s*маш', r'кирмошина', r'kirmoshina'
    ],
    'has_refrigerator': [
        r'холодильник', r'muzlatgich'
    ],
    'has_internet': [
        r'интернет', r'wi-?fi', r'wifi'
    ],
    'has_tv': [
        r'телевизор', r'televizor', r'тв'
    ],
    'has_balcony': [
        r'балкон', r'лоджия', r'balkon'
    ],
}

# Кому сдаётся
TENANT_PATTERNS = {
    'family': [r'семь[яе]', r'oila', r'загс'],
    'girls': [r'девушк', r'qizlar'],
    'guys': [r'парн', r'болларга', r'bollar'],
    'single': [r'одиноч', r'один\s+парень', r'один\s+человек'],
}


# ============================================
# HELPER FUNCTIONS
# ============================================

def normalize_text(text: str) -> str:
    """Нормализует текст для парсинга."""
    if not text:
        return ""
    # Заменяем разные виды тире и пробелов
    t = text.replace('\u00a0', ' ')  # non-breaking space
    t = t.replace('–', '-').replace('—', '-')
    t = re.sub(r'\s+', ' ', t)
    return t.strip()


def extract_number(text: str) -> Optional[int]:
    """Извлекает число из строки."""
    if not text:
        return None
    # Убираем пробелы внутри числа
    digits = re.sub(r'[\s\-]', '', text)
    digits = re.sub(r'\D', '', digits)
    if digits:
        try:
            return int(digits)
        except ValueError:
            pass
    return None


def extract_float(text: str) -> Optional[float]:
    """Извлекает дробное число."""
    if not text:
        return None
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


def check_any_keyword(text: str, keywords: List[str]) -> bool:
    """Проверяет наличие любого ключевого слова."""
    text_lower = text.lower()
    for kw in keywords:
        if re.search(kw, text_lower, re.I):
            return True
    return False


def normalize_district(raw: str) -> Optional[str]:
    """Нормализует название района."""
    if not raw:
        return None
    # Убираем лишние символы
    clean = re.sub(r'[#_\-]', ' ', raw.lower()).strip()
    clean = re.sub(r'\s+', ' ', clean)
    
    # Ищем в словаре
    for key, value in DISTRICTS.items():
        if key in clean or clean in key:
            return value
    
    # Возвращаем очищенное если не нашли
    if len(clean) > 3:
        return clean.title()
    return None


def normalize_metro(raw: str) -> Optional[str]:
    """Нормализует название станции метро."""
    if not raw:
        return None
    clean = raw.lower().strip()
    
    for key, value in METRO_STATIONS.items():
        if key in clean:
            return value
    
    if len(clean) > 2:
        return clean.title()
    return None


# ============================================
# MAIN PARSING FUNCTIONS  
# ============================================

def extract_phones(text: str) -> List[str]:
    """Извлекает телефонные номера."""
    if not text:
        return []
    
    phones = set()
    
    # Ищем полные номера +998XXXXXXXXX
    full_pattern = re.compile(r'\+?998\s*[\-\(\)]?\s*(\d{2})\s*[\-\(\)]?\s*(\d{3})\s*[\-\(\)]?\s*(\d{2})\s*[\-\(\)]?\s*(\d{2})')
    for match in full_pattern.finditer(text):
        phone = '+998' + ''.join(match.groups())
        phones.add(phone)
    
    # Ищем короткие номера (9 цифр без кода страны)
    if not phones:
        short_pattern = re.compile(r'\b(\d{2})[\s\-]?(\d{3})[\s\-]?(\d{2})[\s\-]?(\d{2})\b')
        for match in short_pattern.finditer(text):
            digits = ''.join(match.groups())
            # Проверяем что это похоже на узбекский номер
            if digits[0] in '789':
                phones.add('+998' + digits)
    
    return list(phones)


def parse_triple_format(text: str) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """Парсит формат X/X/X (комнаты/этаж/этажность)."""
    match = TRIPLE_FORMAT.search(text)
    if match:
        try:
            rooms = int(match.group(1))
            floor = int(match.group(2))
            total_floors = int(match.group(3))
            # Валидация
            if 0 < rooms <= 10 and 0 < floor <= 50 and 0 < total_floors <= 50:
                if floor <= total_floors:
                    return rooms, floor, total_floors
        except (ValueError, IndexError):
            pass
    return None, None, None


def parse_rooms(text: str) -> Optional[int]:
    """Извлекает количество комнат."""
    match = match_first(ROOMS_PATTERNS, text)
    if match:
        rooms = extract_number(match.group(1))
        if rooms and 0 < rooms <= 10:
            return rooms
    return None


def parse_floor(text: str) -> Tuple[Optional[int], Optional[int]]:
    """Извлекает этаж и этажность."""
    floor = None
    total_floors = None
    
    # Ищем этаж
    match = match_first(FLOOR_PATTERNS, text)
    if match:
        floor = extract_number(match.group(1))
        if floor and floor > 50:
            floor = None
    
    # Ищем этажность
    match = match_first(TOTAL_FLOORS_PATTERNS, text)
    if match:
        total_floors = extract_number(match.group(1))
        if total_floors and total_floors > 50:
            total_floors = None
    
    return floor, total_floors


def parse_area(text: str) -> Optional[float]:
    """Извлекает площадь."""
    match = match_first(AREA_PATTERNS, text)
    if match:
        area = extract_float(match.group(1))
        if area and 5 < area < 1000:  # разумные пределы
            return area
    return None


def parse_price(text: str) -> Tuple[Optional[int], Optional[str]]:
    """Извлекает цену и валюту."""
    for pattern in PRICE_PATTERNS:
        match = pattern.search(text)
        if match:
            price = extract_number(match.group(1))
            if not price:
                continue
            
            # Определяем валюту
            currency = "usd"  # по умолчанию
            if match.lastindex >= 2 and match.group(2):
                cur_raw = match.group(2).lower()
                if any(x in cur_raw for x in ['сум', 'sum', "so'm"]):
                    currency = "uzs"
            
            # Эвристика: большие числа скорее всего в сумах
            if price > 50000:
                currency = "uzs"
            
            # Валидация цены
            if currency == "usd" and 50 <= price <= 10000:
                return price, currency
            elif currency == "uzs" and price >= 100000:
                return price, currency
    
    return None, None


def parse_deposit(text: str) -> Tuple[Optional[int], bool]:
    """Извлекает депозит и проверяет 'без депозита'."""
    # Проверяем "без депозита"
    if NO_DEPOSIT_PATTERN.search(text):
        return None, True
    
    # Ищем сумму депозита
    for pattern in DEPOSIT_PATTERNS:
        match = pattern.search(text)
        if match:
            deposit = extract_number(match.group(1))
            if deposit and 10 <= deposit <= 10000:
                return deposit, False
    
    # Проверяем наличие слова "депозит" без суммы
    if re.search(r'депозит|deposit', text, re.I):
        return None, False  # депозит есть, но сумма не указана
    
    return None, False


def parse_district(text: str, hashtags: List[str] = None) -> Optional[str]:
    """Извлекает и нормализует район."""
    # Сначала ищем явное упоминание района в тексте
    # "Мирабадский район", "Мирзо-Улугбекский район"
    district_mention = re.search(r'([А-ЯЁа-яё\-]+(?:ский|ий))\s+район', text, re.I)
    if district_mention:
        district = normalize_district(district_mention.group(1))
        if district:
            return district
    
    # Ищем формат "📍 Район: Мирзо Улугбекский"
    district_label = re.search(r'район\s*[:\-–]\s*([А-ЯЁа-яё\s\-]+?)(?:\s*[,\n📍🎯🏢]|$)', text, re.I)
    if district_label:
        district = normalize_district(district_label.group(1))
        if district:
            return district
    
    # Проверяем хештеги (но не все подряд, а только похожие на районы)
    for tag in (hashtags or []):
        tag_lower = tag.lower().replace('_', ' ')
        # Пропускаем явно не районы
        if any(x in tag_lower for x in ['комнат', 'долл', 'oila', 'qiz', 'boll']):
            continue
        district = normalize_district(tag)
        if district:
            return district
    
    # Ищем узбекский формат "TUMANI"
    uz_district = re.search(r"([A-Za-z'\s]+)\s+tumani", text, re.I)
    if uz_district:
        district = normalize_district(uz_district.group(1))
        if district:
            return district
    
    return None


def parse_metro(text: str) -> Optional[str]:
    """Извлекает станцию метро."""
    match = match_first(METRO_PATTERNS, text)
    if match:
        return normalize_metro(match.group(1))
    return None


def parse_address(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Извлекает адрес и ориентир."""
    address = None
    landmark = None
    
    # Ищем адрес
    for pattern in ADDRESS_PATTERNS:
        match = pattern.search(text)
        if match:
            raw = match.group(1).strip()
            if len(raw) > 3:
                if 'ориентир' in pattern.pattern.lower() or 'mo\'ljal' in pattern.pattern.lower():
                    landmark = raw
                else:
                    address = raw
                break
    
    # Ищем ЖК
    jk_match = JK_PATTERN.search(text)
    if jk_match:
        jk_name = jk_match.group(1).strip()
        if address:
            address = f"ЖК {jk_name}, {address}"
        else:
            address = f"ЖК {jk_name}"
    
    return address, landmark


def parse_commission(text: str) -> Tuple[bool, Optional[int]]:
    """Определяет наличие и размер комиссии."""
    # Проверяем "без комиссии"
    for pattern in NO_COMMISSION_PATTERNS:
        if pattern.search(text):
            return False, None
    
    # Ищем размер комиссии
    for pattern in COMMISSION_PATTERNS:
        match = pattern.search(text)
        if match:
            pct = None
            if match.lastindex and match.lastindex >= 1 and match.group(1):
                pct = extract_number(match.group(1))
            return True, pct
    
    return False, None


def parse_condition(text: str) -> Optional[str]:
    """Извлекает состояние/ремонт."""
    for pattern in CONDITION_PATTERNS:
        match = pattern.search(text)
        if match:
            cond = match.group(1).strip() if match.lastindex >= 1 else match.group(0).strip()
            if len(cond) > 2:
                return cond.lower().replace('  ', ' ')
    return None


def parse_house_type(text: str) -> Optional[str]:
    """Определяет тип дома."""
    for pattern in HOUSE_TYPE_PATTERNS:
        match = pattern.search(text)
        if match:
            raw = match.group(1).lower().strip() if match.lastindex >= 1 else match.group(0).lower()
            if 'новостройка' in raw or 'novostroy' in raw:
                return "новостройка"
            if 'вторичн' in raw:
                return "вторичка"
    return None


def parse_amenities(text: str) -> Dict[str, bool]:
    """Извлекает удобства."""
    result = {}
    for key, keywords in AMENITIES.items():
        result[key] = check_any_keyword(text, keywords)
    return result


def parse_tenant_type(text: str) -> List[str]:
    """Определяет кому сдаётся."""
    result = []
    for tenant_type, keywords in TENANT_PATTERNS.items():
        if check_any_keyword(text, keywords):
            result.append(tenant_type)
    return result


def detect_deal_type(text: str, hashtags: List[str] = None) -> str:
    """Определяет тип сделки."""
    t = text.lower()
    tags = ' '.join(h.lower() for h in (hashtags or []))
    
    # Поиск жилья
    if any(x in t for x in ['сниму', 'ищу квартиру', 'ищу комнату', 'нужна квартира']):
        return 'wanted_rent'
    if 'куплю' in t:
        return 'wanted_buy'
    
    # Посуточно
    if any(x in t for x in ['посуточно', 'сутки', 'sutka']):
        return 'rent_daily'
    
    # Продажа
    if any(x in t for x in ['продам', 'продаю', 'продажа', 'sotiladi']) or 'продажа' in tags:
        return 'sale'
    
    # Аренда (по умолчанию)
    return 'rent_long'


def detect_object_type(text: str, rooms: Optional[int] = None) -> str:
    """Определяет тип объекта."""
    t = text.lower()
    
    if any(x in t for x in ['студия', 'studio']):
        return 'studio'
    
    # Проверяем "комната" только если это не "X-комнатная квартира"
    if re.search(r'\bкомнат[уа]\b', t) and not re.search(r'\d+\s*[–\-]?\s*комнат', t):
        return 'room'
    
    # Дом/коттедж - но не "тип дома", "этажей в доме"
    if re.search(r'(?:котедж|коттедж|частный\s+дом|hovli)', t):
        return 'house'
    if re.search(r'\bдом\b', t) and not re.search(r'тип\s+дома|в\s+доме|этажей\s+в\s+доме', t):
        return 'house'
    
    if any(x in t for x in ['участок', 'соток', 'yer']):
        return 'land'
    
    if any(x in t for x in ['офис', 'коммерч']):
        return 'commercial'
    
    return 'flat'


def detect_is_real_estate(text: str, hashtags: List[str] = None) -> bool:
    """Определяет, является ли это объявлением о недвижимости."""
    t = text.lower()
    tags = ' '.join(h.lower() for h in (hashtags or []))
    
    # Ключевые слова недвижимости
    keywords = [
        'квартир', 'комнат', 'аренда', 'сдается', 'сдаётся', 'сдам',
        'этаж', 'депозит', 'комисс', 'риелтор', 'маклер',
        'xona', 'ijara', 'kvartira', 'narx', 'maklerskiy'
    ]
    
    if any(k in t for k in keywords):
        return True
    
    if any(k in tags for k in ['аренда', 'квартира', 'rent']):
        return True
    
    # Формат X/X/X
    if TRIPLE_FORMAT.search(text):
        return True
    
    # Цена в долларах
    if re.search(r'\d{2,4}\s*\$', t):
        return True
    
    return False


def extract_hashtags(text: str) -> List[str]:
    """Извлекает хештеги из текста."""
    return re.findall(r'#([А-ЯЁа-яёA-Za-z0-9_]+)', text)


def clean_description(text: str) -> str:
    """Создаёт чистое описание без технических данных."""
    if not text:
        return ""
    
    result = text
    
    # Убираем технические паттерны
    patterns_to_remove = [
        r'#\S+',  # хештеги
        r'[⚫🟠🔴🔹🔸💮📍🎯🏡💸📐⏫🔼♦️📣☎️✏️📲🔑✉️👉🪧☑️🔎🏷💵📱🔵📝🔗✅🎖💰⛳️👤🔥💎🏢]',  # эмодзи
        r'\+998[\d\s\-]+',  # телефоны
        r't\.me/\S+',  # ссылки telegram
        r'https?://\S+',  # ссылки
        r'@\S+',  # юзернеймы
        r'ID\s*[:\-]?\s*\d+',  # ID объявления
        r'\d+/\d+/\d+',  # формат X/X/X
        r'комисс\w*\s*\d*\s*%?[^\n]*',  # информация о комиссии
        r'maklerskiy[^\n]*',
        r'риелтор[^\n]*',
    ]
    
    for pattern in patterns_to_remove:
        result = re.sub(pattern, ' ', result, flags=re.I)
    
    # Убираем строки с техническими данными
    lines = result.split('\n')
    clean_lines = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Пропускаем строки только с цифрами/символами
        if re.match(r'^[\d\s\-\+\(\)\.,:;/\\]+$', line):
            continue
        # Пропускаем короткие строки
        if len(line) < 10:
            continue
        # Пропускаем технические строки
        if re.match(r'^[А-Яа-яЁё\s]+\s*[:\-–]\s*\d+', line):
            continue
        clean_lines.append(line)
    
    result = ' '.join(clean_lines)
    result = re.sub(r'\s+', ' ', result).strip()
    
    return result if len(result) >= 20 else ""


# ============================================
# MAIN PARSER FUNCTION
# ============================================

def parse_listing(text: str, hashtags: List[str] = None) -> Dict[str, Any]:
    """
    Главная функция парсинга объявления.
    
    Args:
        text: Сырой текст объявления
        hashtags: Список хештегов (опционально)
    
    Returns:
        Словарь с распарсенными данными
    """
    if not text:
        return {"is_real_estate": False}
    
    text = text.strip()
    hashtags = hashtags or extract_hashtags(text)
    
    # Проверяем, является ли это объявлением о недвижимости
    if not detect_is_real_estate(text, hashtags):
        return {"is_real_estate": False}
    
    # Парсим формат X/X/X
    rooms, floor, total_floors = parse_triple_format(text)
    
    # Если не нашли в тройном формате, парсим отдельно
    if rooms is None:
        rooms = parse_rooms(text)
    
    if floor is None or total_floors is None:
        f, tf = parse_floor(text)
        if floor is None:
            floor = f
        if total_floors is None:
            total_floors = tf
    
    # Остальные поля
    area = parse_area(text)
    price, currency = parse_price(text)
    deposit, no_deposit = parse_deposit(text)
    district = parse_district(text, hashtags)
    metro = parse_metro(text)
    address, landmark = parse_address(text)
    has_commission, commission_pct = parse_commission(text)
    condition = parse_condition(text)
    house_type = parse_house_type(text)
    amenities = parse_amenities(text)
    tenant_types = parse_tenant_type(text)
    phones = extract_phones(text)
    deal_type = detect_deal_type(text, hashtags)
    object_type = detect_object_type(text, rooms)
    description = clean_description(text)
    
    # Рассчитываем качество парсинга
    parse_score = 0
    if rooms: parse_score += 2
    if floor: parse_score += 1
    if total_floors: parse_score += 1
    if area: parse_score += 1
    if price: parse_score += 3
    if district: parse_score += 2
    if metro: parse_score += 1
    if phones: parse_score += 1
    
    return {
        "is_real_estate": True,
        
        # Тип сделки
        "deal_type": deal_type,
        "object_type": object_type,
        
        # Параметры объекта
        "rooms": rooms,
        "floor": floor,
        "total_floors": total_floors,
        "area_m2": area,
        
        # Цена
        "price": price,
        "currency": currency,
        "price_period": "day" if deal_type == "rent_daily" else "month",
        
        # Депозит
        "deposit": deposit,
        "no_deposit": no_deposit,
        
        # Локация
        "district_raw": district,
        "metro_raw": metro,
        "address_raw": address,
        "landmark": landmark,
        
        # Комиссия
        "has_commission": has_commission,
        "commission_pct": commission_pct,
        
        # Состояние
        "condition": condition,
        "house_type": house_type,
        
        # Удобства
        **{f"has_{k.replace('has_', '')}": v for k, v in amenities.items()},
        
        # Кому сдаётся
        "tenant_types": tenant_types,
        
        # Контакты
        "phones": phones,
        
        # Описание
        "description_clean": description,
        
        # Метаданные
        "parse_score": parse_score,
        "needs_review": parse_score < 5,
    }


# ============================================
# TEST
# ============================================

if __name__ == "__main__":
    test_cases = [
        """⛳️  Manzil-Адрес
МАССИВ ЯЛАНГАЧ ПАРК ЛОКОМАТИВ 

  Xonalar-Комнаты
⚫️🟠  1/4/4  🟠⚫️

👤 Kimga-Кому 
Семья ЗАГС или Одиночка Порядочные

💰 Narx-Цена: 350$+300$ Депозит 

🎖 Maklerskiy-Коммисия  (50%)

☎️ Tel: 903335552

#Мирзо_Улугбек      #2979""",

        """#Чиланзарский район,7-квартал
Ор-р Hi-Tech Мечеть
🔹Тип дома:Вторичный фонд
🔹Кол-во комнат: 2*3
🔹Этаж: 
🔹Этажность: 4
🔹Площадь кв.метр:55
🔹Цена: 600$ 
🔷Состояние:Евроремонт 
ID:12399
Комиссионные 50% от первого месяца""",

        """🟣Мирабадский район
     Новостройка 
     Премиум класса
    ЖК Mirabad avenue

📱Ориентир: Мирабадский рынок

🔸Комнат: 2
🔸Этаж: 6
🔸Этажей в доме: 13
🔸Общая площадь: 55 м² 

Цена: 1000 

🔗@Tasha16 | +998903257308""",

        """IJARAGA KVARTIRA ✅
TARTIBLI INSONLARGA ✅
#OILAGA | #QIZLARGA | #BOLLARGA ✅

Manzil: MIRZO ULUG'BEK TUMANI, QORASUV-6

Xonalar soni: 1 XONA 3/3

Sharoiti: KIRMOSHINA, KONDITSIONER, MUZLATGICH, TELEVIZOR

Narx: 350 $

Tel.: 📞 +998937576775 EGASI ✅

BEZMAKLER ✅""",
    ]
    
    for i, text in enumerate(test_cases, 1):
        print(f"\n{'='*60}")
        print(f"ТЕСТ {i}:")
        print('='*60)
        
        result = parse_listing(text)
        
        for key, value in result.items():
            if value is not None and value != "" and value != False and value != []:
                print(f"  {key}: {value}")