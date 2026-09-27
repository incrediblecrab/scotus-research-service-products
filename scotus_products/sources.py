"""The collections: where each is listed, how its listing pages are read, and how their entries become units.

A unit is one document file, a PDF, an HTML page (Orders by Circuit, press releases, media advisories, and some Year-End Reports and filing pages) or an MP3 (argument audio), and becomes one row. Several entries can link one file: Opinions Relating to Orders lists two opinions in one PDF as #page anchors, and for October Terms 2017 and 2018 the Opinions of the Court pages link pages of bound volumes and preliminary prints. So a unit carries every entry that links it, each entry with its page anchor, and the file is stored once, whole: nothing is cut out of a document.

Every entry records the listing's own text (markup.field: the rendered text with ASCII whitespace runs collapsed): the table cells under their headers, or the list item, with the headings it sits under. The typed columns (term, date, docket, title) are read from those strings; the strings themselves stay in the entries.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field as dataclass_field
from datetime import date
from urllib.parse import urlsplit

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
# Public-information PDFs that the older document-listing parsers should not sweep up implicitly; dedicated News Media collections include the ones that are in scope.
NOT_LISTED = ("publicinfo/",)
_DATE = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4}|\d{2})$")
_TERM = re.compile(r"October Term (\d{4})")
_TERM_YEAR = re.compile(r"Term Year:\s*(\d{4})")
_VOLUME = re.compile(r"^Volume\s+(\d+)\b")
_ORIG = re.compile(r"^(No\. (\d+), Orig\.)")


class ListingError(RuntimeError):
    """A listing page could not be read as a listing; the run stops rather than record the collection as empty."""


def shown_terms(main):
    """The terms a term page's main column says it lists: each term page prints "Term Year: 2025" under its term menu. On September 27, 2026 every term page the collections read that answered with a page showed its own term but one: /oral_arguments/argument_audio/2017 answered with a copy of the October Term 2025 page, Last-Modified January 22, 2026, where the argument-audio listing begun at 10:03 UTC that day had found the 63 arguments of 2017."""
    return {int(year) for year in _TERM_YEAR.findall(field(main))}


def reread_url(url):
    """url with a query string the Court's pages ignore. The site's CDN keeps its copies by the whole URL, so this reaches a copy apart from the one that showed another term. On September 27, 2026, from a home network, argument_audio/2017 came from the CDN's copy of the October Term 2025 page (Last-Modified January 22, 2026, Server-Timing cdn-cache REVALIDATE), while argument_audio/2017?reread=1 missed the cache and the origin answered with the October Term 2017 page (Last-Modified October 24, 2025, 63 entries); the GitHub runner that listed the collection that day was served the 2017 page at the plain URL."""
    return url + ("&" if "?" in url else "?") + "reread=1"


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


def scan(main, page_url, headings=(), is_doc=None, skip=NOT_LISTED):
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
        if not is_doc(uid) or any(uid.startswith(prefix) for prefix in skip):
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


# The columns of the site's footer, as it names and links them (read September 27, 2026), that the datasets follow.
CATEGORIES = {
    "opinions": ("Opinions", f"{BASE}/opinions/opinions.aspx"),
    "filing-and-rules": ("Filing & Rules", f"{BASE}/filingandrules/"),
    "oral-arguments": ("Oral Arguments", f"{BASE}/oral_arguments/oral_arguments.aspx"),
    "case-documents": ("Case Documents", f"{BASE}/case_documents.aspx"),
    "news-media": ("News Media", f"{BASE}/publicinfo/publicinfo.aspx"),
    "about": ("About", f"{BASE}/about/justices.aspx"),
}



def repo_id(category):
    return f"{OWNER}/scotus-{category}"


@dataclass(frozen=True)
class Collection:
    name: str
    title: str
    source_page: str
    pages: object
    parse: object
    typed: object
    partition_label: str = "October Term"
    # Every run asks the server whether the collection's recent files changed, and every file of no known term: set for calendars, lists and journals, argument audio, the rules, guides and forms, the news-media services sheet, and the About pages, which may be replaced under the same URL.
    mutable: bool = False
    # The documents are HTML pages rather than PDFs.
    html: bool = False
    notes: tuple = ()
    # The column of the site's footer that links the collection (a key of CATEGORIES): one dataset repo per column, one directory and config per collection.
    category: str = None

    @property
    def repo_id(self):
        return repo_id(self.category)

    @property
    def prefix(self):
        """Where the collection's files sit in its category's repo."""
        return f"{self.name}/"


# ---- listing pages


