from __future__ import annotations

import random
import time
from collections import OrderedDict
from threading import Lock
from typing import Any, ClassVar
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class JsonApiClient:
    _host_last_request: ClassVar[dict[str, float]] = {}
    _rate_lock: ClassVar[Lock] = Lock()

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout: float = 20,
        minimum_interval: float = 0.1,
        max_cache_entries: int = 128,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.minimum_interval = max(0.0, minimum_interval)
        self.max_cache_entries = max(0, max_cache_entries)
        self._cache: OrderedDict[str, tuple[str | None, Any]] = OrderedDict()
        retry = Retry(
            total=4,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        if hasattr(self.session, "mount"):
            adapter = HTTPAdapter(max_retries=retry)
            self.session.mount("https://", adapter)
            self.session.mount("http://", adapter)

    def get_json(self, url: str, **kwargs: Any) -> Any:
        self._wait_for_host(url)
        headers = dict(kwargs.pop("headers", {}) or {})
        params = kwargs.get("params") or {}
        cache_key = f"{url}?{sorted(params.items())!r}"
        cached = self._cache.get(cache_key)
        if cached:
            self._cache.move_to_end(cache_key)
        if cached and cached[0]:
            headers["If-None-Match"] = cached[0]
        response = self.session.get(url, timeout=self.timeout, headers=headers, **kwargs)
        if response.status_code == 304 and cached:
            return cached[1]
        response.raise_for_status()
        payload = response.json()
        etag = response.headers.get("ETag")
        if etag:
            self._cache[cache_key] = (etag, payload)
            self._cache.move_to_end(cache_key)
            while len(self._cache) > self.max_cache_entries:
                self._cache.popitem(last=False)
        return payload

    def _wait_for_host(self, url: str) -> None:
        host = urlparse(url).netloc
        with self._rate_lock:
            now = time.monotonic()
            wait = self.minimum_interval - (now - self._host_last_request.get(host, 0.0))
            if wait > 0:
                time.sleep(wait + random.uniform(0, min(0.025, self.minimum_interval)))
            self._host_last_request[host] = time.monotonic()
