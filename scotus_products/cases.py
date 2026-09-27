"""Builds the case-centric `incrediblecrab/scotus-cases` dataset from the SCOTUS document datasets and the Court's public docket pages.

The document datasets hold one row per file. This one holds one row per `(term, docket)`: the case's docket page as served with the fields parsed from it, the case's opinions and argument transcripts copied with their text from the document datasets, and links to its argument audio. Where the opinion listings link one file of the United States Reports at the page each opinion starts on, a bound volume or a preliminary print, each case gets the pages of its own opinion, found from those pages and the running heads (see shared_doc).

A run assembles a term again when one of its source partitions changed, when BUILDER changed, or when one of its docket pages is due: a page of the current or previous term once CURRENT_RECHECK has passed, a found page of an older term after RECHECK["found"], and a page that was not found after RECHECK["missing"], or at once if the builder now knows an address for it that was not asked. A docket that is not due, or that the run had no time or permission to reach, keeps the page the last run stored, so the next run carries on where this one stopped. Parsed fields are always taken from the stored page by this builder, so a parser change reaches every row without asking the Court again.
"""

import argparse
import copy
import hashlib
import json
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urljoin

import pyarrow as pa
import pyarrow.parquet as pq
from huggingface_hub import HfFileSystem, hf_hub_download
from huggingface_hub.errors import EntryNotFoundError, RemoteEntryNotFoundError

from .cli import step_outputs, trusted_publishing
from .http import Blocked, Fetcher, QuotaExhausted, Unavailable
from .markup import field, parse, render, resolve, squash
from .sources import BASE, COLLECTIONS, iso_date, long_date, repo_id
from .store import MANIFEST, HubStore, LocalStore, sha256_file

