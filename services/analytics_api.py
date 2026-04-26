# services/analytics_api.py
"""
Analytics API - tracking sessions and events.
"""
import os
import json
from urllib.parse import parse_qs
from typing import Optional, List
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from database import get_session

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def extract_user_id(init_data: str) -> Optional[int]:
    if not init_data:
        return None
    try:
        parsed = parse_qs(init_data)
        user_json = parsed.get('user', ['{}'])[0]
        user_data = json.loads(user_json)
        return user_data.get('id')
    except:
        return None


class SessionStart(BaseModel):
    session_id: str
    device_info: Optional[dict] = None

class EventBatch(BaseModel):
    session_id: str
    events: List[dict]

class SessionEnd(BaseModel):
    session_id: str
    duration_sec: int


@router.post("/session/start")
async def start_session(body: SessionStart, init_data: str = Query("")):
    user_id = extract_user_id(init_data)
    async with get_session() as session:
        await session.execute(
            text("""
                INSERT INTO analytics_sessions (id, telegram_user_id, device_info)
                VALUES (:id, :user_id, :device_info)
                ON CONFLICT (id) DO UPDATE SET last_active_at = NOW()
            """),
            {
                "id": body.session_id,
                "user_id": user_id,
                "device_info": json.dumps(body.device_info) if body.device_info else None,
            }
        )
    return {"ok": True}


@router.post("/events")
async def track_events(body: EventBatch, init_data: str = Query("")):
    user_id = extract_user_id(init_data)
    if not body.events:
        return {"ok": True, "tracked": 0}

    async with get_session() as session:
        for event in body.events:
            event_type = event.get("type", "unknown")
            event_data = {k: v for k, v in event.items() if k not in ("type", "listing_id", "timestamp")}
            listing_id = event.get("listing_id")

            await session.execute(
                text("""
                    INSERT INTO analytics_events (session_id, telegram_user_id, event_type, event_data, listing_id)
                    VALUES (:session_id, :user_id, :event_type, :event_data, :listing_id)
                """),
                {
                    "session_id": body.session_id,
                    "user_id": user_id,
                    "event_type": event_type,
                    "event_data": json.dumps(event_data) if event_data else None,
                    "listing_id": listing_id,
                }
            )

        await session.execute(
            text("""
                UPDATE analytics_sessions SET
                    last_active_at = NOW(),
                    events_count = events_count + :count,
                    listings_viewed = listings_viewed + :views,
                    details_opened = details_opened + :details,
                    contacts_clicked = contacts_clicked + :contacts,
                    shares_count = shares_count + :shares,
                    favorites_added = favorites_added + :favs,
                    filters_applied = filters_applied + :filters
                WHERE id = :session_id
            """),
            {
                "session_id": body.session_id,
                "count": len(body.events),
                "views": sum(1 for e in body.events if e.get("type") == "listing_view"),
                "details": sum(1 for e in body.events if e.get("type") == "listing_details"),
                "contacts": sum(1 for e in body.events if e.get("type") in ("contact_message", "contact_call")),
                "shares": sum(1 for e in body.events if e.get("type") in ("share_click", "share_sent")),
                "favs": sum(1 for e in body.events if e.get("type") == "favorite_add"),
                "filters": sum(1 for e in body.events if e.get("type") == "filter_apply"),
            }
        )

    return {"ok": True, "tracked": len(body.events)}


@router.post("/session/end")
async def end_session(body: SessionEnd, init_data: str = Query("")):
    async with get_session() as session:
        await session.execute(
            text("""
                UPDATE analytics_sessions SET duration_sec = :duration, last_active_at = NOW()
                WHERE id = :session_id
            """),
            {"session_id": body.session_id, "duration": body.duration_sec}
        )
    return {"ok": True}


@router.get("/dashboard/summary")
async def dashboard_summary(days: int = Query(7, ge=1, le=90)):
    async with get_session() as session:
        result = await session.execute(
            text("""
                SELECT
                    COUNT(DISTINCT s.id) as total_sessions,
                    COUNT(DISTINCT s.telegram_user_id) as unique_users,
                    COALESCE(AVG(s.duration_sec), 0) as avg_duration_sec,
                    COALESCE(AVG(s.listings_viewed), 0) as avg_listings_viewed,
                    COALESCE(SUM(s.contacts_clicked), 0) as total_contacts,
                    COALESCE(SUM(s.shares_count), 0) as total_shares,
                    COALESCE(SUM(s.favorites_added), 0) as total_favorites,
                    COALESCE(SUM(s.events_count), 0) as total_events
                FROM analytics_sessions s
                WHERE s.started_at > NOW() - INTERVAL '1 day' * :days
            """),
            {"days": days}
        )
        row = result.fetchone()

        funnel = await session.execute(
            text("""
                SELECT
                    COUNT(DISTINCT CASE WHEN event_type = 'app_open' THEN session_id END) as opened,
                    COUNT(DISTINCT CASE WHEN event_type = 'listing_view' THEN session_id END) as viewed_listing,
                    COUNT(DISTINCT CASE WHEN event_type = 'listing_details' THEN session_id END) as opened_details,
                    COUNT(DISTINCT CASE WHEN event_type IN ('contact_message', 'contact_call') THEN session_id END) as contacted,
                    COUNT(DISTINCT CASE WHEN event_type = 'share_sent' THEN session_id END) as shared
                FROM analytics_events
                WHERE created_at > NOW() - INTERVAL '1 day' * :days
            """),
            {"days": days}
        )
        funnel_row = funnel.fetchone()

        return {
            "period_days": days,
            "sessions": {
                "total": row[0], "unique_users": row[1],
                "avg_duration_sec": round(float(row[2])),
                "avg_listings_viewed": round(float(row[3]), 1),
            },
            "totals": {
                "contacts_clicked": row[4], "shares": row[5],
                "favorites": row[6], "events": row[7],
            },
            "funnel": {
                "opened_app": funnel_row[0], "viewed_listing": funnel_row[1],
                "opened_details": funnel_row[2], "contacted": funnel_row[3],
                "shared": funnel_row[4],
            },
        }


@router.get("/dashboard/popular-listings")
async def popular_listings(days: int = Query(7, ge=1, le=90), limit: int = Query(10, ge=1, le=50)):
    async with get_session() as session:
        result = await session.execute(
            text("""
                SELECT
                    e.listing_id,
                    COUNT(*) FILTER (WHERE e.event_type = 'listing_view') as views,
                    COUNT(*) FILTER (WHERE e.event_type IN ('contact_message', 'contact_call')) as contacts,
                    COUNT(*) FILTER (WHERE e.event_type = 'favorite_add') as favorites,
                    COUNT(*) FILTER (WHERE e.event_type = 'share_sent') as shares
                FROM analytics_events e
                WHERE e.listing_id IS NOT NULL
                  AND e.created_at > NOW() - INTERVAL '1 day' * :days
                GROUP BY e.listing_id
                ORDER BY views DESC
                LIMIT :limit
            """),
            {"days": days, "limit": limit}
        )
        return [
            {"listing_id": r[0], "views": r[1], "contacts": r[2], "favorites": r[3], "shares": r[4]}
            for r in result.fetchall()
        ]