# database/post_service.py
"""
Сервис для сохранения постов из Telegram в БД.
Используется в tg_listener.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any

from database.connection import get_session
from database.repository import ChannelRepo, PostRepo, MediaRepo, ListingRepo, DuplicateRepo


class PostService:
    """
    Высокоуровневый сервис для работы с постами.
    Инкапсулирует логику сохранения, дедупликации, удаления.
    """

    async def save_post(self, post_data: Dict[str, Any]) -> Optional[int]:
        """
        Сохраняет пост из listener в БД.
        
        Args:
            post_data: словарь от listener (post_uid, channel_id, text, parsed, media и т.д.)
        
        Returns:
            post.id если успешно, None если дубликат или ошибка
        """
        async with get_session() as session:
            channel_repo = ChannelRepo(session)
            post_repo = PostRepo(session)
            media_repo = MediaRepo(session)
            listing_repo = ListingRepo(session)
            dup_repo = DuplicateRepo(session)

            # 1. Проверяем, не обрабатывали ли уже
            if await post_repo.exists(post_data["post_uid"]):
                return None

            # 2. Получаем/создаём канал
            channel = await channel_repo.get_or_create(
                telegram_id=post_data["channel_id"],
                title=post_data.get("channel_title"),
                chat_type=post_data.get("chat_type", "channel"),
            )

            # 3. Парсим дату
            published_at = self._parse_date(post_data.get("date_utc"))

            # 4. Создаём пост
            post = await post_repo.create(
                post_uid=post_data["post_uid"],
                channel_id=channel.id,
                message_id=post_data["message_id"],
                published_at=published_at,
                text_raw=post_data.get("text"),
                phones=post_data.get("phones"),
                links=post_data.get("links"),
                hashtags=post_data.get("hashtags"),
                mentions=post_data.get("mentions"),
                grouped_id=post_data.get("grouped_id"),
            )

            # 5. Сохраняем медиа
            await self._save_media(media_repo, post.id, post_data)

            # 6. Сохраняем распарсенные данные (listing)
            parsed = post_data.get("parsed", {})
            if parsed.get("is_real_estate"):
                await listing_repo.create(post.id, parsed)

            # 7. Проверяем дубликаты
            await self._check_duplicates(post_repo, dup_repo, post)

            return post.id

    async def _save_media(self, repo: MediaRepo, post_id: int, post_data: Dict[str, Any]):
        """Сохраняет медиафайлы поста."""
        # Альбом
        media_items = post_data.get("media") or []
        
        # Или одиночное медиа
        if not media_items and post_data.get("media_type"):
            media_items = [{
                "message_id": post_data["message_id"],
                "media_type": post_data["media_type"],
                "saved_path": post_data.get("saved_path"),
            }]

        for item in media_items:
            if item.get("media_type"):
                await repo.create(
                    post_id=post_id,
                    message_id=item["message_id"],
                    media_type=item["media_type"],
                    local_path=item.get("saved_path"),
                )

    async def _check_duplicates(self, post_repo: PostRepo, dup_repo: DuplicateRepo, post):
        """Проверяет пост на дубликаты и связывает."""
        # Точные дубли по хэшу текста
        if post.text_hash:
            duplicates = await post_repo.find_duplicates_by_hash(post.text_hash, exclude_id=post.id)
            for dup in duplicates:
                # Оригинал — самый ранний
                original = dup if dup.published_at < post.published_at else post
                duplicate = post if dup.published_at < post.published_at else dup
                
                await dup_repo.create(
                    original_id=original.id,
                    duplicate_id=duplicate.id,
                    similarity=1.0,
                    match_type="text_exact",
                )
                
                # Помечаем дубликат
                duplicate.duplicate_of = original.id

        # Дубли по телефонам (менее строгий критерий)
        if post.phones:
            phone_matches = await post_repo.find_duplicates_by_phone(post.phones, exclude_id=post.id)
            for match in phone_matches:
                # Не создаём дубль если уже есть exact match
                if post.duplicate_of:
                    continue
                    
                await dup_repo.create(
                    original_id=match.id if match.published_at < post.published_at else post.id,
                    duplicate_id=post.id if match.published_at < post.published_at else match.id,
                    similarity=0.7,
                    match_type="phone",
                )

    async def mark_deleted(
        self,
        message_ids: List[int],
        channel_id: Optional[int] = None
    ) -> int:
        """
        Помечает сообщения как удалённые.
        
        Returns:
            количество обновлённых постов
        """
        async with get_session() as session:
            post_repo = PostRepo(session)
            count = 0
            for msg_id in message_ids:
                deleted_ids = await post_repo.mark_deleted_by_message_id(msg_id, channel_id)
                count += len(deleted_ids)
            return count

    async def get_active_listings(
        self,
        deal_type: Optional[str] = None,
        rooms: Optional[int] = None,
        max_price: Optional[float] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Получает активные объявления с фильтрами.
        Возвращает список словарей для API.
        """
        async with get_session() as session:
            listing_repo = ListingRepo(session)
            listings = await listing_repo.search(
                deal_type=deal_type,
                rooms=rooms,
                max_price=max_price,
                limit=limit,
            )
            
            # Подгружаем связанные данные
            results = []
            for l in listings:
                post = l.post
                results.append({
                    "id": l.id,
                    "post_uid": post.post_uid,
                    "deal_type": l.deal_type,
                    "object_type": l.object_type,
                    "rooms": l.rooms,
                    "floor": l.floor,
                    "total_floors": l.total_floors,
                    "area_m2": float(l.area_m2) if l.area_m2 else None,
                    "price": float(l.price) if l.price else None,
                    "currency": l.currency,
                    "price_period": l.price_period,
                    "deposit": float(l.deposit) if l.deposit else None,
                    "district": l.district_raw,
                    "metro": l.metro_raw,
                    "phones": post.phones,
                    "text": post.text_raw[:500] if post.text_raw else None,
                    "published_at": post.published_at.isoformat(),
                    "parse_score": l.parse_score,
                })
            
            return results

    @staticmethod
    def _parse_date(date_str: Optional[str]) -> datetime:
        """Парсит дату из строки или возвращает текущую."""
        if not date_str:
            return datetime.utcnow()
        try:
            return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return datetime.utcnow()


# Singleton для удобства
post_service = PostService()