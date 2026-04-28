# services/olx_scraper/api_client.py
"""
HTTP client for olx.uz (HTML scraping, no public API).

We mimic a normal browser: real UA string, Accept-Encoding gzip, ru-RU language.
OLX classifieds historically don't aggressively block scrapers as long as
request rate is reasonable (we keep ~1 req/sec).

If 403 / Cloudflare interstitial starts appearing, we'd switch to Playwright,
but plain httpx is enough as of now.
"""
import asyncio
import os
import random
from typing import Optional

import httpx


OLX_BASE = os.getenv("OLX_BASE", "https://www.olx.uz")

# Pacing — slow-ish to be safe. ~25 list pages * 1.5s = 37s for list crawl.
# Detail pages dominate: 1000 listings * 1.2s = 20min. Acceptable per cycle.
DEFAULT_DELAY_SEC = float(os.getenv("OLX_DELAY_SEC", "1.2"))
DETAIL_DELAY_SEC = float(os.getenv("OLX_DETAIL_DELAY_SEC", "1.0"))

MAX_RETRIES = 4
RETRY_BACKOFF = 2.0


# Pool of recent Chrome UAs — rotate per session
USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


def _headers(extra: Optional[dict] = None) -> dict:
    h = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8,uz;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    if extra:
        h.update(extra)
    return h


class OlxClient:
    def __init__(self, base: str = OLX_BASE, timeout: float = 30.0):
        self.base = base.rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None
        self._timeout = timeout

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            headers=_headers(),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            follow_redirects=True,
            # OLX sometimes returns Cloudflare cookies — keep them
            cookies=httpx.Cookies(),
        )
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()

    async def _get_text(self, url: str, label: str) -> str:
        last_err: Optional[Exception] = None
        for attempt in range(MAX_RETRIES):
            try:
                assert self._client is not None
                r = await self._client.get(url)
                if r.status_code == 429 or r.status_code == 503:
                    wait = (RETRY_BACKOFF ** (attempt + 1)) * 2
                    print(f"[olx] {r.status_code} on {label}, sleeping {wait:.1f}s")
                    await asyncio.sleep(wait)
                    continue
                if r.status_code == 403:
                    # could be Cloudflare; brief wait and retry
                    wait = RETRY_BACKOFF ** (attempt + 2)
                    print(f"[olx] 403 on {label}, may be Cloudflare; retry in {wait:.1f}s")
                    await asyncio.sleep(wait)
                    continue
                if r.status_code >= 500:
                    wait = RETRY_BACKOFF ** attempt
                    await asyncio.sleep(wait)
                    continue
                r.raise_for_status()
                return r.text
            except (httpx.RequestError, httpx.HTTPStatusError) as e:
                last_err = e
                wait = RETRY_BACKOFF ** attempt
                print(f"[olx] error on {label} (attempt {attempt+1}): {e}; retry in {wait:.1f}s")
                await asyncio.sleep(wait)

        raise RuntimeError(f"Failed to fetch {label}: {last_err}")

    async def get_list_page(self, page: int, currency: str = "UZS") -> str:
        """Fetch HTML of the long-term-rent list page."""
        if page == 1:
            url = f"{self.base}/nedvizhimost/kvartiry/arenda-dolgosrochnaya/?currency={currency}"
        else:
            url = f"{self.base}/nedvizhimost/kvartiry/arenda-dolgosrochnaya/?currency={currency}&page={page}"
        return await self._get_text(url, f"list p{page}")

    async def get_detail(self, url_path_or_full: str) -> str:
        """Fetch a single listing detail page."""
        if url_path_or_full.startswith("http"):
            url = url_path_or_full
        else:
            url = f"{self.base}{url_path_or_full}"
        return await self._get_text(url, f"detail {url[-40:]}")

    async def download_image(self, url: str) -> Optional[bytes]:
        try:
            assert self._client is not None
            # CDN images are public — minimal headers
            r = await self._client.get(
                url,
                headers={"User-Agent": _headers()["User-Agent"], "Accept": "image/*,*/*"},
                timeout=20.0,
            )
            if r.status_code == 200:
                return r.content
            return None
        except httpx.RequestError as e:
            print(f"[olx] image download failed {url}: {e}")
            return None