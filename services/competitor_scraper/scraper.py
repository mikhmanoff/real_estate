# services/competitor_scraper/scraper.py
"""
Основная логика сбора. Один цикл = полный обход всех страниц.

Что делает один цикл (run):
1. Создаёт запись в competitor_scrape_runs (status=running).
2. Идёт по страницам list-combined постранично.
3. Для каждого item:
   - upsert seller
   - upsert listing (last_seen_at = now, disappeared_at -> NULL если был помечен)
   - upsert media (главное фото)
   - запись в competitor_snapshots
   - если фото ещё не скачано — скачать, посчитать phash, сохранить
4. После полного прохода — помечает disappeared_at для тех, кого не видел в этом run.
5. Закрывает run (status=done).
"""
import asyncio
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import select, update, and_, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database.connection import get_session
from database.competitor_models import (
    CompetitorListing,
    CompetitorMedia,
    CompetitorScrapeRun,
    CompetitorSeller,
    CompetitorSnapshot,
)

from .api_client import JoymiClient, DEFAULT_DELAY_SEC, DEFAULT_PER_PAGE
from .parser import parse_listing
from .media import (
    compute_phash,
    get_image_dimensions,
    relative_path,
    save_image,
)


# Если хочется ограничить парсинг только Ташкент-шахри — выставить TASHKENT_ONLY=1
TASHKENT_ONLY = os.getenv("TASHKENT_ONLY", "0") == "1"

# Максимум страниц за один цикл (предохранитель от runaway)
MAX_PAGES = int(os.getenv("JOYMI_MAX_PAGES", "20000"))

# Сколько фото скачивать параллельно
MEDIA_CONCURRENCY = int(os.getenv("JOYMI_MEDIA_CONCURRENCY", "5"))


def _is_tashkent(item: Dict[str, Any]) -> bool:
    addr = (item.get("address_line") or "").lower()
    return "toshkent shahri" in addr or "ташкент" in addr


def _to_decimal(v) -> Optional[Decimal]:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


# ============================================
# UPSERTS
# ============================================

async def upsert_seller(session, raw: Dict[str, Any]) -> Optional[int]:
    if not raw:
        return None
    sid = raw.get("id")
    if sid is None:
        return None

    stmt = pg_insert(CompetitorSeller).values(
        id=sid,
        uuid=raw.get("uuid"),
        profile_name=raw.get("profile_name"),
        avatar_url=raw.get("images") if isinstance(raw.get("images"), str) else None,
    ).on_conflict_do_update(
        index_elements=[CompetitorSeller.id],
        set_={
            "profile_name": raw.get("profile_name"),
            "avatar_url": raw.get("images") if isinstance(raw.get("images"), str) else None,
            "last_seen_at": func.now(),
        },
    )
    await session.execute(stmt)
    return sid


async def upsert_listing(session, item: Dict[str, Any], parsed: Dict[str, Any], seller_id: Optional[int]) -> bool:
    """
    Returns True if this is the first time we see this listing (was new).
    """
    listing_id = item["id"]

    # Парсим created_at_remote
    created_at_remote = None
    if item.get("created_at"):
        try:
            created_at_remote = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass

    values = {
        "id": listing_id,
        "uuid": item.get("uuid"),
        "slug": item.get("slug"),
        "title": item.get("title"),

        "price_raw": _to_decimal(item.get("price")),
        "currency_raw": item.get("currency"),
        "currency": parsed.get("currency"),
        "price": _to_decimal(item.get("price")),
        "deal_type": parsed.get("deal_type"),

        "object_type": parsed.get("object_type"),
        "rooms": parsed.get("rooms"),
        "floor": parsed.get("floor"),
        "total_floors": parsed.get("total_floors"),
        "area_m2": _to_decimal(parsed.get("area_m2")),
        "area_sotok": _to_decimal(parsed.get("area_sotok")),

        "address_line": item.get("address_line"),
        "region": parsed.get("region"),
        "district_raw": parsed.get("district_raw"),
        "district_norm": parsed.get("district_norm"),

        "seller_id": seller_id,

        "is_vip": item.get("is_vip"),
        "is_top": item.get("is_top"),
        "is_raised": item.get("is_raised"),
        "status": item.get("status"),

        "created_at_remote": created_at_remote,
        "parse_score": parsed.get("parse_score"),
        "needs_review": parsed.get("needs_review"),
    }

    stmt = pg_insert(CompetitorListing).values(**values)
    # On conflict — update everything except first_seen_at, и ресетим disappeared_at
    update_set = {k: v for k, v in values.items() if k != "id"}
    update_set["last_seen_at"] = func.now()
    update_set["disappeared_at"] = None  # появился снова — снять метку
    stmt = stmt.on_conflict_do_update(
        index_elements=[CompetitorListing.id],
        set_=update_set,
    ).returning(CompetitorListing.first_seen_at, CompetitorListing.last_seen_at)

    result = await session.execute(stmt)
    row = result.fetchone()
    if row is None:
        return False
    first_seen, last_seen = row
    # NOW() в одной транзакции одинаков для всех вызовов, поэтому
    # при INSERT first_seen == last_seen. При UPDATE first_seen не трогается,
    # last_seen обновляется → они разные.
    return first_seen == last_seen


