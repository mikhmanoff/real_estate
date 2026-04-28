# services/olx_scraper/runner.py
"""
Long-running worker для Railway. Бесконечный цикл с интервалом между прогонами.

Запуск (Railway Start Command):
    python -m services.olx_scraper.runner

Однократный прогон (для отладки):
    python -m services.olx_scraper.runner --once

Env vars:
    OLX_INTERVAL_HOURS    — частота (default 6)
    OLX_INITIAL_DELAY_S   — задержка перед первым стартом (default 0)
    OLX_MAX_LIST_PAGES    — лимит страниц списка (default 60)
    OLX_DETAIL_CONCURRENCY — параллельность detail (default 3)
    OLX_DOWNLOAD_MEDIA    — 1/0 (default 1)
    OLX_REVEAL_PHONES     — 1/0 (default 0)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
import traceback
from datetime import datetime, timezone

from .scraper import run_one_cycle


# ============================================================
# Config
# ============================================================

def _interval_hours() -> float:
    try:
        v = float(os.getenv("OLX_INTERVAL_HOURS", "6"))
        return v if v > 0 else 6.0
    except ValueError:
        return 6.0


def _initial_delay() -> float:
    try:
        return max(0.0, float(os.getenv("OLX_INITIAL_DELAY_S", "0")))
    except ValueError:
        return 0.0


# ============================================================
# Graceful shutdown
# ============================================================

_shutdown = asyncio.Event()


def _install_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    def _handler(signame: str) -> None:
        print(f"[olx-runner] caught {signame}, will exit after current cycle finishes")
        _shutdown.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handler, sig.name)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            signal.signal(sig, lambda s, f: _shutdown.set())


# ============================================================
# Run helpers
# ============================================================

async def _run_safe() -> dict:
    """Запускает run_one_cycle(), не пробрасывает исключения — worker не должен падать."""
    started = datetime.now(timezone.utc)
    try:
        result = await run_one_cycle()
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        print(f"[olx-runner] cycle finished in {elapsed:.1f}s: {result}")
        return result
    except Exception:
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        print(f"[olx-runner] cycle FAILED after {elapsed:.1f}s:")
        traceback.print_exc()
        return {}


async def main_loop() -> None:
    interval_sec = _interval_hours() * 3600
    initial_delay = _initial_delay()

    print(f"[olx-runner] starting; interval={_interval_hours():.2f}h initial_delay={initial_delay}s")

    if initial_delay > 0:
        print(f"[olx-runner] waiting {initial_delay}s before first cycle...")
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=initial_delay)
            print("[olx-runner] shutdown requested during initial delay, exiting")
            return
        except asyncio.TimeoutError:
            pass

    while not _shutdown.is_set():
        await _run_safe()

        if _shutdown.is_set():
            break

        print(f"[olx-runner] sleeping {interval_sec:.0f}s ({_interval_hours():.1f}h) before next cycle")
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=interval_sec)
            print("[olx-runner] shutdown requested, exiting after sleep")
        except asyncio.TimeoutError:
            pass  # нормальное пробуждение по таймеру

    print("[olx-runner] stopped")


async def main_once() -> None:
    print("[olx-runner] one-shot mode")
    await _run_safe()


# ============================================================
# Entry point
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="OLX scraper worker")
    parser.add_argument("--once", action="store_true", help="run a single cycle then exit")
    args = parser.parse_args()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    if not args.once:
        _install_signal_handlers(loop)

    try:
        if args.once:
            loop.run_until_complete(main_once())
        else:
            loop.run_until_complete(main_loop())
    except KeyboardInterrupt:
        print("[olx-runner] interrupted")
    finally:
        loop.close()
        sys.exit(0)


if __name__ == "__main__":
    main()