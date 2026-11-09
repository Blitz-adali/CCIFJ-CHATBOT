"""Small process-local cache for successful public-corpus retrieval only."""
from collections import OrderedDict
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import threading
import time

from .config import DB_PATH, INDEX_PATH, CHUNKS_PATH, PAGES_PATH


class SearchCache:
    def __init__(self, capacity=32, ttl=120, clock=time.monotonic):
        self.capacity, self.ttl, self.clock = capacity, ttl, clock
        self._entries = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            item = self._entries.get(key)
            if item is None:
                return None
            expiry, passages = item
            if self.clock() >= expiry:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return deepcopy(passages)

    def put(self, key, passages):
        if not passages:
            return
        with self._lock:
            self._entries[key] = (self.clock()+self.ttl,deepcopy(passages))
            self._entries.move_to_end(key)
            while len(self._entries)>self.capacity:
                self._entries.popitem(last=False)


def cache_key(query, retriever, model):
    # Index generation changes invalidate entries. No raw query or user/session
    # identifier is retained, and nothing is persisted to disk.
    files = (Path(CHUNKS_PATH),Path(PAGES_PATH),Path(INDEX_PATH)/'corpus.jsonl',
             Path(DB_PATH)/'chroma.sqlite3',Path(DB_PATH)/'chroma.sqlite3-wal')
    signature = []
    for path in files:
        try:
            stat = path.stat()
            signature.append((stat.st_mtime_ns,stat.st_size))
        except OSError:
            signature.append(None)
    return (sha256(query.strip().encode()).digest(),id(retriever),id(model),tuple(signature))


public_search_cache = SearchCache()