def term_pages(name, template):
    def pages(today):
        return [(template.format(yy=f"{term % 100:02d}", yyyy=term), term) for term in range(FIRST_TERM[name], current_term(today) + 2)]

    return pages


def media_kind(uid):
    path = urlsplit(uid).path.lower()
    if path.endswith(".pdf"):
        return "pdf"
    if path.endswith(".mp3"):
        return "audio"
    if path.endswith(".mp4"):
        return "video"
    return "html"


def is_document(uid):
    path = urlsplit(uid).path.lower()
    return path.endswith((".pdf", ".aspx", ".html", ".htm")) or "." not in path.rsplit("/", 1)[-1]


def parse_year(text):
    match = re.search(r"(?<!\d)((?:18|19|20)\d{2})(?!\d)", text or "")
    return int(match.group(1)) if match else None


MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
_LONG_DATE = re.compile(rf"\b({'|'.join(MONTH_NAMES)}) (\d{{1,2}}), (\d{{4}})\b")


def long_date(text):
    """ISO date of the last date written out like May 8, 2025 in the text (a speech's title ends with its date); None if there is none."""
    found = _LONG_DATE.findall(text or "")
    if not found:
        return None
    month, day, year = found[-1]
    try:
        return date(int(year), MONTH_NAMES.index(month) + 1, int(day)).isoformat()
    except ValueError:
        return None


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


def parse_news_html(main, page_url, term):
    found = []
    for uid, url, entry in scan(main, page_url, is_doc=lambda uid: "." not in uid.rsplit("/", 1)[-1], skip=()):
        if entry.get("row") is None:
            raise ListingError(f"{page_url}: a news link outside a table row: {entry['href']}")
        entry["document_type"] = "html"
        found.append((uid, url, entry, f"{parse_year(' '.join((entry.get('row') or {}).values())) or 'undated'}"))
    return found


def typed_news_html(unit, today=None):
    entry = unit.entries[0]
    row = entry.get("row") or {}
    date_text = row.get("Date Posted") or row.get("Date") or ""
    return {"term": None, "date": iso_date(date_text, today) or long_date(date_text), "docket": None, "title": entry.get("link_text") or row.get("Subject") or None}


def parse_speeches(main, page_url, term):
    found = []
    for uid, url, entry in scan(main, page_url, is_doc=lambda uid: uid.lower().endswith(".pdf"), skip=()):
        text = entry.get("link_text") or entry.get("line") or entry.get("item")
        year = int(long_date(text)[:4]) if long_date(text) else parse_year(text)
        entry["document_type"] = "pdf"
        entry["year"] = year
        found.append((uid, url, entry, f"{year or 'undated'}"))
    return found


def parse_year_end_reports(main, page_url, term):
    found = []
    for uid, url, entry in scan(main, page_url, is_doc=is_document, skip=()):
        year = parse_year(entry.get("link_text"))
        if not year:
            raise ListingError(f"{page_url}: no year in {entry['link_text']!r}")
        entry["document_type"] = media_kind(uid)
        entry["year"] = year
        found.append((uid, url, entry, f"{year}"))
    return found


def typed_year_document(unit, today=None):
    """A speech's or Year-End Report's year is no October Term: it stays in the entry and the partition, and a speech's date is the one its title ends with."""
    entry = unit.entries[0]
    return {"term": None, "date": long_date(entry.get("link_text")), "docket": None, "title": entry.get("link_text") or None}


def cited_url_pages(today):
    return [(f"{BASE}/opinions/cited_urls/{term % 100:02d}", term) for term in range(2005, current_term(today) + 1)]


def parse_online_sources(main, page_url, term):
    found = []
    for uid, url, entry in scan(main, page_url, is_doc=lambda uid: uid.startswith("opinions/urls_cited/") and uid.endswith(".pdf"), skip=()):
        if entry.get("row") is None:
            raise ListingError(f"{page_url}: a cited URL file link outside a table: {entry['href']}")
        entry["term"] = term
        entry["document_type"] = "pdf"
        found.append((uid, url, entry, f"OT{term}"))
    return found


def typed_online_source(unit, today=None):
    entry = unit.entries[0]
    row = entry.get("row") or {}
    return {"term": entry.get("term"), "date": None, "docket": row.get("Case Number") or None, "title": entry.get("link_text") or None}


def row_with_date(anchor):
    node = anchor
    while node is not None:
        if isinstance(node.tag, str) and node.tag.lower() == "tr":
            cells = [field(cell) for cell in node.xpath("./td|./th")]
            if cells and iso_date(cells[0]):
                headers = ("Date", "Docket", "Media File", "File Size")
                return dict(zip(headers, cells[:len(headers)]))
        node = node.getparent()
    return None


