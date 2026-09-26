"""Text from the stored bytes, and whether that text is born-digital or somebody's OCR.

PDF text is the output of `pdftotext -raw -enc UTF-8` (poppler), stored exactly as it comes, form feeds between pages included. pdftotext's default mode was rejected because it deletes a hyphen that ends a line and joins the two lines, which changes words: on U.S. Reports volume 587 it printed 2,454 hyphens where -raw, -layout and pypdf each print 9,217 (measured September 25, 2026). -layout was rejected because on one 2009 transcript it lost 53 characters that -raw and pypdf both print. -raw keeps the order in which the file draws its text.

A page is a scanned page when one image covers at least half of it and the page has a text layer: that layer is OCR, made by whoever scanned the page. A file with any such page gets its text in ocr_text, never in text. On 28 sample files (September 25, 2026) the born-digital files had no page even half covered by an image, and every page of every scan was. Invisible text (render mode 3) is not a sign of OCR here: several born-digital orders and day calls draw invisible spaces.

The cross-check extracts the text again with pypdf, which shares no code with poppler. xcheck_equal says whether the two agree on the sequence of non-whitespace characters; xcheck_equal_nfkd whether they agree once both sides are NFKD-normalized (poppler writes a ligature glyph as its letters and pypdf as the ligature character, the one systematic difference on the samples); xcheck_delta counts the NFKD-normalized characters one side has and the other lacks, in any order. Only the comparison normalizes: the stored text never is.
"""

import logging
import re
import subprocess
import tempfile
import unicodedata
from collections import Counter
from functools import cache
from pathlib import Path

import pypdf

from .markup import parse, render

PDFTOTEXT = ("pdftotext", "-raw", "-enc", "UTF-8")
IMAGE_SHARE = 0.5
TIMEOUT = 900
HTML_RENDERER = "scotus_products.markup.render 1"
_WS = re.compile(r"\s+")
_BOX = re.compile(r"^Page\s+(\d+)\s+CropBox:\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*$")
_INFO = re.compile(r"^([A-Za-z][A-Za-z ]*?):\s*(.*)$")
# The Orders by Circuit content sits in this editor div inside the page's main column.
CIRCUIT_CONTENT = '//div[@id="ctl00_ctl00_MainEditable_mainContent_RadEditor1"]'

logging.getLogger("pypdf").setLevel(logging.ERROR)


class NotAPdf(ValueError):
    """The response to a PDF link was something else, such as an HTML error page."""


@cache
def poppler_version():
    out = subprocess.run(["pdftotext", "-v"], capture_output=True, text=True, check=False)
    match = re.search(r"pdftotext version (\S+)", out.stderr + out.stdout)
    if not match:
        raise RuntimeError("pdftotext -v printed no version: install poppler")
    return match.group(1)


def extractor():
    return f"pdftotext {poppler_version()} {' '.join(PDFTOTEXT[1:])}"


def xcheck_extractor():
    return f"pypdf {pypdf.__version__}"


def _run(args):
    out = subprocess.run(args, capture_output=True, timeout=TIMEOUT, check=False)
    if out.returncode != 0:
        raise RuntimeError(f"{args[0]} exited {out.returncode}: {out.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return out.stdout


def pdf_info(path):
    """(document information as pdfinfo prints it, {page: crop box area in square points})."""
    raw = _run(["pdfinfo", "-enc", "UTF-8", "-box", "-f", "1", "-l", "1000000", str(path)])
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "replace")
    info, areas = {}, {}
    for line in text.splitlines():
        box = _BOX.match(line)
        if box:
            x0, y0, x1, y1 = map(float, box.groups()[1:])
            areas[int(box.group(1))] = abs((x1 - x0) * (y1 - y0))
        elif not line.startswith("Page "):
            match = _INFO.match(line)
            if match:
                info[match.group(1)] = match.group(2)
    return info, areas


def image_cover(path, areas):
    """{page: the largest share of the page that one image covers}. pdfimages gives each image's size in pixels and its resolution as placed on the page."""
    cover = {}
    for line in _run(["pdfimages", "-list", str(path)]).decode("utf-8", "replace").splitlines()[2:]:
        cells = line.split()
        if len(cells) < 14:
            continue
        try:
            page, width, height, xppi, yppi = int(cells[0]), int(cells[3]), int(cells[4]), float(cells[12]), float(cells[13])
        except ValueError:
            continue
        if xppi <= 0 or yppi <= 0 or not areas.get(page):
            continue
        share = (width / xppi * 72) * (height / yppi * 72) / areas[page]
        cover[page] = max(cover.get(page, 0.0), share)
    return cover


