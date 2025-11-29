# services/tg_listener.py
import asyncio, json, re
from telethon import TelegramClient, events, utils
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.types import (
    MessageEntityHashtag, MessageEntityUrl, MessageEntityTextUrl, MessageEntityMention, MessageEntityCashtag,
    KeyboardButtonUrl, KeyboardButtonRow, MessageMediaWebPage
)
from core.registry import ChannelRegistry
from core.utils import env, ensure_dirs
from pathlib import Path
from telethon.tl.types import Channel, Chat, User
from datetime import datetime

PHONE_RE = re.compile(r"(?:\+?\d[\s\-\(\)]*){7,15}")
TRIPLE_ROOMS_FLOORS = re.compile(r"\b(\d+)\s*/\s*(\d+)\s*/\s*(\d+)\b")

ROOMS_LINE_PATTERNS = [
    re.compile(r"кол-во\s+комнат\s*[:\-]\s*(\d+)", re.I),
    re.compile(r"комнат\s*[:\-]\s*(\d+)", re.I),
    re.compile(r"комнат-?\s*(\d+)", re.I),
]

ROOMS_MULTI_PATTERN = re.compile(r"xonalar.*?([\d/]+)", re.I)

FLOOR_PATTERN = re.compile(r"этаж\s*[:\-]\s*(\d+)", re.I)
TOTAL_FLOORS_PATTERNS = [
    re.compile(r"этажность\s*[:\-]\s*(\d+)", re.I),
    re.compile(r"этажей\s+в\s+доме\s*[:\-]\s*(\d+)", re.I),
]

AREA_PATTERNS = [
    re.compile(r"площад[ьия].*?[:\-]\s*(\d+)\s*м", re.I),
    re.compile(r"площад[ьия]-\s*(\d+)", re.I),
]

PRICE_PATTERNS = [
    re.compile(r"цена\s*[:\-]\s*(\d[\d\s]{0,12})\s*(\$|уе|у\.е|y\.e|сум|sum|so'm)?", re.I),
    re.compile(r"(\d[\d\s]{0,12})\s*\$", re.I),
]

DEPOSIT_PATTERN = re.compile(r"депозит\s*[:\-]?\s*(\d[\d\s]{0,12})", re.I)

DISTRICT_PATTERN = re.compile(r"([А-ЯЁA-Z][а-яёa-z]+ский\s+район)", re.I)
METRO_PATTERN = re.compile(r"(?:м\.|метро)\s*([А-ЯЁA-Z][^,\n]+)", re.I)

def normalize_text(text: str) -> str:
    t = text.replace("\u00a0", " ")
    t = re.sub(r"\s+", " ", t)
    return t.strip()

def detect_rooms_floors_triple(text_norm: str):
    """
    Ищем формат 1/2/5 = комнаты/этаж/этажность.
    """
    m = TRIPLE_ROOMS_FLOORS.search(text_norm)
    if not m:
        return None, None, None
    try:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    except:
        return None, None, None
    
def detect_is_real_estate(text_norm: str, hashtags: list[str]) -> bool:
    t = text_norm.lower()
    tags = " ".join(h.lower() for h in (hashtags or []))

    keywords = [
        "квартира", "кв ", "комнат", "участок", "недвижим", "аренда",
        "посуточно", "риелтор", "депозит", "комиссионные", "maklerskiy", "narx-цена"
    ]
    if any(k in t for k in keywords):
        return True
    if any(k in tags for k in ["аренда", "квартира", "дом", "недвиж"]):
        return True
    return False

def detect_deal_type(text_norm: str, hashtags: list[str]) -> str:
    t = text_norm.lower()
    tags = " ".join(h.lower() for h in (hashtags or []))

    if any(x in t for x in ["сниму", "ищу квартиру", "ищу дом", "куплю"]):
        if "куплю" in t:
            return "wanted_buy"
        return "wanted_rent"

    if any(x in t for x in ["посуточно", "сутки", "по суткам"]):
        return "rent_daily"

    if "аренда" in t or "аренда" in tags or "аренда квартир" in t:
        return "rent_long"

    if any(x in t for x in ["продам", "продаю", "продажа", "на продажу"]) or "продажа" in tags:
        return "sale"

    # эвристика: есть депозит или комиссионные → скорее аренда
    if any(x in t for x in ["депозит", "комиссионные", "maklerskiy", "риелтор"]):
        return "rent_long"

    return "unknown"

