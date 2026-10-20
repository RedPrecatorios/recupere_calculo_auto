"""Helpers HTTP com proxy rotativo (Webshare) e fallback direto."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import structlog
from dotenv import load_dotenv
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ProxyError, Timeout

from recupere_monday.proxy_pool import get_shared_proxy_pool, resolve_proxies_list_path

load_dotenv()

logger = structlog.get_logger(__name__)

_PROXY_RETRY_EXCEPTIONS = (ProxyError, RequestsConnectionError, Timeout)


@dataclass(frozen=True)
class ProxyRuntimeConfig:
    enabled: bool = True
    max_attempts: int = 3
    explicit_url: str | None = None
    list_path: Path | None = None
    allow_direct_fallback: bool = True


_runtime: ProxyRuntimeConfig | None = None


def env_truthy(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def configure_proxy_from_settings(settings: Any | None = None) -> ProxyRuntimeConfig:
    """
    Fonte única de verdade: Settings (Pydantic) sobrescreve o ambiente.
    Chamado no boot do CLI.
    """
    global _runtime
    if settings is None:
        try:
            from recupere_monday.config import get_settings

            settings = get_settings()
        except Exception:  # noqa: BLE001
            settings = None

    if settings is None:
        _runtime = ProxyRuntimeConfig(
            enabled=env_truthy("HTTP_USE_PROXY", default=True),
            max_attempts=_max_proxy_attempts_from_env(),
            explicit_url=_explicit_proxy_url_from_env(),
            list_path=resolve_proxies_list_path(),
            allow_direct_fallback=env_truthy(
                "HTTP_PROXY_ALLOW_DIRECT_FALLBACK", default=True
            ),
        )
    else:
        list_path = Path(settings.webshare_proxies_list).expanduser().resolve()
        _runtime = ProxyRuntimeConfig(
            enabled=bool(settings.http_use_proxy),
            max_attempts=int(settings.http_proxy_max_attempts),
            explicit_url=(settings.http_proxy_url or "").strip() or None,
            list_path=list_path,
            allow_direct_fallback=env_truthy(
                "HTTP_PROXY_ALLOW_DIRECT_FALLBACK", default=True
            ),
        )
        os.environ["HTTP_USE_PROXY"] = "true" if _runtime.enabled else "false"
        os.environ["WEBSHARE_PROXIES_LIST"] = str(list_path)
        os.environ["HTTP_PROXY_MAX_ATTEMPTS"] = str(_runtime.max_attempts)
        if _runtime.explicit_url:
            os.environ["HTTP_PROXY_URL"] = _runtime.explicit_url
        else:
            os.environ.pop("HTTP_PROXY_URL", None)

    # Evita misturar com gateway rotativo legado do SO
    os.environ.pop("HTTP_PROXY", None)
    os.environ.pop("HTTPS_PROXY", None)
    get_shared_proxy_pool(force_reload=True)
    logger.info(
        "proxy_runtime_configured",
        enabled=_runtime.enabled,
        max_attempts=_runtime.max_attempts,
        list_path=str(_runtime.list_path) if _runtime.list_path else None,
        explicit=bool(_runtime.explicit_url),
        allow_direct_fallback=_runtime.allow_direct_fallback,
    )
    return _runtime


def sync_proxy_env_from_settings() -> None:
    """Compatibilidade: alias de configure_proxy_from_settings()."""
    configure_proxy_from_settings()


def get_proxy_runtime() -> ProxyRuntimeConfig:
    global _runtime
    if _runtime is None:
        return configure_proxy_from_settings()
    return _runtime


def _max_proxy_attempts_from_env() -> int:
    try:
        return max(1, int(os.getenv("HTTP_PROXY_MAX_ATTEMPTS") or 3))
    except ValueError:
        return 3


def _explicit_proxy_url_from_env() -> str | None:
    raw = (os.getenv("HTTP_PROXY_URL") or "").strip()
    return raw or None


def is_proxy_enabled() -> bool:
    return get_proxy_runtime().enabled


def iter_proxy_dicts(
    *,
    max_attempts: int | None = None,
    include_direct_fallback: bool | None = None,
) -> list[dict[str, str] | None]:
    """
    Plano de proxies para uma operação HTTP.

    Ordem: proxies do pool (round-robin) e, por padrão, fallback sem proxy.
    """
    runtime = get_proxy_runtime()
    if not runtime.enabled:
        return [None]

    allow_direct = (
        runtime.allow_direct_fallback
        if include_direct_fallback is None
        else include_direct_fallback
    )

    if runtime.explicit_url:
        plan: list[dict[str, str] | None] = [
            {"http": runtime.explicit_url, "https": runtime.explicit_url},
        ]
        if allow_direct:
            plan.append(None)
        return plan

    attempts = max_attempts if max_attempts is not None else runtime.max_attempts
    pool = get_shared_proxy_pool()
    out: list[dict[str, str] | None] = [
        ep.as_requests_proxies() for ep in pool.candidates(max_attempts=attempts)
    ]
    if allow_direct:
        out.append(None)
    return out or [None]


def next_sticky_proxies(
    *,
    max_attempts: int | None = None,
) -> dict[str, str] | None:
    """
    Um único dict de proxies (ou None) para sessão com cookies.

    Round-robin no pool; se desabilitado / lista vazia, retorna None.
    """
    plan = iter_proxy_dicts(
        max_attempts=1 if max_attempts is None else max_attempts,
        include_direct_fallback=False,
    )
    if not plan:
        return None
    return plan[0]


def prepare_session(session: requests.Session) -> requests.Session:
    """Evita que HTTP_PROXY/HTTPS_PROXY do SO sobrescrevam a escolha do app."""
    session.trust_env = False
    return session


def proxy_label(proxies: dict[str, str] | None) -> str:
    if not proxies:
        return "direct"
    url = proxies.get("https") or proxies.get("http") or ""
    if "@" in url:
        return url.split("@", 1)[1]
    return url or "proxy"


def request_with_proxies(
    session: requests.Session,
    method: str,
    url: str,
    *,
    proxies: dict[str, str] | None = None,
    **kwargs: Any,
) -> requests.Response:
    """Request único com proxies sticky (incluindo None = direto)."""
    return session.request(method, url, proxies=proxies, **kwargs)


def request_with_proxy_rotation(
    session: requests.Session,
    method: str,
    url: str,
    *,
    max_attempts: int | None = None,
    **kwargs: Any,
) -> requests.Response:
    """Tenta N proxies do pool e depois conexão direta (se permitido)."""
    last_error: BaseException | None = None
    for proxies in iter_proxy_dicts(max_attempts=max_attempts):
        try:
            return session.request(method, url, proxies=proxies, **kwargs)
        except _PROXY_RETRY_EXCEPTIONS as exc:
            last_error = exc
            logger.warning(
                "http_proxy_attempt_failed",
                method=method,
                url=url,
                proxy=proxy_label(proxies),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            continue

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"falha HTTP sem resposta: {method} {url}")