def parse_media_files(main, page_url, term):
    found = []
    for element in main.xpath('.//a[@href]'):
        href = element.get("href")
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        uid, url, page = resolve(page_url, href)
        if not (url.startswith(BASE + "/") and uid.startswith("media/") and uid.endswith((".mp3", ".mp4"))):
            continue
        entry = {"listing": page_url, "href": href, "link_text": field(element), "document_type": media_kind(uid)}
        row = row_with_date(element)
        if row is not None:
            entry["row"] = row
        line = line_of(element)
        if line and line != entry["link_text"]:
            entry["line"] = line
        if page is not None:
            entry["page"] = page
        media_date = iso_date((row or {}).get("Date"))
        entry_term = current_term(date.fromisoformat(media_date)) if media_date else None
        if entry_term:
            entry["term"] = entry_term
        found.append((uid, url, entry, f"OT{entry_term}" if entry_term else "undated"))
    return found


def typed_media_file(unit, today=None):
    entry = unit.entries[0]
    row = entry.get("row") or {}
    media = row.get("Media File") or entry.get("line") or entry.get("link_text")
    title = media.rsplit(" - ", 1)[0] if media else None
    return {"term": entry.get("term"), "date": iso_date(row.get("Date"), today), "docket": row.get("Docket") or None, "title": title}


def typed_single_document(unit, today=None):
    entry = unit.entries[0]
    titles = {"publicinfo/reportersguide.pdf": "A Reporter's Guide to Applications", "publicinfo/pioservices.pdf": "Services for News Media"}
    title = titles.get(unit.id, entry.get("link_text"))
    return {"term": None, "date": None, "docket": None, "title": title or None}


def parse_reporters_guide(main, page_url, term):
    url = f"{BASE}/publicinfo/reportersguide.pdf"
    return [("publicinfo/reportersguide.pdf", url, {"listing": page_url, "href": "reportersguide.pdf", "link_text": "News Media", "document_type": "pdf"}, "all")]


def parse_publicinfo_pdf(target_uid, title):
    def parser(main, page_url, term):
        found = []
        for uid, url, entry in scan(main, page_url, is_doc=lambda uid: uid == target_uid, skip=()):
            entry["document_type"] = "pdf"
            found.append((uid, url, entry, "all"))
        if not found:
            raise ListingError(f"{page_url}: no link to {title}")
        return found
    return parser


def parse_filing_documents(main, page_url, term):
    found = []
    listing_pages = {"ctrules/scannedrules.aspx"}
    for uid, url, entry in scan(main, page_url, is_doc=is_document, skip=()):
        if not url.startswith(BASE + "/"):
            continue
        if "/elearning/" in f"/{uid}":
            continue
        if uid in listing_pages:
            continue
        entry["document_type"] = media_kind(uid)
        year = parse_year(entry.get("link_text") or uid)
        entry["year"] = year
        partition = f"{year}" if year else "current"
        found.append((uid, url, entry, partition))
    return found


def typed_filing_document(unit, today=None):
    """The year a filing document's link names (an edition of the Rules, a revision) is no October Term, and its dates are effective or revision dates rather than one date of the document, so term and date stay null."""
    entry = unit.entries[0]
    return {"term": None, "date": None, "docket": None, "title": entry.get("link_text") or None}


ABOUT_PAGES = {
    f"{BASE}/about/justices.aspx": "Justices",
    f"{BASE}/about/courtatwork.aspx": "The Supreme Court at Work",
    f"{BASE}/about/code-of-conduct-for-justices.aspx": "Code of Conduct for Justices",
    f"{BASE}/about/historyandtraditions.aspx": "History and Traditions",
    f"{BASE}/about/courtbuilding.aspx": "The Supreme Court Building",
    f"{BASE}/about/buildingregulations.aspx": "Building Regulations",
    f"{BASE}/about/faq.aspx": "Frequently Asked Questions",
    f"{BASE}/about/faq_justices.aspx": "Frequently Asked Questions: Supreme Court Justices",
    f"{BASE}/about/faq_general.aspx": "Frequently Asked Questions: General Information",
    f"{BASE}/about/faq_documents.aspx": "Frequently Asked Questions: Locating Court Documents and Information",
}


def about_pages(today):
    return [(url, None) for url in ABOUT_PAGES]


