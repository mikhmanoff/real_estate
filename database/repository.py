# database/repository.py
import hashlib
from datetime import datetime
from typing import Optional, List, Dict, Any

from sqlalchemy import select, update, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert

from database.models import Channel, Post, Media, Listing, DeletionLog, Duplicate


class ChannelRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self, telegram_id: int, **kwargs) -> Channel:
        """Получает канал по telegram_id или создаёт новый."""
        stmt = select(Channel).where(Channel.telegram_id == telegram_id)
        result = await self.session.execute(stmt)
        channel = result.scalar_one_or_none()
        
        if channel:
            # Обновляем поля если переданы
            for key, value in kwargs.items():
                if hasattr(channel, key) and value is not None:
                    setattr(channel, key, value)
            return channel
        
        channel = Channel(telegram_id=telegram_id, **kwargs)
        self.session.add(channel)
        await self.session.flush()
        return channel

    async def get_by_telegram_id(self, telegram_id: int) -> Optional[Channel]:
        stmt = select(Channel).where(Channel.telegram_id == telegram_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active(self) -> List[Channel]:
        stmt = select(Channel).where(Channel.is_active == True)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class PostRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def compute_text_hash(text: str) -> str:
        """SHA256 хэш нормализованного текста."""
        normalized = " ".join(text.lower().split())
        return hashlib.sha256(normalized.encode()).hexdigest()

    @staticmethod
    def compute_fingerprint(text: str, phones: List[str]) -> str:
        """
        Fingerprint для поиска похожих объявлений.
        Комбинация: первые слова + телефоны.
        """
        words = text.lower().split()[:20]
        phones_str = ",".join(sorted(phones or []))
        data = " ".join(words) + "|" + phones_str
        return hashlib.md5(data.encode()).hexdigest()

    async def create(
        self,
        post_uid: str,
        channel_id: int,
        message_id: int,
        published_at: datetime,
        text_raw: Optional[str] = None,
        phones: Optional[List[str]] = None,
        links: Optional[List[str]] = None,
        hashtags: Optional[List[str]] = None,
        mentions: Optional[List[str]] = None,
        grouped_id: Optional[int] = None,
    ) -> Post:
        """Создаёт новый пост."""
        text_hash = self.compute_text_hash(text_raw) if text_raw else None
        fingerprint = self.compute_fingerprint(text_raw or "", phones or [])

        post = Post(
            post_uid=post_uid,
            channel_id=channel_id,
            message_id=message_id,
            grouped_id=grouped_id,
            text_raw=text_raw,
            text_len=len(text_raw) if text_raw else 0,
            phones=phones,
            links=links,
            hashtags=hashtags,
            mentions=mentions,
            published_at=published_at,
            text_hash=text_hash,
            fingerprint=fingerprint,
        )
        self.session.add(post)
        await self.session.flush()
        return post

    async def get_by_uid(self, post_uid: str) -> Optional[Post]:
        stmt = select(Post).where(Post.post_uid == post_uid)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_message(self, channel_id: int, message_id: int) -> Optional[Post]:
        stmt = select(Post).where(
            and_(Post.channel_id == channel_id, Post.message_id == message_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def exists(self, post_uid: str) -> bool:
        stmt = select(Post.id).where(Post.post_uid == post_uid)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def mark_deleted(self, post_id: int) -> None:
        """Помечает пост как удалённый."""
        stmt = (
            update(Post)
            .where(Post.id == post_id)
            .values(is_deleted=True, deleted_at=datetime.utcnow())
        )
        await self.session.execute(stmt)

    async def mark_deleted_by_message_id(self, message_id: int, channel_id: Optional[int] = None) -> List[int]:
        """
        Помечает посты как удалённые по message_id.
        Возвращает список ID обновлённых постов.
        """
        conditions = [Post.message_id == message_id, Post.is_deleted == False]
        if channel_id:
            conditions.append(Post.channel_id == channel_id)

        # Сначала получаем ID
        stmt = select(Post.id).where(and_(*conditions))
        result = await self.session.execute(stmt)
        post_ids = [row[0] for row in result.fetchall()]

        if post_ids:
            stmt = (
                update(Post)
                .where(Post.id.in_(post_ids))
                .values(is_deleted=True, deleted_at=datetime.utcnow())
            )
            await self.session.execute(stmt)

        return post_ids

    async def find_duplicates_by_hash(self, text_hash: str, exclude_id: Optional[int] = None) -> List[Post]:
        """Находит посты с таким же text_hash (точные дубли)."""
        conditions = [Post.text_hash == text_hash, Post.is_deleted == False]
        if exclude_id:
            conditions.append(Post.id != exclude_id)

        stmt = select(Post).where(and_(*conditions)).order_by(Post.published_at)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def find_duplicates_by_phone(self, phones: List[str], exclude_id: Optional[int] = None) -> List[Post]:
        """Находит посты с пересечением телефонов."""
        if not phones:
            return []

        conditions = [Post.phones.overlap(phones), Post.is_deleted == False]
        if exclude_id:
            conditions.append(Post.id != exclude_id)

        stmt = select(Post).where(and_(*conditions)).order_by(Post.published_at)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_recent(self, limit: int = 100, channel_id: Optional[int] = None) -> List[Post]:
        """Последние посты."""
        conditions = [Post.is_deleted == False]
        if channel_id:
            conditions.append(Post.channel_id == channel_id)

        stmt = (
            select(Post)
            .where(and_(*conditions))
            .order_by(Post.published_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class MediaRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        post_id: int,
        message_id: int,
        media_type: str,
        local_path: Optional[str] = None,
        **kwargs
    ) -> Media:
        media = Media(
            post_id=post_id,
            message_id=message_id,
            media_type=media_type,
            local_path=local_path,
            **kwargs
        )
        self.session.add(media)
        await self.session.flush()
        return media

    async def create_many(self, items: List[Dict[str, Any]]) -> List[Media]:
        """Batch создание медиа."""
        media_list = [Media(**item) for item in items]
        self.session.add_all(media_list)
        await self.session.flush()
        return media_list

    async def get_by_post(self, post_id: int) -> List[Media]:
        stmt = select(Media).where(Media.post_id == post_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def find_by_phash(self, phash: str) -> List[Media]:
        """Поиск по perceptual hash."""
        stmt = select(Media).where(Media.phash == phash)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class ListingRepo:
    def __init__(self, session):
        self.session = session

    async def create(self, post_id: int, parsed: dict) -> Listing:
        """
        Создаёт Listing из распарсенных данных.
        
        Args:
            post_id: ID поста в БД
            parsed: Результат parse_real_estate() из parser.py
        
        Returns:
            Созданный Listing
        """
        # Рассчитываем parse_score (качество парсинга)
        parse_score = 0
        if parsed.get("rooms"): parse_score += 1
        if parsed.get("floor"): parse_score += 1
        if parsed.get("area_m2"): parse_score += 1
        if parsed.get("price"): parse_score += 2
        if parsed.get("district_raw"): parse_score += 1
        if parsed.get("metro_raw"): parse_score += 1
        if parsed.get("description_clean"): parse_score += 1
        if parsed.get("phones"): parse_score += 1
        
        # Определяем, нужна ли ручная проверка
        needs_review = parse_score < 4 or parsed.get("deal_type") == "unknown"
        
        # Берём первый телефон как контакт
        phones = parsed.get("phones", [])
        contact_phone = phones[0] if phones else None

        listing = Listing(
            post_id=post_id,
            
            # Classification
            is_real_estate=True,
            deal_type=parsed.get("deal_type"),
            object_type=parsed.get("object_type"),
            
            # Object parameters
            rooms=parsed.get("rooms"),
            rooms_options=parsed.get("rooms_options"),
            floor=parsed.get("floor"),
            total_floors=parsed.get("total_floors"),
            area_m2=parsed.get("area_m2"),
            
            # Price
            price=parsed.get("price"),
            currency=parsed.get("currency"),
            price_period=parsed.get("price_period"),
            deposit=parsed.get("deposit"),
            has_commission=parsed.get("has_commission", False),
            
            # Location
            district_raw=parsed.get("district_raw"),
            metro_raw=parsed.get("metro_raw"),
            landmark=parsed.get("landmark"),
            
            # Rental conditions (NEW)
            min_period_months=parsed.get("min_period_months"),
            utilities_included=parsed.get("utilities_included"),
            no_deposit=parsed.get("no_deposit", False),
            
            # Property condition (NEW)
            condition=parsed.get("condition"),
            house_type=parsed.get("house_type"),
            
            # Amenities
            has_furniture=parsed.get("has_furniture"),
            has_conditioner=parsed.get("has_conditioner"),
            has_washing_machine=parsed.get("has_washing_machine"),
            has_refrigerator=parsed.get("has_refrigerator"),
            has_internet=parsed.get("has_internet"),
            has_parking=parsed.get("has_parking"),
            has_balcony=parsed.get("has_balcony"),
            has_appliances=parsed.get("has_washing_machine") or parsed.get("has_refrigerator"),
            
            # Rules (NEW)
            pets_allowed=parsed.get("pets_allowed"),
            kids_allowed=parsed.get("kids_allowed"),
            
            # Contact
            contact_phone=contact_phone,
            
            # Description (NEW)
            description_clean=parsed.get("description_clean"),
            
            # Quality
            parse_score=parse_score,
            needs_review=needs_review,
        )
        
        self.session.add(listing)
        await self.session.flush()
        return listing


class DuplicateRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        original_id: int,
        duplicate_id: int,
        similarity: float,
        match_type: str,
    ) -> Duplicate:
        dup = Duplicate(
            original_id=original_id,
            duplicate_id=duplicate_id,
            similarity=similarity,
            match_type=match_type,
        )
        self.session.add(dup)
        await self.session.flush()
        return dup

    async def get_duplicates_of(self, original_id: int) -> List[Duplicate]:
        stmt = select(Duplicate).where(Duplicate.original_id == original_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())