# database/models.py
from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey, Index, Integer,
    Numeric, SmallInteger, String, Text, UniqueConstraint, func
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(255))
    title: Mapped[Optional[str]] = mapped_column(String(500))
    chat_type: Mapped[str] = mapped_column(String(50), default="channel")
    invite_link: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Relationships
    posts: Mapped[List["Post"]] = relationship(back_populates="channel", cascade="all, delete-orphan")


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        UniqueConstraint("channel_id", "message_id", name="uq_post_channel_message"),
        Index("idx_posts_text_hash", "text_hash"),
        Index("idx_posts_fingerprint", "fingerprint"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    post_uid: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)

    # Telegram IDs
    channel_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("channels.id", ondelete="CASCADE"), nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    grouped_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    # Content
    text_raw: Mapped[Optional[str]] = mapped_column(Text)
    text_len: Mapped[int] = mapped_column(Integer, default=0)

    # Extracted data (PostgreSQL arrays)
    phones: Mapped[Optional[List[str]]] = mapped_column(ARRAY(Text))
    links: Mapped[Optional[List[str]]] = mapped_column(ARRAY(Text))
    hashtags: Mapped[Optional[List[str]]] = mapped_column(ARRAY(Text))
    mentions: Mapped[Optional[List[str]]] = mapped_column(ARRAY(Text))

    # Timestamps
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), onupdate=func.now())

    # Status
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Deduplication
    text_hash: Mapped[Optional[str]] = mapped_column(String(64))
    fingerprint: Mapped[Optional[str]] = mapped_column(String(64))
    duplicate_of: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("posts.id"))

    # Relationships
    channel: Mapped["Channel"] = relationship(back_populates="posts")
    media_items: Mapped[List["Media"]] = relationship(back_populates="post", cascade="all, delete-orphan")
    listing: Mapped[Optional["Listing"]] = relationship(back_populates="post", uselist=False, cascade="all, delete-orphan")


