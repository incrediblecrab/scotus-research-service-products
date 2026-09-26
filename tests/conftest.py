"""Shared helpers: saved real pages and PDFs, and stand-ins for the site that serve them without a network."""

import gzip
import hashlib
import tempfile
from pathlib import Path

import pytest

from scotus_products.sources import Collection, Unit, iso_date

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://www.supremecourt.gov/"


def page_bytes(name):
    return gzip.decompress((FIXTURES / "pages" / f"{name}.html.gz").read_bytes())


def pdf_bytes(name):
    return (FIXTURES / "pdfs" / name).read_bytes()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class Response:
    def __init__(self, status_code=200, content=b"", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}


class FakeSite:
    """Serves {url: bytes}; a missing URL answers 404. ETag and Last-Modified follow the bytes, so replacing a file moves them. Counts every GET and HEAD."""

    def __init__(self, files=None):
        self.files = dict(files or {})
        self.gets, self.heads = [], []
        self.requests = {}
        self.bytes = 0

    def _response(self, url, body):
        data = self.files.get(url)
        if data is None:
            return Response(404, b"not found")
        headers = {"content-type": "application/pdf", "content-length": str(len(data)), "etag": f'"{sha256(data)[:12]}"', "last-modified": "Mon, 18 Mar 2024 22:54:16 GMT"}
        return Response(200, data if body else b"", headers)

    def get(self, url):
        self.gets.append(url)
        self.requests["www.supremecourt.gov"] = self.requests.get("www.supremecourt.gov", 0) + 1
        response = self._response(url, True)
        self.bytes += len(response.content)
        return response

    def head(self, url):
        self.heads.append(url)
        self.requests["www.supremecourt.gov"] = self.requests.get("www.supremecourt.gov", 0) + 1
        return self._response(url, False)

    def close(self):
        pass


def typed(unit, today=None):
    entry = unit.entries[0]
    return {"term": entry.get("term"), "date": iso_date(entry.get("date"), today), "docket": entry.get("docket"), "title": entry.get("link_text")}


def collection(mutable=False, html=False, name="test-documents"):
    return Collection(name, "Test Documents", BASE + "test/", pages=None, parse=None, typed=typed, mutable=mutable, html=html)


def unit(path, partition="OT2023", term=2023, **entry):
    url = BASE + path
    return Unit(path.lower(), url, partition, [{"listing": BASE + "test/", "href": "/" + path, "link_text": entry.pop("link_text", path.rsplit("/", 1)[-1]), "term": term, **entry}])


class FakeListing:
    """What sources.Listing returns, from a list of units; pages records one page per partition with its entry count."""

    def __init__(self, units, pages=None):
        self.units = {u.id: u for u in units}
        self.pages = pages if pages is not None else {BASE + "test/": {"term": None, "entries": len(units)}}

    def list_all(self):
        # A fresh copy each time, as a real listing parses the pages anew: the pipeline reassigns partitions on the units it gets.
        units = {uid: Unit(u.id, u.url, u.partition, [dict(e) for e in u.entries]) for uid, u in self.units.items()}
        return {"count": len(units), "entries": sum(len(u.entries) for u in units.values()), "pages": len(self.pages)}, units


@pytest.fixture
def born_digital():
    return pdf_bytes("inchambers.pdf")


@pytest.fixture
def scanned():
    return pdf_bytes("oj-page3.pdf")


@pytest.fixture(autouse=True)
def stores_are_closed(tmp_path_factory, monkeypatch):
    """A store left open leaves its empty staging directory behind, so each test gets its own temporary directory and must leave no staging directory in it."""
    scratch = tmp_path_factory.mktemp("tempdir")
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    yield
    left = sorted(p.name for p in scratch.glob("scotus-products-*"))
    assert not left, f"staging left behind by a store never closed: {left}"
