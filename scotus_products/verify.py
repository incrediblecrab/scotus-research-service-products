"""Checks a dataset: that its files are what the manifest says, with the schema's columns, and its rows keep the verbatim rules; with a listing (--live), that it holds what the site lists; with deep, that every stored file still hashes to its file_sha256 and extracts to exactly its stored text; with redownload, that the site still serves a sample of the stored files."""

import hashlib
import random
import tempfile
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pyarrow.compute as pc

from .extract import extract_audio, extract_html, extract_video, nonspace, pdftotext
from .pipeline import MAX_ATTEMPTS
from .store import SCHEMA, partition_path, schema_differences

SAMPLE = 10
# Files the live listing and the dataset may differ by without a problem: documents posted or removed since the last run.
TOLERANCE = 2
TEXT_SOURCES = {"born_digital", "scanned", "mixed", "no_text", "html"}
# text is only born-digital (or HTML) text, ocr_text only OCR.
TEXT_ONLY = {"born_digital", "html"}
OCR_ONLY = {"scanned", "mixed"}
# Files stored as served, with no text: a fresh check of the bytes must accept them as what their row says they are.
STORED_AS_SERVED = {"audio/mpeg": extract_audio, "video/mp4": extract_video}


def verify(store, listing=None, deep=False, redownload=0, fetcher=None, workers=6, workdir=None, seed=None):
    """Returns a report; report["problems"] is empty when every check passed."""
    manifest = store.read_manifest()
    if not manifest:
        return {"problems": ["no manifest.json"]}
    problems = []
    entries = manifest.get("partitions") or {}
    failures = manifest.get("failures") or {}
    listing_record = manifest.get("listing") or {}
    files = set(store.list_files("data/"))
    expected = {key: entry.get("file") or partition_path(key) for key, entry in entries.items()}
    sha256s = store.file_sha256s(sorted(path for path in expected.values() if path in files))
    every_id, listed_ids, sources, unreadable = Counter(), set(), Counter(), set()
    for key, path in sorted(expected.items()):
        entry = entries[key]
        if path not in files:
            problems.append(f"{key}: {path} is in the manifest but not in the repo")
            continue
        if sha256s.get(path) != entry.get("sha256"):
            problems.append(f"{key}: {path} has sha256 {str(sha256s.get(path))[:12]}, the manifest says {str(entry.get('sha256'))[:12]}")
        try:
            differences = schema_differences(store.read_schema(path), SCHEMA)
            table = store.read_table(path, ["id", "partition", "listed", "text_source", "file_sha256", "file_size", "text", "ocr_text"])
        except Exception as error:  # noqa: BLE001 - a file that cannot be read is a finding, and the other partitions are still checked
            problems.append(f"{key}: {path} cannot be read: {type(error).__name__}: {error}"[:300])
            unreadable.add(path)
            continue
        problems.extend(f"{key}: {path} {difference}" for difference in differences)
        ids = table.column("id").to_pylist()
        if len(ids) != entry.get("rows"):
            problems.append(f"{key}: {len(ids)} rows, the manifest says {entry.get('rows')}")
        if ids != entry.get("ids"):
            problems.append(f"{key}: the ids differ from the manifest's")
        if any(uid is None for uid in ids):
            problems.append(f"{key}: null ids")
        wrong = sorted({value for value in table.column("partition").to_pylist() if value != key}, key=str)
        if wrong:
            problems.append(f"{key}: rows with partition {wrong[:SAMPLE]}")
        listed = table.column("listed").to_pylist()
        if sum(1 for value in listed if value) != entry.get("listed"):
            problems.append(f"{key}: {sum(1 for value in listed if value)} listed rows, the manifest says {entry.get('listed')}")
        text_sources = table.column("text_source").to_pylist()
        odd = sorted({value for value in text_sources if value not in TEXT_SOURCES}, key=str)
        if odd:
            problems.append(f"{key}: text_source values {odd}")
        has_text = pc.is_valid(table.column("text")).to_pylist()
        has_ocr = pc.is_valid(table.column("ocr_text")).to_pylist()
        for uid, source, text, ocr in zip(ids, text_sources, has_text, has_ocr):
            if text != (source in TEXT_ONLY) or ocr != (source in OCR_ONLY):
                problems.append(f"{key}: {uid} is {source} with text {'set' if text else 'null'} and ocr_text {'set' if ocr else 'null'}")
        if any(value is None for value in table.column("file_sha256").to_pylist()):
            problems.append(f"{key}: rows without file_sha256")
        if entry.get("complete"):
            unstored = sum(1 for uid, f in failures.items() if f.get("partition") == key and f["attempts"] >= MAX_ATTEMPTS and uid not in set(ids))
            at_listing = (listing_record.get("partitions") or {}).get(key)
            if at_listing is not None and entry.get("listed", 0) + unstored != at_listing:
                problems.append(f"{key}: complete, but {entry.get('listed')} listed rows + {unstored} failed != {at_listing} files at the last complete listing")
        every_id.update(ids)
        listed_ids.update(uid for uid, value in zip(ids, listed) if value)
        sources.update(value or "none" for value in text_sources)
    duplicates = sorted(uid for uid, n in every_id.items() if n > 1)
    if duplicates:
        problems.append(f"ids in more than one row: {duplicates[:SAMPLE]}")
    for path in sorted(files - set(expected.values())):
        problems.append(f"{path} is not in the manifest")
    report = {
        "collection": manifest.get("collection"),
        "rows": sum(every_id.values()),
        "listed": len(listed_ids),
        "listed_at_last_complete_run": listing_record.get("count"),
        "partitions": len(entries),
        "complete": sum(1 for entry in entries.values() if entry.get("complete")),
        "failures": len(failures),
        "failed_max_attempts": sum(1 for f in failures.values() if f["attempts"] >= MAX_ATTEMPTS),
        "failed_sample": {uid: f.get("error") for uid, f in sorted(failures.items())[:SAMPLE]},
        "text_sources": dict(sorted(sources.items())),
    }
    if listing is not None:
        report["live"] = live_diff(manifest, set(every_id), listed_ids, listing, problems)
    readable = {key: path for key, path in expected.items() if path in files and path not in unreadable}
    if deep:
        report["deep"] = deep_check(store, readable, problems, workers, workdir)
    if redownload:
        report["redownload"] = redownload_check(store, readable, fetcher, redownload, problems, seed)
    report["problems"] = problems
    return report


