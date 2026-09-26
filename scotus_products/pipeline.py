"""The sync loop for one collection.

A run reads every listing page of the collection (sources.Listing), which yields one unit per document file with every listing entry that links it. Units are grouped into partitions; a unit already stored stays in the partition that holds it (the manifest keeps each partition's ids), so a file never moves or doubles. For each partition the run loads the stored rows and then:

- fetches units it has never stored, and stores the file with its extracted text (extract.py);
- rewrites the listing columns (entries, title, date, ...) of a stored unit whose entries changed, without fetching the file again;
- marks a stored unit the listing no longer shows listed = false with delisted_at, and a returning one listed = true; no row is ever deleted;
- asks the server (HEAD) whether a stored file changed, and fetches it again if the ETag, Last-Modified or Content-Length moved: on every run for the recent files of a collection marked mutable (calendars, lists and journals, which may be replaced under the same URL), and for every listed stored file when revalidate_all is set. A fetched file whose bytes are what is stored (for HTML: whose rendered text is) keeps the stored row's fetched_at.

Files are fetched one at a time on the main thread, paced by http.Fetcher; extraction runs in a thread pool, and results are applied in the order the files were fetched. Staged work is committed every checkpoint_seconds and at the end, so a run that dies loses at most one interval.

One writer at a time: the manifest names the last writer and when it wrote (the lease); a run defers while another writer's lease is fresh. HubStore commits name their parent, so of two racing writers the second is refused (Superseded).
"""

import copy
import hashlib
import json
import logging
import os
import shutil
import time
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from .extract import extract_html, extract_pdf
from .http import Blocked, QuotaExhausted
from .sources import ListingError, current_term
from .store import Superseded, dumps

log = logging.getLogger("scotus_products")

MANIFEST_VERSION = 1
MAX_ATTEMPTS = 3
# A unit that failed MAX_ATTEMPTS times is left alone this long, then tried again.
RETRY_AFTER_HOURS = 24
LEASE_MINUTES = 45
RUNS_KEPT = 20
# Files fetched but not yet extracted are held in memory up to this many bytes (a scanned volume is up to 160 MB).
PENDING_BYTES = 1 << 30


class LowDisk(RuntimeError):
    """Free disk space fell below the floor the run was given."""


# Stop the run rather than record a failure against the unit: these say the site, the Hub or the machine is refusing, not that one document is bad.
FATAL = (Blocked, QuotaExhausted, Superseded, ListingError, LowDisk)
CLEAN_STOPS = (None, "budget")
STAMP = "%Y-%m-%dT%H:%M:%SZ"
KEPT_HEADERS = ("content-type", "content-length", "last-modified", "etag")
LISTING_COLUMNS = ("url", "term", "date", "docket", "title", "entries")


class FetchFailed(RuntimeError):
    """The server answered a document link with a status other than 200."""


def utcnow():
    return datetime.now(timezone.utc).strftime(STAMP)


def later(stamp, hours):
    return (datetime.strptime(stamp, STAMP) + timedelta(hours=hours)).strftime(STAMP)


def age_hours(stamp):
    if not stamp:
        return float("inf")
    then = datetime.strptime(stamp, STAMP).replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds() / 3600


def writer_identity():
    return "github-actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "local"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


@dataclass
class Context:
    """deadline is a time.monotonic() value. only (partition keys) and max_units (files fetched per partition) bound a smoke run; a capped partition stays incomplete and resumes on the next run. refetch fetches every listed file of each partition the run reaches, as after an extractor change; revalidate_all asks the server about every listed stored file, in any collection, not only the recent files of a mutable one."""

    store: object
    collection: object
    fetcher: object
    deadline: float
    checkpoint_seconds: float = 600.0
    only: frozenset | None = None
    max_units: int | None = None
    refetch: bool = False
    revalidate_all: bool = False
    workers: int = 6
    workdir: str | None = None
    min_free_bytes: int = 0
    today: date | None = None
    writer: str = field(default_factory=writer_identity)
    stats: dict = field(default_factory=lambda: defaultdict(int))
    pending: list = field(default_factory=list)
    last_commit: float = field(default_factory=time.monotonic)
    claimed: bool = False

    def out_of_time(self):
        return time.monotonic() > self.deadline


