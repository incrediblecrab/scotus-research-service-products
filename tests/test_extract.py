import subprocess
import unicodedata

import pytest

from conftest import FIXTURES, page_bytes, pdf_bytes
from scotus_products import extract
from scotus_products.extract import NotAPdf, classify, compare, extract_html, extract_pdf


def pdftotext_raw(name):
    return subprocess.run(["pdftotext", "-raw", "-enc", "UTF-8", str(FIXTURES / "pdfs" / name), "-"], capture_output=True, check=True).stdout.decode("utf-8")


def test_born_digital_text_is_the_raw_extractor_output_unchanged(born_digital):
    row = extract_pdf(born_digital)
    assert row["text_source"] == "born_digital"
    assert row["text"] == pdftotext_raw("inchambers.pdf")
    assert row["ocr_text"] is None
    assert row["extractor"].startswith("pdftotext ") and row["extractor"].endswith(" -raw -enc UTF-8")


def test_raw_mode_keeps_hyphens_that_end_a_line(born_digital):
    # pdftotext's default mode prints none of these 7: it removes a hyphen that ends a line and joins the word, which changes the text.
    text = extract_pdf(born_digital)["text"]
    assert sum(1 for line in text.split("\n") if line.endswith("-")) == 7


def test_one_form_feed_per_page(born_digital):
    row = extract_pdf(born_digital)
    assert row["text"].count("\f") == row["pages"] == 1


def test_invisible_text_is_not_taken_for_ocr():
    # This day call draws invisible spaces (render mode 3) and has no image: it is born-digital.
    row = extract_pdf(pdf_bytes("daycall.pdf"))
    assert (row["text_source"], row["image_pages"], row["ocr_pages"]) == ("born_digital", 0, 0)
    assert row["text"] == pdftotext_raw("daycall.pdf")


def test_ocr_layer_of_a_scan_goes_to_ocr_text_only(scanned):
    row = extract_pdf(scanned)
    assert (row["text_source"], row["image_pages"], row["ocr_pages"]) == ("scanned", 1, 1)
    assert row["text"] is None
    assert row["ocr_text"] == pdftotext_raw("oj-page3.pdf")
    assert "STATE OF WISCONSIN" in row["ocr_text"]
    # OCR is not cross-checked: agreement between two readers of one OCR layer says nothing about the page.
    assert row["xcheck_equal"] is None


def test_cross_check_agrees_on_the_born_digital_fixture(born_digital):
    row = extract_pdf(born_digital)
    assert (row["xcheck_equal"], row["xcheck_equal_nfkd"], row["xcheck_delta"]) == (True, True, 0)
    assert row["xcheck_extractor"].startswith("pypdf ")


def test_cross_check_runs_on_its_own_extractor(born_digital, monkeypatch):
    monkeypatch.setattr(extract, "pypdf_text", lambda path: "something else entirely")
    row = extract_pdf(born_digital)
    assert row["xcheck_equal"] is False and row["xcheck_delta"] > 0
    # The stored text never depends on the cross-check.
    assert row["text"] == pdftotext_raw("inchambers.pdf")


def test_compare_counts_a_ligature_as_equal_only_after_nfkd():
    assert unicodedata.normalize("NFKD", "\ufb01") == "fi"
    assert compare("the \ufb01nal order", "the final\norder") == (False, True, 0)
    assert compare("abc", "abd") == (False, False, 2)
    assert compare("a b\nc", "abc") == (True, True, 0)


def test_classify():
    assert classify(["text", "more"], {}) == ("born_digital", 0, 0)
    assert classify(["ocr", "ocr"], {1: 0.9, 2: 1.0}) == ("scanned", 2, 2)
    assert classify(["typed", "ocr"], {2: 0.8}) == ("mixed", 1, 1)
    assert classify([" \n", ""], {1: 1.0}) == ("no_text", 1, 0)
    # A small image (a seal or signature) does not make a page scanned.
    assert classify(["text"], {1: 0.2}) == ("born_digital", 0, 0)


def test_a_response_that_is_not_a_pdf_is_refused():
    with pytest.raises(NotAPdf):
        extract_pdf(b"<!DOCTYPE html><html><body>Error</body></html>")


def test_html_document_text_is_the_rendered_content_without_scripts():
    row = extract_html(page_bytes("orders_ordersbycircuit_ordercasebycircuit_090426OrderCasesByCircuit"))
    assert row["text_source"] == "html" and row["media_type"] == "text/html"
    assert row["text"].startswith("Order List by Circuit for Friday, September 4, 2026\nCircuit\u00a0\u00a02\n24-1015\u00a0(22-2858)\t25-7003\u00a0(24-2574)\n")
    assert "function" not in row["text"] and "Caution" not in row["text"]
