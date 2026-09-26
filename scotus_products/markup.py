"""Reading the Court's HTML: the listing pages, and the Orders by Circuit pages, which are documents themselves.

Text taken from HTML is the text a browser renders, character for character, with one change: each run of ASCII whitespace (space, tab, line feed, form feed, carriage return) becomes one space, and such runs at either end are dropped. HTML renders those runs as one space, so this is the rendered text. Every other character, the no-break space among them, is kept as it is. A <br> or a block element (p, div, li, tr, ...) ends a line, and a table cell starts a new cell.
"""

import re
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

import lxml.html

ASCII_WS = re.compile(r"[ \t\n\f\r]+")
BLOCK = frozenset("address article aside blockquote body caption center dd div dl dt fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header hr html li main nav ol p pre section table tbody tfoot thead tr ul".split())
CELL = frozenset(("td", "th"))
SKIP = frozenset(("script", "style", "noscript", "template", "head", "title", "meta", "link"))
BREAK, TAB, START, END = object(), object(), object(), object()
PAGE_ANCHOR = re.compile(r"(?:^|&)page=(\d+)", re.I)
# Characters left unescaped in a URL path; everything else, the space and non-ASCII among them, is percent-encoded.
PATH_SAFE = "/:@!$&'()*+,;=-._~"


def squash(text):
    return ASCII_WS.sub(" ", text or "").strip(" ")


def parse(data, encoding="utf-8"):
    """A parsed page. The bytes are decoded strictly: a page in another encoding fails here instead of yielding wrong characters."""
    return lxml.html.document_fromstring(data.decode(encoding))


def _walk(element, out, target=None):
    tag = element.tag if isinstance(element.tag, str) else None
    if tag is None:
        return
    tag = tag.lower()
    if tag in SKIP:
        return
    if tag == "br":
        out.append(BREAK)
        return
    block = tag in BLOCK
    if block:
        out.append(BREAK)
    if tag in CELL:
        out.append(TAB)
    if element is target:
        out.append(START)
    if element.text:
        out.append(element.text)
    for child in element:
        _walk(child, out, target)
        if child.tail:
            out.append(child.tail)
    if element is target:
        out.append(END)
    if block:
        out.append(BREAK)


def _lines(tokens):
    """[(line text, holds part of the target)], empty lines dropped. Cells of a line are joined by a tab."""
    lines, cells, marked, inside = [], [[]], False, False

    def flush():
        nonlocal cells, marked
        text = "\t".join(cell for cell in (squash("".join(parts)) for parts in cells) if cell)
        if text:
            lines.append((text, marked))
        cells, marked = [[]], False

    for token in tokens:
        if token is BREAK:
            flush()
        elif token is TAB:
            cells.append([])
        elif token is START or token is END:
            marked, inside = True, token is START
        else:
            cells[-1].append(token)
            marked = marked or inside
    flush()
    return lines


def render(element):
    """The rendered text of an element as lines joined by line feeds."""
    tokens = []
    _walk(element, tokens)
    return "\n".join(text for text, _ in _lines(tokens))


def field(element):
    """The rendered text of an element on one line: line and cell breaks become spaces."""
    tokens = []
    _walk(element, tokens)
    return squash("".join(token if isinstance(token, str) else " " for token in tokens))


def line_of(anchor):
    """The rendered line that holds the anchor, within its nearest block or cell: "Revisions: 7/01/26" for a revision link. A link that spans lines gives all of them, joined by spaces."""
    container = anchor.getparent()
    while container is not None and not (isinstance(container.tag, str) and container.tag.lower() in BLOCK | CELL):
        container = container.getparent()
    if container is None:
        return field(anchor)
    tokens = []
    _walk(container, tokens, target=anchor)
    held = [text.replace("\t", " ") for text, marked in _lines(tokens) if marked]
    return " ".join(held) if held else field(anchor)


def resolve(page_url, href):
    """(id, url, page) of a link: id is the path, percent-decoded and lower-cased, without the leading slash (the site is served by IIS, which ignores case); url is absolute, percent-encoded and without the fragment; page is the number in a #page=N fragment, else None."""
    parts = urlsplit(urljoin(page_url, href.strip()))
    path = unquote(parts.path)
    match = PAGE_ANCHOR.search(parts.fragment or "")
    url = urlunsplit((parts.scheme, parts.netloc, quote(path, safe=PATH_SAFE), parts.query, ""))
    return path.lstrip("/").lower(), url, int(match.group(1)) if match else None


def main_content(doc):
    """The page's main column, where every listing sits; the navigation around it is left out."""
    found = doc.xpath('//div[@id="pagemaindiv"]')
    if not found:
        raise ValueError("the page has no div#pagemaindiv")
    return found[0]
