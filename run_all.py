import asyncio, os
from core.utils import env

async def run_registry_api():
    host = env("HOST", "127.0.0.1")
    port = env("REGISTRY_PORT", "8000")
    cmd = ["uvicorn", "services.registry_api:app", "--host", host, "--port", port, "--reload"]
    proc = await asyncio.create_subprocess_exec(*cmd)
    return proc

async def run_listener():
    cmd = ["python", "-m", "services.tg_listener"]
    proc = await asyncio.create_subprocess_exec(*cmd)
    return proc

async def main():
    api = await run_registry_api()
    tg  = await run_listener()
    print("Запущены: registry_api (FastAPI) и tg_listener (Telethon).")
    await asyncio.gather(api.wait(), tg.wait())

if __name__ == "__main__":
    asyncio.run(main())
