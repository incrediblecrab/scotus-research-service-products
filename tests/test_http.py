import time

import httpx
import pytest

from scotus_products import http
from scotus_products.http import USER_AGENT, Blocked, Disallowed, Fetcher, QuotaExhausted, Unavailable


def fetcher_with(handler, intervals=None):
    """A real Fetcher whose client answers from handler instead of the network: its headers, redirect policy and pacing are the real ones."""
    fetcher = Fetcher(intervals=intervals)
    fetcher.client._transport = httpx.MockTransport(handler)
    return fetcher


def test_robots_disallowed_paths_are_never_requested():
    seen = []
    fetcher = fetcher_with(lambda request: seen.append(request) or httpx.Response(200))
    for path in ("/images/seal.png", "/rss/news.xml", "/cdn/x.js", "/Images/Seal.png"):
        with pytest.raises(Disallowed):
            fetcher.get("https://www.supremecourt.gov" + path)
    assert seen == []


def test_only_https():
    fetcher = fetcher_with(lambda request: httpx.Response(200))
    with pytest.raises(ValueError):
        fetcher.get("http://www.supremecourt.gov/opinions/in-chambers.aspx")


def test_requests_identify_the_pipeline_and_redirects_are_not_followed():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"Location": "/opinions/USReports.aspx"})

    response = fetcher_with(handler, intervals={"www.supremecourt.gov": 0}).get("https://www.supremecourt.gov/opinions/slipopinion/10")
    assert response.status_code == 302
    assert len(seen) == 1 and seen[0].headers["user-agent"] == USER_AGENT
    assert "github.com/incrediblecrab/scotus-research-service-products" in USER_AGENT


def test_requests_to_a_host_are_paced():
    starts = []

    def handler(request):
        starts.append(time.monotonic())
        return httpx.Response(200, content=b"ok")

    fetcher = fetcher_with(handler, intervals={"www.supremecourt.gov": 0.25})
    for _ in range(4):
        fetcher.get("https://www.supremecourt.gov/a.pdf")
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert min(gaps) >= 0.24, gaps


def test_the_default_pace_is_above_the_crawl_delay():
    assert http.HOST_INTERVAL["www.supremecourt.gov"] >= 1.0 and http.DEFAULT_INTERVAL >= 1.0


def test_pace_counts_from_the_end_of_a_slow_request():
    starts, ends = [], []

    def handler(request):
        starts.append(time.monotonic())
        time.sleep(0.3)
        ends.append(time.monotonic())
        return httpx.Response(200)

    fetcher = fetcher_with(handler, intervals={"www.supremecourt.gov": 0.2})
    fetcher.get("https://www.supremecourt.gov/a.pdf")
    fetcher.get("https://www.supremecourt.gov/b.pdf")
    assert starts[1] - ends[0] >= 0.19


def test_403_stops_the_run():
    fetcher = fetcher_with(lambda request: httpx.Response(403), intervals={"www.supremecourt.gov": 0})
    with pytest.raises(Blocked):
        fetcher.get("https://www.supremecourt.gov/a.pdf")


def test_repeated_429_stops_the_run(monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda seconds: None)
    fetcher = fetcher_with(lambda request: httpx.Response(429, headers={"Retry-After": "0"}), intervals={"www.supremecourt.gov": 0})
    with pytest.raises(QuotaExhausted):
        fetcher.get("https://www.supremecourt.gov/a.pdf")


def test_server_errors_are_retried_then_raised(monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda seconds: None)
    calls = []
    fetcher = fetcher_with(lambda request: calls.append(1) or httpx.Response(503), intervals={"www.supremecourt.gov": 0})
    with pytest.raises(Unavailable):
        fetcher.get("https://www.supremecourt.gov/a.pdf")
    assert len(calls) == fetcher.max_retries + 1


def test_a_server_error_that_clears_returns_the_answer(monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda seconds: None)
    answers = iter([httpx.Response(502), httpx.Response(200, content=b"%PDF-1.4")])
    fetcher = fetcher_with(lambda request: next(answers), intervals={"www.supremecourt.gov": 0})
    response = fetcher.get("https://www.supremecourt.gov/a.pdf")
    assert response.content == b"%PDF-1.4" and fetcher.bytes == 8
