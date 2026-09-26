"""Polite HTTP client for www.supremecourt.gov: one pacing clock per host, bounded retries, and a refusal to request any path robots.txt disallows.

robots.txt (read September 25, 2026) sets Crawl-delay: 1 and disallows /images/, /rss/ and /cdn/ for every agent. A request waits until a little over one second has passed both since the previous request to the host started and since it ended, so a long download is followed by the full delay too.
"""

import threading
import time
from urllib.parse import urlsplit

import httpx

USER_AGENT = "scotus-research-service-products/0.1 (+https://github.com/incrediblecrab/scotus-research-service-products)"
HOST_INTERVAL = {"www.supremecourt.gov": 1.1, "supremecourt.gov": 1.1}
DEFAULT_INTERVAL = 1.1
DISALLOWED = ("/images/", "/rss/", "/cdn/")


class Blocked(RuntimeError):
    """The site refused the client (403), which says nothing about the one document asked for."""


class QuotaExhausted(RuntimeError):
    """The host keeps answering 429; the caller should stop for this run."""


class Unavailable(RuntimeError):
    """Server errors or network failures outlasted every retry."""


class Disallowed(ValueError):
    """robots.txt disallows the path."""


class Fetcher:
    def __init__(self, intervals=None, max_retries=5, timeout=300.0):
        self.intervals = dict(HOST_INTERVAL, **(intervals or {}))
        self.max_retries = max_retries
        # Redirects are not followed: a term page that does not exist answers 302 to /opinions/USReports.aspx, and the caller has to see that.
        self.client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=False)
        self._next_at = {}
        self._lock = threading.Lock()
        self.requests = {}
        self.bytes = 0

    def _pace(self, host):
        interval = self.intervals.get(host, DEFAULT_INTERVAL)
        with self._lock:
            now = time.monotonic()
            at = max(now, self._next_at.get(host, 0.0))
            self._next_at[host] = at + interval
        if at > now:
            time.sleep(at - now)

    def _done(self, host):
        interval = self.intervals.get(host, DEFAULT_INTERVAL)
        with self._lock:
            self._next_at[host] = max(self._next_at.get(host, 0.0), time.monotonic() + interval)

    def request(self, method, url, headers=None):
        """One request with pacing and retries. Returns the response for any status below 500 except 403 and 429."""
        parts = urlsplit(url)
        if parts.scheme != "https":
            raise ValueError(f"not an https URL: {url}")
        if any(parts.path.lower().startswith(prefix) for prefix in DISALLOWED):
            raise Disallowed(f"robots.txt disallows {parts.path}")
        host = parts.hostname or ""
        last_error = None
        for attempt in range(self.max_retries + 1):
            self._pace(host)
            self.requests[host] = self.requests.get(host, 0) + 1
            try:
                response = self.client.request(method, url, headers=headers)
            except httpx.TransportError as error:
                last_error = Unavailable(f"{type(error).__name__} from {host}{parts.path}")
                time.sleep(min(300, 2 ** (attempt + 2)))
                continue
            finally:
                self._done(host)
            status = response.status_code
            if status == 403:
                raise Blocked(f"403 from {host}{parts.path}")
            if status == 429:
                last_error = QuotaExhausted(f"429 from {host}")
                if attempt >= 2:
                    raise last_error
                time.sleep(_retry_after(response, default=60 * (attempt + 1)))
                continue
            if status >= 500:
                last_error = Unavailable(f"HTTP {status} from {host}{parts.path}")
                time.sleep(min(300, 2 ** (attempt + 2)))
                continue
            self.bytes += len(response.content)
            return response
        raise last_error

    def get(self, url):
        return self.request("GET", url)

    def head(self, url):
        return self.request("HEAD", url)

    def close(self):
        self.client.close()


def _retry_after(response, default):
    value = response.headers.get("Retry-After", "")
    return min(3600, int(value)) if value.isdigit() else default
