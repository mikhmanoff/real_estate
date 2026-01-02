# services/api.py
"""
REST API для Telegram Mini App.
"""
import os
import hashlib
import hmac
from urllib.parse import parse_qs
from typing import Optional, List
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pathlib import Path
from pydantic import BaseModel
from decimal import Decimal

from database import get_session, PostRepo, ListingRepo, MediaRepo
from sqlalchemy import select, and_, or_, func
from database.models import Post, Listing, Media, Channel, District, MetroStation, Favorite


app = FastAPI(title="Rent Finder API")
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "/app/downloads"))
BOT_TOKEN = os.getenv("BOT_TOKEN", "")  # Токен твоего бота

""" # Монтируем папку с медиа
DOWNLOAD_DIR = Path("/home/mikhmanoff/project/downloads")
if DOWNLOAD_DIR.exists():
    app.mount("/media", StaticFiles(directory=str(DOWNLOAD_DIR)), name="media")
 """
# CORS для Mini App
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В проде указать домен Mini App
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def validate_telegram_data(init_data: str) -> Optional[int]:
    """
    Валидирует initData из Telegram и возвращает user_id.
    Возвращает None если данные невалидны.
    """
    if not init_data or not BOT_TOKEN:
        return None
    
    try:
        parsed = parse_qs(init_data)
        
        # Получаем hash
        received_hash = parsed.get('hash', [''])[0]
        if not received_hash:
            return None
        
        # Собираем строку для проверки
        data_check_arr = []
        for key, value in parsed.items():
            if key != 'hash':
                data_check_arr.append(f"{key}={value[0]}")
        data_check_arr.sort()
        data_check_string = '\n'.join(data_check_arr)
        
        # Создаём secret key
        secret_key = hmac.new(
            b"WebAppData", 
            BOT_TOKEN.encode(), 
            hashlib.sha256
        ).digest()
        
        # Проверяем подпись
        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()
        
        if calculated_hash != received_hash:
            return None
        
        # Извлекаем user_id из user JSON
        import json
        user_json = parsed.get('user', ['{}'])[0]
        user_data = json.loads(user_json)
        return user_data.get('id')
        
    except Exception as e:
        print(f"Telegram validation error: {e}")
        return None


# ============================================
# FAVORITES ENDPOINTS
# ============================================

@app.get("/api/favorites")
async def get_favorites(
    init_data: str = Query(..., description="Telegram initData"),
):
    """Получить список избранных объявлений пользователя."""
    user_id = validate_telegram_data(init_data)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid Telegram auth")
    
    async with get_session() as session:
        query = (
            select(Favorite.listing_id)
            .where(Favorite.telegram_user_id == user_id)
        )
        result = await session.execute(query)
        favorite_ids = [row[0] for row in result.fetchall()]
        
        return {"favorites": favorite_ids}


@app.post("/api/favorites/{listing_id}")
async def add_favorite(
    listing_id: int,
    init_data: str = Query(..., description="Telegram initData"),
):
    """Добавить объявление в избранное."""
    user_id = validate_telegram_data(init_data)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid Telegram auth")
    
    async with get_session() as session:
        # Проверяем, существует ли listing
        listing = await session.get(Listing, listing_id)
        if not listing:
            raise HTTPException(status_code=404, detail="Listing not found")
        
        # Проверяем, не добавлен ли уже
        existing = await session.execute(
            select(Favorite).where(
                Favorite.telegram_user_id == user_id,
                Favorite.listing_id == listing_id
            )
        )
        if existing.scalar_one_or_none():
            return {"status": "already_exists"}
        
        # Добавляем
        favorite = Favorite(
            telegram_user_id=user_id,
            listing_id=listing_id
        )
        session.add(favorite)
        
        return {"status": "added"}


@app.delete("/api/favorites/{listing_id}")
async def remove_favorite(
    listing_id: int,
    init_data: str = Query(..., description="Telegram initData"),
):
    """Удалить объявление из избранного."""
    user_id = validate_telegram_data(init_data)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid Telegram auth")
    
    async with get_session() as session:
        result = await session.execute(
            select(Favorite).where(
                Favorite.telegram_user_id == user_id,
                Favorite.listing_id == listing_id
            )
        )
        favorite = result.scalar_one_or_none()
        
        if favorite:
            await session.delete(favorite)
            return {"status": "removed"}
        
        return {"status": "not_found"}


@app.get("/api/favorites/count/{listing_id}")
async def get_favorites_count(listing_id: int):
    """Получить количество добавлений в избранное."""
    async with get_session() as session:
        result = await session.execute(
            select(func.count(Favorite.id)).where(Favorite.listing_id == listing_id)
        )
        count = result.scalar() or 0
        return {"count": count}


# ============================================
# RESPONSE MODELS
# ============================================

