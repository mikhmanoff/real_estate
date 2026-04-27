# services/competitor_scraper/media.py
"""
Загрузка и pHash изображений с joymi.

Pillow умеет webp нативно. imagehash работает по downsampled grayscale, поэтому
pHash от webp и от jpg одной и той же фотки будут совпадать (Hamming distance ≤ 4-6).
Это даст нам матчинг с фотками из Telegram-постов в существующей таблице `media`.

Файлы сохраняются на Railway Volume в COMPETITOR_DOWNLOAD_DIR (отдельная
папка от основной — чтобы не мешалось).
"""
import io
import os
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse

from PIL import Image
import imagehash


COMPETITOR_DOWNLOAD_DIR = Path(os.getenv("COMPETITOR_DOWNLOAD_DIR", "/app/competitor_downloads"))
PHASH_SIZE = 8  # 8x8 = 64 bits, стандарт. Совместимо со всеми imagehash хешами.


def ensure_download_dir() -> None:
    COMPETITOR_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


def url_to_filename(url: str, listing_id: int, sort_order: int) -> str:
    """
    Превращает URL в стабильное имя файла. Используем хеш URL'а
    в filename, чтобы не зависеть от их .webp имён, которые могут быть
    не уникальны или содержать спецсимволы.
    """
    parsed = urlparse(url)
    ext = Path(parsed.path).suffix or ".webp"
    if ext.lower() not in (".webp", ".jpg", ".jpeg", ".png"):
        ext = ".webp"
    return f"{listing_id}_{sort_order}{ext}"


def listing_dir(listing_id: int) -> Path:
    """
    Шардим по последним 3 цифрам ID, чтобы не было миллиона файлов
    в одной папке.
    """
    shard = str(listing_id % 1000).zfill(3)
    p = COMPETITOR_DOWNLOAD_DIR / shard
    p.mkdir(parents=True, exist_ok=True)
    return p


def compute_phash(image_bytes: bytes) -> Optional[str]:
    """
    Считает perceptual hash. Возвращает 16-символьную hex-строку (64 бита).
    Совместимо с phash из основной БД (если посчитан с тем же size=8).
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            # Сводим к RGB на случай RGBA/P/CMYK.
            if img.mode != "RGB":
                img = img.convert("RGB")
            h = imagehash.phash(img, hash_size=PHASH_SIZE)
            return str(h)  # imagehash возвращает 16 hex chars для 8x8
    except Exception as e:
        print(f"[media] phash failed: {e}")
        return None


def get_image_dimensions(image_bytes: bytes) -> Tuple[Optional[int], Optional[int]]:
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            return img.width, img.height
    except Exception:
        return None, None


def save_image(image_bytes: bytes, listing_id: int, sort_order: int, url: str) -> Path:
    """Сохраняет файл на Volume и возвращает путь."""
    ensure_download_dir()
    target_dir = listing_dir(listing_id)
    filename = url_to_filename(url, listing_id, sort_order)
    target_path = target_dir / filename
    target_path.write_bytes(image_bytes)
    return target_path


def relative_path(p: Path) -> str:
    """
    Возвращает путь относительно COMPETITOR_DOWNLOAD_DIR — чтобы в БД хранить
    стабильные относительные пути (выживут смену volume mount-point).
    """
    try:
        return str(p.relative_to(COMPETITOR_DOWNLOAD_DIR))
    except ValueError:
        return str(p)


def hamming_distance(h1: str, h2: str) -> Optional[int]:
    """Расстояние Хемминга между двумя phash. Для матчинга фото."""
    if not h1 or not h2 or len(h1) != len(h2):
        return None
    try:
        a = int(h1, 16)
        b = int(h2, 16)
        return bin(a ^ b).count("1")
    except ValueError:
        return None