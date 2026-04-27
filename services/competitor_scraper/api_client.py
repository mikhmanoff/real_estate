# services/competitor_scraper/api_client.py
"""
Клиент для api.joymi.uz.

list-combined endpoint:
    GET /api/v1/announcement/list-combined/?page=N&per_page=M
    
Параметр per_page принимает значения от ~10 до 50 (выше API может игнорировать).
Авторизация для list НЕ требуется (мы проверили fetch без Authorization работает).

Если API в будущем закроет анонимный доступ — можно подставить Bearer
через JOYMI_TOKEN env var.
"""
import asyncio
import os
from typing import Any, Dict, Optional

import httpx


JOYMI_BASE = os.getenv("JOYMI_BASE", "https://api.joymi.uz")
JOYMI_TOKEN = os.getenv("JOYMI_TOKEN", "")  # опционально

# Раcсчитано так, чтобы не убить их API: 0.4-0.5s между запросами.
# 84k / 50 = ~1700 страниц * 0.5s = 14 минут на полный обход.
DEFAULT_PER_PAGE = int(os.getenv("JOYMI_PER_PAGE", "50"))
DEFAULT_DELAY_SEC = float(os.getenv("JOYMI_DELAY_SEC", "0.4"))

# Retry config
MAX_RETRIES = 4
RETRY_BACKOFF = 2.0  # exponential


def _headers() -> Dict[str, str]:
    h = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "uz",
        "Origin": "https://joymee.uz",
        "Referer": "https://joymee.uz/",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    }
    if JOYMI_TOKEN:
        h["Authorization"] = f"Bearer {JOYMI_TOKEN}"
    return h


class JoymiClient:
    """Простой async клиент с retry и connection pooling."""

    def __init__(self, base: str = JOYMI_BASE, timeout: float = 30.0):
        self.base = base.rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None
        self._timeout = timeout

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            headers=_headers(),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()

    async def get_page(self, page: int, per_page: int = DEFAULT_PER_PAGE) -> Dict[str, Any]:
        """Получить одну страницу listings. Возвращает JSON dict."""
        url = f"{self.base}/api/v1/announcement/list-combined/"
        params = {"page": page, "per_page": per_page}

        last_err: Optional[Exception] = None
        for attempt in range(MAX_RETRIES):
            try:
                assert self._client is not None
                r = await self._client.get(url, params=params)
                if r.status_code == 429:
                    # rate limited - back off harder
                    wait = (RETRY_BACKOFF ** (attempt + 1)) * 2
                    print(f"[joymi] 429 on page {page}, sleeping {wait:.1f}s")
                    await asyncio.sleep(wait)
                    continue
                if r.status_code >= 500:
                    wait = RETRY_BACKOFF ** attempt
                    print(f"[joymi] {r.status_code} on page {page}, retry in {wait:.1f}s")
                    await asyncio.sleep(wait)
                    continue
                r.raise_for_status()
                return r.json()
            except (httpx.RequestError, httpx.HTTPStatusError) as e:
                last_err = e
                wait = RETRY_BACKOFF ** attempt
                print(f"[joymi] error on page {page} (attempt {attempt+1}): {e}; retry in {wait:.1f}s")
                await asyncio.sleep(wait)

        raise RuntimeError(f"Failed to fetch page {page}: {last_err}")

    async def download_image(self, url: str) -> Optional[bytes]:
        """Скачать одно изображение. None при ошибке (не fatal)."""
        try:
            assert self._client is not None
            r = await self._client.get(url, timeout=20.0)
            if r.status_code == 200:
                return r.content
            return None
        except httpx.RequestError as e:
            print(f"[joymi] image download failed {url}: {e}")
            return None