async def upsert_main_media(session, listing_id: int, image: Dict[str, Any]) -> Optional[int]:
    """Upsert по (listing_id, remote_url). Возвращает id записи media."""
    if not image or not image.get("url"):
        return None

    values = {
        "listing_id": listing_id,
        "remote_id": image.get("id"),
        "file_id": image.get("file_id"),
        "remote_url": image["url"],
        "is_main": bool(image.get("is_main")),
        "sort_order": int(image.get("order") or 0),
    }
    stmt = pg_insert(CompetitorMedia).values(**values).on_conflict_do_update(
        index_elements=[CompetitorMedia.listing_id, CompetitorMedia.remote_url],
        set_={
            "is_main": values["is_main"],
            "sort_order": values["sort_order"],
            "remote_id": values["remote_id"],
            "file_id": values["file_id"],
        },
    ).returning(CompetitorMedia.id, CompetitorMedia.download_status)
    result = await session.execute(stmt)
    row = result.fetchone()
    return row[0] if row else None


async def record_snapshot(session, run_id: int, listing_id: int, page_index: int, item: Dict[str, Any]) -> None:
    stmt = pg_insert(CompetitorSnapshot).values(
        run_id=run_id,
        listing_id=listing_id,
        page_index=page_index,
        is_vip=item.get("is_vip"),
        is_top=item.get("is_top"),
        is_raised=item.get("is_raised"),
    ).on_conflict_do_nothing(index_elements=[CompetitorSnapshot.run_id, CompetitorSnapshot.listing_id])
    await session.execute(stmt)


# ============================================
# MEDIA DOWNLOAD
# ============================================

async def download_one_media(client, media_id: int) -> Dict[str, Any]:
    """
    Скачивает фото для одной записи media (если ещё не скачано).
    Возвращает {ok: bool, error: str|None}.
    """
    async with get_session() as session:
        # Возьмём актуальное состояние записи
        m = await session.get(CompetitorMedia, media_id)
        if m is None:
            return {"ok": False, "error": "not found"}
        if m.download_status == "ok" and m.local_path:
            return {"ok": True, "error": None, "skipped": True}

        url = m.remote_url
        listing_id = m.listing_id
        sort_order = m.sort_order or 0

    # Скачивание — без сессии БД
    data = await client.download_image(url)
    if data is None:
        async with get_session() as session:
            await session.execute(
                update(CompetitorMedia)
                .where(CompetitorMedia.id == media_id)
                .values(download_status="failed", download_error="http error")
            )
        return {"ok": False, "error": "http error"}

    # Сохраняем + считаем pHash + размеры
    try:
        path = save_image(data, listing_id, sort_order, url)
        rel = relative_path(path)
        phash = compute_phash(data)
        w, h = get_image_dimensions(data)
        size = len(data)
    except Exception as e:
        async with get_session() as session:
            await session.execute(
                update(CompetitorMedia)
                .where(CompetitorMedia.id == media_id)
                .values(download_status="failed", download_error=str(e)[:500])
            )
        return {"ok": False, "error": str(e)}

    async with get_session() as session:
        await session.execute(
            update(CompetitorMedia)
            .where(CompetitorMedia.id == media_id)
            .values(
                local_path=rel,
                phash=phash,
                width=w,
                height=h,
                file_size=size,
                download_status="ok",
                download_error=None,
            )
        )
    return {"ok": True, "error": None}


async def download_media_batch(client, media_ids: List[int]) -> Dict[str, int]:
    """Качает много фото параллельно с лимитом."""
    if not media_ids:
        return {"ok": 0, "failed": 0}

    sem = asyncio.Semaphore(MEDIA_CONCURRENCY)

    async def _one(mid):
        async with sem:
            return await download_one_media(client, mid)

    results = await asyncio.gather(*[_one(m) for m in media_ids], return_exceptions=True)

    ok = sum(1 for r in results if isinstance(r, dict) and r.get("ok"))
    failed = len(results) - ok
    return {"ok": ok, "failed": failed}


# ============================================
# MARK DISAPPEARED
# ============================================

async def mark_disappeared(run_started_at: datetime) -> int:
    """
    После полного прохода — listings, чей last_seen_at < run_started_at,
    больше не появляются на сайте → помечаем disappeared.
    """
    async with get_session() as session:
        result = await session.execute(
            update(CompetitorListing)
            .where(
                and_(
                    CompetitorListing.last_seen_at < run_started_at,
                    CompetitorListing.disappeared_at.is_(None),
                )
            )
            .values(disappeared_at=func.now())
            .returning(CompetitorListing.id)
        )
        rows = result.fetchall()
        return len(rows)