def detect_object_type(text_norm: str, hashtags: list[str]) -> str:
    t = text_norm.lower()
    tags = " ".join(h.lower() for h in (hashtags or []))

    if "квартира" in t or " кв " in t or "квартира" in tags:
        return "flat"
    if "комната" in t or "комнату" in t:
        return "room"
    if any(x in t for x in ["частный дом", "дом ", "дом,", "дом."]) or "дом" in tags:
        return "house"
    if any(x in t for x in ["участок", "соток", "сотки"]):
        return "land"
    if any(x in t for x in ["офис", "коммерческ", "торговая площадь"]):
        return "office"
    return "flat"  # по каналам про квартиры можно дефолтом flat

def detect_rooms(text_norm: str):
    t = text_norm

    # сначала ищем явные строки типа "Кол-во комнат: 2"
    for p in ROOMS_LINE_PATTERNS:
        m = p.search(t)
        if m:
            try:
                return int(m.group(1)), None
            except:
                pass

    # "Комнат-2"
    m = re.search(r"комнат-?\s*(\d+)", t, re.I)
    if m:
        return int(m.group(1)), None

    # вариант с несколькими комнатами "Xonalar-Комнаты 1/2/5"
    m = ROOMS_MULTI_PATTERN.search(t)
    if m:
        nums = re.findall(r"\d+", m.group(1))
        opts = [int(x) for x in nums]
        return None, opts if opts else None

    return None, None

def detect_floor(text_norm: str):
    floor = None
    total_floors = None

    m = FLOOR_PATTERN.search(text_norm)
    if m:
        floor = int(m.group(1))

    for p in TOTAL_FLOORS_PATTERNS:
        m2 = p.search(text_norm)
        if m2:
            total_floors = int(m2.group(1))
            break

    return floor, total_floors

def detect_area(text_norm: str):
    for p in AREA_PATTERNS:
        m = p.search(text_norm)
        if m:
            try:
                return int(m.group(1))
            except:
                pass
    return None

def detect_price_and_period(text_norm: str, deal_type: str):
    t = text_norm
    price = None
    currency = None
    period = None

    for p in PRICE_PATTERNS:
        m = p.search(t)
        if not m:
            continue

        raw = m.group(1)
        cur = (m.group(2) or "").lower()

        digits = int(re.sub(r"\D", "", raw))

        if "сум" in cur or "sum" in cur or "so'm" in cur:
            currency = "uzs"
        else:
            currency = "usd"

        price = digits
        break

    if price is None:
        return None, None, None

    # период
    if any(x in t.lower() for x in ["в месяц", "месяц", "ежемесячно", "/мес"]):
        period = "month"
    elif any(x in t.lower() for x in ["в сутки", "сутки", "посуточно", "/сут"]):
        period = "day"
    else:
        # если это аренда и нет "посуточно" → считаем месяц
        if deal_type.startswith("rent"):
            period = "month"
        else:
            period = "total"

    return price, currency, period

def detect_deposit(text_norm: str):
    m = DEPOSIT_PATTERN.search(text_norm)
    if not m:
        # варианты "500$+Депозит"
        m2 = re.search(r"(\d[\d\s]{0,12})\s*\$\s*\+\s*депозит", text_norm, re.I)
        if not m2:
            return None
        raw = m2.group(1)
    else:
        raw = m.group(1)

    try:
        return int(re.sub(r"\D", "", raw))
    except:
        return None

def detect_location(text_norm: str):
    district = None
    metro = None

    m = DISTRICT_PATTERN.search(text_norm)
    if m:
        district = m.group(1).strip()

    m2 = METRO_PATTERN.search(text_norm)
    if m2:
        metro = m2.group(1).strip()

    return district, metro

def extract_phones(text: str):
    if not text: return []
    return list({re.sub(r"\s+", " ", m.strip()) for m in PHONE_RE.findall(text)})

def media_type_of(msg):
    if getattr(msg, "photo", None): return "image"
    if getattr(msg, "video", None): return "video"
    if getattr(msg, "voice", None): return "voice"
    if getattr(msg, "audio", None): return "audio"
    if getattr(msg, "sticker", None): return "sticker"
    if getattr(msg, "document", None):
        mt = getattr(msg.document, "mime_type", "") or ""
        if mt.startswith("image/"): return "image"
        if mt.startswith("video/"): return "video"
        if mt.startswith("audio/"): return "audio"
        return "document"
    return None

