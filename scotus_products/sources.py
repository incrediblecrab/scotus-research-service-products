"""The eleven collections: where each is listed, how its listing pages are read, and how their entries become units.

A unit is one document file, a PDF or (for Orders by Circuit) an HTML page, and becomes one row. Several entries can link one file: Opinions Relating to Orders lists two opinions in one PDF as #page anchors, and for October Terms 2017 and 2018 the Opinions of the Court pages link pages of bound volumes and preliminary prints. So a unit carries every entry that links it, each entry with its page anchor, and the file is stored once, whole: nothing is cut out of a document.

Every entry records the listing's own text (markup.field: the rendered text with ASCII whitespace runs collapsed): the table cells under their headers, or the list item, with the headings it sits under. The typed columns (term, date, docket, title) are read from those strings; the strings themselves stay in the entries.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field as dataclass_field
from datetime import date

from .markup import field, line_of, main_content, parse, resolve, squash

BASE = "https://www.supremecourt.gov"
OWNER = "incrediblecrab"
# Terms before these list nothing (measured September 25, 2026): Opinions of the Court pages for 2010-2016 redirect to /opinions/USReports.aspx; Relating to Orders 2004 redirects and 2005-2010 are empty; argument_transcript pages for 1998-1999, orders 1999-2002 and Orders by Circuit 2008 are empty pages.
FIRST_TERM = {
    "opinions-of-the-court": 2017,
    "opinions-relating-to-orders": 2005,
    "argument-transcripts": 2000,
    "orders-of-the-court": 2003,
    "orders-by-circuit": 2009,
}
# Transcripts of the October Terms 1968-1999 are listed on archived_transcripts/{term} pages; 1967 and 2000 redirect (measured September 25, 2026).
ARCHIVED_TRANSCRIPTS = range(1968, 2000)
# PDFs the site links from every listing (press and reporter guides), which belong to no collection.
NOT_LISTED = ("publicinfo/",)
_DATE = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4}|\d{2})$")
_TERM = re.compile(r"October Term (\d{4})")
_VOLUME = re.compile(r"^Volume\s+(\d+)\b")
_ORIG = re.compile(r"^(No\. (\d+), Orig\.)")


class ListingError(RuntimeError):
    """A listing page could not be read as a listing; the run stops rather than record the collection as empty."""


def current_term(today):
    """The October Term in progress: a term starts on the first Monday of October, and the site files the weeks before it under the term that is ending."""
    return today.year if today.month >= 10 else today.year - 1


def iso_date(text, today=None):
    """ISO date of a listing date such as 6/27/19, 04/19/10, 7/14/1922 or 10-03-22; None for anything else. A two-digit year is 20yy up to next year, else 19yy."""
    match = _DATE.match((text or "").strip())
    if not match:
        return None
    month, day, year = (int(part) for part in match.groups())
    if len(match.group(3)) == 2:
        limit = ((today or date.today()).year + 1) % 100
        year += 2000 if year <= limit else 1900
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


@dataclass
class Unit:
    id: str
    url: str
    partition: str
    entries: list = dataclass_field(default_factory=list)

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.entries, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _heading_kinds(element):
    tag = element.tag.lower() if isinstance(element.tag, str) else ""
    classes = (element.get("class") or "").split()
    if tag == "td" and "listtitlerow" in classes:
        return "section"
    if tag == "h4":
        return "group"
    if tag == "a" and "toggle-text" in classes:
        return "subgroup"
    return None


LEVELS = ("section", "group", "subgroup")


def scan(main, page_url, headings=(), is_doc=None):
    """Every document link in the main column, in page order, with its listing context: the link's text and title, the table row or list item it sits in, the rendered line that holds it where that line is none of those, and the headings above it (any of section, group, subgroup)."""
    is_doc = is_doc or (lambda uid: uid.endswith(".pdf"))
    state, out = {}, []
    for element in main.iter():
        kind = _heading_kinds(element)
        if kind:
            state[kind] = field(element)
            for lower in LEVELS[LEVELS.index(kind) + 1:]:
                state.pop(lower, None)
            if kind != "subgroup":
                continue
        if not (isinstance(element.tag, str) and element.tag.lower() == "a" and element.get("href")):
            continue
        href = element.get("href")
        if href.startswith(("#", "mailto:", "javascript:")):
            continue
        uid, url, page = resolve(page_url, href)
        if not is_doc(uid) or uid.startswith(NOT_LISTED):
            continue
        entry = {"listing": page_url, "href": href, "link_text": field(element)}
        if page is not None:
            entry["page"] = page
        if element.get("title"):
            entry["link_title"] = squash(element.get("title"))
        row = _row_of(element)
        if row is not None:
            entry["row"] = row
        item = _item_of(element)
        if item is not None and item != entry["link_text"]:
            entry["item"] = item
        line = line_of(element)
        if line and line != entry["link_text"] and line != item and line not in (row or {}).values():
            entry["line"] = line
        for kind in headings:
            if state.get(kind):
                entry[kind] = state[kind]
        out.append((uid, url, entry))
    return out


