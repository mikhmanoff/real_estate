# database/competitor_models.py
"""
SQLAlchemy models for joymi.uz competitor scraper.
Uses the same Base as the rest of the app — these tables live in the same DB.

Run database/competitor_schema.sql once to create them, or rely on
Base.metadata.create_all (the schema file is the source of truth for indexes
and triggers).
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional, List

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Index, Integer,
    Numeric, SmallInteger, String, Text, UniqueConstraint, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.models import Base


class CompetitorSeller(Base):
    __tablename__ = "competitor_sellers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    uuid: Mapped[Optional[str]] = mapped_column(String(64))
    profile_name: Mapped[Optional[str]] = mapped_column(String(255))
    avatar_url: Mapped[Optional[str]] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    listings_count: Mapped[int] = mapped_column(Integer, default=0)


class CompetitorListing(Base):
    __tablename__ = "competitor_listings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    uuid: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    slug: Mapped[Optional[str]] = mapped_column(Text)
    title: Mapped[Optional[str]] = mapped_column(Text)

    # Pricing
    price_raw: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    currency_raw: Mapped[Optional[int]] = mapped_column(SmallInteger)
    currency: Mapped[Optional[str]] = mapped_column(String(10))
    price: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    deal_type: Mapped[Optional[str]] = mapped_column(String(50))

    # Object
    object_type: Mapped[Optional[str]] = mapped_column(String(50))
    rooms: Mapped[Optional[int]] = mapped_column(SmallInteger)
    floor: Mapped[Optional[int]] = mapped_column(SmallInteger)
    total_floors: Mapped[Optional[int]] = mapped_column(SmallInteger)
    area_m2: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    area_sotok: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))

    # Location
    address_line: Mapped[Optional[str]] = mapped_column(Text)
    region: Mapped[Optional[str]] = mapped_column(String(100))
    district_raw: Mapped[Optional[str]] = mapped_column(String(100))
    district_norm: Mapped[Optional[str]] = mapped_column(String(100))

    # Seller
    seller_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("competitor_sellers.id"))

    # Flags
    is_vip: Mapped[Optional[bool]] = mapped_column(Boolean)
    is_top: Mapped[Optional[bool]] = mapped_column(Boolean)
    is_raised: Mapped[Optional[bool]] = mapped_column(Boolean)
    status: Mapped[Optional[str]] = mapped_column(String(50))

    # Timeline
    created_at_remote: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    disappeared_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    parse_score: Mapped[int] = mapped_column(SmallInteger, default=0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)

    inserted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), onupdate=func.now())

    media: Mapped[List["CompetitorMedia"]] = relationship(back_populates="listing", cascade="all, delete-orphan")
    seller: Mapped[Optional["CompetitorSeller"]] = relationship()


class CompetitorMedia(Base):
    __tablename__ = "competitor_media"
    __table_args__ = (
        UniqueConstraint("listing_id", "remote_url", name="uq_competitor_media_listing_url"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    listing_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("competitor_listings.id", ondelete="CASCADE"), nullable=False)
    remote_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    file_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    remote_url: Mapped[str] = mapped_column(Text, nullable=False)
    local_path: Mapped[Optional[str]] = mapped_column(Text)
    is_main: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, default=0)
    file_size: Mapped[Optional[int]] = mapped_column(Integer)
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    phash: Mapped[Optional[str]] = mapped_column(String(64))
    download_status: Mapped[str] = mapped_column(String(20), default="pending")
    download_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())

    listing: Mapped["CompetitorListing"] = relationship(back_populates="media")


class CompetitorScrapeRun(Base):
    __tablename__ = "competitor_scrape_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="running")
    pages_fetched: Mapped[int] = mapped_column(Integer, default=0)
    listings_seen: Mapped[int] = mapped_column(Integer, default=0)
    listings_new: Mapped[int] = mapped_column(Integer, default=0)
    listings_disappeared: Mapped[int] = mapped_column(Integer, default=0)
    media_downloaded: Mapped[int] = mapped_column(Integer, default=0)
    media_failed: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text)


class CompetitorSnapshot(Base):
    __tablename__ = "competitor_snapshots"
    __table_args__ = (
        UniqueConstraint("run_id", "listing_id", name="uq_competitor_snapshot_run_listing"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("competitor_scrape_runs.id", ondelete="CASCADE"), nullable=False)
    listing_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("competitor_listings.id", ondelete="CASCADE"), nullable=False)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    page_index: Mapped[Optional[int]] = mapped_column(Integer)
    is_vip: Mapped[Optional[bool]] = mapped_column(Boolean)
    is_top: Mapped[Optional[bool]] = mapped_column(Boolean)
    is_raised: Mapped[Optional[bool]] = mapped_column(Boolean)