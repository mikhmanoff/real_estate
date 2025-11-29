import json, threading
from pathlib import Path
from typing import Iterable, Set
from .models import Channels

class ChannelRegistry:
    """
    Хранит список каналов в файле, даёт потокобезопасный Set chat_id/username/links.
    Hot-reload для слушателя: он держит ссылку на registry.allowed_ids (Set[int]).
    """
    def __init__(self, path: str):
        self._path = Path(path)
        self._lock = threading.RLock()
        self._channels = Channels()
        self._allowed_ids: Set[int] = set()   # обновляется слушателем
        self.load()

    def load(self):
        with self._lock:
            if self._path.exists():
                self._channels = Channels(**json.loads(self._path.read_text(encoding="utf-8")))
            else:
                self._channels = Channels()
                self.save()

    def save(self):
        with self._lock:
            self._path.write_text(self._channels.model_dump_json(indent=2, ensure_ascii=False), encoding="utf-8")

    # --- публичные методы для API ---
    def list_all(self) -> Channels:
        with self._lock:
            return self._channels

    def add_public(self, username: str):
        username = username.strip()
        if not username.startswith("@"):
            username = "@" + username
        with self._lock:
            if username not in self._channels.public:
                self._channels.public.append(username)
                self.save()

    def add_invite(self, invite_link: str):
        with self._lock:
            if invite_link not in self._channels.invites:
                self._channels.invites.append(invite_link)
                self.save()

    def remove(self, value: str):
        with self._lock:
            if value in self._channels.public:
                self._channels.public.remove(value)
            elif value in self._channels.invites:
                self._channels.invites.remove(value)
            else:
                # может это числовой id?
                try:
                    vid = int(value)
                    if vid in self._channels.resolved_ids:
                        self._channels.resolved_ids.remove(vid)
                        # и из runtime набора убрать
                        self._allowed_ids.discard(vid)
                except ValueError:
                    pass
            self.save()

    # --- то, что использует слушатель ---
    @property
    def allowed_ids(self) -> Set[int]:
        return self._allowed_ids

    def set_allowed_ids(self, ids: Iterable[int]):
        with self._lock:
            self._allowed_ids.clear()
            self._allowed_ids.update(ids)

    def add_resolved_id(self, chat_id: int):
        with self._lock:
            if chat_id not in self._channels.resolved_ids:
                self._channels.resolved_ids.append(chat_id)
                self.save()
                self._allowed_ids.add(chat_id)