def parse_about_documents(main, page_url, term):
    uid, url, _ = resolve(page_url, page_url)
    found = [(uid, url, {"listing": page_url, "href": page_url, "link_text": ABOUT_PAGES.get(page_url) or field(main), "document_type": "html"}, "pages")]
    for link_uid, link_url, entry in scan(main, page_url, is_doc=is_document, skip=()):
        if not link_url.startswith(BASE + "/") or link_uid == uid:
            continue
        if link_uid in {"about/faq_visiting.aspx", "filingandrules/faq_electronicfiling.aspx", ""}:
            continue
        if not (link_uid.startswith("about/") and (link_uid.endswith(".pdf") or link_uid.endswith(".aspx"))):
            continue
        entry["document_type"] = media_kind(link_uid)
        found.append((link_uid, link_url, entry, "pdfs" if link_uid.endswith(".pdf") else "pages"))
    return found


def typed_about_document(unit, today=None):
    entry = unit.entries[0]
    return {"term": None, "date": None, "docket": None, "title": entry.get("link_text") or None}


def audio_pages(today):
    return [(f"{BASE}/oral_arguments/argument_audio/{term}", term) for term in range(2010, current_term(today) + 2)]


def parse_argument_audio(main, page_url, term):
    found = []
    for uid, url, entry in scan(main, page_url, headings=("group",), is_doc=lambda uid: "/oral_arguments/audio/" in f"/{uid}", skip=()):
        docket = (entry.get("link_text") or entry["href"].rsplit("/", 1)[-1]).strip()
        file_stem = entry["href"].rsplit("/", 1)[-1]
        entry["term"] = term
        entry["docket"] = docket
        entry["document_type"] = "audio"
        row = entry.get("row") or {}
        date_text = row.get("Date") or row.get("Date Argued") or entry.get("group") or ""
        date_value = iso_date(date_text)
        partition = date_value[:7] if date_value else f"OT{term}"
        audio_uid = f"media/audio/mp3files/{file_stem.lower()}.mp3"
        audio_url = f"{BASE}/media/audio/mp3files/{file_stem}.mp3"
        found.append((audio_uid, audio_url, entry, partition))
    return found


def typed_argument_audio(unit, today=None):
    entry = unit.entries[0]
    row = entry.get("row") or {}
    date_text = row.get("Date") or row.get("Date Argued") or ""
    docket = entry.get("docket")
    title = row.get("Case Name") or row.get("Case") or row.get("Oral Argument") or entry.get("line") or entry.get("link_text")
    if title and docket and title.startswith(docket):
        title = title[len(docket):].strip()
    return {"term": entry.get("term"), "date": iso_date(date_text, today), "docket": docket, "title": title or None}


# Measured on the rows built September 26, 2026. Shared by the three collections whose files have the two code points.
LIGATURES = "Where `text` has the Private Use Area code point U+E405 or U+E406, the page prints fi or fl: each of the 196 words that U+E405 occurs in, across Opinions of the Court, Opinions Relating to Orders and U. S. Reports, and each of the 36 that U+E406 occurs in reads as a word or a name with fi or fl in its place, so `text` spells Office `Of\\uE405ce` and Netflix `Net\\uE406ix` (counted September 26, 2026). A search of `text` for such a word misses those rows unless it allows for the two code points."
# How pdftotext treats a hyphen that a file marks as no text; the collections' notes give their own counts.
NO_TEXT_HYPHENS = "mark hyphens that end a line as no text (an empty ActualText), and pdftotext prints what a file declares, not the glyph, so `text` lacks {hyphens} hyphens that these files draw. pypdf prints them, so these rows have `xcheck_equal` false; `file` has them."


