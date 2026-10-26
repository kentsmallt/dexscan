"""Small client for the public DEX Screener pair endpoints.

Documented at https://docs.dexscreener.com/api/reference. The four pair/token
routes allow 300 requests per minute and do not use an API key. This client
stays under that ceiling and retries a single HTTP 429.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from dexscan import __version__
from dexscan.summary import extract_pairs

BASE_URL = "https://api.dexscreener.com"
HOST = "api.dexscreener.com"
MAX_BODY = 8 * 1024 * 1024
_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

Opener = Callable[..., object]


class DexscreenerError(Exception):
    """The public API could not be read."""


class Pace:
    """Block so a process stays within ``max_calls`` per ``period`` seconds."""

    def __init__(
        self,
        max_calls: int = 250,
        period: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be >= 1")
        if period <= 0:
            raise ValueError("period must be > 0")
        self.max_calls = max_calls
        self.period = period
        self._sleep = sleep
        self._now = now
        self._hits: list[float] = []

    def wait(self) -> None:
        now = self._now()
        self._hits = [hit for hit in self._hits if now - hit < self.period]
        if len(self._hits) >= self.max_calls:
            delay = self.period - (now - self._hits[0])
            if delay > 0:
                self._sleep(delay)
            now = self._now()
            self._hits = [hit for hit in self._hits if now - hit < self.period]
        self._hits.append(self._now())


class _PinHost(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or host != HOST:
            raise DexscreenerError(
                "refusing redirect off the public DEX Screener API host"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_opener() -> Opener:
    return urllib.request.build_opener(_PinHost()).open


def _segment(value: str, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(
            f"invalid {label}: use 1-128 letters, digits, '.', '_', ':', or '-'"
        )
    return urllib.parse.quote(value, safe="")


def _check_query(query: str) -> str:
    if not isinstance(query, str):
        raise ValueError("query must be a string")
    text = query.strip()
    if not text or len(text) > 200:
        raise ValueError("query must be 1-200 characters")
    if any(ord(ch) < 32 for ch in text):
        raise ValueError("query contains control characters")
    return text


def _read_limited(resp: object) -> bytes:
    read = getattr(resp, "read")
    chunks: list[bytes] = []
    total = 0
    while True:
        block = read(65536)
        if not block:
            break
        total += len(block)
        if total > MAX_BODY:
            raise DexscreenerError("response exceeded 8 MiB")
        chunks.append(block)
    return b"".join(chunks)


def _retry_after(headers: object) -> float:
    raw = None
    getter = getattr(headers, "get", None)
    if getter is not None:
        raw = getter("Retry-After")
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return 5.0
    if seconds != seconds or seconds in (float("inf"), float("-inf")):
        return 5.0
    return min(max(seconds, 0.0), 30.0)


class DexscreenerClient:
    def __init__(
        self,
        opener: Opener | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
        max_calls: int = 250,
        period: float = 60.0,
        timeout: float = 20.0,
        user_agent: str | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be > 0")
        self._opener = opener or _default_opener()
        self._sleep = sleep
        self._pace = Pace(max_calls=max_calls, period=period, sleep=sleep, now=now)
        self.timeout = timeout
        self.user_agent = user_agent or (
            f"dexscan/{__version__} (+https://github.com/kentsmallt/dexscan)"
        )

    def search(self, query: str) -> tuple[list[dict], str]:
        text = _check_query(query)
        path = "/latest/dex/search?" + urllib.parse.urlencode({"q": text})
        return self._get(path), "/latest/dex/search"

    def pair(self, chain: str, pair_address: str) -> tuple[list[dict], str]:
        path = f"/latest/dex/pairs/{_segment(chain, 'chain')}/{_segment(pair_address, 'pair address')}"
        return self._get(path), path

    def token_pairs(self, chain: str, token_address: str) -> tuple[list[dict], str]:
        path = (
            f"/token-pairs/v1/{_segment(chain, 'chain')}/"
            f"{_segment(token_address, 'token address')}"
        )
        return self._get(path), path

    def _get(self, path: str) -> list[dict]:
        body, status, headers = self._request(path)
        if status == 429:
            self._sleep(_retry_after(headers))
            body, status, _headers = self._request(path)
        if status != 200:
            raise DexscreenerError(f"HTTP {status} from {path.split('?', 1)[0]}")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DexscreenerError(f"invalid JSON from {path.split('?', 1)[0]}") from exc
        return extract_pairs(payload)

    def _request(self, path: str) -> tuple[bytes, int, object]:
        self._pace.wait()
        url = BASE_URL + path
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": self.user_agent,
            },
            method="GET",
        )
        try:
            resp = self._opener(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            resp = exc
        except urllib.error.URLError as exc:
            raise DexscreenerError(f"request failed: {exc.reason}") from exc
        try:
            status = getattr(resp, "status", None) or resp.getcode()
            body = _read_limited(resp)
            headers = getattr(resp, "headers", {})
        finally:
            close = getattr(resp, "close", None)
            if close:
                close()
        return body, int(status), headers