def new_manifest(collection):
    return {
        "version": MANIFEST_VERSION, "collection": collection.name, "title": collection.title, "source": collection.source_page,
        "repo_id": collection.repo_id, "partitions": {}, "failures": {}, "listing": None, "runs": [], "writer": None, "updated_at": None,
    }


def other_writer(manifest, writer):
    lease = (manifest or {}).get("writer") or {}
    if lease.get("by") and lease["by"] != writer and age_hours(lease.get("at")) * 60 < LEASE_MINUTES:
        return lease
    return None


def fingerprint(units):
    digest = hashlib.sha256()
    for uid in sorted(units):
        digest.update(f"{uid}\t{units[uid].fingerprint}\n".encode())
    return digest.hexdigest()


def listing_columns(collection, unit, today):
    typed = collection.typed(unit, today)
    return {"url": unit.url, "term": typed["term"], "date": typed["date"], "docket": typed["docket"], "title": typed["title"], "entries": dumps(unit.entries)}


def recent(ctx, term):
    """Whether a mutable collection's file of this term is asked about on every run: the current term, the last one, later ones, and files of no known term. Older files are asked about only when revalidate_all is set."""
    return term is None or term >= current_term(ctx.today or date.today()) - 1


def revalidate_due(ctx, row):
    return row["listed"] and (ctx.revalidate_all or (ctx.collection.mutable and recent(ctx, row.get("term"))))


def partition_due(ctx, entry):
    """Whether a stored partition holds a file to ask about, from the manifest alone."""
    if ctx.revalidate_all:
        return True
    if not ctx.collection.mutable:
        return False
    newest = (entry.get("terms") or [None, None])[1]
    return bool(entry.get("undated")) or (newest is not None and recent(ctx, newest))


def retry_due(manifest, key, now):
    return any(f.get("partition") == key and f["attempts"] >= MAX_ATTEMPTS and later(f["at"], RETRY_AFTER_HOURS) <= now for f in manifest["failures"].values())


def order(manifest, partitions):
    """Partitions that are incomplete or changed first, then the rest; newest key first within each (the current term before older ones)."""
    def rank(key):
        entry = manifest["partitions"].get(key) or {}
        return 0 if not entry.get("complete") or entry.get("fingerprint") != fingerprint(partitions[key]) else 1

    return sorted(sorted(partitions, reverse=True), key=rank)


def flush(ctx, manifest, message=None):
    """Stamp the lease and commit everything staged, the manifest and card included."""
    manifest["writer"] = {"by": ctx.writer, "at": utcnow()}
    ctx.store.stage_manifest(manifest)
    text = message or "; ".join(ctx.pending) or "manifest"
    if ctx.store.commit(text if len(text) <= 500 else text[:497] + "..."):
        ctx.stats["commits"] += 1
    ctx.pending = []
    ctx.claimed = True
    ctx.last_commit = time.monotonic()


def check_listing(base, pages):
    """A term or list page that answered but listed nothing, where the last complete listing found entries on it, is a broken page rather than an emptied one."""
    before = ((base or {}).get("listing") or {}).get("pages") or {}
    for url, page in pages.items():
        if not page.get("redirected") and page["entries"] == 0 and (before.get(url) or {}).get("entries"):
            raise ListingError(f"{url} lists nothing; the last complete listing found {before[url]['entries']} entries there")