class Media(Base):
    __tablename__ = "media"
    __table_args__ = (
        UniqueConstraint("post_id", "message_id", name="uq_media_post_message"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    post_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # File paths
    local_path: Mapped[Optional[str]] = mapped_column(Text)
    remote_url: Mapped[Optional[str]] = mapped_column(Text)

    # Metadata
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger)
    mime_type: Mapped[Optional[str]] = mapped_column(String(100))
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    duration_sec: Mapped[Optional[int]] = mapped_column(Integer)

    # Image deduplication
    phash: Mapped[Optional[str]] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    # Relationships
    post: Mapped["Post"] = relationship(back_populates="media_items")


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    post_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("posts.id", ondelete="CASCADE"), unique=True, nullable=False)

    # Classification
    is_real_estate: Mapped[bool] = mapped_column(Boolean, default=True)
    deal_type: Mapped[Optional[str]] = mapped_column(String(50))  # rent_long, rent_daily, sale, wanted_rent
    object_type: Mapped[Optional[str]] = mapped_column(String(50))  # flat, room, house, land, office

    # Object parameters
    rooms: Mapped[Optional[int]] = mapped_column(SmallInteger)
    rooms_options: Mapped[Optional[List[int]]] = mapped_column(ARRAY(SmallInteger))
    floor: Mapped[Optional[int]] = mapped_column(SmallInteger)
    total_floors: Mapped[Optional[int]] = mapped_column(SmallInteger)
    area_m2: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))

    # Price
    price: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    currency: Mapped[Optional[str]] = mapped_column(String(10))
    price_period: Mapped[Optional[str]] = mapped_column(String(20))
    deposit: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    has_commission: Mapped[bool] = mapped_column(Boolean, default=False)

    # Location (raw)
    district_raw: Mapped[Optional[str]] = mapped_column(String(255))
    metro_raw: Mapped[Optional[str]] = mapped_column(String(255))
    address_raw: Mapped[Optional[str]] = mapped_column(Text)
    landmark: Mapped[Optional[str]] = mapped_column(String(255))  # NEW: Ориентир

    # Normalized location
    district_id: Mapped[Optional[int]] = mapped_column(Integer)
    metro_id: Mapped[Optional[int]] = mapped_column(Integer)
    latitude: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7))
    longitude: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7))

    # Rental conditions (NEW)
    min_period_months: Mapped[Optional[int]] = mapped_column(SmallInteger)  # Мин. срок аренды
    utilities_included: Mapped[Optional[bool]] = mapped_column(Boolean)  # Коммуналка включена
    no_deposit: Mapped[bool] = mapped_column(Boolean, default=False)  # Без депозита

    # Property condition (NEW)
    condition: Mapped[Optional[str]] = mapped_column(String(100))  # Состояние/ремонт
    house_type: Mapped[Optional[str]] = mapped_column(String(100))  # Тип дома (вторичка, новостройка)

    # Amenities (extended)
    has_furniture: Mapped[Optional[bool]] = mapped_column(Boolean)
    has_appliances: Mapped[Optional[bool]] = mapped_column(Boolean)
    has_internet: Mapped[Optional[bool]] = mapped_column(Boolean)
    has_parking: Mapped[Optional[bool]] = mapped_column(Boolean)
    has_conditioner: Mapped[Optional[bool]] = mapped_column(Boolean)
    has_washing_machine: Mapped[Optional[bool]] = mapped_column(Boolean)  # NEW
    has_refrigerator: Mapped[Optional[bool]] = mapped_column(Boolean)  # NEW
    has_balcony: Mapped[Optional[bool]] = mapped_column(Boolean)  # NEW

    # Rules (NEW)
    pets_allowed: Mapped[Optional[bool]] = mapped_column(Boolean)
    kids_allowed: Mapped[Optional[bool]] = mapped_column(Boolean)

    # Contact
    contact_phone: Mapped[Optional[str]] = mapped_column(String(50))
    contact_name: Mapped[Optional[str]] = mapped_column(String(255))
    contact_tg: Mapped[Optional[str]] = mapped_column(String(100))
    is_agent: Mapped[Optional[bool]] = mapped_column(Boolean)

    # Description (NEW)
    description_clean: Mapped[Optional[str]] = mapped_column(Text)  # Чистое описание без технических данных

    # Parse quality
    parse_score: Mapped[int] = mapped_column(SmallInteger, default=0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), onupdate=func.now())

    # Relationships
    post: Mapped["Post"] = relationship(back_populates="listing")


class District(Base):
    __tablename__ = "districts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name_ru: Mapped[str] = mapped_column(String(255), nullable=False)
    name_uz: Mapped[Optional[str]] = mapped_column(String(255))
    city: Mapped[str] = mapped_column(String(100), default="Ташкент")
    aliases: Mapped[Optional[List[str]]] = mapped_column(ARRAY(Text))
    center_lat: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7))
    center_lng: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7))


class MetroStation(Base):
    __tablename__ = "metro_stations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name_ru: Mapped[str] = mapped_column(String(255), nullable=False)
    name_uz: Mapped[Optional[str]] = mapped_column(String(255))
    line_name: Mapped[Optional[str]] = mapped_column(String(100))
    line_color: Mapped[Optional[str]] = mapped_column(String(20))
    latitude: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7))
    longitude: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7))
    aliases: Mapped[Optional[List[str]]] = mapped_column(ARRAY(Text))


class DeletionLog(Base):
    __tablename__ = "deletion_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    post_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("posts.id"))
    channel_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    lifetime_hours: Mapped[Optional[int]] = mapped_column(Integer)


class Duplicate(Base):
    __tablename__ = "duplicates"
    __table_args__ = (
        UniqueConstraint("original_id", "duplicate_id", name="uq_duplicate_pair"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    original_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    duplicate_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    similarity: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4))
    match_type: Mapped[Optional[str]] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())