def _ancestor(element, predicate):
    node = element.getparent()
    while node is not None:
        if isinstance(node.tag, str) and predicate(node):
            return node
        node = node.getparent()
    return None


def _row_of(anchor):
    """{header: cell text} of the table row holding the anchor, keyed by the headers of the table's first row of th cells. None outside a table whose header row has at least two columns: the calendars and Orders by Circuit pages use one-cell tables for layout, and a whole list is no row."""
    row = _ancestor(anchor, lambda node: node.tag.lower() == "tr")
    if row is None:
        return None
    table = _ancestor(row, lambda node: node.tag.lower() == "table")
    headers = []
    if table is not None:
        for candidate in table.xpath(".//tr"):
            ths = candidate.xpath("./th")
            if ths:
                headers = [field(th) for th in ths]
                break
    if len(headers) < 2:
        return None
    values = [field(cell) for cell in row.xpath("./td|./th")]
    if len(headers) != len(values):
        raise ListingError(f"a row of {len(values)} cells under {len(headers)} headers: {values[:3]}")
    return dict(zip(headers, values))


def _item_of(anchor):
    """The rendered text of the list item holding the anchor: an li, or a div laid out as a block (the orders pages), else None."""
    item = _ancestor(anchor, lambda node: node.tag.lower() == "li" or (node.tag.lower() == "div" and "display:block" in (node.get("style") or "").replace(" ", "")))
    return field(item) if item is not None else None


def group_units(found):
    """{id: Unit} from (id, url, entry, partition) tuples: one unit per file, its entries in listing order, its partition the least of its entries' partitions, so a file linked from two terms or cases has one home."""
    units = {}
    for uid, url, entry, partition in found:
        unit = units.get(uid)
        if unit is None:
            units[uid] = Unit(uid, url, partition, [entry])
        else:
            unit.entries.append(entry)
            unit.partition = min(unit.partition, partition)
    return units


@dataclass(frozen=True)
class Collection:
    name: str
    title: str
    source_page: str
    pages: object
    parse: object
    typed: object
    partition_label: str = "October Term"
    # Every run asks the server whether the collection's recent files changed: set for calendars, lists and journals, which may be replaced under the same URL.
    mutable: bool = False
    # The documents are HTML pages rather than PDFs.
    html: bool = False
    notes: tuple = ()

    @property
    def repo_id(self):
        return f"{OWNER}/scotus-{self.name}"


# ---- listing pages


def term_pages(name, template):
    def pages(today):
        return [(template.format(yy=f"{term % 100:02d}", yyyy=term), term) for term in range(FIRST_TERM[name], current_term(today) + 2)]

    return pages


