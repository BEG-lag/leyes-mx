"""A small, polite HTTP client for government and archive servers.

* Generic, honest User-Agent that points to the project.
* Timeouts on every request.
* Retries with exponential backoff on network errors and 429/5xx.
* A minimum delay between consecutive requests to the same host.
"""

from __future__ import annotations

import http.client
import logging
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

USER_AGENT = "leyes-mx/0.1 (+https://github.com/BEG-lag/leyes-mx)"
DEFAULT_TIMEOUT = 60.0
DEFAULT_DELAY = 2.0
RETRY_STATUS = {429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    pass


class PoliteClient:
    def __init__(
        self,
        delay: float = DEFAULT_DELAY,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = 4,
        backoff: float = 5.0,
        user_agent: str = USER_AGENT,
    ):
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.user_agent = user_agent
        self._last: dict[str, float] = {}

    def _wait_turn(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            remaining = self.delay - (time.monotonic() - last)
            if remaining > 0:
                time.sleep(remaining)
        self._last[host] = time.monotonic()

    def get(self, url: str) -> bytes:
        host = urlsplit(url).netloc
        attempt = 0
        while True:
            self._wait_turn(host)
            request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                if exc.code not in RETRY_STATUS or attempt >= self.retries:
                    raise FetchError(f"HTTP {exc.code} for {url}") from exc
                reason = f"HTTP {exc.code}"
            except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError) as exc:
                if attempt >= self.retries:
                    raise FetchError(f"{exc} for {url}") from exc
                reason = str(exc)
            wait = self.backoff * (2**attempt)
            log.warning("retrying %s in %.0fs (%s)", url, wait, reason)
            time.sleep(wait)
            attempt += 1

    def get_text(self, url: str, encoding: str | None = None) -> str:
        data = self.get(url)
        if encoding:
            return data.decode(encoding, errors="replace")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("cp1252", errors="replace")
