# services/competitor_scraper/runner.py
"""
Long-running worker для Railway. Бесконечный цикл с интервалом между прогонами.

Запуск локально:
    python -m services.competitor_scraper.runner

Однократный прогон (для отладки):
    python -m services.competitor_scraper.runner --once

Env vars:
    DATABASE_URL              — подключение к БД (берётся из общего .env)
    COMPETITOR_DOWNLOAD_DIR   — путь к папке с медиа на Volume (default /app/competitor_downloads)
    JOYMI_INTERVAL_HOURS      — интервал между прогонами (default 6)
    JOYMI_PER_PAGE            — items per page (default 50)
    JOYMI_DELAY_SEC           — задержка между запросами страниц (default 0.4)
    JOYMI_MEDIA_CONCURRENCY   — параллельность скачивания фото (default 5)
    TASHKENT_ONLY             — '1' чтобы парсить только Ташкент-шахри
    JOYMI_MAX_PAGES           — предохранитель (default 20000)
"""
import argparse
import asyncio
import os
import sys
import traceback

from .scraper import run_one_cycle


INTERVAL_HOURS = float(os.getenv("JOYMI_INTERVAL_HOURS", "6"))


async def main_loop():
    print(f"[runner] starting; interval={INTERVAL_HOURS}h")
    while True:
        try:
            await run_one_cycle()
        except Exception:
            print("[runner] cycle errored:")
            traceback.print_exc()

        sleep_sec = int(INTERVAL_HOURS * 3600)
        print(f"[runner] sleeping {sleep_sec}s ({INTERVAL_HOURS}h) before next cycle")
        await asyncio.sleep(sleep_sec)


async def main_once():
    print("[runner] one-shot mode")
    await run_one_cycle()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run a single cycle then exit")
    args = parser.parse_args()

    try:
        if args.once:
            asyncio.run(main_once())
        else:
            asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("[runner] interrupted")
        sys.exit(0)


if __name__ == "__main__":
    main()