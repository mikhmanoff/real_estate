# services/olx_scraper/media.py
"""
Image storage + pHash for OLX listings.

Files saved to OLX_DOWNLOAD_DIR (default: <COMPETITOR_DOWNLOAD_DIR>/olx).
We keep this separate from joymi's tree so the two scrapers can be reasoned
about independently, and so a `du -sh` per source is straightforward.

pHash uses imagehash with hash_size=8 — the SAME parameters as joymi and as
the Telegram media table. This is the basis of cross-source matching: a
listing on Telegram, joymi, and OLX with the same photo will have hamming
distance ≤ 4-6 across all three.
"""
import io
import os
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse

from PIL import Image
import imagehash


_DEFAULT_BASE = os.getenv("COMPETITOR_DOWNLOAD_DIR", "/app/competitor_downloads")
OLX_DOWNLOAD_DIR = Path(os.getenv("OLX_DOWNLOAD_DIR", os.path.join(_DEFAULT_BASE, "olx")))
PHASH_SIZE = 8


def ensure_download_dir() -> None:
    OLX_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _ext_from_url(url: str) -> str:
    parsed = urlparse(url.split(";s=")[0])
    ext = Path(parsed.path).suffix.lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        ext = ".jpg"
    return ext


def listing_dir(listing_id: int) -> Path:
    shard = str(listing_id % 1000).zfill(3)
    p = OLX_DOWNLOAD_DIR / shard
    p.mkdir(parents=True, exist_ok=True)
    return p


def filename_for(listing_id: int, sort_order: int, url: str) -> str:
    return f"{listing_id}_{sort_order}{_ext_from_url(url)}"


def compute_phash(image_bytes: bytes) -> Optional[str]:
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")
            return str(imagehash.phash(img, hash_size=PHASH_SIZE))
    except Exception as e:
        print(f"[olx-media] phash failed: {e}")
        return None


def get_image_dimensions(image_bytes: bytes) -> Tuple[Optional[int], Optional[int]]:
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            return img.width, img.height
    except Exception:
        return None, None


def save_image(image_bytes: bytes, listing_id: int, sort_order: int, url: str) -> Path:
    ensure_download_dir()
    target_dir = listing_dir(listing_id)
    target_path = target_dir / filename_for(listing_id, sort_order, url)
    target_path.write_bytes(image_bytes)
    return target_path


def relative_path(p: Path) -> str:
    """
    Return path relative to COMPETITOR_DOWNLOAD_DIR (parent of OLX_DOWNLOAD_DIR
    in the default layout). Stable across Volume re-mounts and renames of the
    leaf source folder.
    """
    try:
        base = Path(_DEFAULT_BASE)
        return str(p.relative_to(base))
    except ValueError:
        try:
            return str(p.relative_to(OLX_DOWNLOAD_DIR))
        except ValueError:
            return str(p)