def extract_links_tags_mentions(msg):
    links, tags, mentions, cashtags = set(), set(), set(), set()
    s = msg.message or ""
    for e in (msg.entities or []):
        try:
            if isinstance(e, MessageEntityTextUrl) and e.url:
                links.add(e.url)
            elif isinstance(e, MessageEntityUrl):
                links.add(s[e.offset:e.offset+e.length])
            elif isinstance(e, MessageEntityHashtag):
                tags.add(s[e.offset:e.offset+e.length])
            elif isinstance(e, MessageEntityMention):
                mentions.add(s[e.offset:e.offset+e.length])
            elif isinstance(e, MessageEntityCashtag):
                cashtags.add(s[e.offset:e.offset+e.length])
        except Exception:
            pass
    if isinstance(getattr(msg, "media", None), MessageMediaWebPage):
        wp = getattr(msg.media, "webpage", None)
        if getattr(wp, "url", None):
            links.add(wp.url)
    if getattr(msg, "buttons", None):
        for row in msg.buttons:
            for btn in (row.buttons if isinstance(row, KeyboardButtonRow) else row):
                if isinstance(btn, KeyboardButtonUrl) and getattr(btn, "url", None):
                    links.add(btn.url)
    return list(links), list(tags), list(mentions), list(cashtags)

def print_post_json(obj):
    print(json.dumps(obj, ensure_ascii=False))

async def resolve_targets_once(client: TelegramClient, reg: ChannelRegistry):
    """
    Резолвит usernames/invites -> peer_id (-100XXXXXXXXXX для каналов),
    пробует вступить в канал / импортировать инвайт,
    и добавляет в allowed_ids оба формата (peer_id и raw id).
    """
    ids = set(reg.allowed_ids)
    ch = reg.list_all()

    # --- приватные инвайты (t.me/+hash) ---
    for link in ch.invites:
        try:
            if "t.me/+" in link:
                invite_hash = link.rsplit("+", 1)[-1]
                try:
                    await client(ImportChatInviteRequest(invite_hash))
                except Exception:
                    pass  # возможно уже внутри; ок
            entity = await client.get_entity(link)
            peer_id = utils.get_peer_id(entity)      # -100XXXXXXXXXX (каналы)
            raw_id = int(getattr(entity, "id"))      # положительный id
            ids.update({peer_id, raw_id})
            reg.add_resolved_id(raw_id)
        except Exception as e:
            print("⚠️ resolve invite error:", link, e)

    # --- публичные @usernames ---
    for uname in ch.public:
        try:
            entity = await client.get_entity(uname)
            try:
                await client(JoinChannelRequest(entity))  # вступим в канал, если возможно
            except Exception:
                pass
            peer_id = utils.get_peer_id(entity)
            raw_id = int(getattr(entity, "id"))
            ids.update({peer_id, raw_id})
            reg.add_resolved_id(raw_id)
        except Exception as e:
            print("⚠️ resolve public error:", uname, e)

    # --- учтём уже сохранённые raw ids ---
    for x in ch.resolved_ids:
        try:
            xi = int(x)
            ids.add(xi)
            # добавим типичный peer_id-вид для каналов (минусовые):
            # Telethon сам верно даст peer_id через utils.get_peer_id(entity),
            # но на случай «сырого» id в файле — пытаемся подстраховаться.
            # Универсально гарантировать формулу нельзя, поэтому основной способ — через get_entity.
        except Exception:
            pass

    reg.set_allowed_ids(ids)

async def watch_registry_loop(client: TelegramClient, reg: ChannelRegistry, interval_sec: int = 5):
    """
    Следим за изменениям файла каналов и при изменении — перечитываем и РЕЗОЛВИМ.
    """
    import os
    path = reg._path
    last_mtime = os.path.getmtime(path) if path.exists() else 0
    while True:
        await asyncio.sleep(interval_sec)
        try:
            if path.exists():
                mtime = os.path.getmtime(path)
                if mtime > last_mtime:
                    last_mtime = mtime
                    reg.load()
                    await resolve_targets_once(client, reg)
                    print(f"[registry] reloaded & resolved, allowed_ids={len(reg.allowed_ids)}")
        except Exception as e:
            print("[registry] watch error:", e)