def transcript_pages(today):
    archived = [(f"{BASE}/oral_arguments/archived_transcripts/{term}", term) for term in ARCHIVED_TRANSCRIPTS]
    return archived + term_pages("argument-transcripts", BASE + "/oral_arguments/argument_transcript/{yyyy}")(today)


def single(*urls):
    def pages(today):
        return [(url, None) for url in urls]

    return pages


# ---- parsers: (main column, page url, term) -> [(id, url, entry, partition)]


def parse_opinion_table(main, page_url, term):
    found = []
    for uid, url, entry in scan(main, page_url):
        row = entry.get("row")
        if row is None:
            raise ListingError(f"{page_url}: a document link outside the opinions table: {entry['href']}")
        entry_term = term
        if term is None:
            term_text = row.get("Term Year") or ""
            if not term_text.isdigit():
                raise ListingError(f"{page_url}: no Term Year for {entry['href']}")
            entry_term = int(term_text)
        entry["term"] = entry_term
        found.append((uid, url, entry, f"OT{entry_term}"))
    return found


def typed_opinion(unit, today=None):
    entry = unit.entries[0]
    row = entry.get("row") or {}
    return {
        "term": entry.get("term"),
        "date": iso_date(row.get("Date"), today),
        "docket": row.get("Docket") or None,
        "title": _name_of(entry),
    }


def _name_of(entry):
    """The case name: the link text, unless the link is a revision's, whose text is its date; then the Name cell up to its "Revisions" line."""
    line = entry.get("line") or ""
    if line.startswith("Revisions"):
        name = (entry.get("row") or {}).get("Name") or ""
        return name.split(" Revisions")[0] or None
    return entry.get("link_text") or None


def parse_transcripts(main, page_url, term):
    found = []
    for uid, url, entry in scan(main, page_url, headings=("group",)):
        if entry.get("row") is None:
            raise ListingError(f"{page_url}: a transcript link outside a session table: {entry['href']}")
        entry["term"] = term
        found.append((uid, url, entry, f"OT{term}"))
    return found


def typed_transcript(unit, today=None):
    entry = unit.entries[0]
    row = entry.get("row") or {}
    docket = entry.get("link_text") or ""
    cell = row.get("Oral Argument") or ""
    name = cell[len(docket):].strip(" ") if docket and cell.startswith(docket) else cell
    return {"term": entry.get("term"), "date": iso_date(row.get("Date Argued"), today), "docket": docket or None, "title": name or None}


def parse_dated_items(main, page_url, term, is_doc=None):
    """The orders pages: each entry a block holding the date and a link."""
    found = []
    for uid, url, entry in scan(main, page_url, is_doc=is_doc):
        if entry.get("item") is None:
            raise ListingError(f"{page_url}: a link outside a dated item: {entry['href']}")
        entry["term"] = term
        found.append((uid, url, entry, f"OT{term}"))
    return found


def typed_dated_item(unit, today=None):
    entry = unit.entries[0]
    match = re.match(r"(\d{1,2}/\d{1,2}/\d{2,4})", entry.get("item") or "")
    return {"term": entry.get("term"), "date": iso_date(match.group(1), today) if match else None, "docket": None, "title": entry.get("link_text") or None}


def _circuit_doc(uid):
    return "/ordercasebycircuit/" in f"/{uid}"


def parse_circuit(main, page_url, term):
    return parse_dated_items(main, page_url, term, is_doc=_circuit_doc)


def parse_calendars(main, page_url, term):
    found = []
    for uid, url, entry in scan(main, page_url, headings=LEVELS):
        entry_term = _calendar_term(uid, entry)
        entry["term"] = entry_term
        found.append((uid, url, entry, f"OT{entry_term}" if entry_term else "undated"))
    return found


def _calendar_term(uid, entry):
    for text in (entry.get("subgroup"), entry.get("item"), entry.get("line")):
        match = _TERM.search(text or "")
        if match:
            return int(match.group(1))
    match = re.search(r"(?<!\d)((?:19|20)\d{2})term", uid.rsplit("/", 1)[-1])
    return int(match.group(1)) if match else None


