"""Renders a collection's card ({collection}/README.md in its category's repo) from its manifest alone, and the category's card (the repo's README.md, the one the Hub shows) from the manifests of its collections, so both are staged with every manifest commit and never disagree with it."""

from collections import Counter

from .pipeline import MAX_ATTEMPTS, RETRY_AFTER_HOURS
from .sources import CATEGORIES, COLLECTIONS, repo_id as category_repo
from .store import SCHEMA

GITHUB = "https://github.com/incrediblecrab/scotus-research-service-products"
COLUMN_DOCS = {
    "id": "The file's path on www.supremecourt.gov, percent-decoded and lower-cased, without the leading slash (the site ignores case). One row per file",
    "partition": "The Parquet file that holds the row (`data/{partition}.parquet`), assigned when the row was first stored and never changed",
    "url": "Where the file was fetched from",
    "term": "October Term, as the listing gives it (for U. S. Reports, the Term in the volume's listing entry; null where that entry names more than one); null where the listing gives none",
    "date": "ISO date read from the listing (decision, argument, order, filing or posting date, or the date a speech's title ends with); null where the listing gives no single date",
    "docket": "Docket number as the listing gives it; for Original Jurisdiction, the case number (\"No. 1, Orig.\")",
    "title": "Case name or document title as the listing gives it",
    "entries": "JSON list of every listing entry that links the file, in listing order: the listing page (`listing`), the link's `href` and text (`link_text`), its title attribute where the page gives one (`link_title`, the case summary on the opinions pages), its #page anchor (`page`), the table row it sits in (`row`, cells keyed by column header) or list item (`item`), the rendered line that holds it where that is neither (`line`), and the headings above it (`section`, `group`, `subgroup`). All strings are the page's text (rendered text, or the title attribute) with runs of ASCII whitespace collapsed to one space; no other character is changed. `term`, `volume` and `year` are numbers the pipeline read from the page or its address; `docket` (Argument Audio) is the link's text, and `document_type` (`pdf`, `html`, `audio` or `video`) is the kind of file the pipeline took the link for, in the collections whose listings mix them",
    "listed": "Whether the latest complete listing still links the file. Rows are never deleted; a file the Court stops listing stays with listed = false",
    "media_type": "application/pdf, text/html for HTML documents, audio/mpeg for MP3 audio, or video/mp4 for MP4 video",
    "file": "The file's bytes exactly as the server sent them",
    "file_sha256": "SHA-256 of `file`",
    "file_size": "Length of `file` in bytes",
    "etag": "The ETag header the server sent with `file`",
    "last_modified": "The Last-Modified header the server sent with `file`",
    "pages": "Page count (pdfinfo); null for HTML, audio and video",
    "image_pages": "Pages on which one image covers at least half the page",
    "ocr_pages": "Pages that have text and are image pages, whose text the pipeline takes for an OCR layer",
    "text_source": "born_digital (the PDF's own text; in `text`), scanned or mixed (every page with text, or only some, is an image page, whose text the pipeline takes for OCR; in `ocr_text`), no_text (no page has text or the file is audio/video), or html",
    "text": "The file's own text, unchanged: the text layer of a born-digital PDF as pdftotext -raw prints it (form feed between pages), or the rendered text of an HTML page; see What is verbatim for where that differs from the page. Null when the pipeline takes the file's text for OCR",
    "ocr_text": "The same pdftotext -raw output when the pipeline takes the PDF's text for an OCR layer made by whoever scanned it (see `ocr_pages`). On a scanned page it is OCR, which misreads characters, so it is not verbatim. The same rule catches a born-digital page that one image, such as a map or a photograph, covers at least half of, and that page's text is the file's own",
    "extractor": "Program, version and options that produced `text` or `ocr_text`",
    "xcheck_extractor": "The independent extractor used for the cross-check (pypdf); null when there was no cross-check (only `text` from PDFs is cross-checked)",
    "xcheck_equal": "Whether pypdf yields the same sequence of non-whitespace characters as `text`",
    "xcheck_equal_nfkd": "The same comparison after NFKD normalization of both sides (ligatures and compatibility characters decomposed)",
    "xcheck_delta": "Characters (after NFKD, whitespace ignored) that one extraction has and the other lacks, counted without regard to order",
    "metadata": "JSON: the PDF's document information (pdfinfo), extraction notes, and the response headers kept",
    "first_seen_at": "When the pipeline first stored this file (UTC)",
    "fetched_at": "When the stored bytes were fetched (UTC)",
    "checked_at": "When the pipeline last confirmed the stored bytes are current: by fetching again or by the server's validators (UTC)",
    "delisted_at": "When the listing stopped linking the file (UTC); null while listed",
}
LICENSE = {
    "default": "The Supreme Court's own pages and documents (including its opinions, orders, calendars, lists and journals, its rules, forms and guides, its press releases, media advisories and reports, and its About pages) are works of the United States Government, which are not subject to copyright in the United States ([17 U.S.C. § 105](https://www.copyright.gov/title17/92chap1.html#105)). This card does not give legal advice; check the status of any document you rely on.",
    "argument-transcripts": "The transcripts are prepared for the Court by court reporting companies (see the notes below). This dataset makes no claim about their copyright status. This card does not give legal advice.",
    "argument-audio": "The argument audio is published by the Court, but this dataset makes no claim about its copyright status. This card does not give legal advice.",
    "online-sources-cited-in-opinions": "Each PDF is the Court's capture of web material that an opinion cites, published on sites other than the Court's, and this dataset makes no claim about its copyright status. This card does not give legal advice.",
    "media-files-cited-in-opinions": "The Court posts these files because an opinion cites them; its media page gives as an example \"a video or audio file that is part of the record in the lower court\". This dataset makes no claim about their copyright status. This card does not give legal advice.",
    "speeches": "The speeches are the Justices' own, given at universities, bar associations and other events, and published by the Court. Whether a speech is a work of the United States Government (17 U.S.C. § 105) turns on whether it was prepared as part of official duties, and this dataset makes no claim about the status of any speech in it. This card does not give legal advice.",
    "original-jurisdiction-records-and-briefs": "The collection holds the parties' filings in original cases (States, the United States and others), reports of special masters, and documents titled as the Court's own, digitized by the Supreme Court Library. Works of the United States Government are not subject to copyright in the United States ([17 U.S.C. § 105](https://www.copyright.gov/title17/92chap1.html#105)); filings by States and other parties may not be such works, and this dataset makes no claim about the status of any document in it. This card does not give legal advice.",
}


