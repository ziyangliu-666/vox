"""Every transcript, newest first, kept in history.json in the app data folder."""

import json
import threading
import time
import uuid

from vox.config import data_dir

# A few years of heavy use. Past this, the oldest items go, so the file
# never grows without bound.
LIMIT = 10_000


class History:
    def __init__(self) -> None:
        self._path = data_dir() / "history.json"
        self._lock = threading.Lock()
        self._items: list[dict] = self._load()

    def _load(self) -> list[dict]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        now = time.time()
        items = []
        for i in raw:
            if isinstance(i, dict) and i.get("text"):
                # Items from vox 0.2 have no id and no timestamp.
                items.append({"id": i.get("id") or uuid.uuid4().hex[:12],
                              "text": i["text"], "ts": i.get("ts", now)})
        return items

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._items, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)

    @staticmethod
    def _view(item: dict) -> dict:
        t = time.localtime(item["ts"])
        return {"id": item["id"], "text": item["text"],
                "time": time.strftime("%H:%M", t), "date": time.strftime("%Y-%m-%d", t)}

    def add(self, text: str) -> None:
        with self._lock:
            self._items.insert(0, {"id": uuid.uuid4().hex[:12], "text": text, "ts": time.time()})
            del self._items[LIMIT:]
            self._save()

    def recent(self, n: int = 5) -> list[dict]:
        with self._lock:
            return [self._view(i) for i in self._items[:n]]

    @property
    def total(self) -> int:
        return len(self._items)

    @property
    def last_text(self) -> str | None:
        with self._lock:
            return self._items[0]["text"] if self._items else None

    def query(self, text: str = "", offset: int = 0, limit: int = 50) -> dict:
        needle = (text or "").strip().casefold()
        with self._lock:
            hits = [i for i in self._items if needle in i["text"].casefold()] if needle \
                else self._items
            page = hits[max(0, offset):max(0, offset) + max(1, min(limit, 200))]
            return {"items": [self._view(i) for i in page], "total": len(hits)}

    def delete(self, item_id: str) -> None:
        with self._lock:
            self._items = [i for i in self._items if i["id"] != item_id]
            self._save()

    def clear(self) -> None:
        with self._lock:
            self._items = []
            self._save()
