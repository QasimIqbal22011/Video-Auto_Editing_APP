"""In-process allowlist for files exposed by the local media streaming routes."""

import os
import threading
from typing import Iterable, Optional


class MediaPathRegistry:
    """Only stream media paths explicitly registered by application workflows."""

    def __init__(self):
        self._lock = threading.RLock()
        self._paths: set[str] = set()

    @staticmethod
    def _canonical(path: str) -> str:
        return os.path.normcase(os.path.realpath(os.path.abspath(path)))

    def register(self, path: str) -> Optional[str]:
        if not path:
            return None
        canonical = self._canonical(path)
        if not os.path.isfile(canonical):
            return None
        with self._lock:
            self._paths.add(canonical)
        return canonical

    def resolve(self, requested_path: str, allowed_extensions: Iterable[str]) -> Optional[str]:
        if not requested_path:
            return None
        canonical = self._canonical(requested_path)
        allowed = {ext.lower() for ext in allowed_extensions}
        if os.path.splitext(canonical)[1].lower() not in allowed:
            return None
        with self._lock:
            if canonical not in self._paths:
                return None
        if not os.path.isfile(canonical):
            return None
        return canonical