TAGS = ("tags:", "- legal", "- law", "- supreme-court", "- united-states", "- government", "- court-documents")


def license_lines(unknown):
    if unknown:
        return ["license: unknown"]
    return ["license: other", "license_name: us-government-works", "license_link: https://www.copyright.gov/title17/92chap1.html#105"]


def config_lines(*names):
    lines = ["configs:"]
    for name in names:
        lines += [f"- config_name: {name}", "  data_files:", "  - split: train", f"    path: {name}/data/*.parquet"]
    return lines


def size_category(rows):
    for limit, label in ((1_000, "n<1K"), (10_000, "1K<n<10K"), (100_000, "10K<n<100K"), (1_000_000, "100K<n<1M")):
        if rows < limit:
            return label
    return "1M<n<10M"


def gigabytes(n):
    if n >= 1e8:
        return f"{n / 1e9:.2f} GB"
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


def count(n, word, plural=None):
    return f"{n:,} {word if n == 1 else plural or word + 's'}"


def agree(n):
    return "agrees" if n == 1 else "agree"


FAILURES_SHOWN = 25


def codepoint_counts(points, uncounted):
    if uncounted:
        return f"These characters are not yet counted in {count(uncounted, 'partition')}, whose summary a run wrote before the pipeline counted them."
    private, private_rows, replacement, replacement_rows = (points[key] for key in ("private_use", "private_use_rows", "replacement", "replacement_rows"))
    if not private and not replacement:
        return "No row of `text` holds a Private Use Area code point or U+FFFD."
    first = f"{count(private_rows, 'row')} of `text` {'holds' if private_rows == 1 else 'hold'} {count(private, 'Private Use Area code point')}" if private else "No row of `text` holds a Private Use Area code point"
    second = f"{count(replacement_rows, 'row')} {'holds' if replacement_rows == 1 else 'hold'} {count(replacement, 'U+FFFD character')}" if replacement else "none holds U+FFFD"
    return f"{first}, and {second}."