class ListingResponse(BaseModel):
    id: int
    post_id: int
    
    # Price
    price: Optional[float]
    currency: Optional[str]
    price_period: Optional[str]
    deposit: Optional[float]
    
    # Basic info
    rooms: Optional[int]
    area: Optional[float]
    floor: Optional[int]
    total_floors: Optional[int]
    
    # Location
    district: Optional[str]
    metro: Optional[str]
    address: Optional[str]
    
    # Features
    deal_type: Optional[str]
    object_type: Optional[str]
    has_furniture: Optional[bool]
    has_conditioner: Optional[bool]
    has_commission: bool
    
    # Media
    photos: List[str]
    
    # Text
    description: Optional[str]
    
    # Contacts
    phones: List[str]
    
    # Stats
    views_today: int = 0
    favorites_count: int = 0
    
    # Timestamps
    published_at: str

    class Config:
        from_attributes = True

class ListingsPageResponse(BaseModel):
    items: List[ListingResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class DistrictResponse(BaseModel):
    id: int
    name_ru: str
    name_uz: Optional[str]


class MetroResponse(BaseModel):
    id: int
    name_ru: str
    name_uz: Optional[str]
    line_name: Optional[str]
    line_color: Optional[str]


class StatsResponse(BaseModel):
    total_listings: int
    active_listings: int
    channels_count: int


# ============================================
# ENDPOINTS
# ============================================

@app.get("/")
async def root():
    return {"status": "ok", "service": "Rent Finder API"}


# Кастомный endpoint для медиа с CORS
@app.get("/media/{channel_id}/{filename}")
async def get_media(channel_id: str, filename: str):
    """Отдаёт медиа файлы с CORS заголовками."""
    file_path = DOWNLOAD_DIR / channel_id / filename
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    # Определяем content-type
    suffix = file_path.suffix.lower()
    media_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg", 
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
    }
    media_type = media_types.get(suffix, "application/octet-stream")
    
    return FileResponse(
        file_path,
        media_type=media_type,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "public, max-age=86400"
        }
    )