def sync(ctx, listing):
    """One run over one collection. listing is a sources.Listing. Returns the run record."""
    started = utcnow()
    requests_before, bytes_before = sum(ctx.fetcher.requests.values()), ctx.fetcher.bytes
    base = ctx.store.read_manifest() or new_manifest(ctx.collection)
    holder = other_writer(base, ctx.writer)
    if holder:
        log.info("deferring to %s, which wrote at %s", holder["by"], holder["at"])
        return {"started": started, "ended": utcnow(), "writer": ctx.writer, "finished": False, "stopped": "deferred", "holder": holder, "commits": 0}
    manifest = copy.deepcopy(base)
    manifest.update({key: value for key, value in new_manifest(ctx.collection).items() if key in ("version", "collection", "title", "source", "repo_id")})
    finished, reason, record = True, None, None
    try:
        head, units = listing.list_all()
        check_listing(base, listing.pages)
        homes = {uid: key for key, entry in manifest["partitions"].items() for uid in entry.get("ids") or ()}
        partitions = {key: {} for key in manifest["partitions"]}
        for uid, unit in units.items():
            unit.partition = homes.get(uid, unit.partition)
            partitions.setdefault(unit.partition, {})[uid] = unit
        record = {"count": len(units), "entries": head["entries"], "pages": listing.pages, "at": started,
                  "partitions": {key: len(partitions[key]) for key in sorted(partitions)}}
        manifest["seen"] = {"count": len(units), "entries": head["entries"], "at": started}
        with ThreadPoolExecutor(max_workers=max(1, ctx.workers)) as pool:
            for key in order(manifest, partitions):
                if ctx.only is not None and key not in ctx.only:
                    continue
                if ctx.out_of_time() or not sync_partition(ctx, pool, manifest, key, partitions[key]):
                    finished, reason = False, "budget"
                    break
    except Superseded as error:
        finished, reason = False, "superseded"
        log.warning("stopped: %s", error)
    except FATAL as error:
        finished, reason = False, f"{type(error).__name__}: {error}"[:300]
        log.warning("stopped: %s", reason)
    except Exception as error:  # noqa: BLE001 - recorded in the run record
        finished, reason = False, f"{type(error).__name__}: {error}"[:300]
        log.exception("run failed")
    run = {"started": started, "ended": utcnow(), "writer": ctx.writer, "finished": finished, "stopped": reason}
    run.update({key: ctx.stats[key] for key in ("fetched", "added", "replaced", "unchanged", "updated", "checked", "delisted", "relisted", "failed")})
    run["requests"] = sum(ctx.fetcher.requests.values()) - requests_before
    run["bytes_downloaded"] = ctx.fetcher.bytes - bytes_before
    if reason == "superseded":
        return dict(run, commits=ctx.stats["commits"])
    # The listing is published only by a run that brought every partition up to date with it.
    if finished and ctx.only is None and record:
        manifest["listing"] = record
    manifest["runs"] = (base.get("runs") or [])[-(RUNS_KEPT - 1):] + [dict(run, commits=ctx.stats["commits"] + 1)]
    try:
        flush(ctx, manifest)
    except Superseded as error:
        run.update(finished=False, stopped="superseded")
        log.warning("stopped: %s", error)
    except Exception as error:  # noqa: BLE001 - the store refused the final commit
        run.update(finished=False, stopped=f"{type(error).__name__}: {error}"[:300])
        log.exception("final commit failed")
    return dict(run, commits=ctx.stats["commits"])


def fetch(ctx, url):
    response = ctx.fetcher.get(url)
    if response.status_code != 200:
        raise FetchFailed(f"HTTP {response.status_code} for {url}")
    return response.content, {name: response.headers[name] for name in KEPT_HEADERS if name in response.headers}


def extract(ctx, data):
    return extract_html(data) if ctx.collection.html else extract_pdf(data, ctx.workdir)


def build_row(ctx, key, unit, data, headers, extracted, now):
    row = {
        "id": unit.id, "partition": key, **listing_columns(ctx.collection, unit, ctx.today), "listed": True,
        "media_type": extracted["media_type"], "file": data, "file_sha256": sha256(data), "file_size": len(data),
        "etag": headers.get("etag"), "last_modified": headers.get("last-modified"),
        "metadata": {"pdf_info": extracted["pdf_info"], "notes": extracted["notes"], "headers": headers},
        "first_seen_at": now, "fetched_at": now, "checked_at": now, "delisted_at": None,
    }
    for name in ("pages", "image_pages", "ocr_pages", "text_source", "text", "ocr_text", "extractor", "xcheck_extractor", "xcheck_equal", "xcheck_equal_nfkd", "xcheck_delta"):
        row[name] = extracted[name]
    return row


def same_content(ctx, stored, row):
    """Whether a fetch returned what is stored: the same bytes, or for an HTML page (whose served bytes carry per-request analytics tokens) the same rendered text."""
    if stored is None:
        return False
    if ctx.collection.html:
        return stored.get("text") is not None and stored["text"] == row["text"]
    return stored["file_sha256"] == row["file_sha256"]