# ============================================
# MAIN CYCLE
# ============================================

async def run_one_cycle() -> Dict[str, Any]:
    """Полный цикл: один прогон по всем страницам joymi."""
    cycle_started = datetime.now(timezone.utc)

    # 1. Открываем run
    async with get_session() as session:
        run = CompetitorScrapeRun(status="running")
        session.add(run)
        await session.flush()
        run_id = run.id

    print(f"[scraper] === run {run_id} started at {cycle_started.isoformat()} ===")

    pages_fetched = 0
    listings_seen = 0
    listings_new = 0
    media_ids_to_download: List[int] = []
    error_message: Optional[str] = None

    try:
        async with JoymiClient() as client:
            page = 1
            while page <= MAX_PAGES:
                try:
                    payload = await client.get_page(page)
                except Exception as e:
                    print(f"[scraper] failed page {page}: {e}; aborting cycle")
                    error_message = f"page {page}: {e}"
                    break

                items = payload.get("results") or []
                if not items:
                    break

                pages_fetched += 1

                # Фильтр Ташкент (если включен)
                if TASHKENT_ONLY:
                    items = [i for i in items if _is_tashkent(i)]

                # Обрабатываем порциями в одной транзакции
                async with get_session() as session:
                    for item in items:
                        try:
                            parsed = parse_listing(item)
                            seller_id = await upsert_seller(session, item.get("seller") or {})
                            is_new = await upsert_listing(session, item, parsed, seller_id)
                            await record_snapshot(session, run_id, item["id"], page, item)
                            listings_seen += 1
                            if is_new:
                                listings_new += 1

                            # Главное фото
                            images = item.get("images") or []
                            if images:
                                main = next((im for im in images if im.get("is_main")), images[0])
                                mid = await upsert_main_media(session, item["id"], main)
                                if mid is not None:
                                    media_ids_to_download.append(mid)
                        except Exception as e:
                            print(f"[scraper] item {item.get('id')} failed: {e}")

                if pages_fetched % 20 == 0:
                    print(f"[scraper] page {page}: seen={listings_seen} new={listings_new} media_queued={len(media_ids_to_download)}")

                if not payload.get("has_next"):
                    break
                page += 1
                await asyncio.sleep(DEFAULT_DELAY_SEC)

            # 2. Качаем медиа (новые/не скачанные)
            print(f"[scraper] downloading media: {len(media_ids_to_download)} items...")
            stats = {"ok": 0, "failed": 0}
            if media_ids_to_download:
                # Берём только те, что ещё не скачаны (download_status != 'ok')
                async with get_session() as session:
                    pending_q = await session.execute(
                        select(CompetitorMedia.id).where(
                            and_(
                                CompetitorMedia.id.in_(media_ids_to_download),
                                CompetitorMedia.download_status != "ok",
                            )
                        )
                    )
                    pending_ids = [r[0] for r in pending_q.fetchall()]

                stats = await download_media_batch(client, pending_ids)
                print(f"[scraper] media: ok={stats['ok']} failed={stats['failed']}")

        # 3. Помечаем пропавшие
        disappeared = await mark_disappeared(cycle_started)
        print(f"[scraper] disappeared: {disappeared}")

        # 4. Закрываем run
        async with get_session() as session:
            await session.execute(
                update(CompetitorScrapeRun)
                .where(CompetitorScrapeRun.id == run_id)
                .values(
                    finished_at=func.now(),
                    status="done" if not error_message else "interrupted",
                    pages_fetched=pages_fetched,
                    listings_seen=listings_seen,
                    listings_new=listings_new,
                    listings_disappeared=disappeared,
                    media_downloaded=stats["ok"],
                    media_failed=stats["failed"],
                    error_message=error_message,
                )
            )

        result = {
            "run_id": run_id,
            "pages": pages_fetched,
            "seen": listings_seen,
            "new": listings_new,
            "disappeared": disappeared,
            "media_ok": stats["ok"],
            "media_failed": stats["failed"],
        }
        print(f"[scraper] === run {run_id} done: {result}")
        return result

    except Exception as e:
        print(f"[scraper] cycle FAILED: {e}")
        async with get_session() as session:
            await session.execute(
                update(CompetitorScrapeRun)
                .where(CompetitorScrapeRun.id == run_id)
                .values(
                    finished_at=func.now(),
                    status="failed",
                    pages_fetched=pages_fetched,
                    listings_seen=listings_seen,
                    listings_new=listings_new,
                    error_message=str(e)[:1000],
                )
            )
        raise