COLLECTIONS = {c.name: c for c in (
    Collection(
        "opinions-of-the-court", "Opinions of the Court", f"{BASE}/opinions/slipopinion/",
        term_pages("opinions-of-the-court", BASE + "/opinions/slipopinion/{yy}"), parse_opinion_table, typed_opinion,
        notes=(
            "The Court's page says: \"Opinions are posted on the website upon release in slip opinion format. Slip opinions remain posted until replaced with opinions edited to reflect the usual publication style of the United States Reports, including final pagination that will carry forward unchanged in the corresponding preliminary prints and the bound volumes of the United States Reports.\" A file the listing stops linking stays in the dataset with listed = false.",
            "For older terms the listing can link an opinion to a page inside a bound volume or preliminary print rather than to a file of its own. On September 25, 2026 it did so for all 73 entries of October Term 2018, all 63 of 2019 and 15 of the 68 of 2020, and the October Term 2017 page linked all 56 of its entries to four preliminary prints, which the server answered with 404 Not Found. Such a row holds the whole volume, with one entry per opinion and the page its link points to.",
            LIGATURES,
        ),
        category="opinions",
    ),
    Collection(
        "opinions-relating-to-orders", "Opinions Relating to Orders", f"{BASE}/opinions/relatingtoorders/",
        term_pages("opinions-relating-to-orders", BASE + "/opinions/relatingtoorders/{yy}"), parse_opinion_table, typed_opinion,
        notes=(
            "Several opinions can share one PDF; the row holds the file once, with one entry per opinion, and an entry's page is the #page anchor of its link where the link has one.",
            "The pages for October Terms 2005 through 2010 list 57 opinions in tables without a link to any file, only a citation to the U. S. Reports (counted September 25, 2026), so those terms have no rows here.",
            LIGATURES,
        ),
        category="opinions",
    ),
    Collection(
        "in-chambers-opinions", "In-Chambers Opinions", f"{BASE}/opinions/in-chambers.aspx",
        single(f"{BASE}/opinions/in-chambers.aspx"), parse_opinion_table, typed_opinion,
        notes=("The Court's page says in-chambers opinions \"will be posted here on the day of their issuance and will remain posted until published in the bound volume of the United States Reports.\" A row the page no longer lists stays in the dataset with listed = false.",),
        category="opinions",
    ),
    Collection(
        "us-reports", "U. S. Reports", f"{BASE}/opinions/USReports.aspx",
        single(f"{BASE}/opinions/USReports.aspx"), parse_us_reports, typed_us_reports, partition_label="volumes",
        notes=(
            "Older volumes are scans of the printed books. A scan's only text is the OCR layer inside the file, which is in ocr_text and is not verbatim; text_source on each row says which kind of file it is, and the table above counts them.",
            "Seven bound volumes that are not scans are marked mixed, so their text is in ocr_text too: volumes 515, 532, 533, 534, 545, 571 and 576. In each, one to three pages carry an image, such as a map or a photograph, that covers at least half of the page, and the text on those pages is a heading, a caption or a map's labels, such as \"Red arrow points to Ten Commandments Monument.\" in volume 545. That text is drawn as visible type, while the scan of volume 500 draws its OCR layer as invisible text (render mode 3); both were examined on September 26, 2026.",
            "The Court's page says: \"PDFs of partial volumes made available for the convenience of the bench and bar, as well as page proofs of volumes not yet published by GPO, will be posted bearing a “page proof” watermark.\"",
            LIGATURES,
            "Measured September 26, 2026: 13 bound volumes from 529 to 544 set some signs in a font that names its glyphs H and a number, such as H11503, and gives no Unicode value for them, and pdftotext takes the number for a code point, so `text` has 218 characters from blocks such as CJK and Coptic that the volumes do not use. Page 596 of the file of volume 541 prints \"1.5%×2×$2,000=$60\", and `text` has \"1.5%⳯2⳯$2,000⳱$60\"; its page 1097 prints two empty check boxes, and `text` has 䡺 for each. For all 218, the file holds a glyph named H and the character's code point in decimal; pypdf prints the glyph's name instead (/H11033).",
        ),
        category="opinions",
    ),
    Collection(
        "online-sources-cited-in-opinions", "Online Sources Cited in Opinions", f"{BASE}/opinions/urls_cited.aspx",
        cited_url_pages, parse_online_sources, typed_online_source,
        notes=(
            "The Court's page says: \"Because some URLs cited in the Court’s opinions may change over time or disappear altogether, an attempt is made to capture in PDF format the material cited in an opinion.\" This collection stores only those Court-hosted PDF captures under `/opinions/URLs_Cited/`; the outside URLs themselves are kept as listing text in `entries.link_text` and are not fetched.",
            "Read from the listing pages on September 27, 2026: the October Terms 2005 to 2024 link 1,223 Court-hosted PDFs 1,234 times, as one capture can be listed under more than one case, and the October Term 2025 page lists none yet.",
        ),
        category="opinions",
    ),
    Collection(
        "media-files-cited-in-opinions", "Media Files Cited in Opinions", f"{BASE}/media/media.aspx",
        single(f"{BASE}/media/media.aspx"), parse_media_files, typed_media_file,
        notes=(
            "The Court's page says: \"On occasion, an opinion may cite to a media file, e.g., a video or audio file that is part of the record in the lower court. Those files are posted here.\" This collection stores only Court-hosted files linked by the page; it does not follow any outside source.",
            "On September 27, 2026 the page linked 10 files: 9 MP4 videos and 1 MP3 recording. Audio and video rows store the bytes in `file` and have `text_source` = `no_text`; a response that does not open as MP3 audio or as an MP4 file (an `ftyp` box) is a failure, not a row.",
        ),
        category="opinions",
    ),
    Collection(
        "argument-transcripts", "Argument Transcripts", f"{BASE}/oral_arguments/argument_transcript/",
        transcript_pages, parse_transcripts, typed_transcript,
        notes=(
            "The Court's transcript pages say: \"Same-day transcripts are considered official but subject to final review.\" and \"Transcripts for oral arguments prior to October Term 2000 have been scanned from the Supreme Court Library collection. Please disregard any stray or handwritten markings on these copies.\"",
            "The Court's page on the availability of transcripts says: \"(Heritage Reporting Corporation has provided transcripts for the Court beginning in October Term 2017; prior to that Term, Alderson Reporting Corporation provided the transcripts.)\"",
            "Measured September 26, 2026: 273 transcripts of October Terms 2004 to 2010 " + NO_TEXT_HYPHENS.format(hyphens="17,322") + " The transcript of 07-1372 prints \"purposes --\" at the end of a line on its page 4, and `text` has \"purposes -\". In 50 other transcripts, of October Terms 2004 and 2005, the files draw hyphens with code 0xAD, which `text` gives as a hyphen (U+002D) and pypdf as a soft hyphen (U+00AD), and that accounts for the whole difference the cross-check counts.",
        ),
        category="oral-arguments",
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
        category="oral-arguments",
    ),
    Collection(
        "orders-of-the-court", "Orders of the Court", f"{BASE}/orders/ordersofthecourt/",
        term_pages("orders-of-the-court", BASE + "/orders/ordersofthecourt/{yy}"), parse_dated_items, typed_dated_item,
        notes=(
            "The Court's page says: \"Caution: These electronic orders may contain computer-generated errors or other deviations from the official printed versions. Moreover, all order lists and miscellaneous orders are replaced within a few months by paginated versions of them in a preliminary print of the United States Reports, and one year after the issuance of the preliminary print by the final version of the orders in a U. S. Reports bound volume. In case of discrepancies between the print and electronic versions of orders, the print version controls. In case of discrepancies between order lists or miscellaneous orders and any later official version of them, the later version controls.\"",
            "Four files are marked mixed, so their text is in ocr_text: \"Rules of Appellate Procedure\", \"Rules of Bankruptcy Procedure\", \"Rules of Criminal Procedure\" and \"Rules of Civil Procedure\", each dated 03/26/09 on the listing. In each, the first three pages are born-digital and every later page is a scan with an invisible OCR layer (examined September 26, 2026).",
            "Measured September 26, 2026: 33 files of October Terms 2005, 2009 and 2010 " + NO_TEXT_HYPHENS.format(hyphens="1,319"),
            "Some files set bullets and dashes in a font that gives them Private Use Area code points, and `text` keeps those: the \"Rules of Appellate Procedure\" of 04/28/16 prints a bullet on page 38 of the file where `text` has U+F0B7, and that of 04/27/17 prints \"Rule 4. Appeal as of Right—When Taken\" on page 4 where `text` has U+F0BE in place of the em dash (read from the rendered pages on September 26, 2026).",
        ),
        category="case-documents",
    ),
    Collection(
        "orders-by-circuit", "Orders by Circuit", f"{BASE}/orders/ordersbycircuit/",
        term_pages("orders-by-circuit", BASE + "/orders/ordersbycircuit/{yy}"), parse_circuit, typed_dated_item, html=True,
        notes=("The Court's page says: \"Caution: These electronic orders may contain computer-generated errors or other deviations from the official printed versions.\"", "Each document is an HTML page. The file column holds the page as served, which carries values that change from one request to the next (ASP.NET's __VIEWSTATE and __EVENTVALIDATION fields, and the request values of an analytics script), so file_sha256 need not repeat across downloads. text is the rendered content the Court wrote, which leaves those out: one document fetched twice on September 25, 2026, two hours apart, gave different bytes and the same text.",),
        category="case-documents",
    ),
    Collection(
        "granted-noted-cases-list", "Granted/Noted Cases List", f"{BASE}/orders/grantednotedlists.aspx",
        single(f"{BASE}/orders/grantednotedlists.aspx"), parse_granted, typed_term_link, partition_label="all", mutable=True,
        category="case-documents",
    ),
    Collection(
        "journal", "Journal", f"{BASE}/orders/journal.aspx",
        single(f"{BASE}/orders/journal.aspx", f"{BASE}/orders/scannedjournals.aspx"), parse_journal, typed_term_link, partition_label="decade", mutable=True,
        notes=(
            "The journals from 1993 onward are listed on the Journal page and the earlier ones on the Scanned Journals page, which says: \"These volumes were scanned from a working collection at the Supreme Court. Please disregard any stray marks on the initial pages of each volume.\" A scan's only text is the OCR layer inside the file, which is in ocr_text and is not verbatim.",
        ),
        category="case-documents",
    ),
    Collection(
        "original-jurisdiction-records-and-briefs", "Original Jurisdiction Records & Briefs", f"{BASE}/casedocuments/original_jurisdiction_cases.aspx",
        single(f"{BASE}/casedocuments/original_jurisdiction_cases.aspx"), parse_original, typed_original, partition_label="case",
        notes=(
            "The Court's page says: \"The collection here is a digitized version of the physical collection in the Supreme Court's Library and may not contain all records and briefs that were filed in a given case.\" Besides the parties' filings, the listing has reports of special masters and documents titled as the Court's own, such as \"Opinion of the Court\", \"Slip Opinion\", \"Order\" and \"Decree\" (read from the listing's document titles on September 25, 2026).",
            "A scanned file's only text is the OCR layer inside it, which is in ocr_text and is not verbatim; the text-source table above counts the files of each kind.",
        ),
        category="case-documents",
    ),
    Collection(
        "press-releases", "Press Releases", f"{BASE}/publicinfo/press/pressreleases.aspx",
        single(f"{BASE}/publicinfo/press/pressreleases.aspx"), parse_news_html, typed_news_html, partition_label="year", html=True,
        notes=("The collection stores the press release HTML pages linked by the Press Releases listing. Press credentials and other services pages are not included because they are forms or logistics pages, not document listings.",),
        category="news-media",
    ),
    Collection(
        "media-advisories", "Media Advisories", f"{BASE}/publicinfo/media/mediaadvisories.aspx",
        single(f"{BASE}/publicinfo/media/mediaadvisories.aspx"), parse_news_html, typed_news_html, partition_label="year", html=True,
        notes=("The collection stores the media advisory HTML pages linked by the Media Advisories listing. Press Credentials and Courtroom Seating are not included: they are a credentials form and a visitor-service page, not documents.",),
        category="news-media",
    ),
    Collection(
        "speeches", "Speeches", f"{BASE}/publicinfo/speeches/speeches.aspx",
        single(f"{BASE}/publicinfo/speeches/speeches.aspx"), parse_speeches, typed_year_document, partition_label="year",
        notes=("The collection stores the speech PDFs linked by the Speeches listing. The year partition is read from the listing text when present.",),
        category="news-media",
    ),
    Collection(
        "chief-justice-year-end-reports", "Chief Justice's Year-End Reports on the Federal Judiciary", f"{BASE}/publicinfo/year-end/year-endreports.aspx",
        single(f"{BASE}/publicinfo/year-end/year-endreports.aspx"), parse_year_end_reports, typed_year_document, partition_label="year",
        notes=("The collection stores the Year-End Reports linked by the Court's listing. Newer reports are PDFs; older reports are HTML pages, and both are kept as served.",),
        category="news-media",
    ),
    Collection(
        "reporters-guide-to-applications", "A Reporter's Guide to Applications", f"{BASE}/publicinfo/publicinfo.aspx",
        single(f"{BASE}/publicinfo/publicinfo.aspx"), parse_reporters_guide, typed_single_document, mutable=True,
        notes=("The footer links A Reporter's Guide to Applications as a single PDF. Press Credentials is excluded because it is a credentials process.",),
        category="news-media",
    ),
    Collection(
        "services-for-news-media", "Services for News Media", f"{BASE}/publicinfo/publicinfo.aspx",
        single(f"{BASE}/publicinfo/publicinfo.aspx"), parse_publicinfo_pdf("publicinfo/pioservices.pdf", "Services for News Media"), typed_single_document, mutable=True,
        notes=("The News Media landing page links Services for News Media as a single PDF: the Public Information Office's account of its services to the press, such as its pressroom, the argument calendar, argument audio, and briefs and petitions. The Court revises it under the same address (the copy read on September 27, 2026 names the 2025 Term calendar), so every run asks the server whether it changed.",),
        category="news-media",
    ),
    Collection(
        "rules-and-guidance", "Rules and Guidance", f"{BASE}/filingandrules/rules_guidance.aspx",
        single(f"{BASE}/filingandrules/rules_guidance.aspx", f"{BASE}/ctrules/scannedrules.aspx"), parse_filing_documents, typed_filing_document, partition_label="year or current", mutable=True,
        notes=("The collection stores documents linked from Rules and Guidance, including the historical Rules PDFs linked by the Historical Rules page that Rules and Guidance names. External filing-system links, the case citation tool and service pages are not followed.",),
        category="filing-and-rules",
    ),
    Collection(
        "electronic-filing-documents", "Electronic Filing Documents", f"{BASE}/filingandrules/electronicfiling.aspx",
        single(f"{BASE}/filingandrules/electronicfiling.aspx"), parse_filing_documents, typed_filing_document, partition_label="year or current", mutable=True,
        notes=("The collection stores PDF and HTML documents linked by the Electronic Filing page. The external electronic filing system itself is excluded because it is a service outside www.supremecourt.gov, and interactive eLearning tutorials are excluded because they are training applications rather than document files.",),
        category="filing-and-rules",
    ),
    Collection(
        "supreme-court-bar-documents", "Supreme Court Bar Documents", f"{BASE}/filingandrules/supremecourtbar.aspx",
        single(f"{BASE}/filingandrules/supremecourtbar.aspx"), parse_filing_documents, typed_filing_document, partition_label="year or current", mutable=True,
        notes=("The collection stores the bar admissions form and admissions instructions linked by the Supreme Court Bar page. It does not submit or automate bar admission services.",),
        category="filing-and-rules",
    ),
    Collection(
        "about-the-court", "About the Court", f"{BASE}/about/justices.aspx",
        about_pages, parse_about_documents, typed_about_document, partition_label="document kind", mutable=True,
        notes=(
            "The collection stores the About-column pages for Justices, Supreme Court at Work, Code of Conduct for Justices, History and Traditions, The Supreme Court Building, Building Regulations and Frequently Asked Questions, plus same-site `/about/` HTML or PDF documents those pages link directly. The Visiting the Court FAQ and the Supreme Court Electronic Filing System FAQ are excluded because Visit is visitor logistics and electronic filing belongs to Filing & Rules. The Court edits these pages in place, so every run reads each stored page again and asks the server whether each stored PDF changed. A HEAD request cannot show that a page was edited: on September 27, 2026 three of the pages sent no ETag or Content-Length, and two of them (Justices and The Supreme Court Building) sent the same Last-Modified, October 24, 2025, 22:22:55 GMT, which looks like the time the site was deployed. A page whose rendered text is unchanged keeps its stored row.",
            "Rows store each page's HTML, not the images it shows: the photographs and icons the pages reference sit under `/images/`, which robots.txt disallows, and are not fetched.",
        ),
        category="about",
    ),
    Collection(
        "argument-audio", "Argument Audio", f"{BASE}/oral_arguments/argument_audio/",
        audio_pages, parse_argument_audio, typed_argument_audio, partition_label="argument month", mutable=True,
        notes=("The collection stores one MP3 file per oral argument audio page, with the audio bytes in `file` and no extracted text (`text_source` is `no_text`). Argument audio pages begin with October Term 2010 on the Court's site.",),
        category="oral-arguments",
    ),
)}