def typed_calendar(unit, today=None):
    entry = unit.entries[0]
    link = entry.get("link_text") or ""
    return {"term": entry.get("term"), "date": iso_date(link, today), "docket": None, "title": entry.get("item") or entry.get("line") or link or None}


def parse_granted(main, page_url, term):
    found = []
    for uid, url, entry in scan(main, page_url, headings=("group",)):
        match = _TERM.search(entry["link_text"])
        if not match:
            raise ListingError(f"{page_url}: no October Term in {entry['link_text']!r}")
        entry["term"] = int(match.group(1))
        found.append((uid, url, entry, "all"))
    return found


def typed_term_link(unit, today=None):
    entry = unit.entries[0]
    return {"term": entry.get("term"), "date": None, "docket": None, "title": entry.get("link_text") or None}


def parse_journal(main, page_url, term):
    found = []
    for uid, url, entry in scan(main, page_url, headings=("group",)):
        match = re.search(r"(?<!\d)(1[89]\d{2}|20\d{2})(?!\d)", entry["link_text"])
        if not match:
            raise ListingError(f"{page_url}: no year in {entry['link_text']!r}")
        entry["term"] = int(match.group(1))
        found.append((uid, url, entry, f"{entry['term'] // 10 * 10}s"))
    return found


def parse_us_reports(main, page_url, term):
    """Volumes go to partitions of ten (volumes-000-009 holds 2-9). A PDF the page links that is no volume, such as its explanatory note on the dates of early decisions, goes to "other"."""
    found = []
    for uid, url, entry in scan(main, page_url, headings=("group",)):
        match = _VOLUME.match(entry["link_text"])
        if not match:
            found.append((uid, url, entry, "other"))
            continue
        volume = int(match.group(1))
        entry["volume"] = volume
        low = volume // 10 * 10
        found.append((uid, url, entry, f"volumes-{low:03d}-{low + 9:03d}"))
    return found


def typed_us_reports(unit, today=None):
    entry = unit.entries[0]
    match = re.search(r"\((\d{4}) Term\b", entry.get("link_text") or "")
    return {"term": int(match.group(1)) if match else None, "date": None, "docket": None, "title": entry.get("link_text") or None}


def parse_original(main, page_url, term):
    found = []
    for uid, url, entry in scan(main, page_url, headings=("group",)):
        match = _ORIG.match(entry.get("group") or "")
        if not match or entry.get("row") is None:
            raise ListingError(f"{page_url}: a record link outside a case table: {entry['href']}")
        found.append((uid, url, entry, f"orig-{int(match.group(2)):03d}"))
    return found


def typed_original(unit, today=None):
    entry = unit.entries[0]
    row = entry.get("row") or {}
    match = _ORIG.match(entry.get("group") or "")
    return {"term": None, "date": iso_date(row.get("File Date"), today), "docket": match.group(1) if match else None, "title": row.get("Document Title") or entry.get("link_text") or None}