def status(manifest):
    """What a collection's manifest says it holds and where its gaps are: the numbers both cards print."""
    entries = manifest.get("partitions") or {}
    failures = manifest.get("failures") or {}
    stored_ids = {uid for entry in entries.values() for uid in entry.get("ids") or ()}
    listing = manifest.get("listing") or {}
    seen = manifest.get("seen") or listing
    out = {
        "entries": entries, "failures": failures, "listing": listing, "seen": seen,
        "missing": {uid: f for uid, f in failures.items() if uid not in stored_ids},
        "stale": sorted(uid for uid in failures if uid in stored_ids),
        "incomplete": sorted(key for key, entry in entries.items() if not entry.get("complete")),
        # Partitions the latest listing links files in that have no entry: the run that read it stopped (budget, disk) before it reached them.
        "unreached": sorted(key for key, n in (seen.get("partitions") or {}).items() if n and key not in entries),
    }
    for name in ("rows", "listed", "delisted", "file_bytes", "pages"):
        out[name] = sum(entry.get(name) or 0 for entry in entries.values())
    out["whole"] = bool(listing.get("at")) and not out["incomplete"] and not out["unreached"] and not out["missing"] and out["listed"] == seen.get("count")
    return out


def render(manifest):
    manifest = manifest or {}
    collection = COLLECTIONS[manifest["collection"]]
    held = status(manifest)
    entries, failures, listing, seen, missing, stale = (held[key] for key in ("entries", "failures", "listing", "seen", "missing", "stale"))
    rows, listed, delisted, file_bytes, pages = (held[key] for key in ("rows", "listed", "delisted", "file_bytes", "pages"))
    sources = Counter()
    xcheck = Counter()
    points = Counter()
    uncounted = 0
    max_delta = 0
    for entry in entries.values():
        sources.update(entry.get("text_sources") or {})
        if entry.get("codepoints") is None:
            uncounted += 1
        else:
            points.update(entry["codepoints"])
        for key in ("checked", "equal", "equal_nfkd"):
            xcheck[key] += (entry.get("xcheck") or {}).get(key) or 0
        max_delta = max(max_delta, (entry.get("xcheck") or {}).get("max_delta") or 0)
    unstored = {uid: f for uid, f in missing.items() if f["attempts"] >= MAX_ATTEMPTS}
    repo_id = collection.repo_id
    lines = ["---", f"pretty_name: \"Supreme Court of the United States: {collection.title}\"", *license_lines(collection.name in LICENSE), "language:", "- en",
             *TAGS, "size_categories:", f"- {size_category(rows)}"]
    if entries:
        lines += config_lines(collection.name)
    lines += ["---", "", f"# Supreme Court of the United States: {collection.title}", ""]
    incomplete, unreached, whole = held["incomplete"], held["unreached"], held["whole"]
    where = f"the Supreme Court's website lists under [{collection.title}]({collection.source_page})"
    if whole:
        lead = f"Every document file that {where}, with the file itself, byte for byte, and its text. One row per file."
    else:
        lead = f"Document files that {where}, each with the file itself, byte for byte, and its text. One row per file. This collection does not hold every file the listing links: the status below says what it holds and where the gaps are."
    category_title = CATEGORIES[collection.category][0]
    lines += [
        lead,
        "",
        f"This is the `{collection.name}` config of [{repo_id}](https://huggingface.co/datasets/{repo_id}), which holds the collections the site's footer lists under {category_title}, and this card is `{collection.prefix}README.md` there.",
        "",
        f"Nothing here is edited by hand, and no text is corrected, normalized or generated. The pipeline and its tests are in [{GITHUB.removeprefix('https://')}]({GITHUB}), and this card is rendered from `{collection.prefix}manifest.json` in the same commit.",
        "",
        "## Status",
        "",
    ]
    if seen.get("count") is not None:
        lines.append(f"**{count(rows, 'file')}** ({gigabytes(file_bytes)}, {count(pages, 'PDF page')}); the listing of {seen.get('at')} linked {count(seen['count'], 'file')} from {count(seen.get('entries', 0), 'entry', 'entries')}. {count(listed, 'row')} {'is' if listed == 1 else 'are'} listed now and {delisted:,} {'is' if delisted == 1 else 'are'} no longer listed.")
    else:
        lines.append("The first run has not read the listing yet.")
    if listing.get("at"):
        lines += ["", f"Last complete run: {listing['at']}."]
    else:
        lines += ["", "No run has yet brought every partition up to date with the listing."]
    if incomplete:
        lines += ["", f"{count(len(incomplete), 'partition')} {'is' if len(incomplete) == 1 else 'are'} not yet complete: {', '.join(incomplete[:FAILURES_SHOWN])}{' ...' if len(incomplete) > FAILURES_SHOWN else ''}."]
    if unreached:
        files = sum(seen["partitions"][key] for key in unreached)
        lines += ["", f"The listing of {seen.get('at')} links {count(files, 'file')} in {count(len(unreached), 'partition')} that no run has reached yet, so {'it holds' if len(unreached) == 1 else 'they hold'} no rows: {', '.join(unreached[:FAILURES_SHOWN])}{' ...' if len(unreached) > FAILURES_SHOWN else ''}."]
    if missing:
        lines += ["", f"### Files not stored", "",
                  f"The listing links these files, but fetching or reading them failed. A file is tried on every run until it has failed {MAX_ATTEMPTS} times, then once every {RETRY_AFTER_HOURS} hours. {count(len(unstored), 'file')} {'has' if len(unstored) == 1 else 'have'} failed {MAX_ATTEMPTS} times.", "",
                  "| File | Attempts | Last error |", "|---|---:|---|"]
        for uid, failure in sorted(missing.items())[:FAILURES_SHOWN]:
            lines.append(f"| [{uid}]({failure['url']}) | {failure['attempts']} | {failure['error'].replace('|', '/')} |")
        if len(missing) > FAILURES_SHOWN:
            lines.append(f"| ... {len(missing) - FAILURES_SHOWN:,} more in `manifest.json` | | |")
    if stale:
        lines += ["", f"The last attempt to fetch {count(len(stale), 'stored file')} again failed, so {'its row holds' if len(stale) == 1 else 'their rows hold'} the version fetched earlier: {', '.join(stale[:FAILURES_SHOWN])}{' ...' if len(stale) > FAILURES_SHOWN else ''}. The errors are in `manifest.json`."]
    lines += ["", "| Text source | Rows |", "|---|---:|"]
    for name, n in sorted(sources.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| {name} | {n:,} |")
    if not sources:
        lines.append("| (none yet) | 0 |")
    lines += [
        "",
        f"Cross-check of `text` against pypdf: {count(xcheck['checked'], 'file')} compared; {xcheck['equal']:,} {agree(xcheck['equal'])} exactly on the sequence of non-whitespace characters, {xcheck['equal_nfkd']:,} {agree(xcheck['equal_nfkd'])} after NFKD normalization of both sides, and the largest difference is {count(max_delta, 'character')}. pypdf reads the same text layer, so agreeing does not show that `text` matches the page, and differing does not show that `text` is wrong: see below.",
        "",
        "## What is verbatim",
        "",
        "- `file` is the file as the server sent it. `file_sha256` lets you check it, and `url` says where it came from.",
        "- `text` is the PDF's own text layer as `pdftotext -raw -enc UTF-8` (poppler) prints it, stored unchanged: line breaks, page headers and footers, and a form feed between pages. `-raw` keeps the characters in the order the file draws them. The default mode was rejected because it removes a hyphen that ends a line and joins the words; `-layout` was rejected because it dropped characters on a sample file. For an HTML page, `text` is the text the page renders.",
        "- The text layer is the publisher's, and `text` keeps it where it does not say what the page shows. A PDF can declare what a run of its glyphs says (ActualText), and pdftotext prints the declaration in place of the glyphs, so a glyph that a file declares to be no text is not in `text`. `text` is plain text and marks no type style, such as italics or underlining, and a letter set in small capitals is whatever character the file maps it to, so an opinion that prints JUSTICE ALITO in small capitals has \"Justice Alito\" in `text`. A code point from Unicode's Private Use Areas (U+E000 to U+F8FF, and planes 15 and 16) has no standard meaning: a file's font puts a ligature, a bullet, a dash or another sign there, and only `file` shows which. U+FFFD, the replacement character, says only that no character is known for a glyph. " + codepoint_counts(points, uncounted),
        "- `text` holds only born-digital text. When one image covers at least half of a page that has text, the pipeline takes that text for an OCR layer made by whoever scanned the page, and the whole file's text goes to `ocr_text`, to be checked against `file`: on a scanned page it is OCR and not verbatim. The rule errs toward `ocr_text`, because it also catches a born-digital page that one image, such as a map or a photograph, covers at least half of. A file with no text on any page (no_text) has neither.",
        "- Every other text field (`title`, `docket`, `entries`) is the listing page's rendered text, with each run of ASCII whitespace collapsed to one space. No-break spaces, curly quotes and dashes are kept as the page has them.",
        "- `term` and `date` are read from those strings; the strings themselves stay in `entries`.",
        "",
        "## Use",
        "",
        "```python",
        "from datasets import load_dataset",
        f'ds = load_dataset("{repo_id}", "{collection.name}", split="train")',
        "```",
        "",
        "```sql",
        "-- DuckDB, straight from the Hub, without the file bytes",
        f"SELECT id, term, date, docket, title, text_source FROM 'hf://datasets/{repo_id}/{collection.prefix}data/*.parquet' WHERE listed ORDER BY date DESC LIMIT 10;",
        "```",
        "",
        "## Files",
        "",
        f"- `{collection.prefix}data/{{partition}}.parquet`: the rows, sorted by id. Partitions are by {collection.partition_label}.",
        f"- `{collection.prefix}manifest.json`: per partition, the row count, SHA-256 and summary counts; the files that failed and why; the listing pages read; the last 20 runs.",
        "",
        "## Schema",
        "",
        "| Column | Type | Description |",
        "|---|---|---|",
    ]
    for column in SCHEMA:
        lines.append(f"| `{column.name}` | {column.type} | {COLUMN_DOCS[column.name]} |")
    lines += ["", "## Notes on this collection", ""]
    lines += [f"- {note}" for note in collection.notes]
    if collection.mutable:
        lines.append("- Only one version of a file is kept. Every run asks the server (HEAD) whether a stored file of the previous term or a later one, or of no known term, changed, and fetches it again if its ETag, Last-Modified or Content-Length moved; an HTML page is read again instead, as the site's headers do not show when a page was edited, and compared by its rendered text. A changed file replaces the row. Older files are asked about only by a run with `--revalidate-all`. `checked_at` says when a row was last confirmed against the server, and the Hub's commit history holds earlier versions of the Parquet files.")
    else:
        lines.append("- Only one version of a file is kept: the one fetched at `fetched_at`. A run asks the server whether stored files changed only when it is run with `--revalidate-all`, which asks (HEAD) about every stored file and fetches again the ones whose ETag, Last-Modified or Content-Length moved, and reads every HTML page again. `checked_at` says when a row was last confirmed against the server, and the Hub's commit history holds earlier versions of the Parquet files.")
    lines += [
        "",
        "## How it is updated",
        "",
        "The pipeline reads every listing page of the collection, fetches files it has not stored, updates the listing fields of files whose entries changed, and marks files that are no longer listed. Requests are paced to at least 1.1 seconds apart, as the site's robots.txt asks (Crawl-delay: 1), with the user agent `scotus-research-service-products` and a link to the repository.",
        "",
        "## License",
        "",
        LICENSE.get(collection.name, LICENSE["default"]),
        "",
        f"The pipeline's code is under the MIT License ([{GITHUB.removeprefix('https://')}]({GITHUB})).",
        "",
    ]
    return "\n".join(lines)


def render_category(category, manifests):
    """The repo's README.md: one config per collection that has rows, and each collection's status in one line, from the manifests of the collections in the repo ({collection: manifest})."""
    title, page = CATEGORIES[category]
    members = [collection for collection in COLLECTIONS.values() if collection.category == category]
    repo_id = members[0].repo_id
    held = {c.name: status(manifests[c.name]) for c in members if c.name in manifests}
    present = [c.name for c in members if held.get(c.name, {}).get("entries")]
    rows = sum(h["rows"] for h in held.values())
    lines = ["---", f"pretty_name: \"Supreme Court of the United States: {title}\"", *license_lines(any(c.name in LICENSE for c in members)), "language:", "- en",
             *TAGS, "size_categories:", f"- {size_category(rows)}"]
    if present:
        lines += config_lines(*present)
    lines += [
        "---", "", f"# Supreme Court of the United States: {title}", "",
        f"The document files that the Supreme Court's website lists under [{title}]({page}) in its footer, one config per collection, each row a file with the file itself, byte for byte, and its text. Nothing here is edited by hand, and no text is corrected, normalized or generated. The pipeline and its tests are in [{GITHUB.removeprefix('https://')}]({GITHUB}), and this card is rendered from the collections' manifests in the same commit.",
        "",
        "## Collections",
        "",
        "| Config | Files | Size | Listed now | Last complete run | Status |",
        "|---|---:|---:|---:|---|---|",
    ]
    for collection in members:
        h = held.get(collection.name)
        name = f"[`{collection.name}`]({collection.prefix}README.md)"
        if h is None or h["seen"].get("count") is None:
            lines.append(f"| {name} | 0 | 0 KB | 0 | none | [{collection.title}]({collection.source_page}): no run has read the listing yet |")
            continue
        gaps = []
        if h["unreached"] or h["incomplete"]:
            gaps.append(f"{count(len(set(h['unreached']) | set(h['incomplete'])), 'partition')} not yet complete")
        if h["missing"]:
            gaps.append(f"{count(len(h['missing']), 'listed file')} not stored")
        state = "every listed file" if h["whole"] else "; ".join(gaps) or "not every listed file"
        lines.append(f"| {name} | {h['rows']:,} | {gigabytes(h['file_bytes'])} | {h['listed']:,} of {h['seen']['count']:,} | {h['listing'].get('at') or 'none yet'} | [{collection.title}]({collection.source_page}): {state} |")
    first = present[0] if present else members[0].name
    lines += [
        "",
        "Each collection's card, linked from its name, says what it holds and where the gaps are, which files failed and why, what is verbatim, and what each column means. Every config has the same columns.",
        "",
        "## Use",
        "",
        "```python",
        "from datasets import load_dataset",
        f'ds = load_dataset("{repo_id}", "{first}", split="train")',
        "```",
        "",
        "```sql",
        "-- DuckDB, straight from the Hub, without the file bytes",
        f"SELECT id, term, date, docket, title, text_source FROM 'hf://datasets/{repo_id}/{first}/data/*.parquet' WHERE listed ORDER BY date DESC LIMIT 10;",
        "```",
        "",
        "## Files",
        "",
        "- `{collection}/data/{partition}.parquet`: the collection's rows, sorted by id.",
        "- `{collection}/manifest.json`: per partition, the row count, SHA-256 and summary counts; the files that failed and why; the listing pages read; the last 20 runs.",
        "- `{collection}/README.md`: the collection's card.",
        "",
        "## Related datasets",
        "",
    ]
    for other, (other_title, _) in CATEGORIES.items():
        if other != category:
            lines.append(f"- [{category_repo(other)}](https://huggingface.co/datasets/{category_repo(other)}): {other_title}")
    lines += ["", "## License", ""]
    grouped = {}
    for collection in members:
        grouped.setdefault(LICENSE.get(collection.name, LICENSE["default"]), []).append(f"`{collection.name}`")
    for text, configs in grouped.items():
        lines.append(f"- {', '.join(configs)}: {text}")
    lines += ["", f"The pipeline's code is under the MIT License ([{GITHUB.removeprefix('https://')}]({GITHUB})).", ""]
    return "\n".join(lines)