def guess_filename(msg, chat_id: int) -> str:
    """
    Выбираем имя файла: используем исходное имя документа, иначе подставляем по типу.
    """
    # document c исходным именем
    if getattr(msg, "document", None):
        try:
            for attr in msg.document.attributes or []:
                if getattr(attr, "file_name", None):
                    return f"{chat_id}_{msg.id}__{attr.file_name}"
        except Exception:
            pass
        # по mime
        mt = (getattr(msg.document, "mime_type", "") or "").lower()
        ext = ".bin"
        if mt.startswith("image/"): ext = ".jpg"
        elif mt.startswith("video/"): ext = ".mp4"
        elif mt.startswith("audio/"): ext = ".mp3"
        return f"{chat_id}_{msg.id}{ext}"

    # photo
    if getattr(msg, "photo", None):
        return f"{chat_id}_{msg.id}.jpg"

    # voice / audio / sticker
    if getattr(msg, "voice", None):
        return f"{chat_id}_{msg.id}.ogg"
    if getattr(msg, "audio", None):
        return f"{chat_id}_{msg.id}.mp3"
    if getattr(msg, "sticker", None):
        return f"{chat_id}_{msg.id}.webp"

    # fallback
    return f"{chat_id}_{msg.id}.bin"

def parse_real_estate(text: str, hashtags: list[str]) -> dict:
    """
    Берём сырое сообщение + хэштеги и возвращаем
    структурированный объект 'parsed' для post.
    """
    text = text or ""
    text_norm = normalize_text(text)
    hashtags = hashtags or []

    if not text_norm:
        return {"is_real_estate": False}

    if not detect_is_real_estate(text_norm, hashtags):
        return {"is_real_estate": False}

    deal_type = detect_deal_type(text_norm, hashtags)
    obj_type  = detect_object_type(text_norm, hashtags)

    # 1) сначала пробуем паттерн 1/2/5 (комнаты/этаж/этажность)
    rooms, floor, total_floors = detect_rooms_floors_triple(text_norm)
    rooms_options = None

    # 2) если тройной формат не найден — обычные паттерны
    if rooms is None and floor is None and total_floors is None:
        rooms, rooms_options = detect_rooms(text_norm)
        f, tf = detect_floor(text_norm)
        if floor is None:        floor = f
        if total_floors is None: total_floors = tf

    area          = detect_area(text_norm)
    price, cur, period = detect_price_and_period(text_norm, deal_type)
    deposit       = detect_deposit(text_norm)
    district, metro = detect_location(text_norm)

    parsed = {
        "is_real_estate": True,
        "deal_type": deal_type,       # rent_long / rent_daily / sale / ...
        "object_type": obj_type,      # flat / house / room / ...
        "rooms": rooms,               # например 2
        "rooms_options": rooms_options, # если был формат 1/2/5 и ты захочешь хранить варианты
        "floor": floor,               # 4
        "total_floors": total_floors, # 9
        "area_m2": area,              # 65
        "price": price,               # 600
        "currency": cur,              # usd / uzs
        "price_period": period,       # month / day / total
        "deposit": deposit,           # 800 и т.п.
        "district_raw": district,     # "Мирабадский район"
        "metro_raw": metro,           # "Ойбек"
        "has_commission": any(x in text_norm.lower()
                              for x in ["комиссионные", "maklerskiy", "риелтор"]),
    }

    return parsed