class Listing:
    """Reads a collection's listing pages. list_all() returns (head, {id: Unit}); pages that redirect elsewhere (term pages that do not exist) count as empty. A term page that shows another term (see shown_terms) is read again at reread_url; where that shows its term, its entries are taken and the page records the terms the plain URL showed as `reread`. Otherwise it lists nothing for its term: its entries are the other term's, so they are not taken, and where the last complete listing found entries on it, pipeline.held_pages holds what they listed."""

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

    def reread(self, url, term):
        """The main column of the page at reread_url(url), or None where it redirects, fails or does not show term."""
        try:
            data = self.read(reread_url(url))
            main = None if data is None else main_content(parse(data))
        except (ListingError, ValueError):
            return None
        return main if main is not None and sorted(shown_terms(main)) == [term] else None

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
            shown = sorted(shown_terms(main)) if term is not None else []
            reread = bool(shown) and shown != [term]
            if reread:
                main = self.reread(url, term)
                if main is None:
                    self.pages[url] = {"term": term, "entries": 0, "shown_term": shown}
                    continue
            entries = self.collection.parse(main, url, term)
            self.pages[url] = {"term": term, "entries": len(entries)} | ({"reread": shown} if reread else {})
            found += entries
        units = group_units(found)
        head = {"count": len(units), "entries": len(found), "pages": len(self.pages)}
        return head, units