def classify(page_texts, cover):
    """(text_source, image pages, OCR pages): scanned when every page with text is an image page, mixed when only some are, born_digital when none is, no_text when no page has text."""
    pages = len(page_texts)
    image_pages = sum(1 for page in range(1, pages + 1) if cover.get(page, 0.0) >= IMAGE_SHARE)
    with_text = [page for page in range(1, pages + 1) if _WS.sub("", page_texts[page - 1])]
    ocr_pages = sum(1 for page in with_text if cover.get(page, 0.0) >= IMAGE_SHARE)
    if not with_text:
        source = "no_text"
    elif ocr_pages == 0:
        source = "born_digital"
    elif ocr_pages == len(with_text):
        source = "scanned"
    else:
        source = "mixed"
    return source, image_pages, ocr_pages


def nonspace(text):
    return _WS.sub("", text)


def compare(text, other):
    """(equal, equal after NFKD, NFKD character-count delta) of two extractions."""
    a, b = nonspace(text), nonspace(other)
    na, nb = nonspace(unicodedata.normalize("NFKD", text)), nonspace(unicodedata.normalize("NFKD", other))
    ca, cb = Counter(na), Counter(nb)
    return a == b, na == nb, sum(((ca - cb) + (cb - ca)).values())


def pypdf_text(path):
    reader = pypdf.PdfReader(str(path))
    return "\f".join((page.extract_text() or "") for page in reader.pages)


def pdftotext(path):
    return _run([*PDFTOTEXT, str(path), "-"]).decode("utf-8")


def extract_pdf(data, workdir=None, xcheck=True):
    """Everything a PDF row holds apart from its listing: pages, classification, text or ocr_text, and the cross-check."""
    if data[:5] != b"%PDF-" and b"%PDF-" not in data[:1024]:
        raise NotAPdf(f"the response starts {data[:40]!r}")
    with tempfile.TemporaryDirectory(dir=workdir) as scratch:
        path = Path(scratch) / "document.pdf"
        path.write_bytes(data)
        info, areas = pdf_info(path)
        pages = int(info.get("Pages") or 0)
        output = pdftotext(path)
        page_texts = output.split("\f")
        # pdftotext ends every page with a form feed, so the last piece is what follows the last one: nothing.
        if len(page_texts) != pages + 1 or page_texts[-1]:
            raise RuntimeError(f"pdftotext printed {len(page_texts) - 1} form feeds for {pages} pages")
        page_texts = page_texts[:-1]
        cover = image_cover(path, areas)
        source, image_pages, ocr_pages = classify(page_texts, cover)
        row = {
            "media_type": "application/pdf",
            "pages": pages,
            "image_pages": image_pages,
            "ocr_pages": ocr_pages,
            "text_source": source,
            "text": output if source == "born_digital" else None,
            "ocr_text": output if source in ("scanned", "mixed") else None,
            "extractor": extractor(),
            "xcheck_extractor": None,
            "xcheck_equal": None,
            "xcheck_equal_nfkd": None,
            "xcheck_delta": None,
            "pdf_info": info,
            "notes": [],
        }
        if xcheck and row["text"] is not None:
            try:
                other = pypdf_text(path)
            except Exception as error:  # noqa: BLE001 - a cross-check that cannot run is recorded, not fatal
                row["notes"].append(f"pypdf failed: {type(error).__name__}: {error}"[:300])
            else:
                row["xcheck_extractor"] = xcheck_extractor()
                row["xcheck_equal"], row["xcheck_equal_nfkd"], row["xcheck_delta"] = compare(output, other)
        return row


def extract_html(data):
    """An Orders by Circuit page: its text is the rendered text of the content the Court wrote, without the site's scripts and form fields, which change per request."""
    doc = parse(data)
    found = doc.xpath(CIRCUIT_CONTENT) or doc.xpath('//div[@id="pagemaindiv"]')
    if not found:
        raise ValueError("the page has neither the editor div nor div#pagemaindiv")
    text = render(found[0])
    if not text:
        raise ValueError("the page's content renders to no text")
    return {
        "media_type": "text/html",
        "pages": None,
        "image_pages": None,
        "ocr_pages": None,
        "text_source": "html",
        "text": text,
        "ocr_text": None,
        "extractor": HTML_RENDERER,
        "xcheck_extractor": None,
        "xcheck_equal": None,
        "xcheck_equal_nfkd": None,
        "xcheck_delta": None,
        "pdf_info": None,
        "notes": [],
    }