async def run_listener():
    api_id = int(env("API_ID"))
    api_hash = env("API_HASH")
    session_dir = env("SESSION_DIR")
    session_name = env("SESSION_NAME")
    download_dir = env("DOWNLOAD_DIR")
    channels_file = env("CHANNELS_FILE")
    ensure_dirs(session_dir, download_dir)

    reg = ChannelRegistry(channels_file)
    client = TelegramClient(f"{session_dir.rstrip('/')}/{session_name}", api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("Сессия не авторизована. Сначала запусти: python -m tools.auth_login")

    # первичный резолв
    await resolve_targets_once(client, reg)

    @client.on(events.NewMessage)
    async def on_message(event):
        msg = event.message

        # если это часть медиальбома — обрабатываем в on_album, здесь пропускаем
        if msg.grouped_id:
            return

        # ВАЖНО: для каналов event.chat_id будет вида -100XXXXXXXXXX
        chat_id = int(event.chat_id)
        if chat_id not in reg.allowed_ids:
            return

        chat = await event.get_chat()

        # определяем тип чата
        if isinstance(chat, Channel):
            chat_type = "channel" if getattr(chat, "broadcast", False) else "supergroup"
        elif isinstance(chat, Chat):
            chat_type = "group"
        elif isinstance(chat, User):
            chat_type = "private"
        else:
            chat_type = "unknown"

        links, tags, mentions, cashtags = extract_links_tags_mentions(msg)

        # --- определяем тип и качаем медиа ---
        media_kind = media_type_of(msg)

        saved_path = None
        if media_kind is not None:
            base_dir = Path(env("DOWNLOAD_DIR"))
            out_dir = base_dir / str(chat_id)   # по каналам — в подпапки
            out_dir.mkdir(parents=True, exist_ok=True)
            target_name = guess_filename(msg, chat_id)
            target_path = out_dir / target_name
            try:
                saved = await msg.download_media(file=str(target_path))
                if saved:
                    saved_path = saved  # фактический путь от Telethon
            except Exception as e:
                print(f"[media] download error chat={chat_id} msg={msg.id}: {e}")
                saved_path = None

        post_text = msg.message or ""
        parsed = parse_real_estate(post_text, tags)

        post = {
            "post_uid": f"msg:{chat_id}:{int(msg.id)}",
            "message_id": int(msg.id),  # явный id сообщения
            "channel_title": getattr(chat, "title", getattr(chat, "username", str(chat_id))),
            "channel_id": chat_id,
            "chat_type": chat_type,
            "date_utc": msg.date.strftime("%Y-%m-%d %H:%M:%S"),  # когда написал автор
            "fetched_at_utc": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),  # когда мы обработали
            "media_type": media_kind,
            "saved_path": saved_path,
            "media": None,  # для единообразия со структурой альбома
            "text_len": len(post_text),
            "phones": extract_phones(post_text),
            "links": links,
            "hashtags": tags,
            "mentions": mentions,
            "cashtags": cashtags,
            "text": post_text,
            "is_deleted": False,
            "parsed": parsed,
        }

        print_post_json(post)
        print("=== PARSED ===")
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
        print("==============")

    @client.on(events.Album)
    async def on_album(event):
        chat_id = int(event.chat_id)
        if chat_id not in reg.allowed_ids:
            return

        chat = await event.get_chat()
        messages = event.messages  # список Message

        # --- выбираем "главное" сообщение с текстом ---
        main_msg = None
        for m in messages:
            if (m.message or "").strip():
                main_msg = m
                break
        if main_msg is None:
            main_msg = messages[0]

        # --- собираем все медиа в альбоме ---
        media_items = []
        base_dir = Path(env("DOWNLOAD_DIR"))
        out_dir = base_dir / str(chat_id)
        out_dir.mkdir(parents=True, exist_ok=True)

        for m in messages:
            kind = media_type_of(m)
            if not kind:
                continue

            target_name = guess_filename(m, chat_id)
            target_path = out_dir / target_name
            saved_path = None
            try:
                saved = await m.download_media(file=str(target_path))
                if saved:
                    saved_path = saved
            except Exception as e:
                print(f"[album media] download error chat={chat_id} msg={m.id}: {e}")

            media_items.append({
                "message_id": int(m.id),
                "media_type": kind,
                "saved_path": saved_path,
            })

        links, tags, mentions, cashtags = extract_links_tags_mentions(main_msg)
        post_text = main_msg.message or ""
        parsed = parse_real_estate(post_text, tags)

        grouped_id = int(main_msg.grouped_id) if main_msg.grouped_id else None

        # для совместимости: первая картинка альбома
        first_media_type = media_items[0]["media_type"] if media_items else None
        first_saved_path = media_items[0]["saved_path"] if media_items else None

        # тип чата (для канала это Channel с broadcast=True)
        if isinstance(chat, Channel):
            chat_type = "channel" if getattr(chat, "broadcast", False) else "supergroup"
        elif isinstance(chat, Chat):
            chat_type = "group"
        elif isinstance(chat, User):
            chat_type = "private"
        else:
            chat_type = "unknown"

        post = {
            "post_uid": f"album:{chat_id}:{int(main_msg.id)}",
            "message_id": int(main_msg.id),
            "grouped_id": grouped_id,

            "channel_title": getattr(chat, "title", getattr(chat, "username", str(chat_id))),
            "channel_id": chat_id,
            "chat_type": chat_type,

            "date_utc": main_msg.date.strftime("%Y-%m-%d %H:%M:%S"),
            "fetched_at_utc": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),

            "media_type": first_media_type,
            "saved_path": first_saved_path,
            "media": media_items,

            "text_len": len(post_text),
            "phones": extract_phones(post_text),
            "links": links,
            "hashtags": tags,
            "mentions": mentions,
            "cashtags": cashtags,
            "text": post_text,
            "is_deleted": False,
            "parsed": parsed,
        }

        print_post_json(post)
        print("=== PARSED ALBUM ===")
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
        print("==============")

    # watcher с резолвом при каждом изменении файла
    watcher = asyncio.create_task(watch_registry_loop(client, reg))
    print("Слушаю… (hot-reload каналов включён)")
    try:
        await client.run_until_disconnected()
    finally:
        watcher.cancel()
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(run_listener())