@app.get("/api/listings", response_model=ListingsPageResponse)
async def get_listings(
    # Filters
    deal_type: Optional[str] = Query(None, description="rent_long, rent_daily, sale"),
    object_type: Optional[str] = Query(None, description="flat, room, house"),
    rooms: Optional[str] = Query(None, description="Комнаты: 1,2,3 или studio,1,2"),
    price_min: Optional[float] = Query(None),
    price_max: Optional[float] = Query(None),
    currency: Optional[str] = Query("usd", description="usd или uzs"),
    district: Optional[str] = Query(None, description="ID районов через запятую"),
    metro: Optional[str] = Query(None, description="ID метро через запятую"),
    
    # Pagination
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Получить список объявлений с фильтрами."""
    
    async with get_session() as session:
        # Base query
        query = (
            select(Listing, Post, Media)
            .join(Post, Listing.post_id == Post.id)
            .outerjoin(Media, and_(Media.post_id == Post.id))
            .where(
                Listing.is_real_estate == True,
                Post.is_deleted == False,
                Post.duplicate_of.is_(None),
            )
        )
        
        # Apply filters
        conditions = []
        
        if deal_type:
            conditions.append(Listing.deal_type == deal_type)
        
        if object_type:
            conditions.append(Listing.object_type == object_type)
        
        if rooms:
            room_list = []
            for r in rooms.split(","):
                r = r.strip().lower()
                if r == "studio":
                    room_list.append(0)
                elif r.endswith("+"):
                    # 4+ значит >= 4
                    pass  # handled separately
                else:
                    try:
                        room_list.append(int(r))
                    except ValueError:
                        pass
            if room_list:
                conditions.append(Listing.rooms.in_(room_list))
        
        if price_min is not None:
            conditions.append(Listing.price >= price_min)
        
        if price_max is not None:
            conditions.append(Listing.price <= price_max)
        
        if currency:
            conditions.append(Listing.currency == currency.lower())
        
        if district:
            district_ids = [int(d.strip()) for d in district.split(",") if d.strip().isdigit()]
            if district_ids:
                conditions.append(Listing.district_id.in_(district_ids))
        
        if metro:
            metro_ids = [int(m.strip()) for m in metro.split(",") if m.strip().isdigit()]
            if metro_ids:
                conditions.append(Listing.metro_id.in_(metro_ids))
        
        if conditions:
            query = query.where(and_(*conditions))
        
        # Count total
        count_query = (
            select(func.count(Listing.id))
            .join(Post, Listing.post_id == Post.id)
            .where(
                Listing.is_real_estate == True,
                Post.is_deleted == False,
                Post.duplicate_of.is_(None),
            )
        )
        if conditions:
            count_query = count_query.where(and_(*conditions))
        
        total_result = await session.execute(count_query)
        total = total_result.scalar() or 0
        
        # Order and paginate
        query = (
            query
            .order_by(Post.published_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        
        result = await session.execute(query)
        rows = result.all()
        
        # Group media by post_id
        listings_map = {}
        for listing, post, media in rows:
            if listing.id not in listings_map:
                listings_map[listing.id] = {
                    "listing": listing,
                    "post": post,
                    "photos": []
                }
            if media and media.local_path:
                # Convert local path to URL (настроить под свой CDN/static)
                parts = media.local_path.split('/')
                photo_url = f"/media/{parts[-2]}/{parts[-1]}"
                if photo_url not in listings_map[listing.id]["photos"]:
                    listings_map[listing.id]["photos"].append(photo_url)
        
        # Build response
        items = []
        for data in listings_map.values():
            listing = data["listing"]
            post = data["post"]
            photos = data["photos"]
            
            items.append(ListingResponse(
                id=listing.id,
                post_id=post.id,
                price=float(listing.price) if listing.price else None,
                currency=listing.currency,
                price_period=listing.price_period,
                deposit=float(listing.deposit) if listing.deposit else None,
                rooms=listing.rooms,
                area=float(listing.area_m2) if listing.area_m2 else None,
                floor=listing.floor,
                total_floors=listing.total_floors,
                district=listing.district_raw,
                metro=listing.metro_raw,
                address=listing.district_raw,  # TODO: улучшить
                deal_type=listing.deal_type,
                object_type=listing.object_type,
                has_furniture=listing.has_furniture,
                has_conditioner=listing.has_conditioner,
                has_commission=listing.has_commission,
                photos=photos or ["https://via.placeholder.com/800x600?text=No+Photo"],
                description=post.text_raw[:500] if post.text_raw else None,
                phones=post.phones or [],
                views_today=0,  # TODO: реализовать счётчик
                favorites_count=0,  # TODO: реализовать
                published_at=post.published_at.isoformat(),
            ))
        
        return ListingsPageResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            has_more=(page * page_size) < total,
        )


@app.get("/api/listings/{listing_id}", response_model=ListingResponse)
async def get_listing(listing_id: int):
    """Получить одно объявление по ID."""
    async with get_session() as session:
        query = (
            select(Listing, Post)
            .join(Post, Listing.post_id == Post.id)
            .where(Listing.id == listing_id)
        )
        result = await session.execute(query)
        row = result.first()
        
        if not row:
            raise HTTPException(status_code=404, detail="Listing not found")
        
        listing, post = row
        
        # Get photos
        media_query = select(Media).where(Media.post_id == post.id)
        media_result = await session.execute(media_query)
        photos = []
        for m in media_result.scalars():
            if m.local_path:
                parts = m.local_path.split('/')
                photos.append(f"/media/{parts[-2]}/{parts[-1]}")
        
        return ListingResponse(
            id=listing.id,
            post_id=post.id,
            price=float(listing.price) if listing.price else None,
            currency=listing.currency,
            price_period=listing.price_period,
            deposit=float(listing.deposit) if listing.deposit else None,
            rooms=listing.rooms,
            area=float(listing.area_m2) if listing.area_m2 else None,
            floor=listing.floor,
            total_floors=listing.total_floors,
            district=listing.district_raw,
            metro=listing.metro_raw,
            address=listing.district_raw,
            deal_type=listing.deal_type,
            object_type=listing.object_type,
            has_furniture=listing.has_furniture,
            has_conditioner=listing.has_conditioner,
            has_commission=listing.has_commission,
            photos=photos or ["https://via.placeholder.com/800x600?text=No+Photo"],
            description=post.text_raw,
            phones=post.phones or [],
            views_today=0,
            favorites_count=0,
            published_at=post.published_at.isoformat(),
        )


@app.get("/api/districts", response_model=List[DistrictResponse])
async def get_districts():
    """Список районов."""
    async with get_session() as session:
        result = await session.execute(select(District).order_by(District.name_ru))
        return [DistrictResponse(id=d.id, name_ru=d.name_ru, name_uz=d.name_uz) for d in result.scalars()]


@app.get("/api/metro", response_model=List[MetroResponse])
async def get_metro_stations():
    """Список станций метро."""
    async with get_session() as session:
        result = await session.execute(select(MetroStation).order_by(MetroStation.line_name, MetroStation.name_ru))
        return [
            MetroResponse(
                id=m.id, 
                name_ru=m.name_ru, 
                name_uz=m.name_uz,
                line_name=m.line_name,
                line_color=m.line_color
            ) 
            for m in result.scalars()
        ]


@app.get("/api/stats", response_model=StatsResponse)
async def get_stats():
    """Общая статистика."""
    async with get_session() as session:
        total = await session.execute(select(func.count(Listing.id)))
        active = await session.execute(
            select(func.count(Listing.id))
            .join(Post, Listing.post_id == Post.id)
            .where(Post.is_deleted == False)
        )
        channels = await session.execute(select(func.count(Channel.id)))
        
        return StatsResponse(
            total_listings=total.scalar() or 0,
            active_listings=active.scalar() or 0,
            channels_count=channels.scalar() or 0,
        )