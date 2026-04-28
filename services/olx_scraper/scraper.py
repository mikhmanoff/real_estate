# services/olx_scraper/scraper.py
"""
Main OLX scraper cycle.

Stages within one run:
  1) List crawl: walk pagination of /nedvizhimost/kvartiry/arenda-dolgosrochnaya/
     and upsert minimal listings (title, region, district, preview price).
  2) Detail crawl: for each listing where detail_fetched_at is NULL or stale,
     fetch the detail page and update the row with full data + queue media.
  3) Media batch download: pHash + local save.
  4) (Optional) Phone reveal pass.
  5) Mark listings that weren't seen in this cycle as disappeared.

All disappeared/last_seen logic is scoped by `source='olx'` so this scraper
NEVER touches joymi rows.
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database.connection import get_session
from database.competitor_models import (
    CompetitorListing,
    CompetitorMedia,
    CompetitorScrapeRun,
    CompetitorSnapshot,
)

from .api_client import OlxClient, DEFAULT_DELAY_SEC, DETAIL_DELAY_SEC
from .parser import (
    DetailData,
    ListCard,
    get_total_pages,
    parse_detail_page,
    parse_list_page,
)
from .media import compute_phash, get_image_dimensions, relative_path, save_image
from .phones import REVEAL_ENABLED, normalize_phone, reveal_phone


# ============================================================
# Config
# ============================================================

MAX_LIST_PAGES = int(os.getenv("OLX_MAX_LIST_PAGES", "60"))
DETAIL_CONCURRENCY = int(os.getenv("OLX_DETAIL_CONCURRENCY", "3"))
MEDIA_CONCURRENCY = int(os.getenv("OLX_MEDIA_CONCURRENCY", "5"))
DETAIL_REFRESH_DAYS = int(os.getenv("OLX_DETAIL_REFRESH_DAYS", "7"))
MAX_PHOTOS_PER_LISTING = int(os.getenv("OLX_MAX_PHOTOS", "10"))
DOWNLOAD_MEDIA = os.getenv("OLX_DOWNLOAD_MEDIA", "1") == "1"


# ============================================================
# Helpers
# ============================================================

def _to_decimal(v) -> Optional[Decimal]:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


# ============================================================
# Listing upsert: from list card (minimal)
# ============================================================

async def upsert_listing_from_card(session, card: ListCard) -> Tuple[int, bool]:
    """
    Insert/update a listing row from a list-page card.
    Returns (listing.id, is_new).
    """
    values = {
        "source": "olx",
        "source_id": card.source_id,
        "url": card.url,
        "title": card.title,
        "price_raw": card.price,
        "price": card.price,
        "currency": card.currency,
        "region": card.region,
        "district_raw": card.district_raw,
        "district_norm": card.district_norm,
        "is_top": card.is_promoted,
        "deal_type": "rent_long",
        "object_type": "flat",
        "address_line": ", ".join(p for p in (card.region, card.district_raw) if p) or None,
    }

    update_set = {k: v for k, v in values.items() if k not in ("source", "source_id")}
    update_set["last_seen_at"] = func.now()
    update_set["disappeared_at"] = None

    stmt = pg_insert(CompetitorListing).values(**values).on_conflict_do_update(
        index_elements=[CompetitorListing.source, CompetitorListing.source_id],
        set_=update_set,
    ).returning(
        CompetitorListing.id,
        CompetitorListing.first_seen_at,
        CompetitorListing.last_seen_at,
    )
    result = await session.execute(stmt)
    row = result.fetchone()
    if row is None:
        # Should not happen with ON CONFLICT DO UPDATE … RETURNING
        raise RuntimeError(f"upsert returned no row for {card.source_id}")
    listing_id, first_seen, last_seen = row
    return int(listing_id), (first_seen == last_seen)


# ============================================================
# Listing upsert: from detail page (full)
# ============================================================

async def update_listing_from_detail(session, listing_id: int, data: DetailData) -> None:
    values: Dict[str, Any] = {
        "title": data.title or None,
        "price": data.price,
        "price_raw": data.price,
        "currency": data.currency,
        "rooms": data.rooms,
        "floor": data.floor,
        "total_floors": data.total_floors,
        "area_m2": data.area_m2,
        "region": data.region,
        "district_raw": data.district_raw,
        "district_norm": data.district_norm,
        "address_line": data.address_line,
        "created_at_remote": data.created_at_remote,
        "parse_score": data.parse_score,
        "needs_review": data.needs_review,
        "metadata_": data.metadata(),
        "detail_fetched_at": func.now(),
    }
    # Phones from description text — first pass
    if data.phones_in_text:
        values["contact_phone"] = data.phones_in_text[0]
        if len(data.phones_in_text) > 1:
            values["contact_phones_extra"] = data.phones_in_text[1:]

    # Drop None-valued keys to avoid clobbering existing data with NULL
    clean = {k: v for k, v in values.items() if v is not None or k in ("metadata_",)}

    if not clean:
        return

    await session.execute(
        update(CompetitorListing)
        .where(CompetitorListing.id == listing_id)
        .values(**clean)
    )


async def upsert_media_urls(session, listing_id: int, urls: List[str]) -> List[int]:
    """Insert media rows (URL only) for a listing. Returns inserted/existing media ids."""
    media_ids: List[int] = []
    for sort_order, url in enumerate(urls[:MAX_PHOTOS_PER_LISTING]):
        if not url:
            continue
        stmt = pg_insert(CompetitorMedia).values(
            listing_id=listing_id,
            remote_url=url,
            is_main=(sort_order == 0),
            sort_order=sort_order,
        ).on_conflict_do_update(
            index_elements=[CompetitorMedia.listing_id, CompetitorMedia.remote_url],
            set_={"sort_order": sort_order, "is_main": (sort_order == 0)},
        ).returning(CompetitorMedia.id)
        result = await session.execute(stmt)
        row = result.fetchone()
        if row is not None:
            media_ids.append(int(row[0]))
    return media_ids


async def record_snapshot(session, run_id: int, listing_id: int, page_index: int, card: ListCard) -> None:
    stmt = pg_insert(CompetitorSnapshot).values(
        run_id=run_id,
        listing_id=listing_id,
        page_index=page_index,
        is_top=card.is_promoted,
    ).on_conflict_do_nothing(
        index_elements=[CompetitorSnapshot.run_id, CompetitorSnapshot.listing_id]
    )
    await session.execute(stmt)


# ============================================================
# Detail crawl scheduling
# ============================================================

async def select_listings_needing_detail(seen_listing_ids: List[int]) -> List[Tuple[int, str, Optional[datetime]]]:
    """
    Among listings seen this cycle, return those whose detail data is missing
    or older than DETAIL_REFRESH_DAYS.
    """
    if not seen_listing_ids:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=DETAIL_REFRESH_DAYS)
    async with get_session() as session:
        result = await session.execute(
            select(
                CompetitorListing.id,
                CompetitorListing.url,
                CompetitorListing.detail_fetched_at,
            ).where(
                and_(
                    CompetitorListing.id.in_(seen_listing_ids),
                    CompetitorListing.source == "olx",
                    (CompetitorListing.detail_fetched_at.is_(None))
                    | (CompetitorListing.detail_fetched_at < cutoff),
                )
            )
        )
        return [(int(r[0]), r[1], r[2]) for r in result.fetchall()]


# ============================================================
# Detail fetch worker
# ============================================================

async def fetch_and_store_detail(client: OlxClient, listing_id: int, url: str) -> List[int]:
    """Returns media ids queued for download."""
    try:
        html = await client.get_detail(url)
    except Exception as e:
        print(f"[olx] detail fetch failed listing={listing_id} url={url}: {e}")
        return []

    try:
        data = parse_detail_page(html, url=url)
    except Exception as e:
        print(f"[olx] detail parse failed listing={listing_id}: {e}")
        return []

    media_ids: List[int] = []
    async with get_session() as session:
        await update_listing_from_detail(session, listing_id, data)
        if data.photo_urls:
            media_ids = await upsert_media_urls(session, listing_id, data.photo_urls)

    return media_ids


# ============================================================
# Media download
# ============================================================

async def download_one_media(client: OlxClient, media_id: int) -> Dict[str, Any]:
    async with get_session() as session:
        m = await session.get(CompetitorMedia, media_id)
        if m is None:
            return {"ok": False, "error": "not found"}
        if m.download_status == "ok" and m.local_path:
            return {"ok": True, "skipped": True}
        url = m.remote_url
        listing_id = m.listing_id
        sort_order = m.sort_order or 0

    data = await client.download_image(url)
    if data is None:
        async with get_session() as session:
            await session.execute(
                update(CompetitorMedia)
                .where(CompetitorMedia.id == media_id)
                .values(download_status="failed", download_error="http error")
            )
        return {"ok": False, "error": "http error"}

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
    return {"ok": True}


async def download_media_batch(client: OlxClient, media_ids: List[int]) -> Dict[str, int]:
    if not media_ids:
        return {"ok": 0, "failed": 0}

    sem = asyncio.Semaphore(MEDIA_CONCURRENCY)

    async def _one(mid):
        async with sem:
            try:
                return await download_one_media(client, mid)
            except Exception as e:
                print(f"[olx-media] unexpected error mid={mid}: {e}")
                return {"ok": False, "error": str(e)}

    results = await asyncio.gather(*[_one(m) for m in media_ids], return_exceptions=True)
    ok = sum(1 for r in results if isinstance(r, dict) and r.get("ok"))
    failed = len(results) - ok
    return {"ok": ok, "failed": failed}


# ============================================================
# Phone reveal pass (optional, opt-in)
# ============================================================

async def reveal_phones_pass(client: OlxClient) -> Dict[str, int]:
    """For OLX listings without contact_phone, try the reveal API."""
    if not REVEAL_ENABLED:
        return {"attempted": 0, "succeeded": 0, "skipped": 0}

    async with get_session() as session:
        result = await session.execute(
            select(
                CompetitorListing.id,
                CompetitorListing.url,
                CompetitorListing.metadata_,
            ).where(
                and_(
                    CompetitorListing.source == "olx",
                    CompetitorListing.contact_phone.is_(None),
                    CompetitorListing.phone_revealed_at.is_(None),
                    CompetitorListing.detail_fetched_at.isnot(None),
                )
            ).limit(int(os.getenv("OLX_REVEAL_BATCH", "100")))
        )
        rows = result.fetchall()

    attempted = succeeded = 0
    for row in rows:
        listing_id, url, meta = row
        numeric_id = (meta or {}).get("numeric_id") if meta else None
        if not numeric_id:
            continue
        attempted += 1
        try:
            phones = await reveal_phone(client._client, int(numeric_id), listing_url=url)
        except Exception as e:
            print(f"[olx-reveal] error listing={listing_id}: {e}")
            phones = []
        normalized = [p for p in (normalize_phone(x) for x in phones) if p]
        async with get_session() as session:
            update_vals = {"phone_revealed_at": func.now()}
            if normalized:
                update_vals["contact_phone"] = normalized[0]
                if len(normalized) > 1:
                    update_vals["contact_phones_extra"] = normalized[1:]
                succeeded += 1
            await session.execute(
                update(CompetitorListing)
                .where(CompetitorListing.id == listing_id)
                .values(**update_vals)
            )
        await asyncio.sleep(0.5)  # polite pacing for the reveal API

    return {"attempted": attempted, "succeeded": succeeded, "skipped": len(rows) - attempted}


# ============================================================
# Mark disappeared (source-scoped)
# ============================================================

async def mark_disappeared(run_started_at: datetime) -> int:
    async with get_session() as session:
        result = await session.execute(
            update(CompetitorListing)
            .where(
                and_(
                    CompetitorListing.source == "olx",
                    CompetitorListing.last_seen_at < run_started_at,
                    CompetitorListing.disappeared_at.is_(None),
                )
            )
            .values(disappeared_at=func.now())
            .returning(CompetitorListing.id)
        )
        return len(result.fetchall())


# ============================================================
# Main cycle
# ============================================================

async def run_one_cycle() -> Dict[str, Any]:
    cycle_started = datetime.now(timezone.utc)

    async with get_session() as session:
        run = CompetitorScrapeRun(source="olx", status="running")
        session.add(run)
        await session.flush()
        run_id = run.id

    print(f"[olx] === run {run_id} started at {cycle_started.isoformat()} ===")
    print(f"[olx] config: max_list_pages={MAX_LIST_PAGES} detail_concurrency={DETAIL_CONCURRENCY} "
          f"refresh_days={DETAIL_REFRESH_DAYS} download_media={DOWNLOAD_MEDIA} reveal={REVEAL_ENABLED}")

    pages_fetched = 0
    listings_seen = 0
    listings_new = 0
    seen_listing_ids: List[int] = []
    media_stats = {"ok": 0, "failed": 0}
    error_message: Optional[str] = None
    detail_fetched = 0
    detail_failed = 0

    try:
        async with OlxClient() as client:
            # ----------- Stage 1: list crawl -----------
            page = 1
            total_pages_hint: Optional[int] = None

            while page <= MAX_LIST_PAGES:
                try:
                    html = await client.get_list_page(page)
                except Exception as e:
                    print(f"[olx] list page {page} failed: {e}; aborting list stage")
                    error_message = f"list p{page}: {e}"
                    break

                pages_fetched += 1

                if page == 1:
                    total_pages_hint = get_total_pages(html)
                    if total_pages_hint:
                        print(f"[olx] pagination hint: ~{total_pages_hint} pages")

                cards = parse_list_page(html)
                if not cards:
                    print(f"[olx] page {page}: no cards parsed; stopping list stage")
                    break

                async with get_session() as session:
                    for card in cards:
                        try:
                            lid, is_new = await upsert_listing_from_card(session, card)
                            await record_snapshot(session, run_id, lid, page, card)
                            seen_listing_ids.append(lid)
                            listings_seen += 1
                            if is_new:
                                listings_new += 1
                        except Exception as e:
                            print(f"[olx] card upsert failed source_id={card.source_id}: {e}")

                print(f"[olx] page {page}: cards={len(cards)} | seen={listings_seen} new={listings_new}")

                # Stop early if we've covered the pagination
                if total_pages_hint and page >= total_pages_hint:
                    break
                page += 1
                await asyncio.sleep(DEFAULT_DELAY_SEC)

            # ----------- Stage 2: detail crawl -----------
            to_fetch = await select_listings_needing_detail(seen_listing_ids)
            print(f"[olx] details to fetch: {len(to_fetch)} of {len(seen_listing_ids)} seen")

            sem = asyncio.Semaphore(DETAIL_CONCURRENCY)
            queued_media: List[int] = []

            async def _one_detail(lid: int, url: str) -> List[int]:
                async with sem:
                    await asyncio.sleep(DETAIL_DELAY_SEC)
                    try:
                        return await fetch_and_store_detail(client, lid, url)
                    except Exception as e:
                        print(f"[olx] detail unexpected error lid={lid}: {e}")
                        return []

            tasks = [_one_detail(lid, url) for lid, url, _ in to_fetch if url]
            for coro in asyncio.as_completed(tasks):
                try:
                    mids = await coro
                    detail_fetched += 1
                    if mids:
                        queued_media.extend(mids)
                except Exception as e:
                    detail_failed += 1
                    print(f"[olx] detail task error: {e}")

                # progress flush every 50 details
                if detail_fetched % 50 == 0 and detail_fetched > 0:
                    print(f"[olx] detail progress: fetched={detail_fetched} failed={detail_failed} "
                          f"queued_media={len(queued_media)}")

            # ----------- Stage 3: media download -----------
            if DOWNLOAD_MEDIA and queued_media:
                print(f"[olx] downloading {len(queued_media)} media items")
                stats = await download_media_batch(client, list(set(queued_media)))
                media_stats["ok"] += stats["ok"]
                media_stats["failed"] += stats["failed"]
                print(f"[olx] media download: ok={stats['ok']} failed={stats['failed']}")

            # ----------- Stage 4: phone reveal (opt-in) -----------
            if REVEAL_ENABLED:
                reveal_stats = await reveal_phones_pass(client)
                print(f"[olx] phone reveal: {reveal_stats}")

        # ----------- Stage 5: mark disappeared -----------
        disappeared = await mark_disappeared(cycle_started)
        print(f"[olx] disappeared: {disappeared}")

        async with get_session() as session:
            await session.execute(
                update(CompetitorScrapeRun).where(CompetitorScrapeRun.id == run_id).values(
                    finished_at=func.now(),
                    status="done" if not error_message else "interrupted",
                    pages_fetched=pages_fetched,
                    listings_seen=listings_seen,
                    listings_new=listings_new,
                    listings_disappeared=disappeared,
                    media_downloaded=media_stats["ok"],
                    media_failed=media_stats["failed"],
                    error_message=error_message,
                    notes=f"details_fetched={detail_fetched} details_failed={detail_failed}",
                )
            )

        result = {
            "run_id": run_id,
            "pages": pages_fetched,
            "seen": listings_seen,
            "new": listings_new,
            "details_fetched": detail_fetched,
            "details_failed": detail_failed,
            "disappeared": disappeared,
            "media_ok": media_stats["ok"],
            "media_failed": media_stats["failed"],
        }
        print(f"[olx] === run {run_id} done: {result}")
        return result

    except Exception as e:
        print(f"[olx] cycle FAILED: {e}")
        async with get_session() as session:
            await session.execute(
                update(CompetitorScrapeRun).where(CompetitorScrapeRun.id == run_id).values(
                    finished_at=func.now(),
                    status="failed",
                    pages_fetched=pages_fetched,
                    listings_seen=listings_seen,
                    listings_new=listings_new,
                    media_downloaded=media_stats["ok"],
                    media_failed=media_stats["failed"],
                    error_message=str(e)[:1000],
                )
            )
        raise