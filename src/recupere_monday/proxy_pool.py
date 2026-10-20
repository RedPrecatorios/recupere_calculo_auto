"""Pool de proxies Webshare (ip:porta:user:senha) com round-robin."""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# src/recupere_monday/proxy_pool.py -> raiz do projeto
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_LIST_NAME = "Webshare 500 proxies.txt"


@dataclass(frozen=True)
class ProxyEndpoint:
    host: str
    port: int
    username: str
    password: str

    @property
    def url(self) -> str:
        user = quote(self.username, safe="")
        password = quote(self.password, safe="")
        return f"http://{user}:{password}@{self.host}:{self.port}"

    def as_requests_proxies(self) -> dict[str, str]:
        return {"http": self.url, "https": self.url}

    @property
    def label(self) -> str:
        return f"{self.username}@{self.host}:{self.port}"


def parse_proxy_line(line: str) -> ProxyEndpoint | None:
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    parts = text.split(":")
    if len(parts) != 4:
        return None
    host, port_raw, username, password = parts
    if not host or not username or not password:
        return None
    try:
        port = int(port_raw)
    except ValueError:
        return None
    return ProxyEndpoint(
        host=host.strip(),
        port=port,
        username=username.strip(),
        password=password.strip(),
    )


def resolve_proxies_list_path(explicit: str | Path | None = None) -> Path:
    raw = str(explicit or os.getenv("WEBSHARE_PROXIES_LIST") or "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (_PROJECT_ROOT / path).resolve()
        return path
    return (_PROJECT_ROOT / _DEFAULT_LIST_NAME).resolve()


def load_proxy_endpoints(path: Path | None = None) -> list[ProxyEndpoint]:
    list_path = path or resolve_proxies_list_path()
    if not list_path.is_file():
        logger.warning("proxy_list_not_found path=%s", list_path)
        return []
    endpoints: list[ProxyEndpoint] = []
    for line in list_path.read_text(encoding="utf-8").splitlines():
        endpoint = parse_proxy_line(line)
        if endpoint is not None:
            endpoints.append(endpoint)
    logger.info("proxy_list_loaded path=%s count=%s", list_path, len(endpoints))
    return endpoints


class ProxyPool:
    def __init__(self, endpoints: list[ProxyEndpoint]) -> None:
        self._endpoints = list(endpoints)
        self._index = 0
        self._lock = threading.Lock()

    @property
    def size(self) -> int:
        return len(self._endpoints)

    def next(self) -> ProxyEndpoint | None:
        if not self._endpoints:
            return None
        with self._lock:
            endpoint = self._endpoints[self._index % len(self._endpoints)]
            self._index += 1
            return endpoint

    def candidates(self, *, max_attempts: int) -> list[ProxyEndpoint]:
        if not self._endpoints:
            return []
        attempts = max(1, min(int(max_attempts), len(self._endpoints)))
        return [ep for _ in range(attempts) if (ep := self.next()) is not None]


_pool_lock = threading.Lock()
_shared_pool: ProxyPool | None = None
_shared_pool_path: str | None = None


def get_shared_proxy_pool(*, force_reload: bool = False) -> ProxyPool:
    global _shared_pool, _shared_pool_path
    path = resolve_proxies_list_path()
    path_key = str(path)
    with _pool_lock:
        if force_reload or _shared_pool is None or _shared_pool_path != path_key:
            endpoints = load_proxy_endpoints(path)
            _shared_pool = ProxyPool(endpoints)
            _shared_pool_path = path_key
        return _shared_pool