def changed_on_server(stored, headers):
    """Whether a HEAD answer says the file moved since it was stored. A server that sends none of the three validators counts as changed."""
    pairs = [(stored.get("etag"), headers.get("etag")), (stored.get("last_modified"), headers.get("last-modified")),
             (str(stored["file_size"]) if stored.get("file_size") is not None else None, headers.get("content-length"))]
    known = [(old, new) for old, new in pairs if new is not None]
    return not known or any(old != new for old, new in known)


def sync_partition(ctx, pool, manifest, key, units):
    """Bring one partition up to date. Returns False when the budget ran out first."""
    entry = manifest["partitions"].get(key) or {}
    failures = manifest["failures"]
    now = utcnow()
    if entry.get("complete") and entry.get("fingerprint") == fingerprint(units) and not ctx.refetch and not retry_due(manifest, key, now) and not partition_due(ctx, entry):
        return True
    if not ctx.claimed:
        flush(ctx, manifest, f"{ctx.writer} takes the writer lease")
    stored = {row["id"]: row for row in ctx.store.read_partition(key)} if entry.get("file") else {}
    counts = Counter()
    dirty = False

    for uid, row in stored.items():
        if uid not in units and row["listed"]:
            row.update(listed=False, delisted_at=now)
            counts["delisted"] += 1
            dirty = True
    for uid in [uid for uid, failure in failures.items() if failure.get("partition") == key and uid not in units]:
        del failures[uid]

    todo, heads = [], []
    for uid in sorted(units):
        unit, row, failure = units[uid], stored.get(uid), failures.get(uid)
        if row is None or ctx.refetch:
            if row is None and not ctx.refetch and failure and failure["attempts"] >= MAX_ATTEMPTS and later(failure["at"], RETRY_AFTER_HOURS) > now:
                continue
            todo.append(unit)
            continue
        columns = listing_columns(ctx.collection, unit, ctx.today)
        if not row["listed"]:
            row.update(listed=True, delisted_at=None)
            counts["relisted"] += 1
            dirty = True
        if any(row[name] != columns[name] for name in LISTING_COLUMNS):
            row.update(columns)
            counts["updated"] += 1
            dirty = True
        if revalidate_due(ctx, row):
            heads.append(unit)

    for unit in heads if ctx.max_units is None else heads[:ctx.max_units]:
        if ctx.out_of_time():
            break
        row = stored[unit.id]
        try:
            response = ctx.fetcher.head(unit.url)
        except FATAL:
            _write(ctx, manifest, key, units, stored, todo, set(), counts, dirty, final=False)
            raise
        except Exception as error:  # noqa: BLE001 - the stored row stands; the next run asks again
            log.info("%s: HEAD failed: %s", unit.id, error)
            continue
        headers = {name: response.headers[name] for name in KEPT_HEADERS if name in response.headers}
        if response.status_code == 200 and not changed_on_server(row, headers):
            row["checked_at"] = now
            counts["checked"] += 1
            dirty = True
        else:
            todo.append(unit)

    done, finished = set(), True
    queue, queued_bytes = deque(), 0

    def apply(item):
        nonlocal dirty, queued_bytes
        unit, data, headers, future = item
        queued_bytes -= len(data)
        try:
            extracted = future.result()
        except Exception as error:  # noqa: BLE001 - recorded per unit, retried on later runs
            fail(unit, error)
            return
        row = build_row(ctx, key, unit, data, headers, extracted, utcnow())
        previous = stored.get(unit.id)
        if previous is not None:
            row["first_seen_at"] = previous["first_seen_at"]
            if same_content(ctx, previous, row):
                # The stored bytes stay with the stamps and headers of the fetch that got them; a PDF's bytes are the same, so it takes the new validators.
                kept = ("file", "file_sha256", "file_size", "fetched_at") + (("etag", "last_modified", "metadata") if ctx.collection.html else ())
                row.update({name: previous[name] for name in kept})
                counts["unchanged"] += 1
            else:
                counts["replaced"] += 1
        else:
            counts["added"] += 1
        stored[unit.id] = row
        failures.pop(unit.id, None)
        counts["fetched"] += 1
        done.add(unit.id)
        dirty = True

    def fail(unit, error):
        previous = failures.get(unit.id) or {}
        attempts = previous.get("attempts", 0) + 1
        failures[unit.id] = {"partition": key, "url": unit.url, "attempts": attempts, "error": f"{type(error).__name__}: {error}"[:300], "at": utcnow()}
        counts["failed"] += 1
        done.add(unit.id)
        log.info("%s failed (attempt %d): %s", unit.id, attempts, error)

    def drain(limit_items, limit_bytes):
        while queue and (len(queue) > limit_items or queued_bytes > limit_bytes or queue[0][3].done()):
            apply(queue.popleft())

    try:
        for unit in todo if ctx.max_units is None else todo[:ctx.max_units]:
            if ctx.out_of_time():
                finished = False
                break
            free = shutil.disk_usage(ctx.store.dir).free
            if free < ctx.min_free_bytes:
                raise LowDisk(f"{free / 2**30:.1f} GiB free, below the floor of {ctx.min_free_bytes / 2**30:.1f} GiB")
            try:
                data, headers = fetch(ctx, unit.url)
            except FATAL:
                raise
            except Exception as error:  # noqa: BLE001 - recorded per unit
                fail(unit, error)
                continue
            queue.append((unit, data, headers, pool.submit(extract, ctx, data)))
            queued_bytes += len(data)
            drain(2 * max(1, ctx.workers), PENDING_BYTES)
            if time.monotonic() - ctx.last_commit >= ctx.checkpoint_seconds:
                drain(0, -1)
                dirty = _write(ctx, manifest, key, units, stored, todo, done, counts, dirty, final=False)
    except FATAL:
        drain(0, -1)
        _write(ctx, manifest, key, units, stored, todo, done, counts, dirty, final=False)
        raise
    drain(0, -1)
    _write(ctx, manifest, key, units, stored, todo, done, counts, dirty, final=finished)
    return finished