def live_diff(manifest, stored, listed_ids, listing, problems):
    """Exact id sets: what the site lists now against what the dataset holds. Files that failed MAX_ATTEMPTS times are reported apart."""
    head, units = listing.list_all()
    live = set(units)
    failed = {uid for uid, f in (manifest.get("failures") or {}).items() if f["attempts"] >= MAX_ATTEMPTS}
    missing = sorted(live - stored - failed)
    extra = sorted(listed_ids - live)
    if len(missing) > TOLERANCE:
        problems.append(f"{len(missing)} listed files are neither stored nor recorded as failed: {missing[:SAMPLE]}")
    if len(extra) > TOLERANCE:
        problems.append(f"{len(extra)} rows are marked listed but the site no longer lists them: {extra[:SAMPLE]}")
    return {
        "listed_now": len(live), "entries_now": head["entries"], "stored": len(stored),
        "missing": len(missing), "missing_but_failed": len((live - stored) & failed), "extra": len(extra),
        "missing_sample": missing[:SAMPLE], "extra_sample": extra[:SAMPLE],
    }


def _check_row(row, workdir):
    """Problems with one stored row: its bytes against file_sha256 and file_size, and a fresh extraction against its stored text."""
    found = []
    data = row["file"] or b""
    if hashlib.sha256(data).hexdigest() != row["file_sha256"] or len(data) != row["file_size"]:
        found.append(f"{row['id']}: the stored bytes do not match file_sha256 and file_size")
    if row["media_type"] == "text/html":
        if extract_html(data)["text"] != row["text"]:
            found.append(f"{row['id']}: rendering the stored page again gives other text")
        return found
    if row["media_type"] in STORED_AS_SERVED:
        try:
            fresh = STORED_AS_SERVED[row["media_type"]](data)
        except ValueError as error:
            found.append(f"{row['id']}: {error}")
        else:
            if any(fresh[k] != row[k] for k in ("text_source", "text", "ocr_text", "pages")):
                found.append(f"{row['id']}: checking the stored file again gives other fields")
        return found
    with tempfile.TemporaryDirectory(dir=workdir) as scratch:
        path = Path(scratch) / "document.pdf"
        path.write_bytes(data)
        output = pdftotext(path)
    stored = row["text"] if row["text"] is not None else row["ocr_text"]
    if row["text_source"] == "no_text":
        if nonspace(output):
            found.append(f"{row['id']}: recorded as no_text, but pdftotext prints text")
    elif output != stored:
        found.append(f"{row['id']}: extracting the stored file again gives other text")
    if output.count("\f") != row["pages"]:
        found.append(f"{row['id']}: {output.count(chr(12))} pages extracted, {row['pages']} recorded")
    return found


def deep_check(store, readable, problems, workers, workdir):
    columns = ["id", "file", "file_sha256", "file_size", "media_type", "text_source", "text", "ocr_text", "pages"]
    checked, failed = 0, 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        queue = deque()

        def settle(limit):
            nonlocal failed
            while len(queue) > limit:
                found = queue.popleft().result()
                failed += bool(found)
                problems.extend(found)

        for key, path in sorted(readable.items()):
            for row in store.iter_rows(path, columns):
                queue.append(pool.submit(_check_row, row, workdir))
                checked += 1
                settle(2 * max(1, workers))
        settle(0)
    return {"rows_checked": checked, "rows_with_problems": failed}


def redownload_check(store, readable, fetcher, n, problems, seed):
    """Fetches n listed files chosen at random and compares them with the stored ones: bytes for PDFs, rendered text for HTML."""
    listed = []
    for key, path in sorted(readable.items()):
        columns = store.read_columns(path, ["id", "url", "listed"])
        listed += [(uid, url, path) for uid, url, flag in zip(columns["id"], columns["url"], columns["listed"]) if flag]
    sample = random.Random(seed).sample(listed, min(n, len(listed)))
    wanted = {uid for uid, _, _ in sample}
    stored = {}
    for path in sorted({path for _, _, path in sample}):
        for row in store.iter_rows(path, ["id", "file_sha256", "media_type", "text"]):
            if row["id"] in wanted:
                stored[row["id"]] = row
    same, differ = 0, []
    for uid, url, _ in sample:
        response = fetcher.get(url)
        row = stored[uid]
        if response.status_code != 200:
            differ.append(f"{uid}: HTTP {response.status_code}")
            continue
        if row["media_type"] == "text/html":
            match = extract_html(response.content)["text"] == row["text"]
        else:
            match = hashlib.sha256(response.content).hexdigest() == row["file_sha256"]
        if match:
            same += 1
        else:
            differ.append(f"{uid}: the site now serves other {'text' if row['media_type'] == 'text/html' else 'bytes'}")
    problems.extend(f"redownload: {item}" for item in differ)
    return {"sampled": len(sample), "same": same, "differ": len(differ), "differ_sample": differ[:SAMPLE]}
