"""Minimal cached client for the SerpApi JSON search endpoint.

Every request is keyed by a SHA-256 of its normalized parameters (excluding
``api_key``). A cached response is returned without any HTTP request. An
uncached successful response is persisted as a *sanitized raw response* in
``data/raw/`` (never overwriting an earlier raw file) and in ``data/cache/``,
then returned.

Sanitized raw response: the complete SerpApi response structure and content,
before any application-level transformation, except that secrets are
sanitized before persistence. Concretely, the response bytes are stored as
received with every occurrence of the configured API key replaced by
``[REDACTED]``. Nothing is trimmed, reformatted, or normalized.

The API key is only ever placed in the outgoing request. It is never part of
the cache key, filenames, persisted files, or our error messages, and this
module does no logging. The module does not change any logging configuration;
note that httpx itself logs request URLs (which contain the key) at INFO level
if the application enables that.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
API_KEY_ENV_VAR = "SERPAPI_API_KEY"
REQUEST_TIMEOUT_SECONDS = 60.0

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = PROJECT_ROOT / "data" / "cache"
RAW_DIR = PROJECT_ROOT / "data" / "raw"

RAW_KEY_PREFIX_LENGTH = 16
REDACTED = "[REDACTED]"


class SerpApiError(Exception):
    """A SerpApi request failed or returned an unusable response."""


class MissingApiKeyError(SerpApiError):
    """SERPAPI_API_KEY is not configured."""


def _normalize_value(value: Any) -> str:
    # Mirror how httpx encodes query values so equivalent requests share a key.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    raise TypeError(f"Unsupported parameter value type: {type(value).__name__}")


def normalize_params(params: dict[str, Any]) -> dict[str, str]:
    """Return request parameters without ``api_key``, None values, sorted by name."""
    return {
        name: _normalize_value(value)
        for name, value in sorted(params.items())
        if name != "api_key" and value is not None
    }


def cache_key(params: dict[str, Any]) -> str:
    """SHA-256 hex digest of the normalized parameters (``api_key`` excluded)."""
    canonical = json.dumps(
        normalize_params(params), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _safe_engine(params: dict[str, Any]) -> str:
    engine = params.get("engine")
    if not isinstance(engine, str) or not engine.strip():
        raise ValueError("params must include a non-empty 'engine'")
    return re.sub(r"[^A-Za-z0-9_-]", "_", engine.strip())


def _load_api_key() -> str:
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.environ.get(API_KEY_ENV_VAR, "").strip()
    if not api_key:
        raise MissingApiKeyError(
            f"{API_KEY_ENV_VAR} is not set. Add it to .env (see .env.example)."
        )
    return api_key


def _redact(text: str, api_key: str) -> str:
    return text.replace(api_key, REDACTED)


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_bytes(content)
    os.replace(tmp_path, path)


def _raw_path(raw_dir: Path, engine: str, key: str) -> Path:
    """First unused raw filename, so an existing raw response is never overwritten."""
    stem = f"{engine}_{key[:RAW_KEY_PREFIX_LENGTH]}"
    path = raw_dir / f"{stem}.json"
    counter = 1
    while path.exists():
        path = raw_dir / f"{stem}_{counter}.json"
        counter += 1
    return path


def _cache_path(params: dict[str, Any], cache_dir: Path) -> Path:
    return cache_dir / f"{_safe_engine(params)}_{cache_key(params)}.json"


def is_cached(params: dict[str, Any], *, cache_dir: Path = CACHE_DIR) -> bool:
    """Whether ``search(params)`` would be answered from the cache, with no HTTP request."""
    return _cache_path(params, cache_dir).exists()


def api_key_configured() -> bool:
    """Whether a SerpApi key is available. Never returns or logs the key."""
    try:
        _load_api_key()
    except MissingApiKeyError:
        return False
    return True


def search(
    params: dict[str, Any],
    *,
    client: httpx.Client | None = None,
    cache_dir: Path = CACHE_DIR,
    raw_dir: Path = RAW_DIR,
) -> dict[str, Any]:
    """Run a SerpApi search, returning the cached response when one exists.

    ``params`` are SerpApi query parameters and must include ``engine``. Any
    ``api_key`` in ``params`` is ignored; the key is always read from the
    environment / ``.env``.
    """
    engine = _safe_engine(params)
    key = cache_key(params)
    cache_path = _cache_path(params, cache_dir)

    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise SerpApiError(f"Cached response is not valid JSON: {cache_path}") from None

    api_key = _load_api_key()
    request_params = {**normalize_params(params), "api_key": api_key}

    # Exceptions are re-raised `from None` because httpx messages and
    # tracebacks include the request URL, which contains the API key.
    try:
        if client is None:
            with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as default_client:
                response = default_client.get(SERPAPI_ENDPOINT, params=request_params)
        else:
            response = client.get(SERPAPI_ENDPOINT, params=request_params)
    except httpx.HTTPError as exc:
        raise SerpApiError(f"SerpApi request failed ({type(exc).__name__})") from None

    body = response.content
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise SerpApiError(
            f"SerpApi returned invalid JSON (HTTP {response.status_code})"
        ) from None

    if not isinstance(data, dict):
        raise SerpApiError(
            f"SerpApi returned unexpected JSON type {type(data).__name__} "
            f"(HTTP {response.status_code})"
        )
    if "error" in data:
        message = _redact(str(data["error"]), api_key)
        raise SerpApiError(f"SerpApi error (HTTP {response.status_code}): {message}")
    if response.status_code != 200:
        raise SerpApiError(f"SerpApi returned HTTP {response.status_code}")

    # Sanitized raw response: the body as received, with any occurrence of the
    # API key redacted so the key is never persisted.
    stored = body.replace(api_key.encode("utf-8"), REDACTED.encode("utf-8"))
    _write_atomic(_raw_path(raw_dir, engine, key), stored)
    _write_atomic(cache_path, stored)

    return json.loads(stored)
