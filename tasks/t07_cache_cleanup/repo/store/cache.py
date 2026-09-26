import os, json, time

class DiskCache:
    """A tiny JSON-on-disk cache with per-entry TTL."""

    def __init__(self, root: str, ttl: float, now=time.time):
        self.root = root
        self.ttl = ttl
        self._now = now
        os.makedirs(root, exist_ok=True)

    def _path(self, key):
        return os.path.join(self.root, f"{key}.json")

    def set(self, key, value):
        with open(self._path(key), "w") as f:
            json.dump({"t": self._now(), "v": value}, f)

    def get(self, key, default=None):
        p = self._path(key)
        if not os.path.exists(p):
            return default
        with open(p) as f:
            rec = json.load(f)
        return rec["v"]
