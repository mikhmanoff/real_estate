# run_all.py
import asyncio
from core.utils import env


async def run_registry_api():
    """Registry API на порту 8000."""
    host = env("HOST", "127.0.0.1")
    port = env("REGISTRY_PORT", "8000")
    cmd = ["uvicorn", "services.registry_api:app", "--host", host, "--port", port, "--reload"]
    proc = await asyncio.create_subprocess_exec(*cmd)
    return proc


async def run_main_api():
    """Main API для Mini App на порту 8001."""
    host = env("HOST", "127.0.0.1")
    port = env("API_PORT", "8001")
    cmd = ["uvicorn", "services.api:app", "--host", host, "--port", port, "--reload"]
    proc = await asyncio.create_subprocess_exec(*cmd)
    return proc


async def run_listener():
    """Telegram listener."""
    cmd = ["python", "-m", "services.tg_listener"]
    proc = await asyncio.create_subprocess_exec(*cmd)
    return proc


async def main():
    registry = await run_registry_api()
    api = await run_main_api()
    tg = await run_listener()
    
    print("=" * 50)
    print("Запущены сервисы:")
    print(f"  • Registry API:  http://127.0.0.1:{env('REGISTRY_PORT', '8000')}")
    print(f"  • Main API:      http://127.0.0.1:{env('API_PORT', '8001')}")
    print(f"  • TG Listener:   работает")
    print("=" * 50)
    
    await asyncio.gather(registry.wait(), api.wait(), tg.wait())


if __name__ == "__main__":
    asyncio.run(main())