def summarize(rows):
    """What the card reports about a partition, computed where the rows are."""
    sources = Counter(row["text_source"] or "none" for row in rows)
    checked = [row for row in rows if row.get("xcheck_equal") is not None]
    return {
        "text_sources": dict(sorted(sources.items())),
        "xcheck": {"checked": len(checked), "equal": sum(1 for row in checked if row["xcheck_equal"]), "equal_nfkd": sum(1 for row in checked if row["xcheck_equal_nfkd"]),
                   "max_delta": max((row["xcheck_delta"] or 0 for row in checked), default=0)},
        "pages": sum(row.get("pages") or 0 for row in rows),
        "delisted": sum(1 for row in rows if not row["listed"]),
        "terms": [min((row["term"] for row in rows if row.get("term") is not None), default=None), max((row["term"] for row in rows if row.get("term") is not None), default=None)],
        "undated": sum(1 for row in rows if row.get("term") is None),
    }


def _write(ctx, manifest, key, units, stored, todo, done, counts, dirty, final):
    """Stage the partition if anything in it changed, and record it in the manifest. Returns the new dirty flag (False once staged)."""
    failures = manifest["failures"]
    still_open = [unit for unit in todo if unit.id not in done or (unit.id in failures and failures[unit.id]["attempts"] < MAX_ATTEMPTS)]
    complete = final and not still_open
    rows = list(stored.values())
    entry = manifest["partitions"].setdefault(key, {})
    if dirty or not entry.get("sha256"):
        entry.update(ctx.store.stage_partition(key, rows))
        entry.update(summarize(rows))
        dirty = False
    entry.update({
        "failed": sum(1 for uid, f in failures.items() if f.get("partition") == key and uid not in stored),
        "complete": complete,
        "fingerprint": fingerprint(units) if complete else None,
        "updated_at": utcnow(),
    })
    manifest["updated_at"] = entry["updated_at"]
    summary = ", ".join(f"{counts[name]} {name}" for name in ("added", "replaced", "unchanged", "updated", "checked", "delisted", "relisted", "failed") if counts[name])
    ctx.pending.append(f"{key}: {summary or 'no change'}, {entry['rows']} rows" + ("" if complete else " (partial)"))
    log.info(ctx.pending[-1])
    for name, value in counts.items():
        ctx.stats[name] += value
    counts.clear()
    if time.monotonic() - ctx.last_commit >= ctx.checkpoint_seconds:
        flush(ctx, manifest)
    return dirty
