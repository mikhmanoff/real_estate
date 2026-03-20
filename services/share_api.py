# services/share_api.py
"""
Endpoints for media sharing via Telegram's shareMessage API.
Uses savePreparedInlineMessage to prepare a photo+caption message
that the Mini App can then share via WebApp.shareMessage().
"""
import os
import json
import hashlib
import hmac
import httpx
from urllib.parse import parse_qs
from typing import Optional
from fastapi import APIRouter, Query, HTTPException
from sqlalchemy import select

from database import get_session
from database.models import Listing, Post, Media
from services.share_api import router as share_router

router = APIRouter()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


def validate_telegram_data(init_data: str) -> Optional[int]:
    """Validates Telegram initData, returns user_id or None."""
    if not init_data or not BOT_TOKEN:
        return None
    try:
        parsed = parse_qs(init_data)
        received_hash = parsed.get('hash', [''])[0]
        if not received_hash:
            return None
        
        data_check_arr = []
        for key, value in parsed.items():
            if key != 'hash':
                data_check_arr.append(f"{key}={value[0]}")
        data_check_arr.sort()
        data_check_string = '\n'.join(data_check_arr)
        
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if calculated_hash != received_hash:
            return None
        
        user_json = parsed.get('user', ['{}'])[0]
        user_data = json.loads(user_json)
        return user_data.get('id')
    except Exception as e:
        print(f"Telegram validation error: {e}")
        return None


def build_photo_url(local_path: str) -> Optional[str]:
    """Converts local media path to a full public URL."""
    if not local_path:
        return None
    
    from pathlib import Path
    path = Path(local_path)
    parts = path.parts
    if len(parts) < 2:
        return None
    
    channel_dir = parts[-2]
    filename = parts[-1]
    
    # Build full URL using API base
    api_base = os.getenv("API_PUBLIC_URL") or os.getenv("RAILWAY_PUBLIC_DOMAIN")
    if api_base:
        if not api_base.startswith("http"):
            api_base = f"https://{api_base}"
        return f"{api_base}/media/{channel_dir}/{filename}"
    
    return None


@router.post("/api/prepare-share/{listing_id}")
async def prepare_share_message(
    listing_id: int,
    init_data: str = Query(..., description="Telegram initData"),
):
    """
    Prepares an inline message with photo+caption for sharing via WebApp.shareMessage().
    
    Flow:
    1. Get listing data + first photo URL
    2. Call Bot API savePreparedInlineMessage with InlineQueryResultPhoto
    3. Return prepared_message_id to frontend
    """
    user_id = validate_telegram_data(init_data)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid Telegram auth")
    
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN not configured")
    
    # Get listing data
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
        
        # Get first photo
        media_query = select(Media).where(Media.post_id == post.id).limit(1)
        media_result = await session.execute(media_query)
        media = media_result.scalar_one_or_none()
        
        photo_url = build_photo_url(media.local_path) if media else None
    
    # Build caption
    bot_username = os.getenv("BOT_USERNAME", "rentaly_bot")
    
    price_str = ""
    if listing.price:
        price = int(listing.price)
        currency = listing.currency or "usd"
        if currency == "usd":
            price_str = f"${price:,}".replace(",", " ")
        else:
            price_str = f"{price:,} сум".replace(",", " ")
    
    caption_lines = []
    
    # Main info
    parts = []
    if listing.rooms:
        parts.append(f"{listing.rooms} комн")
    if listing.area_m2:
        parts.append(f"{int(listing.area_m2)} м²")
    if listing.floor and listing.total_floors:
        parts.append(f"{listing.floor}/{listing.total_floors} этаж")
    
    if parts:
        caption_lines.append(f"🏠 {', '.join(parts)}")
    
    if price_str:
        caption_lines.append(f"💰 {price_str}/мес")
    
    if listing.district_raw:
        caption_lines.append(f"📍 {listing.district_raw}")
    
    # Amenities
    amenities = []
    if listing.has_furniture:
        amenities.append("Мебель")
    if listing.has_conditioner:
        amenities.append("Кондиционер")
    if amenities:
        caption_lines.append(f"✅ {' · '.join(amenities)}")
    
    caption_lines.append("")
    caption_lines.append(f"🔍 Найди квартиру мечты в Ташкенте в @{bot_username}")
    
    caption = "\n".join(caption_lines)
    
    # Call Bot API savePreparedInlineMessage
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            if photo_url:
                # InlineQueryResultPhoto
                inline_result = {
                    "type": "photo",
                    "id": f"share_{listing_id}",
                    "photo_url": photo_url,
                    "thumbnail_url": photo_url,
                    "caption": caption,
                    "parse_mode": "HTML",
                }
            else:
                # Fallback: InlineQueryResultArticle (text only)
                inline_result = {
                    "type": "article",
                    "id": f"share_{listing_id}",
                    "title": f"Квартира {price_str}/мес",
                    "input_message_content": {
                        "message_text": caption,
                        "parse_mode": "HTML",
                    },
                }
            
            payload = {
                "user_id": user_id,
                "result": inline_result,
                "allow_user_chats": True,
                "allow_bot_chats": True,
                "allow_group_chats": True,
                "allow_channel_chats": True,
            }
            
            resp = await client.post(
                f"{BOT_API_URL}/savePreparedInlineMessage",
                json=payload,
            )
            
            data = resp.json()
            
            if not data.get("ok"):
                print(f"[share] Bot API error: {data}")
                raise HTTPException(
                    status_code=502, 
                    detail=f"Bot API error: {data.get('description', 'unknown')}"
                )
            
            prepared = data["result"]
            return {
                "prepared_message_id": prepared["id"],
                "expiration_date": prepared.get("expiration_date"),
            }
    
    except httpx.RequestError as e:
        print(f"[share] HTTP error: {e}")
        raise HTTPException(status_code=502, detail="Failed to contact Bot API")