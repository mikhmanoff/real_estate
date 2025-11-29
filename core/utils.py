# core/utils.py
import os
from dotenv import load_dotenv
from pathlib import Path

def env(name: str, default: str | None = None) -> str:
    load_dotenv(override=False)
    val = os.getenv(name, default)
    if val is None:
        raise RuntimeError(f"Missing env var: {name}")
    return val

def ensure_dirs(*paths: str):
    for p in paths:
        Path(p).mkdir(parents=True, exist_ok=True)
