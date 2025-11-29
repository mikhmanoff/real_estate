# tools/auth_login.py
import asyncio
import sys
from getpass import getpass

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from core.utils import env, ensure_dirs

# ---- helper: print QR to terminal ----
def print_qr_ascii(url: str):
    try:
        import qrcode
    except ImportError:
        print("Установи пакет qrcode: pip install qrcode", file=sys.stderr)
        print("URL для QR (можно скопировать в генератор QR-кодов):", url)
        return
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    mat = qr.get_matrix()
    # Рисуем «двойной» блок, чтобы квадрат не был сплющен
    for row in mat:
        line = "".join("██" if cell else "  " for cell in row)
        print(line)

async def login_via_qr(client: TelegramClient):
    qr = await client.qr_login()
    print("Открой Telegram на телефоне → Настройки → Устройства → Подключить устройство.")
    print("Сканируй QR ниже:")
    print_qr_ascii(qr.url)
    me = await qr.wait()  # ждём, пока отсканируешь
    return me

async def login_via_code(client: TelegramClient):
    phone = input("📱 Введи номер телефона в международном формате (+998...): ").strip()
    await client.send_code_request(phone)
    code = input("Код из Telegram/SMS: ").strip().replace(" ", "")
    try:
        me = await client.sign_in(phone=phone, code=code)
        return me
    except SessionPasswordNeededError:
        pwd = getpass("Пароль 2FA: ")
        me = await client.sign_in(password=pwd)
        return me

async def main():
    api_id = int(env("API_ID"))
    api_hash = env("API_HASH")
    session_dir = env("SESSION_DIR")
    session_name = env("SESSION_NAME")
    ensure_dirs(session_dir)

    session_path = f"{session_dir.rstrip('/')}/{session_name}"
    client = TelegramClient(session_path, api_id, api_hash)
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print("✅ Уже авторизованы как:", me.first_name)
        await client.disconnect()
        return

    # Пытаемся через QR
    try:
        me = await login_via_qr(client)
        print("✅ Вошли как:", me.first_name)
    except Exception as e:
        print("⚠️ Не удалось через QR:", e)
        print("Пробуем вход по коду…")
        me = await login_via_code(client)
        print("✅ Вошли как:", me.first_name)

    await client.disconnect()
    print("Сессия сохранена.")

if __name__ == "__main__":
    asyncio.run(main())