COLLECTIONS = {c.name: c for c in (
    Collection(
        "opinions-of-the-court", "Opinions of the Court", f"{BASE}/opinions/slipopinion/",
        term_pages("opinions-of-the-court", BASE + "/opinions/slipopinion/{yy}"), parse_opinion_table, typed_opinion,
        notes=(
            "The Court's page says: \"Opinions are posted on the website upon release in slip opinion format. Slip opinions remain posted until replaced with opinions edited to reflect the usual publication style of the United States Reports, including final pagination that will carry forward unchanged in the corresponding preliminary prints and the bound volumes of the United States Reports.\" A file the listing stops linking stays in the dataset with listed = false.",
            "For older terms the listing can link an opinion to a page inside a bound volume or preliminary print rather than to a file of its own. On September 25, 2026 it did so for all 73 entries of October Term 2018, all 63 of 2019 and 15 of the 68 of 2020, and the October Term 2017 page linked all 56 of its entries to four preliminary prints, which the server answered with 404 Not Found. Such a row holds the whole volume, with one entry per opinion and the page its link points to.",
        ),
    ),
    Collection(
        "opinions-relating-to-orders", "Opinions Relating to Orders", f"{BASE}/opinions/relatingtoorders/",
        term_pages("opinions-relating-to-orders", BASE + "/opinions/relatingtoorders/{yy}"), parse_opinion_table, typed_opinion,
        notes=(
            "Several opinions can share one PDF; the row holds the file once, with one entry per opinion, and an entry's page is the #page anchor of its link where the link has one.",
            "The pages for October Terms 2005 through 2010 list 57 opinions in tables without a link to any file, only a citation to the U. S. Reports (counted September 25, 2026), so those terms have no rows here.",
        ),
    ),
    Collection(
        "in-chambers-opinions", "In-Chambers Opinions", f"{BASE}/opinions/in-chambers.aspx",
        single(f"{BASE}/opinions/in-chambers.aspx"), parse_opinion_table, typed_opinion,
        notes=("The Court's page says in-chambers opinions \"will be posted here on the day of their issuance and will remain posted until published in the bound volume of the United States Reports.\" A row the page no longer lists stays in the dataset with listed = false.",),
    ),
    Collection(
        "us-reports", "U. S. Reports", f"{BASE}/opinions/USReports.aspx",
        single(f"{BASE}/opinions/USReports.aspx"), parse_us_reports, typed_us_reports, partition_label="volumes",
        notes=(
            "Older volumes are scans of the printed books. A scan's only text is the OCR layer inside the file, which is in ocr_text and is not verbatim; text_source on each row says which kind of file it is, and the table above counts them.",
            "The Court's page says: \"PDFs of partial volumes made available for the convenience of the bench and bar, as well as page proofs of volumes not yet published by GPO, will be posted bearing a “page proof” watermark.\"",
        ),
    ),
    Collection(
        "argument-transcripts", "Argument Transcripts", f"{BASE}/oral_arguments/argument_transcript/",
        transcript_pages, parse_transcripts, typed_transcript,
        notes=(
            "The Court's transcript pages say: \"Same-day transcripts are considered official but subject to final review.\" and \"Transcripts for oral arguments prior to October Term 2000 have been scanned from the Supreme Court Library collection. Please disregard any stray or handwritten markings on these copies.\"",
            "The Court's page on the availability of transcripts says: \"(Heritage Reporting Corporation has provided transcripts for the Court beginning in October Term 2017; prior to that Term, Alderson Reporting Corporation provided the transcripts.)\"",
        ),
    ),
    Collection(
        "calendars-and-lists", "Calendars and Lists", f"{BASE}/oral_arguments/calendarsandlists.aspx",
        single(
            f"{BASE}/oral_arguments/calendarsandlists.aspx",
            f"{BASE}/oral_arguments/earliercourtcalendars.aspx",
            f"{BASE}/oral_arguments/earlierargumentcalendars.aspx",
            f"{BASE}/oral_arguments/earlierdaycalls.aspx",
            f"{BASE}/oral_arguments/earlierhearinglists.aspx",
        ),
        parse_calendars, typed_calendar, mutable=True,
    ),
    Collection(
        "orders-of-the-court", "Orders of the Court", f"{BASE}/orders/ordersofthecourt/",
        term_pages("orders-of-the-court", BASE + "/orders/ordersofthecourt/{yy}"), parse_dated_items, typed_dated_item,
        notes=("The Court's page says: \"Caution: These electronic orders may contain computer-generated errors or other deviations from the official printed versions. Moreover, all order lists and miscellaneous orders are replaced within a few months by paginated versions of them in a preliminary print of the United States Reports, and one year after the issuance of the preliminary print by the final version of the orders in a U. S. Reports bound volume. In case of discrepancies between the print and electronic versions of orders, the print version controls. In case of discrepancies between order lists or miscellaneous orders and any later official version of them, the later version controls.\"",),
    ),
    Collection(
        "orders-by-circuit", "Orders by Circuit", f"{BASE}/orders/ordersbycircuit/",
        term_pages("orders-by-circuit", BASE + "/orders/ordersbycircuit/{yy}"), parse_circuit, typed_dated_item, html=True,
        notes=("The Court's page says: \"Caution: These electronic orders may contain computer-generated errors or other deviations from the official printed versions.\"", "Each document is an HTML page. The file column holds the page as served, which carries values that change from one request to the next (ASP.NET's __VIEWSTATE and __EVENTVALIDATION fields, and the request values of an analytics script), so file_sha256 need not repeat across downloads. text is the rendered content the Court wrote, which leaves those out: one document fetched twice on September 25, 2026, two hours apart, gave different bytes and the same text.",),
    ),
    Collection(
        "granted-noted-cases-list", "Granted/Noted Cases List", f"{BASE}/orders/grantednotedlists.aspx",
        single(f"{BASE}/orders/grantednotedlists.aspx"), parse_granted, typed_term_link, partition_label="all", mutable=True,
    ),
    Collection(
        "journal", "Journal", f"{BASE}/orders/journal.aspx",
        single(f"{BASE}/orders/journal.aspx", f"{BASE}/orders/scannedjournals.aspx"), parse_journal, typed_term_link, partition_label="decade", mutable=True,
        notes=(
            "The journals from 1993 onward are listed on the Journal page and the earlier ones on the Scanned Journals page, which says: \"These volumes were scanned from a working collection at the Supreme Court. Please disregard any stray marks on the initial pages of each volume.\" A scan's only text is the OCR layer inside the file, which is in ocr_text and is not verbatim.",
        ),
    ),
    Collection(
        "original-jurisdiction-records-and-briefs", "Original Jurisdiction Records & Briefs", f"{BASE}/casedocuments/original_jurisdiction_cases.aspx",
        single(f"{BASE}/casedocuments/original_jurisdiction_cases.aspx"), parse_original, typed_original, partition_label="case",
        notes=(
            "The Court's page says: \"The collection here is a digitized version of the physical collection in the Supreme Court's Library and may not contain all records and briefs that were filed in a given case.\" Besides the parties' filings, the listing has reports of special masters and documents titled as the Court's own, such as \"Opinion of the Court\", \"Slip Opinion\", \"Order\" and \"Decree\" (read from the listing's document titles on September 25, 2026).",
            "A scanned file's only text is the OCR layer inside it, which is in ocr_text and is not verbatim; the text-source table above counts the files of each kind.",
        ),
    ),
)}


class Listing:
    """Reads a collection's listing pages. list_all() returns (head, {id: Unit}); pages that redirect elsewhere (term pages that do not exist) count as empty."""

    def __init__(self, collection, fetcher, today=None):
        self.collection = collection
        self.fetcher = fetcher
        self.today = today or date.today()
        self.pages = {}

    def read(self, url):
        response = self.fetcher.get(url)
        if response.status_code in (301, 302, 303, 307, 308):
            return None
        if response.status_code != 200:
            raise ListingError(f"{url} answered {response.status_code}")
        return response.content

    def list_all(self):
        found = []
        for url, term in self.collection.pages(self.today):
            data = self.read(url)
            if data is None:
                if term is None:
                    raise ListingError(f"{url} redirected")
                self.pages[url] = {"term": term, "entries": 0, "redirected": True}
                continue
            try:
                main = main_content(parse(data))
            except ValueError as error:
                raise ListingError(f"{url}: {error}") from error
            entries = self.collection.parse(main, url, term)
            self.pages[url] = {"term": term, "entries": len(entries)}
            found += entries
        units = group_units(found)
        head = {"count": len(units), "entries": len(found), "pages": len(self.pages)}
        return head, units