REPO_ID = repo_id("cases")
# Bumped when row assembly changes, so every term is assembled again; its docket pages are carried over, not fetched again.
BUILDER = 3
CURRENT_RECHECK = timedelta(hours=6)
RECHECK = {"found": timedelta(days=7), "missing": timedelta(days=30)}
OPINION_COLLECTIONS = ("opinions-of-the-court", "opinions-relating-to-orders", "in-chambers-opinions")
SOURCE_COLLECTIONS = OPINION_COLLECTIONS + ("argument-transcripts", "granted-noted-cases-list")
SOURCE_COLUMNS = ["id", "partition", "url", "term", "date", "docket", "title", "entries", "text_source", "text", "ocr_text"]
SCHEMA = pa.schema([
    ("term", pa.int32()),
    ("docket", pa.string()),
    ("case_name", pa.string()),
    ("case_name_source", pa.string()),
    ("sources", pa.large_string()),
    ("docket_url", pa.string()),
    ("docket_format", pa.string()),
    ("docket_found", pa.bool_()),
    ("docket_fetched_at", pa.string()),
    ("docket_etag", pa.string()),
    ("docket_last_modified", pa.string()),
    ("docket_html", pa.large_binary()),
    ("docket_text", pa.large_string()),
    ("docketed_date", pa.string()),
    ("linked_with", pa.string()),
    ("lower_court", pa.string()),
    ("lower_court_case_numbers", pa.large_string()),
    ("lower_court_decision_date", pa.string()),
    ("questions_presented_url", pa.string()),
    ("proceedings", pa.large_string()),
    ("attorneys", pa.large_string()),
    ("opinions", pa.large_string()),
    ("transcripts", pa.large_string()),
    ("audio", pa.large_string()),
    ("source_rows", pa.large_string()),
])
JSON_COLUMNS = {"sources", "lower_court_case_numbers", "proceedings", "opinions", "transcripts", "audio", "source_rows"}
DOCKET_RE = re.compile(r"(?<![A-Za-z0-9-])(?:\d{2,4}-\d+|\d{2}A\d+|\d{2}O\d+|\d{1,3},?\s*Orig\.)(?![A-Za-z0-9-])")
# An original-jurisdiction docket as the transcripts' listings and file names also write it: 141-Orig, 141 orig, 141orig, 141_orig, 105original.
ORIGINAL = re.compile(r"(?i)(?<![0-9A-Za-z])(\d{1,3})\s*[,_-]?\s*orig(?:inal|\.)?(?![a-z])")
PROCEEDING_DATE = re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{2}) (\d{4})$")
MONTHS = {name: i for i, name in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
NOT_FOUND = b"404 - Page Not Found"
# Pages of the United States Reports that begin a part no opinion runs into: a Reporter's Note, the orders, the Court's allotment and rules orders, and the index.
SECTION_BREAK = re.compile(r"Reporter[’']s Note|ORDERS FOR |SUPREME COURT OF THE UNITED STATES$|I N D E X|(?:[ivxlc]+ )?INDEX(?: [ivxlc]+)?$")
WATERMARK = "Page Proof Pending Publication"
ORDERS_DATELINE = re.compile(r"(?:\d+ U\. S\. )?(?:January|February|March|April|May|June|July|August|September|October|November|December) \d")
# What the manifest keeps of each docket page; the bytes are in the row.
PAGE_KEYS = ("url", "format", "found", "status", "fetched_at", "etag", "last_modified", "html_sha256", "tried")


class UnexpectedStatus(RuntimeError):
    """A docket page answered with a status that says neither that it is there nor that it is gone."""


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def loads(value, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def parquet_path(key):
    return f"data/{key}.parquet"


def out_of_time(deadline):
    return deadline is not None and time.monotonic() > deadline


def source_root(base, collection):
    return Path(base) / COLLECTIONS[collection].repo_id.split("/")[1] / collection


def source_manifest(args, collection):
    if args.sources_local:
        path = source_root(args.sources_local, collection) / MANIFEST
        return json.loads(path.read_text()) if path.exists() else None
    try:
        path = hf_hub_download(COLLECTIONS[collection].repo_id, f"{collection}/{MANIFEST}", repo_type="dataset", token=False)
    except (EntryNotFoundError, RemoteEntryNotFoundError):
        return None
    return json.loads(Path(path).read_text())


def source_rows(args, collection, entry, columns=SOURCE_COLUMNS):
    if args.sources_local:
        return pq.read_table(source_root(args.sources_local, collection) / entry["file"], columns=columns).to_pylist()
    with HfFileSystem(token=False).open(f"datasets/{COLLECTIONS[collection].repo_id}/{collection}/{entry['file']}", "rb") as handle:
        return pq.read_table(handle, columns=columns).to_pylist()


def source_manifests(args):
    out = {}
    for collection in SOURCE_COLLECTIONS:
        manifest = source_manifest(args, collection)
        if not manifest:
            raise SystemExit(f"{collection}: source manifest not found")
        out[collection] = manifest
    return out


def granted_rows(args, manifests):
    """The granted/noted lists, one row per term's list, read once per run: the collection keeps them all in one partition."""
    entry = manifests["granted-noted-cases-list"].get("partitions", {}).get("all")
    return source_rows(args, "granted-noted-cases-list", entry) if entry else []


def available_terms(manifests):
    terms = set()
    for collection, manifest in manifests.items():
        for key in manifest.get("partitions", {}):
            if key.startswith("OT") and key[2:].isdigit():
                terms.add(int(key[2:]))
        if collection == "granted-noted-cases-list":
            for entry in manifest.get("partitions", {}).values():
                lo, hi = entry.get("terms") or (None, None)
                if lo is not None and hi is not None:
                    terms.update(range(int(lo), int(hi) + 1))
    return sorted(terms)


def parse_terms(value, terms):
    if not value:
        return terms
    picked = []
    for part in value.split(","):
        part = part.strip().upper()
        if part:
            picked.append(int(part[2:] if part.startswith("OT") else part))
    return sorted(set(picked))


def order_terms(terms, manifest, current):
    """The current and previous terms first, as their dockets change most; then terms not yet built completely, newest first; then the rest, newest first."""
    def rank(term):
        entry = manifest.get("terms", {}).get(f"OT{term}") or {}
        group = 0 if term >= current - 1 else 1 if not entry.get("complete") else 2
        return group, -term
    return sorted(terms, key=rank)


def source_signature(manifests, granted, term):
    """What a term's rows are assembled from: each source partition's SHA-256, and for the granted/noted lists, which keep every term in one partition, a digest of this term's lists only."""
    out = {}
    for collection in SOURCE_COLLECTIONS:
        if collection == "granted-noted-cases-list":
            rows = sorted((row for row in granted if row.get("term") == term), key=lambda row: row["id"])
            if rows:
                digest = hashlib.sha256(dumps([[row["id"], row.get("text"), row.get("ocr_text")] for row in rows]).encode()).hexdigest()
                out[collection] = {"rows": len(rows), "sha256": digest}
            continue
        entry = manifests[collection].get("partitions", {}).get(f"OT{term}")
        if entry:
            out[collection] = {"partition": entry.get("file"), "sha256": entry.get("sha256")}
    return out


def spell_originals(text):
    """text with each original-jurisdiction docket written the way the Court's opinions write it, 141, Orig."""
    return ORIGINAL.sub(lambda m: f" {int(m.group(1))}, Orig. ", text or "")


def normalize_docket(text):
    """The docket as this dataset keys it: 24-1260, the application 25A1, the original case 141, Orig., which the Court's docket system calls 22O141."""
    value = squash(spell_originals(str(text or "")).replace("No.", "")).replace(" ", "")
    m = re.fullmatch(r"(\d{1,3}),?Orig\.?", value, re.I) or re.fullmatch(r"\d{2}O(\d+)", value)
    if m:
        return f"{int(m.group(1))}, Orig."
    return value


def docket_candidates(text):
    values = []
    for match in DOCKET_RE.finditer(text or ""):
        value = normalize_docket(match.group(0))
        if value not in values:
            values.append(value)
    return values


def row_dockets(row):
    """The dockets a source row names in its docket field and its file name: pdfs/transcripts/1970/43_orig_44_orig_10-19-1970.pdf names 43, Orig. and 44, Orig. When the file name names an original case, a regular-looking docket with the same number is the listing's name for that case: the OT2007 listing gives No. 134, Orig. as 06-134, and its file is 06-134orig.pdf."""
    named = docket_candidates(spell_originals((row.get("id") or "").rsplit("/", 1)[-1]))
    originals = {docket.split(",")[0] for docket in named if docket.endswith("Orig.")}
    listed = str(row.get("docket") or "")
    out = []
    for docket in docket_candidates(spell_originals(listed)) + named:
        regular = re.fullmatch(r"\d{2,4}-(\d+)", docket)
        if docket not in out and not (regular and str(int(regular.group(1))) in originals):
            out.append(docket)
    # A docket field that only starts with a docket: 11-398-Monday, 14-556-Question-1, 18A142T, and A-483, an application of OT1971.
    loose = re.match(r"\s*(\d{2,4}-\d+|\d{2}A\d+|A-\d+)(?![0-9])", listed)
    return out or ([loose.group(1)] if loose else [])


def docket_slug(docket):
    """The docket's name in the Court's docket system: an original case is 22O plus its number, whatever its term."""
    value = normalize_docket(docket)
    m = re.fullmatch(r"(\d{1,3}), Orig\.", value)
    return f"22O{m.group(1)}" if m else value


def iso_from_court_date(text):
    """ISO date of a docket date: May 08 2025 in a docket entry, May 8, 2025 in a docket's header."""
    text = squash((text or "").replace("\xa0", " "))
    m = PROCEEDING_DATE.match(text)
    if m:
        return f"{int(m.group(3)):04d}-{MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"
    return long_date(text)


def extract_case_name(text):
    lines = [line for line in text.splitlines() if squash(line) and not line.startswith("\xa0")]
    title = []
    taking = False
    for line in lines:
        if line.startswith("Title:"):
            taking = True
            rest = squash(line.split("Title:", 1)[1])
            if rest:
                title.append(rest)
            continue
        if taking:
            if line.startswith(("Docketed:", "Linked with", "Lower Ct:", "Questions Presented", "Proceedings and Orders")):
                break
            title.append(squash(line))
    return squash(" ".join(title)) or None


def link_url(page_url, href):
    try:
        return resolve(page_url, href)[1]
    except ValueError:
        return urljoin(page_url, href.strip())


def parse_proceedings(main, url):
    """Docket entries in page order. Each is a table row whose first cell is a date like May 08 2025 and whose second cell holds the entry and, on the newer pages, the links to its documents."""
    out = []
    for tr in main.iter("tr"):
        cells = [cell for cell in tr if isinstance(cell.tag, str) and cell.tag.lower() == "td"]
        if len(cells) < 2:
            continue
        m = PROCEEDING_DATE.match(squash(field(cells[0]).replace("\xa0", " ")))
        if not m:
            continue
        body = cells[1]
        documents = [{"title": squash(field(anchor)), "url": link_url(url, anchor.get("href"))} for anchor in body.iter("a") if anchor.get("href", "").strip()]
        out.append({"date": iso_from_court_date(" ".join(m.groups())), "text": squash(field(without_links(body))), "documents": documents})
    return out


def without_links(cell):
    """A copy of an entry's cell without its document links, which the entry lists separately."""
    cell = copy.deepcopy(cell)
    for span in cell.xpath('.//span[contains(concat(" ", normalize-space(@class), " "), " documentlinks ")]'):
        span.drop_tree()
    return cell


def parse_attorneys(main):
    """The attorneys section as rendered, one line per line of the page: the Contacts column of a newer page, or the table after an older page's entries."""
    contacts = main.xpath('.//div[@id="Contacts"]')
    if contacts:
        lines = [squash(line) for line in render(contacts[0]).splitlines()]
        lines = [line for line in lines if line]
        if lines and lines[0] == "Attorneys":
            lines = lines[1:]
        return "\n".join(lines) or None
    for table in main.iter("table"):
        rows = table.xpath("./tr | ./tbody/tr")
        if any(squash(field(row)).startswith("Attorneys for") for row in rows):
            lines = [line.replace("\xa0", " ").rstrip() for line in render(table).splitlines() if squash(line) and not squash(line).startswith("~~")]
            return "\n".join(lines) or None
    return None


def parse_docket_page(data, url, status_code=200):
    if status_code == 404 or NOT_FOUND in data[:2048]:
        return {"found": False, "url": url, "html": data, "text": None, "fields": {}, "proceedings": [], "attorneys": None}
    try:
        doc = parse(data)
    except UnicodeDecodeError:
        doc = parse(data, "latin-1")
    main = (doc.xpath('//div[@id="pagemaindiv"]') or [doc])[0]
    text = render(main)
    fields = {}
    lower_case_numbers = []
    for line in text.splitlines():
        clean = squash(line.replace("\xa0", " "))
        if clean.startswith("No. ") and "docket" not in fields:
            fields["docket"] = clean.removeprefix("No. ")
        elif clean.startswith("Docketed:"):
            fields["docketed_date"] = iso_from_court_date(clean.split(":", 1)[1])
        elif clean.startswith("Linked with"):
            fields["linked_with"] = clean.removeprefix("Linked with").lstrip(":").strip() or None
        elif clean.startswith("Lower Ct:"):
            fields["lower_court"] = clean.split(":", 1)[1].strip() or None
        elif clean.startswith(("Case Numbers:", "Case Nos.:")):
            lower_case_numbers.append(clean.split(":", 1)[1].strip())
        elif clean.startswith("Decision Date:"):
            fields["lower_court_decision_date"] = iso_from_court_date(clean.split(":", 1)[1])
    qp = next((anchor.get("href") for anchor in main.iter("a") if anchor.get("href", "").strip() and squash(field(anchor)) == "Questions Presented"), None)
    fields.update(case_name=extract_case_name(text), lower_court_case_numbers=lower_case_numbers, questions_presented_url=link_url(url, qp) if qp else None)
    return {"found": True, "url": url, "html": data, "text": text, "fields": fields, "proceedings": parse_proceedings(main, url), "attorneys": parse_attorneys(main)}


def source_doc(row, collection):
    doc = {key: row.get(key) for key in ("id", "url", "date", "title", "text_source", "text", "ocr_text") if key in row} | {"collection": collection}
    if collection in OPINION_COLLECTIONS:
        doc |= {"citation": next((cell for cell in (listing_cell(entry, "Citation") for entry in loads(row.get("entries"), [])) if cell), None), "pages": None}
    return doc


def listing_cell(entry, name):
    return squash(str((entry.get("row") or {}).get(name) or "")) or None


def entry_dockets(entry):
    """The dockets an opinion listing's row names in its Docket cell; the transcript listings have no such cell."""
    cell = listing_cell(entry, "Docket")
    return row_dockets({"docket": cell}) if cell else []


def split_pages(text):
    """The pages of a text that pdftotext -raw printed, which ends every page with a form feed."""
    pages = text.split("\f")
    if pages[-1] == "":
        pages.pop()
    return pages


def ends_opinion(page):
    """Whether a page of the United States Reports is no part of the opinion before it: one that begins another part, or a page of orders, whose running head gives dates (October 5, 2020 592 U. S.) where an opinion's gives its part (Kavanaugh, J., concurring). A preliminary print can carry a watermark line above the running head."""
    lines = [line.strip() for line in page.strip().split("\n")[:4] if line.strip() != WATERMARK]
    return bool(lines and (SECTION_BREAK.match(lines[0]) or (len(lines) > 1 and ORDERS_DATELINE.match(lines[1]))))


def shared_doc(row, collection, entry, starts):
    """One opinion in a file that holds several, such as a preliminary print of the United States Reports: its pages run from the page its listing entry links, #page=N, to the page before the next page any listing links in the file, or to the file's end."""
    first = entry.get("page")
    later = [page for page in starts if first and page > first]
    doc = {"id": row.get("id"), "url": row.get("url"), "date": iso_date(listing_cell(entry, "Date")) or row.get("date"), "title": listing_cell(entry, "Name") or squash(entry.get("link_text") or "") or row.get("title"), "text_source": row.get("text_source"), "text": None, "ocr_text": None, "collection": collection, "citation": listing_cell(entry, "Citation"), "pages": None}
    for name in ("text", "ocr_text"):
        if not row.get(name) or not first:
            continue
        pages = split_pages(row[name])
        last = min(min(later) - 1 if later else len(pages), len(pages))
        last = next((number - 1 for number in range(first + 1, last + 1) if ends_opinion(pages[number - 1])), last)
        if first <= last:
            doc[name] = "".join(page + "\f" for page in pages[first - 1:last])
            doc["pages"] = [first, last]
    return doc


def add_source(cases, term, docket, row, collection, doc=None):
    key = (term, normalize_docket(docket))
    case = cases.setdefault(key, {"term": term, "docket": key[1], "source_rows": [], "opinions": [], "transcripts": [], "titles": Counter(), "listed_titles": Counter(), "sources": set()})
    case["sources"].add(collection)
    title = doc["title"] if doc else row.get("title")
    if title:
        # The granted/noted lists' titles are in capitals, and a consolidated case's comes out of the list's layout garbled, so they are used last.
        case["listed_titles" if collection == "granted-noted-cases-list" else "titles"][title] += 1
    case["source_rows"].append({"collection": collection, "id": row.get("id"), "date": doc["date"] if doc else row.get("date"), "title": title})
    if collection == "argument-transcripts":
        case["transcripts"].append(source_doc(row, collection))
    elif collection != "granted-noted-cases-list":
        case["opinions"].append(doc or source_doc(row, collection))


def add_granted_list(cases, term, row):
    text = row.get("text") or row.get("ocr_text") or ""
    seen = set()
    # A title is in capitals and may wrap; it ends at the next field label (Court:, Order:, Argument Date:) or the next docket.
    for match in re.finditer(r"(?ms)^\s*(\d{2,4}-\d+|\d{2}A\d+|\d{2}O\d+|\d{1,3},\s*Orig\.)(?:[)#*\d]*)?\s+(?:[A-Z]{2,3}\s+)?(.+?)(?=\n[A-Z][a-z]+(?: [A-Za-z]+)*:|\n\s*(?:\d{2,4}-\d|\d{2}A\d|\d{2}O\d|\d{1,3},\s*Orig\.)|\Z)", text):
        docket = normalize_docket(match.group(1))
        if docket in seen:
            continue
        seen.add(docket)
        title = squash(" ".join(match.group(2).splitlines()))
        listed = {"id": row.get("id"), "date": None, "title": title, "url": row.get("url"), "text_source": row.get("text_source"), "text": None, "ocr_text": None}
        add_source(cases, term, docket, listed, "granted-noted-cases-list")


def build_cases_from_sources(args, manifests, granted, term):
    cases = {}
    for row in granted:
        if row.get("term") == term:
            add_granted_list(cases, term, row)
    loaded = []
    for collection in SOURCE_COLLECTIONS:
        if collection == "granted-noted-cases-list":
            continue
        entry = manifests[collection].get("partitions", {}).get(f"OT{term}")
        if entry:
            loaded += [(collection, row) for row in source_rows(args, collection, entry)]
    # The pages the listings link in each file, from every collection: a preliminary print is linked from Opinions of the Court and Opinions Relating to Orders alike.
    starts = {}
    for collection, row in loaded:
        for item in loads(row.get("entries"), []):
            if collection in OPINION_COLLECTIONS and item.get("page") and entry_dockets(item):
                starts.setdefault(row.get("id"), set()).add(item["page"])
    for collection, row in loaded:
        items = [(item, entry_dockets(item)) for item in loads(row.get("entries"), [])] if collection in OPINION_COLLECTIONS else []
        # A file whose entries name different cases holds one opinion per entry, and the row's own docket field names only the first.
        if len({tuple(dockets) for _, dockets in items if dockets}) > 1:
            added = set()
            for item, dockets in items:
                for docket in dockets:
                    if (docket, item.get("page")) not in added:
                        added.add((docket, item.get("page")))
                        add_source(cases, term, docket, row, collection, shared_doc(row, collection, item, starts.get(row.get("id"), set())))
            continue
        dockets = row_dockets(row)
        if not dockets:
            for entry_data in loads(row.get("entries"), []):
                dockets.extend(docket for docket in docket_candidates(dumps(entry_data)) if docket not in dockets)
        for docket in dockets:
            add_source(cases, term, docket, row, collection)
    return cases


def docket_due(page, docket, term, current, now):
    """Whether to ask the Court for a docket page again; see the module docstring."""
    if not page or not page.get("fetched_at"):
        return True
    if not page.get("found") and not {url for _, url in docket_urls(docket)} <= set(page.get("tried") or ()):
        return True
    age = now - parse_time(page["fetched_at"])
    if term >= current - 1:
        return age >= CURRENT_RECHECK
    return age >= RECHECK["found" if page.get("found") else "missing"]


def docket_urls(docket):
    """(format, url) of the docket's page, the likelier first. The pages under /docket/docketfiles/html/public/ belong to the docket system the Court began in 2017, which also holds every original case; an earlier regular or application docket is more often under /docketfiles/, which holds no original case."""
    slug = docket_slug(docket)
    new = ("new", f"{BASE}/docket/docketfiles/html/public/{quote(slug)}.html")
    if not re.fullmatch(r"\d{2,4}-\d+|\d{2}A\d+", slug):
        return [new]
    old = ("old", f"{BASE}/docketfiles/{quote(slug)}.htm")
    year = int(re.match(r"\d{2}", slug).group(0))
    return [old, new] if not 17 <= year < 50 else [new, old]


def fetch_docket(fetcher, docket, previous=None):
    """The docket's page: asked about conditionally where it was found before, and a 304 keeps what was stored. A status other than 200, 304, 404 or 410 raises UnexpectedStatus, so the stored page stays as it is. tried lists the addresses asked, in order."""
    previous = previous or {}
    urls = docket_urls(docket)
    if previous.get("found") and previous.get("url"):
        urls = [(previous.get("format"), previous["url"])] + [candidate for candidate in urls if candidate[1] != previous["url"]]
    missing, tried = None, []
    for fmt, url in urls:
        headers = {}
        if previous.get("found") and url == previous.get("url"):
            if previous.get("etag"):
                headers["If-None-Match"] = previous["etag"]
            if previous.get("last_modified"):
                headers["If-Modified-Since"] = previous["last_modified"]
        response = fetcher.request("GET", url, headers=headers or None)
        tried.append(url)
        status = response.status_code
        if status == 304 and headers:
            return {**{key: previous.get(key) for key in PAGE_KEYS}, "status": 304, "fetched_at": now_iso(), "tried": tried, "not_modified": True}
        page = {"url": url, "format": fmt, "status": status, "fetched_at": now_iso(), "etag": None, "last_modified": None, "html": response.content, "html_sha256": hashlib.sha256(response.content).hexdigest(), "tried": tried}
        if status == 200 and NOT_FOUND not in response.content[:2048]:
            return {**page, "found": True, "etag": response.headers.get("ETag"), "last_modified": response.headers.get("Last-Modified"), "parsed": parse_docket_page(response.content, url)}
        if status in (200, 404, 410):
            missing = {**page, "found": False, "parsed": parse_docket_page(response.content, url, 404)}
            continue
        raise UnexpectedStatus(f"HTTP {status} for {url}")
    return missing


def page_from_row(row, listed=None):
    """The docket page a stored row holds, parsed again by this builder; None if the row's page was never fetched. listed is the manifest's record of the page, for the status it was served with."""
    if not row or not row.get("docket_fetched_at"):
        return None
    html = row.get("docket_html") or b""
    url = row.get("docket_url") or ""
    found = bool(row.get("docket_found"))
    listed = listed or {}
    return {"url": row.get("docket_url"), "format": row.get("docket_format"), "found": found, "status": listed.get("status"), "tried": listed.get("tried"), "fetched_at": row["docket_fetched_at"], "etag": row.get("docket_etag"), "last_modified": row.get("docket_last_modified"), "html": html, "html_sha256": hashlib.sha256(html).hexdigest(), "parsed": parse_docket_page(html, url, 200 if found else 404)}


def parse_audio_listing(data, url):
    doc = parse(data)
    main = (doc.xpath('//div[@id="pagemaindiv"]') or [doc])[0]
    out = {}
    for anchor in main.xpath('.//a[@href]'):
        href = anchor.get('href')
        text = squash(field(anchor))
        for docket in docket_candidates(spell_originals(text + " " + href)):
            out[docket] = urljoin(url, href)
    return out


def parse_audio_page(data, url):
    doc = parse(data)
    main = (doc.xpath('//div[@id="pagemaindiv"]') or [doc])[0]
    text = render(main)
    date = None
    for line in text.splitlines():
        if line.startswith("Date Argued:"):
            raw = squash(line.split(":", 1)[1])
            try:
                date = datetime.strptime(raw, "%m/%d/%y").date().isoformat()
            except ValueError:
                pass
    mp3 = None
    for anchor in main.xpath('.//a[@href]'):
        href = anchor.get('href')
        if '.mp3' in href.lower():
            mp3 = urljoin(url, href)
            break
    return {"page_url": url, "mp3_url": mp3, "date": date}


def audio_for_term(fetcher, term, wanted, deadline):
    """Audio links for the dockets in wanted: the term's argument audio listing names each argued case's page, and that page names its MP3, which is linked, not downloaded."""
    if not wanted:
        return {}, {"listing_status": None, "pages": 0}
    listing_url = f"{BASE}/oral_arguments/argument_audio/{term}"
    response = fetcher.get(listing_url)
    if response.status_code != 200:
        return {}, {"listing_status": response.status_code, "pages": 0}
    pages = parse_audio_listing(response.content, listing_url)
    out, seen = {}, {}
    for docket in sorted(wanted):
        page = pages.get(docket)
        if not page or out_of_time(deadline):
            continue
        if page not in seen:
            r = fetcher.get(page)
            seen[page] = [parse_audio_page(r.content, page)] if r.status_code == 200 else None
        if seen[page]:
            out[docket] = seen[page]
    return out, {"listing_status": response.status_code, "pages": len(seen)}


def assemble_rows(term, cases, pages, audio):
    """One row per case; pages[docket] is the docket's page, fetched by this run or carried over, or None if it was never fetched."""
    rows = []
    for (case_term, docket), case in cases.items():
        if case_term != term:
            continue
        page = pages.get(docket)
        parsed = (page or {}).get("parsed") or {}
        fields = parsed.get("fields") or {}
        titles = case["titles"] or case.get("listed_titles") or Counter()
        title = fields.get("case_name") or (titles.most_common(1)[0][0] if titles else None)
        rows.append({
            "term": term,
            "docket": docket,
            "case_name": title,
            "case_name_source": "docket" if fields.get("case_name") else "source" if title else None,
            "sources": sorted(case["sources"]),
            "docket_url": page["url"] if page else None,
            "docket_format": page["format"] if page else None,
            "docket_found": page["found"] if page else None,
            "docket_fetched_at": page["fetched_at"] if page else None,
            "docket_etag": page["etag"] if page else None,
            "docket_last_modified": page["last_modified"] if page else None,
            "docket_html": page["html"] if page else None,
            "docket_text": parsed.get("text"),
            "docketed_date": fields.get("docketed_date"),
            "linked_with": fields.get("linked_with"),
            "lower_court": fields.get("lower_court"),
            "lower_court_case_numbers": fields.get("lower_court_case_numbers") or [],
            "lower_court_decision_date": fields.get("lower_court_decision_date"),
            "questions_presented_url": fields.get("questions_presented_url"),
            "proceedings": parsed.get("proceedings") or [],
            "attorneys": parsed.get("attorneys"),
            "opinions": case.get("opinions") or [],
            "transcripts": case.get("transcripts") or [],
            "audio": audio.get(docket) or [],
            "source_rows": case.get("source_rows") or [],
        })
    return sorted(rows, key=lambda row: row["docket"])


def write_case_parquet(rows, path):
    converted = []
    for row in rows:
        item = dict(row)
        for name in JSON_COLUMNS:
            item[name] = dumps(item.get(name) or [])
        converted.append(item)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(converted, schema=SCHEMA), path, compression="zstd", compression_level=9, use_content_defined_chunking=True)
    return {"file": str(path.relative_to(path.parents[1])), "rows": len(rows), "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def open_store(args, write):
    if args.local:
        return LocalStore(Path(args.local) / "scotus-cases", workdir=args.workdir)
    if write:
        trusted_publishing(REPO_ID)
    return HubStore(REPO_ID, workdir=args.workdir, token=None if write else False)


def stage(store, repo_path):
    """The scratch path to write repo_path's new content to; the store's next commit sends it."""
    local = store.dir / "stage" / repo_path
    local.parent.mkdir(parents=True, exist_ok=True)
    store.staged[repo_path] = local
    return local


def stored_rows(store, entry):
    """{docket: row} of a term's stored partition. A term the manifest lists must have its file, so a failed read is raised, not taken for an empty term."""
    if not entry or not entry.get("file"):
        return {}
    return {row["docket"]: row for row in store.read_table(entry["file"], None).to_pylist()}


def empty_manifest():
    return {"dataset": REPO_ID, "generated_at": None, "terms": {}, "runs": []}


def render_card(manifest):
    terms = manifest.get("terms", {})
    total = {"cases": 0, "docket_found": 0, "opinions": 0, "transcripts": 0, "audio": 0}
    lines = ["---", "pretty_name: \"Supreme Court of the United States: Cases\"", "license: other", "license_name: us-government-works", "license_link: https://www.copyright.gov/title17/92chap1.html#105", "language:", "- en", "tags:", "- legal", "- law", "- supreme-court", "- united-states", "- government", "- court-documents", "configs:", "- config_name: default", "  data_files:", "  - split: train", "    path: data/*.parquet", "---", "", "# Supreme Court of the United States: Cases", "", "One row is one `(term, docket)` case: the case's docket page from [www.supremecourt.gov](https://www.supremecourt.gov) as served, with the fields parsed from it, and the case's opinions and argument transcripts, with their text, from the file-centric SCOTUS datasets.", "", "The source datasets are [`incrediblecrab/scotus-opinions`](https://huggingface.co/datasets/incrediblecrab/scotus-opinions), [`incrediblecrab/scotus-oral-arguments`](https://huggingface.co/datasets/incrediblecrab/scotus-oral-arguments) and [`incrediblecrab/scotus-case-documents`](https://huggingface.co/datasets/incrediblecrab/scotus-case-documents). A term's cases are the dockets its argument transcripts, opinions and granted/noted list name, so a petition the Court denied without an opinion has no row.", "", "## Coverage", "", "Counts of cases, and of cases with a docket page online, at least one opinion, at least one argument transcript, and argument audio.", "", "| Term | Cases | Docket page | Opinions | Transcripts | Audio |", "|---|---:|---:|---:|---:|---:|"]
    for key in sorted(terms):
        entry = terms[key]
        total["cases"] += entry.get("rows", 0)
        for name in ("docket_found", "opinions", "transcripts", "audio"):
            total[name] += entry.get(name, 0)
        lines.append(f"| {key} | {entry.get('rows', 0):,} | {entry.get('docket_found', 0):,} | {entry.get('opinions', 0):,} | {entry.get('transcripts', 0):,} | {entry.get('audio', 0):,} |")
    lines += [f"| **Total** | **{total['cases']:,}** | **{total['docket_found']:,}** | **{total['opinions']:,}** | **{total['transcripts']:,}** | **{total['audio']:,}** |", "", "## Schema", "", "| Column | Type | Description |", "|---|---|---|"]
    docs = {
        "term": "October Term.", "docket": "Supreme Court docket number, written one way whichever form a source uses: `24-1260`, the application `25A1`, or the original case `141, Orig.`, which sources also write `141-Orig`, `141orig` or `22O141`.", "case_name": "Case name from the docket page, else the most common title the opinion and transcript rows give, else the granted/noted list's title, which is in capitals and, for a consolidated case, can carry the list's markup, such as `)2 CFX`.", "case_name_source": "`docket` or `source`.", "sources": "JSON list of the source collections that name the docket.", "docket_url": "Address of the docket page fetched, or of the last one tried when none was found.", "docket_format": "`new` for `/docket/docketfiles/html/public/…html`, `old` for `/docketfiles/…htm`.", "docket_found": "Whether the Court has a docket page online for the case; null until a run has asked.", "docket_fetched_at": "UTC time the docket page was last fetched, or checked and found unchanged.", "docket_etag": "ETag the docket page was served with.", "docket_last_modified": "Last-Modified the docket page was served with.", "docket_html": "Docket page bytes as served; for a docket with no page, the Court's not-found page.", "docket_text": "Rendered text of the docket page.", "docketed_date": "ISO date of `Docketed:`.", "linked_with": "The docket page's `Linked with` field.", "lower_court": "The docket page's `Lower Ct:` field.", "lower_court_case_numbers": "JSON list of the lower court's case numbers.", "lower_court_decision_date": "ISO date of the lower court's decision.", "questions_presented_url": "Link to the Questions Presented PDF, which the docket page of a granted case gives.", "proceedings": "JSON list of docket entries in page order: `date`, `text`, and `documents`, the title and link of each document filed with the entry.", "attorneys": "Rendered attorneys section of the docket page.", "opinions": "JSON list of the case's opinions from the source datasets: `id`, `url`, `date`, `title`, `citation` (the United States Reports citation the listing gives), `collection`, `text_source`, `text` and `ocr_text`, and `pages`, null for a file that holds one opinion; for a file of the United States Reports that holds many, `pages` is the first and last page of the case's opinion and the text is those pages.", "transcripts": "JSON list of the case's argument transcript rows from the source dataset, with `text` and `ocr_text`.", "audio": "JSON list of the case's argument audio: the page, the MP3 link and the argument date. The MP3 files are linked, not stored.", "source_rows": "JSON list of the source rows that name the docket."
    }
    for column in SCHEMA:
        lines.append(f"| `{column.name}` | {column.type} | {docs[column.name]} |")
    lines += ["", "## Updates and limits", "", f"A GitHub Actions workflow runs the builder at 00:00 and 12:00 UTC, before it syncs the source datasets, so a row reflects its sources as of the previous run; GitHub starts scheduled runs late when it is busy. A run assembles a term again when one of its source partitions changed, and asks for a docket page again when it is due: after {CURRENT_RECHECK.seconds // 3600} hours for the current and previous terms, after {RECHECK['found'].days} days for a page found in an older term, and after {RECHECK['missing'].days} days for a docket with no page. It asks conditionally where it can, so an unchanged page keeps its stored bytes. A docket that a run does not reach keeps what the last run stored.", "", "The opinion listings start at OT2017 for Opinions of the Court and OT2011 for Opinions Relating to Orders; earlier opinions are only in the bound volumes of the United States Reports (collection `us-reports` of `scotus-opinions`), which this dataset does not split by case. Once opinions are printed, a listing links the bound volume or preliminary print at the page each starts on instead of the slip opinion. A case's pages of such a file run from that page to the page before the next page any listing links in it, and stop early at a page of orders (whose running head gives dates), a Reporter's Note, the heading of the orders, the Court's allotment or rules orders, or the index; an opinion printed on the page of the order it concerns carries the orders printed around it on those pages. Some listed files answer 404 on the Court's site (in September 2026, the four OT2017 preliminary prints of volumes 584 and 585, and every file the OT2011 to OT2015 Opinions Relating to Orders list), so those opinions are missing here; they are in `us-reports`. The Court's online dockets begin during OT1999, where some cases have a page and others do not (No. 99-478 has one, No. 99-5525 none); a case without a page has `docket_found` false. Documents linked from docket entries (petitions, briefs, the Questions Presented) and argument audio are linked, not stored.", "", "## License", "", "Supreme Court opinions, orders and dockets are works of the United States Government, which are not subject to copyright in the United States ([17 U.S.C. § 105](https://www.copyright.gov/title17/92chap1.html#105)). Argument transcripts are prepared for the Court by court reporting companies; this dataset makes no claim about their copyright status. This card does not give legal advice.", ""]
    return "\n".join(lines)


def cmd_card(args):
    store = open_store(args, write=args.write)
    try:
        manifest = store.read_manifest() or empty_manifest()
        text = render_card(manifest)
        if not args.write:
            print(text)
            return 0
        stage(store, "README.md").write_text(text)
        print(store.commit("cases: card"))
        return 0
    finally:
        store.close()


def cmd_verify(args):
    """Checks the stored dataset against its manifest: each term's file and SHA-256, its row count, that every row belongs to its term and to no other term, that the dockets the manifest lists pages for are the rows' dockets, that a complete term has every page fetched, and that the card is the one the manifest renders."""
    store = open_store(args, write=False)
    try:
        manifest = store.read_manifest()
        if manifest is None:
            print(json.dumps({"dataset": REPO_ID, "problems": ["manifest.json not found"]}, indent=1))
            return 1
        problems = []
        terms = manifest.get("terms", {})
        stored = store.file_sha256s([entry["file"] for entry in terms.values()])
        keys = set()
        for key, entry in sorted(terms.items()):
            repo_path = entry["file"]
            if repo_path not in stored:
                problems.append(f"{key}: {repo_path} is missing")
                continue
            if stored[repo_path] != entry.get("sha256"):
                problems.append(f"{key}: {repo_path} has SHA-256 {stored[repo_path][:12]}, manifest says {str(entry.get('sha256'))[:12]}")
            table = store.read_table(repo_path, ["term", "docket", "docket_fetched_at"]).to_pylist()
            if len(table) != entry.get("rows"):
                problems.append(f"{key}: {len(table)} rows, manifest says {entry.get('rows')}")
            dockets = set()
            for row in table:
                if row["term"] != int(key[2:]):
                    problems.append(f"{key}: row {row['docket']} has term {row['term']}")
                if (row["term"], row["docket"]) in keys:
                    problems.append(f"{key}: duplicate key {(row['term'], row['docket'])}")
                keys.add((row["term"], row["docket"]))
                dockets.add(row["docket"])
                if entry.get("complete") and not row["docket_fetched_at"]:
                    problems.append(f"{key}: {row['docket']} has no docket page fetched, but the term is marked complete")
            listed = set(entry.get("dockets") or {})
            if listed - dockets:
                problems.append(f"{key}: the manifest lists pages for dockets with no row: {sorted(listed - dockets)[:5]}")
            fetched = {row["docket"] for row in table if row["docket_fetched_at"]}
            if fetched - listed:
                problems.append(f"{key}: rows with a docket page the manifest does not list: {sorted(fetched - listed)[:5]}")
        if store.read_text("README.md") != render_card(manifest):
            problems.append("README.md is not the card the manifest renders")
        report = {"dataset": REPO_ID, "terms": len(terms), "rows": sum(entry.get("rows", 0) for entry in terms.values()), "problems": problems}
        print(json.dumps(report, indent=1, ensure_ascii=False))
        return 1 if problems else 0
    finally:
        store.close()


def cmd_run(args):
    started = time.monotonic()
    deadline = started + args.budget_minutes * 60 if args.budget_minutes else None
    manifests = source_manifests(args)
    granted = granted_rows(args, manifests)
    all_terms = available_terms(manifests)
    if not all_terms:
        raise SystemExit("the source datasets name no terms")
    current = max(all_terms)
    store = open_store(args, write=True)
    manifest = store.read_manifest() or empty_manifest()
    manifest.setdefault("terms", {})
    now = datetime.now(timezone.utc)
    fetcher = Fetcher()
    run = {"started_at": now_iso(), "current_term": f"OT{current}", "terms": []}
    status = 0
    try:
        for term in order_terms(parse_terms(args.terms, all_terms), manifest, current):
            if out_of_time(deadline):
                run["stopped"] = "budget"
                break
            key = f"OT{term}"
            entry = manifest["terms"].get(key) or {}
            signature = source_signature(manifests, granted, term)
            previous_pages = entry.get("dockets") or {}
            if entry.get("builder") == BUILDER and entry.get("source_partitions") == signature and entry.get("complete") and not any(docket_due(page, docket, term, current, now) for docket, page in previous_pages.items()):
                continue
            reason = "new" if not entry else "builder changed" if entry.get("builder") != BUILDER else "sources changed" if entry.get("source_partitions") != signature else "dockets due"
            cases = build_cases_from_sources(args, manifests, granted, term)
            dockets = sorted(docket for _, docket in cases)
            old_rows = stored_rows(store, entry)
            pages = {docket: page_from_row(old_rows.get(docket), previous_pages.get(docket)) for docket in dockets}
            fetched, failures = Counter(), {}
            for docket in dockets:
                if not docket_due(pages[docket], docket, term, current, now):
                    continue
                if out_of_time(deadline):
                    run["stopped"] = "budget"
                    break
                try:
                    page = fetch_docket(fetcher, docket, pages[docket])
                except UnexpectedStatus as error:
                    failures[docket] = str(error)
                    continue
                except (Blocked, QuotaExhausted, Unavailable) as error:
                    run["stopped"] = f"{type(error).__name__}: {error}"
                    status = 1
                    break
                if page.get("not_modified"):
                    page = {**pages[docket], "status": 304, "fetched_at": page["fetched_at"], "tried": page["tried"]}
                    fetched["not_modified"] += 1
                else:
                    fetched["found" if page["found"] else "missing"] += 1
                pages[docket] = page
            audio = {docket: loads(old_rows[docket]["audio"], []) for docket in dockets if docket in old_rows and loads(old_rows[docket]["audio"], [])}
            audio_stats = {}
            if not run.get("stopped"):
                try:
                    found_audio, audio_stats = audio_for_term(fetcher, term, [docket for docket in dockets if docket not in audio], deadline)
                    audio.update(found_audio)
                except (Blocked, QuotaExhausted, Unavailable) as error:
                    run["stopped"] = f"{type(error).__name__}: {error}"
                    status = 1
            rows = assemble_rows(term, cases, pages, audio)
            stats = write_case_parquet(rows, stage(store, parquet_path(key)))
            manifest["terms"][key] = {**stats, "builder": BUILDER, "source_partitions": signature, "complete": all(pages[docket] for docket in dockets), "dockets": {docket: {name: page.get(name) for name in PAGE_KEYS} for docket, page in pages.items() if page}, "failures": failures, "docket_found": sum(1 for row in rows if row["docket_found"]), "opinions": sum(1 for row in rows if row["opinions"]), "transcripts": sum(1 for row in rows if row["transcripts"]), "audio": sum(1 for row in rows if row["audio"]), "audio_fetch": audio_stats}
            manifest["generated_at"] = now_iso()
            manifest["runs"] = [{"at": manifest["generated_at"], "term": key, "rows": stats["rows"], "reason": reason, "fetched": dict(fetched), "failures": len(failures)}] + manifest.get("runs", [])[:49]
            text = json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
            stage(store, MANIFEST).write_text(text)
            stage(store, "README.md").write_text(render_card(json.loads(text)))
            commit = store.commit(f"cases: {key} ({reason})")
            run["terms"].append({"term": key, "reason": reason, "rows": stats["rows"], "fetched": dict(fetched), "failures": len(failures), "complete": manifest["terms"][key]["complete"], "commit": commit})
            if run.get("stopped"):
                break
    finally:
        fetcher.close()
        store.close()
    run["finished_at"] = now_iso()
    run["minutes"] = round((time.monotonic() - started) / 60, 2)
    run["requests"] = dict(fetcher.requests)
    print(json.dumps(run, indent=1, ensure_ascii=False))
    # For the workflow: only a run the budget stopped has work left that the next run can do now; a docket that keeps failing waits for the schedule.
    step_outputs(more=run.get("stopped") == "budget", fetched=sum(run["requests"].values()))
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m scotus_products.cases")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--local", help="write DIR/scotus-cases instead of the Hub dataset")
    run.add_argument("--sources-local", help="read the source datasets from this local-data layout instead of the Hub")
    run.add_argument("--terms", help="comma-separated terms such as OT2024,OT2025")
    run.add_argument("--budget-minutes", type=float, default=20.0, help="stop starting work after this many minutes (0: no limit)")
    run.add_argument("--workdir", help="scratch directory (default: the system temporary directory)")
    card = sub.add_parser("card")
    card.add_argument("--local", help="read DIR/scotus-cases instead of the Hub dataset")
    card.add_argument("--write", action="store_true")
    card.add_argument("--workdir", help="scratch directory (default: the system temporary directory)")
    verify = sub.add_parser("verify")
    verify.add_argument("--local", help="read DIR/scotus-cases instead of the Hub dataset")
    verify.add_argument("--workdir", help="scratch directory (default: the system temporary directory)")
    args = parser.parse_args(argv)
    return {"run": cmd_run, "card": cmd_card, "verify": cmd_verify}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
