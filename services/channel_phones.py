"""
services/channel_phones.py
==========================
Fallback-извлечение телефонов из описания Telegram-канала ("О канале" / about).

Используется в tg_listener когда extract_phones() не нашёл номер в тексте поста.
Многие риелторские каналы публикуют номер один раз в описании, а в самих
объявлениях его не дублируют.

Кеш в памяти, TTL 24 часа. Один запрос к Telegram API на канал в сутки.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from telethon import TelegramClient
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.types import Channel, Chat

from services.parser import extract_phones


# chat_id -> (phones_list, fetched_at)
_PHONE_CACHE: Dict[int, Tuple[List[str], datetime]] = {}
_CACHE_TTL = timedelta(hours=24)
# Если фетч описания упал — не пробуем снова часто:
_NEGATIVE_TTL = timedelta(hours=6)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def get_channel_fallback_phones(
    client: TelegramClient,
    chat,
) -> List[str]:
    """
    Достаёт телефоны из описания канала/группы.

    Args:
        client: активный TelegramClient
        chat: объект чата (Channel, Chat или User) — обычно через event.get_chat()

    Returns:
        Список нормализованных номеров вида ['+998901234567'].
        Пустой список, если описание отсутствует, недоступно или без телефонов.
    """
    chat_id = getattr(chat, "id", None)
    if not chat_id:
        return []
    chat_id = int(chat_id)

    # --- Cache check ---
    cached = _PHONE_CACHE.get(chat_id)
    if cached:
        phones, ts = cached
        age = _now() - ts
        # Положительный кеш живёт TTL, отрицательный — короче
        if phones and age < _CACHE_TTL:
            return list(phones)
        if not phones and age < _NEGATIVE_TTL:
            return []

    # --- Fetch description from Telegram ---
    about_text = ""
    try:
        if isinstance(chat, Channel):
            full = await client(GetFullChannelRequest(chat))
            about_text = _extract_about(full)
        elif isinstance(chat, Chat):
            full = await client(GetFullChatRequest(chat.id))
            about_text = _extract_about(full)
        else:
            # User / private chat — у них нет публичного "о канале"
            _PHONE_CACHE[chat_id] = ([], _now())
            return []
    except Exception as e:
        print(f"[channel-phones] fetch failed chat_id={chat_id}: {e}")
        _PHONE_CACHE[chat_id] = ([], _now())  # отрицательный кеш
        return []

    phones = extract_phones(about_text) if about_text else []
    _PHONE_CACHE[chat_id] = (phones, _now())

    if phones:
        print(f"[channel-phones] chat {chat_id}: {len(phones)} phones extracted from description")

    return list(phones)


def _extract_about(full_response) -> str:
    """Достаёт поле 'about' из FullChannel / FullChat ответа."""
    full_chat = getattr(full_response, "full_chat", None)
    if not full_chat:
        return ""
    return getattr(full_chat, "about", "") or ""


# ============================================================
# Utility / observability
# ============================================================

def clear_cache(chat_id: Optional[int] = None) -> None:
    """Очистить кеш — целиком или для одного канала. Для отладки."""
    if chat_id is None:
        _PHONE_CACHE.clear()
    else:
        _PHONE_CACHE.pop(int(chat_id), None)


def get_cache_stats() -> Dict[str, int]:
    """Статистика кеша — сколько каналов закешировано, у скольких есть номер."""
    with_phones = sum(1 for ph, _ in _PHONE_CACHE.values() if ph)
    return {
        "total_cached": len(_PHONE_CACHE),
        "with_phones": with_phones,
        "without_phones": len(_PHONE_CACHE) - with_phones,
    }