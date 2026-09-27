"""The case-centric builder: the docket page parser on saved real pages, docket keys and addresses, the fetch and recheck rules, and whole runs into a local store with the source datasets and the Court's site faked."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from scotus_products import cases
from scotus_products.http import Blocked
from scotus_products.store import MANIFEST, sha256_file

FIXTURES = Path(__file__).parent / "fixtures" / "cases"
NEW = "https://www.supremecourt.gov/docket/docketfiles/html/public/"
OLD = "https://www.supremecourt.gov/docketfiles/"
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
SOURCE_SCHEMA = pa.schema([(name, pa.int32() if name == "term" else pa.string()) for name in cases.SOURCE_COLUMNS])


def read(name):
    return (FIXTURES / name).read_bytes()


NOT_FOUND_PAGE = read("dk_16-1.html")


class Response:
    def __init__(self, status_code, content=b"", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = httpx.Headers(headers or {})


class Site:
    """The Court's site from {url: bytes}: an ETag per body, a 304 to a matching If-None-Match, and the Court's 404 page for any other URL. status forces a URL's status; blocked_after makes every request after that many raise Blocked, as a 403 does."""

    def __init__(self, pages=None):
        self.pages = dict(pages or {})
        self.status = {}
        self.calls = []
        self.blocked_after = None
        self.requests = {}

    def request(self, method, url, headers=None):
        if self.blocked_after is not None and len(self.calls) >= self.blocked_after:
            raise Blocked("403 from www.supremecourt.gov")
        self.calls.append((url, dict(headers or {})))
        self.requests["www.supremecourt.gov"] = self.requests.get("www.supremecourt.gov", 0) + 1
        if url in self.status:
            return Response(self.status[url])
        body = self.pages.get(url)
        if body is None:
            return Response(404, NOT_FOUND_PAGE)
        etag = '"' + hashlib.sha256(body).hexdigest()[:12] + '"'
        if (headers or {}).get("If-None-Match") == etag:
            return Response(304, b"", {"ETag": etag})
        return Response(200, body, {"ETag": etag, "Last-Modified": "Mon, 27 Jul 2026 18:00:36 GMT"})

    def get(self, url):
        return self.request("GET", url)

    def asked(self, url):
        return sum(1 for called, _ in self.calls if called == url)

    def close(self):
        pass


def test_parse_current_application_original_old_and_not_found_pages():
    current = cases.parse_docket_page(read("dk_25-1.html"), NEW + "25-1.html")
    assert current["found"] is True
    assert current["fields"] | {"lower_court_case_numbers": None} == {"docket": "25-1", "case_name": "James Skinner, Petitioner v. Louisiana", "docketed_date": "2025-07-01", "linked_with": "24A1088", "lower_court": "Supreme Court of Louisiana", "lower_court_case_numbers": None, "lower_court_decision_date": "2025-02-25", "questions_presented_url": None}
    proceedings = current["proceedings"]
    assert len(proceedings) == 35
    assert proceedings[0]["date"] == "2025-05-08"
    assert proceedings[0]["text"].startswith("Application (24A1088) to extend the time") and "Main Document" not in proceedings[0]["text"]
    assert proceedings[0]["documents"][0] == {"title": "Main Document", "url": "https://www.supremecourt.gov/DocketPDF/25/25-1/358381/20250508152016672_24A____Skinner%20Application%20for%20Extension%20of%20Time_final.pdf"}
    # Each entry holds its own documents: 38 on 14 entries, the last only the detached opinion, and no attorney's contact line among them.
    assert sum(len(p["documents"]) for p in proceedings) == 38
    assert sum(1 for p in proceedings if p["documents"]) == 14
    assert proceedings[-1] == {"date": "2026-03-30", "text": "Petition DENIED. Justice Sotomayor, with whom Justice Jackson joins, dissenting from the denial of certiorari. (Detached Opinion)", "documents": [{"title": "Opinion", "url": "https://www.supremecourt.gov/opinions/25pdf/25-1_4315.pdf"}]}
    assert not any(d["url"].startswith("mailto:") for p in proceedings for d in p["documents"])
    assert current["attorneys"].startswith("Attorneys for Petitioner\nJee Yeong Park\nCounsel of Record")

    application = cases.parse_docket_page(read("dk_25A1.html"), NEW + "25A1.html")
    assert application["fields"]["case_name"] == "Bret Healy, Applicant v. Supreme Court of South Dakota, et al."
    assert application["fields"]["lower_court"] == "United States Court of Appeals for the Eighth Circuit"

    original = cases.parse_docket_page(read("dk_22O141.html"), NEW + "22O141.html")
    assert original["fields"]["case_name"] == "Texas, Plaintiff v. New Mexico and Colorado"
    assert original["fields"]["lower_court"] is None
    assert original["fields"]["questions_presented_url"] == "https://www.supremecourt.gov/docket/docketfiles/html/qp/141%20origqp.pdf"
    assert len(original["proceedings"]) == 167

    old = cases.parse_docket_page(read("docketfiles_05-1.htm"), OLD + "05-1.htm")
    assert old["fields"]["case_name"] == "Barbara McFarland, et al., Petitioners v. Cheminova, Inc., et al."
    assert old["proceedings"][0]["date"] == "2005-05-12" and old["proceedings"][0]["text"].startswith("Application (04A968) to extend the time")
    assert len(old["proceedings"]) == 8 and old["proceedings"][-1]["text"] == "Petition DENIED."
    assert old["attorneys"].splitlines()[:2] == ["Attorneys for Petitioners:", "John Granville Crabtree\t240 Crandon Blvd.\t(305) 361-3770"]
    assert "Attorneys for Respondents:" in old["attorneys"]

    granted = cases.parse_docket_page(read("dk_23-1197.html"), NEW + "23-1197.html")
    assert granted["fields"]["questions_presented_url"] == "https://www.supremecourt.gov/docket/docketfiles/html/qp/23-01197qp.pdf"

    missing = cases.parse_docket_page(NOT_FOUND_PAGE, NEW + "16-1.html", 404)
    assert missing["found"] is False and missing["html"] == NOT_FOUND_PAGE
    # The Court's not-found page served with 200 is not found either.
    assert cases.parse_docket_page(NOT_FOUND_PAGE, NEW + "16-1.html")["found"] is False


def test_audio_pages_give_mp3_links():
    listing = cases.parse_audio_listing(read("oral_arguments_argument_audio_2024.html"), "https://www.supremecourt.gov/oral_arguments/argument_audio/2024")
    assert listing["22-7466"] == "https://www.supremecourt.gov/oral_arguments/audio/2024/22-7466"
    page = cases.parse_audio_page(read("audio_case.html"), listing["22-7466"])
    assert page == {"page_url": listing["22-7466"], "mp3_url": "https://www.supremecourt.gov/media/audio/mp3files/22-7466.mp3", "date": "2024-10-09"}


@pytest.mark.parametrize("docket_field, file_id, expected", [
    ("141, Orig.", "opinions/25pdf/141orig_1a2b.pdf", ["141, Orig."]),
    ("141-Orig", "oral_arguments/argument_transcripts/2023/141-orig_2_5okl.pdf", ["141, Orig."]),
    ("143Orig", "oral_arguments/argument_transcripts/2021/143orig.pdf", ["143, Orig."]),
    ("105 Orig.", "pdfs/transcripts/1994/105original.pdf", ["105, Orig."]),
    ("22O141", "x.pdf", ["141, Orig."]),
    ("43 Orig.", "pdfs/transcripts/1970/43_orig_44_orig_10-19-1970.pdf", ["43, Orig.", "44, Orig."]),
    # The OT2007 listing gives No. 134, Orig. as 06-134; its file name says it is the original case.
    ("06-134", "pdfs/transcripts/2007/06-134orig.pdf", ["134, Orig."]),
    ("24-38, 24-43", "opinions/25pdf/24-38_abcd.pdf", ["24-38", "24-43"]),
    ("21-511", "oral_arguments/argument_transcripts/2021/21-511_71o9.pdf", ["21-511"]),
    ("19-863", "opinions/19pdf/593us1r32_21o3.pdf", ["19-863"]),
    ("11-398-Monday", "oral_arguments/argument_transcripts/2011/11-398-monday.pdf", ["11-398"]),
    ("14-556-Question-1", "oral_arguments/argument_transcripts/2014/14-556q1_l5gm.pdf", ["14-556"]),
    ("18A142T", "opinions/17pdf/18a142t_5h26.pdf", ["18A142"]),
    ("A-483", "pdfs/transcripts/1971/a-483_11-06-1971.pdf", ["A-483"]),
])
def test_row_dockets_key_every_spelling_the_sources_use(docket_field, file_id, expected):
    assert cases.row_dockets({"docket": docket_field, "id": file_id}) == expected


def test_docket_urls_put_the_likelier_format_first():
    assert cases.docket_urls("141, Orig.") == [("new", NEW + "22O141.html")]
    assert cases.docket_urls("24-1260") == [("new", NEW + "24-1260.html"), ("old", OLD + "24-1260.htm")]
    assert cases.docket_urls("05-1") == [("old", OLD + "05-1.htm"), ("new", NEW + "05-1.html")]
    assert cases.docket_urls("70-18") == [("old", OLD + "70-18.htm"), ("new", NEW + "70-18.html")]
    assert cases.docket_urls("10A100") == [("old", OLD + "10A100.htm"), ("new", NEW + "10A100.html")]
    assert cases.docket_urls("25A1") == [("new", NEW + "25A1.html"), ("old", OLD + "25A1.htm")]


def test_fetch_docket_finds_misses_revalidates_and_refuses_other_statuses():
    site = Site({OLD + "05-1.htm": read("docketfiles_05-1.htm")})
    found = cases.fetch_docket(site, "05-1")
    assert found["found"] is True and found["format"] == "old" and found["tried"] == [OLD + "05-1.htm"]
    assert found["etag"] and found["last_modified"] == "Mon, 27 Jul 2026 18:00:36 GMT"
    assert site.calls[0][1] == {}

    # Asked again where it was found, conditionally; a 304 keeps the stored record.
    again = cases.fetch_docket(site, "05-1", found)
    assert again["not_modified"] is True and again["status"] == 304 and again["html_sha256"] == found["html_sha256"]
    assert site.calls[-1] == (OLD + "05-1.htm", {"If-None-Match": found["etag"], "If-Modified-Since": found["last_modified"]})

    # Gone from its old address: the other address is asked without the old page's validators.
    site.pages = {NEW + "05-1.html": read("docketfiles_05-1.htm") + b"<!-- moved -->"}
    moved = cases.fetch_docket(site, "05-1", found)
    assert moved["found"] is True and moved["url"] == NEW + "05-1.html" and moved["tried"] == [OLD + "05-1.htm", NEW + "05-1.html"]
    assert site.calls[-1] == (NEW + "05-1.html", {})

    missing = cases.fetch_docket(Site(), "70-18")
    assert missing["found"] is False and missing["status"] == 404 and missing["tried"] == [OLD + "70-18.htm", NEW + "70-18.html"]

    redirected = Site()
    redirected.status[OLD + "70-18.htm"] = 302
    with pytest.raises(cases.UnexpectedStatus, match="HTTP 302"):
        cases.fetch_docket(redirected, "70-18")

    # A 304 the request did not ask for is not taken as unchanged.
    unasked = Site()
    unasked.status[NEW + "22O141.html"] = 304
    with pytest.raises(cases.UnexpectedStatus, match="HTTP 304"):
        cases.fetch_docket(unasked, "141, Orig.")


def test_docket_due_follows_the_recheck_rules():
    def page(hours, found=True, tried=None):
        return {"fetched_at": (NOW - timedelta(hours=hours)).isoformat().replace("+00:00", "Z"), "found": found, "tried": tried}
    both = [OLD + "70-18.htm", NEW + "70-18.html"]
    assert cases.docket_due(None, "25-1", 2025, 2026, NOW)
    assert not cases.docket_due(page(5), "25-1", 2025, 2026, NOW)
    assert cases.docket_due(page(6), "25-1", 2025, 2026, NOW)
    assert not cases.docket_due(page(24 * 6), "05-1", 2005, 2026, NOW)
    assert cases.docket_due(page(24 * 7), "05-1", 2005, 2026, NOW)
    assert not cases.docket_due(page(24 * 29, found=False, tried=both), "70-18", 1970, 2026, NOW)
    assert cases.docket_due(page(24 * 30, found=False, tried=both), "70-18", 1970, 2026, NOW)
    # A miss recorded without every address this builder asks is due at once.
    assert cases.docket_due(page(1, found=False, tried=both[:1]), "70-18", 1970, 2026, NOW)
    assert cases.docket_due(page(1, found=False, tried=None), "70-18", 1970, 2026, NOW)


def test_order_terms_puts_current_then_unfinished_then_the_rest_newest_first():
    # The current and previous terms come first even when complete, as their dockets change most.
    manifest = {"terms": {"OT2025": {"complete": True}, "OT2024": {"complete": True}, "OT2023": {"complete": True}, "OT2010": {"complete": True}, "OT2009": {"complete": False}}}
    assert cases.order_terms([2009, 2010, 2023, 2024, 2025, 2008], manifest, 2025) == [2025, 2024, 2009, 2008, 2023, 2010]


def test_source_signature_of_the_granted_lists_is_the_terms_own():
    manifests = {collection: {"partitions": {}} for collection in cases.SOURCE_COLLECTIONS}
    manifests["argument-transcripts"]["partitions"]["OT2010"] = {"file": "data/OT2010.parquet", "sha256": "a" * 64}
    granted = [{"id": "orders/10grantednotedlist.pdf", "term": 2010, "text": "10-1 A V. B"}, {"id": "orders/11grantednotedlist.pdf", "term": 2011, "text": "11-1 C V. D"}]
    before = cases.source_signature(manifests, granted, 2010)
    assert before["argument-transcripts"] == {"partition": "data/OT2010.parquet", "sha256": "a" * 64}
    assert before["granted-noted-cases-list"]["rows"] == 1
    granted[1]["text"] = "11-1 C V. D\n11-2 E V. F"
    assert cases.source_signature(manifests, granted, 2010) == before
    granted[0]["text"] = "10-1 A V. B\n10-2 G V. H"
    assert cases.source_signature(manifests, granted, 2010) != before


def test_granted_list_titles_stop_at_field_labels_and_rank_last():
    text = "137, Orig. MONTANA V. WYOMING AND NORTH DAKOTA\nOrder: 10/12/10 – Set for oral argument\nResult: Exception Overruled\n08-1314# CSX WILLIAMSON V. MAZDA MOTOR\nOF AMERICA, INC.\nCourt: CA-CA, 4th\n141, Orig. TEXAS V. NEW MEXICO\n"
    found = {}
    cases.add_granted_list(found, 2010, {"id": "orders/10grantednotedlist.pdf", "text": text})
    assert {docket: list(case["listed_titles"]) for (_, docket), case in found.items()} == {"137, Orig.": ["MONTANA V. WYOMING AND NORTH DAKOTA"], "08-1314": ["WILLIAMSON V. MAZDA MOTOR OF AMERICA, INC."], "141, Orig.": ["TEXAS V. NEW MEXICO"]}
    cases.add_source(found, 2010, "08-1314", {"id": "t.pdf", "title": "Williamson v. Mazda Motor of America, Inc."}, "argument-transcripts")
    rows = {row["docket"]: row for row in cases.assemble_rows(2010, found, {}, {})}
    assert rows["08-1314"]["case_name"] == "Williamson v. Mazda Motor of America, Inc." and rows["137, Orig."]["case_name"] == "MONTANA V. WYOMING AND NORTH DAKOTA"
    # A docket no run has asked about has neither a page nor an answer.
    assert rows["137, Orig."]["docket_found"] is None and rows["137, Orig."]["docket_url"] is None


def write_sources(base, collection, partitions):
    root = cases.source_root(base, collection)
    manifest = {"collection": collection, "partitions": {}}
    for key, rows in partitions.items():
        path = root / "data" / f"{key}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist([{name: row.get(name) for name in cases.SOURCE_COLUMNS} for row in rows], schema=SOURCE_SCHEMA), path)
        manifest["partitions"][key] = {"file": f"data/{key}.parquet", "sha256": sha256_file(path), "rows": len(rows)}
    root.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST).write_text(json.dumps(manifest))


def test_a_file_of_several_opinions_gives_each_case_its_own_pages(tmp_path):
    # A preliminary print of the United States Reports, linked from two listings at the page each opinion starts on. Its text is pdftotext's, a form feed after each page.
    printed = [
        "589\n1 of 2\nPRELIMINARY PRINT",
        "OCTOBER TERM, 2019 132\nSyllabus\nRODRIGUEZ v. FDIC",
        "OCTOBER TERM, 2019 136\nPer Curiam\nSTATE v. PERSON",
        "OCTOBER TERM, 2019 139\nSyllabus\nMcKINNEY v. ARIZONA",
        "Page Proof Pending Publication\n140 McKINNEY v. ARIZONA\nGinsburg, J., dissenting",
        "Reporter’s Note\nThe next page is purposely numbered 901.",
        "ORDERS FOR OCTOBER 7, 2019, THROUGH\nFEBRUARY 24, 2020",
        "1038 OCTOBER TERM, 2019\nOctober 21, 2019 589 U. S.\nNo. 19–7. Order.",
        "ORDERS 1039\n1038 Thomas, J., concurring\nI concur.",
        "Page Proof Pending Publication\n1040 OCTOBER TERM, 2019\nOctober 21, 22, 2019 589 U. S.\nNo. 19–70. Denied.",
        "ORDERS 1041\n589 U. S. November 4, 2019\nNo. 19–8. Order.",
        "1042 OCTOBER TERM, 2019\nSotomayor, J., dissenting 589 U. S.\nI dissent.",
        "I N D E X",
    ]
    text = "".join(page + "\f" for page in printed)
    pp = {"id": "opinions/preliminaryprint/589us1pp_web.pdf", "partition": "OT2019", "url": "https://www.supremecourt.gov/opinions/preliminaryprint/589US1PP_Web.pdf", "term": 2019, "text_source": "born_digital", "text": text}

    def entry(page, docket, name, date, citation):
        return {"href": f"/opinions/preliminaryprint/589US1PP_Web.pdf#page={page}", "link_text": name, "page": page, "row": {"Docket": docket, "Name": name, "Date": date, "Citation": citation}}

    # The row's own fields are those of the first entry only.
    court = pp | {"date": "2020-02-25", "docket": "18-1269", "title": "Rodriguez v. FDIC", "entries": json.dumps([entry(2, "18-1269", "Rodriguez v. FDIC", "2/25/20", "589 U.S. 132"), entry(4, "18-1109", "McKinney v. Arizona", "2/26/20", "589 U.S. 139")])}
    orders = pp | {"date": "2019-10-21", "docket": "19-7", "title": "A v. B", "entries": json.dumps([entry(8, "19-7", "A v. B", "10/21/19", "589 U.S. 1038"), entry(11, "19-8", "C v. D", "11/4/19", "589 U.S. 1041"), entry(11, "19-8", "C v. D", "11/4/19", "589 U.S. 1041"), entry(3, "19-9", "State v. Person", "10/7/19", "589 U.S. 136")])}
    # A slip opinion for consolidated cases, listed twice, holds one opinion however many dockets its entries name.
    slip = {"id": "opinions/19pdf/19-1_abcd.pdf", "partition": "OT2019", "url": "https://www.supremecourt.gov/opinions/19pdf/19-1_abcd.pdf", "term": 2019, "date": "2020-03-02", "docket": "19-1, 19-2", "title": "E v. F", "text_source": "born_digital", "text": "slip\fopinion\f", "entries": json.dumps([{"href": "/opinions/19pdf/19-1_abcd.pdf", "page": None, "row": {"Docket": "19-1, 19-2", "Name": "E v. F", "Date": "3/2/20", "Citation": "590 U.S. 1"}}] * 2)}
    write_sources(tmp_path, "opinions-of-the-court", {"OT2019": [court, slip]})
    write_sources(tmp_path, "opinions-relating-to-orders", {"OT2019": [orders]})
    for collection in ("in-chambers-opinions", "argument-transcripts", "granted-noted-cases-list"):
        write_sources(tmp_path, collection, {})
    args = SimpleNamespace(sources_local=str(tmp_path))
    built = cases.build_cases_from_sources(args, cases.source_manifests(args), [], 2019)
    opinions = {docket: case["opinions"] for (_, docket), case in built.items()}
    assert sorted(opinions) == ["18-1109", "18-1269", "19-1", "19-2", "19-7", "19-8", "19-9"]

    def pages(first, last):
        return "".join(page + "\f" for page in printed[first - 1:last])

    # Each opinion runs to the page before the next one either listing links, and stops early at a page of another part or of orders, watermarked or not.
    expected = {"18-1269": ([2, 2], "Rodriguez v. FDIC", "2020-02-25", "589 U.S. 132"), "19-9": ([3, 3], "State v. Person", "2019-10-07", "589 U.S. 136"), "18-1109": ([4, 5], "McKinney v. Arizona", "2020-02-26", "589 U.S. 139"), "19-7": ([8, 9], "A v. B", "2019-10-21", "589 U.S. 1038"), "19-8": ([11, 12], "C v. D", "2019-11-04", "589 U.S. 1041")}
    for docket, (span, title, date, citation) in expected.items():
        [doc] = opinions[docket]
        assert (doc["pages"], doc["title"], doc["date"], doc["citation"]) == (span, title, date, citation), docket
        assert doc["text"] == pages(*span) and doc["id"] == pp["id"] and doc["url"] == pp["url"]
    assert opinions["19-9"][0]["collection"] == "opinions-relating-to-orders" and opinions["18-1109"][0]["collection"] == "opinions-of-the-court"
    assert built[(2019, "18-1109")]["source_rows"] == [{"collection": "opinions-of-the-court", "id": pp["id"], "date": "2020-02-26", "title": "McKinney v. Arizona"}]
    for docket in ("19-1", "19-2"):
        [doc] = opinions[docket]
        assert (doc["text"], doc["pages"], doc["citation"], doc["title"]) == ("slip\fopinion\f", None, "590 U.S. 1", "E v. F")


def test_a_page_of_orders_or_of_another_part_ends_an_opinion():
    assert cases.ends_opinion("ORDERS 1025\n591 U. S. July 2, 2020\ntext")
    assert cases.ends_opinion("1026 OCTOBER TERM, 2019\nJuly 2, 6, 2020 591 U. S.\ntext")
    assert cases.ends_opinion("1056\nNovember 12, 2019\nCertiorari Granted")
    assert cases.ends_opinion("Page Proof Pending Publication\nORDERS 913\n586 U. S. January 7, 2019")
    assert cases.ends_opinion("SUPREME COURT OF THE UNITED STATES\nApril 25, 2019\nOrdered:")
    assert cases.ends_opinion("iv INDEX\nAbortion") and cases.ends_opinion("INDEX v\nZoning")
    for page in ("Cite as: 591 U. S. 979 (2020) 981\nBreyer, J., dissenting", "982 BARR v. LEE\nBreyer, J., dissenting", "1030 OCTOBER TERM, 2019\nSotomayor, J., dissenting 591 U. S.", "ORDERS 1043\n1039 Kavanaugh, J., concurring", "Page Proof Pending Publication\n140 McKINNEY v. ARIZONA\nOpinion of the Court", "OCTOBER TERM, 2019 979\nPer Curiam"):
        assert not cases.ends_opinion(page), page


# Whole runs: OT2025 is the current term, OT2010 an older one.
def source_layout(base):
    def write(collection, partitions):
        write_sources(base, collection, partitions)

    def transcript(term, docket, name, title):
        return {"id": f"oral_arguments/argument_transcripts/{term}/{name}", "partition": f"OT{term}", "url": f"https://www.supremecourt.gov/oral_arguments/argument_transcripts/{term}/{name}", "term": term, "date": f"{term}-11-01", "docket": docket, "title": title, "text_source": "born_digital", "text": f"transcript of {title}"}

    write("argument-transcripts", {"OT2025": [transcript(2025, "25-1", "25-1_a1b2.pdf", "Skinner v. Louisiana"), transcript(2025, "141-Orig", "141-orig_c3d4.pdf", "Texas v. New Mexico")], "OT2010": [transcript(2010, "10-1", "10-1.pdf", "Alpha v. Beta"), transcript(2010, "10-2", "10-2.pdf", "Gamma v. Delta")]})
    for collection in ("opinions-of-the-court", "opinions-relating-to-orders", "in-chambers-opinions", "granted-noted-cases-list"):
        write(collection, {})


def pages():
    old = read("docketfiles_05-1.htm")
    return {NEW + "25-1.html": read("dk_25-1.html"), NEW + "22O141.html": read("dk_22O141.html"), OLD + "10-1.htm": old + b"<!-- 10-1 -->", OLD + "10-2.htm": old + b"<!-- 10-2 -->"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    source_layout(tmp_path / "sources")
    site = Site(pages())
    monkeypatch.setattr(cases, "Fetcher", lambda: site)
    args = SimpleNamespace(local=str(tmp_path / "out"), sources_local=str(tmp_path / "sources"), terms=None, budget_minutes=0, workdir=str(tmp_path))
    root = tmp_path / "out" / "scotus-cases"

    def run(capsys):
        status = cases.cmd_run(args)
        return status, json.loads(capsys.readouterr().out)

    def rows(term):
        return {row["docket"]: row for row in pq.read_table(root / "data" / f"OT{term}.parquet").to_pylist()}

    def manifest():
        return json.loads((root / MANIFEST).read_text())

    return SimpleNamespace(site=site, args=args, root=root, run=run, rows=rows, manifest=manifest)


def test_run_builds_terms_then_leaves_them_until_due(world, capsys):
    status, report = world.run(capsys)
    assert status == 0 and [t["term"] for t in report["terms"]] == ["OT2025", "OT2010"]
    current = world.rows(2025)
    assert sorted(current) == ["141, Orig.", "25-1"]
    assert current["141, Orig."]["docket_url"] == NEW + "22O141.html" and current["141, Orig."]["case_name"] == "Texas, Plaintiff v. New Mexico and Colorado"
    assert current["25-1"]["docket_found"] is True and len(json.loads(current["25-1"]["proceedings"])) == 35
    assert json.loads(current["25-1"]["transcripts"])[0]["text"] == "transcript of Skinner v. Louisiana"
    entry = world.manifest()["terms"]["OT2010"]
    assert entry["complete"] is True and entry["docket_found"] == 2 and entry["dockets"]["10-1"]["tried"] == [OLD + "10-1.htm"]
    assert cases.cmd_verify(world.args) == 0
    capsys.readouterr()

    asked = len(world.site.calls)
    status, report = world.run(capsys)
    assert status == 0 and report["terms"] == [] and len(world.site.calls) == asked


def test_budget_stop_keeps_what_it_has_and_the_next_run_finishes(world, capsys, monkeypatch):
    monkeypatch.setattr(cases, "out_of_time", lambda deadline: len(world.site.calls) >= 1)
    monkeypatch.setenv("GITHUB_OUTPUT", str(world.root.parent / "budget_output"))
    status, report = world.run(capsys)
    assert status == 0 and report["stopped"] == "budget" and [t["term"] for t in report["terms"]] == ["OT2025"]
    assert (world.root.parent / "budget_output").read_text() == "more=true\nfetched=1\n"
    rows = world.rows(2025)
    assert rows["141, Orig."]["docket_found"] is True and rows["25-1"]["docket_fetched_at"] is None and rows["25-1"]["docket_found"] is None
    assert world.manifest()["terms"]["OT2025"]["complete"] is False
    assert cases.cmd_verify(world.args) == 0
    capsys.readouterr()

    outputs = world.root.parent / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    monkeypatch.setattr(cases, "out_of_time", lambda deadline: False)
    status, report = world.run(capsys)
    assert status == 0 and [t["term"] for t in report["terms"]] == ["OT2025", "OT2010"]
    assert outputs.read_text() == f"more=false\nfetched={sum(report['requests'].values())}\n"
    assert world.site.asked(NEW + "22O141.html") == 1 and world.site.asked(NEW + "25-1.html") == 1
    assert world.manifest()["terms"]["OT2025"]["complete"] is True and world.rows(2025)["25-1"]["docket_found"] is True


def test_rechecks_revalidate_and_a_refusal_keeps_the_stored_pages(world, capsys, monkeypatch):
    world.run(capsys)
    before = world.rows(2025)
    monkeypatch.setattr(cases, "CURRENT_RECHECK", timedelta(0))

    # A redirect is neither found nor missing: the stored page stays and the docket is listed as a failure; the other page answers 304.
    world.site.status[NEW + "25-1.html"] = 302
    status, report = world.run(capsys)
    assert status == 0 and [t["term"] for t in report["terms"]] == ["OT2025"]
    entry = world.manifest()["terms"]["OT2025"]
    assert entry["failures"] == {"25-1": f"HTTP 302 for {NEW}25-1.html"}
    assert entry["dockets"]["141, Orig."]["status"] == 304
    after = world.rows(2025)
    assert after["25-1"]["docket_html"] == before["25-1"]["docket_html"] and after["141, Orig."]["docket_html"] == before["141, Orig."]["docket_html"]
    assert after["141, Orig."]["docket_fetched_at"] >= before["141, Orig."]["docket_fetched_at"]

    # Refused outright: the run stops with status 1, and the term keeps every page it had.
    world.site.blocked_after = len(world.site.calls)
    status, report = world.run(capsys)
    assert status == 1 and report["stopped"].startswith("Blocked")
    refused = world.rows(2025)
    assert {docket: row["docket_html"] for docket, row in refused.items()} == {docket: row["docket_html"] for docket, row in before.items()}
    assert all(row["docket_found"] for row in refused.values())
    assert cases.cmd_verify(world.args) == 0


def test_only_a_budget_stop_asks_for_another_run(world, capsys, monkeypatch):
    # A docket that keeps failing, or a refusal, waits for the schedule; asking for another run at once would chain runs that make no progress.
    outputs = world.root.parent / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    world.site.status[NEW + "25-1.html"] = 302
    status, report = world.run(capsys)
    assert status == 0 and "stopped" not in report and not world.manifest()["terms"]["OT2025"]["complete"]
    world.site.blocked_after = len(world.site.calls)
    status, report = world.run(capsys)
    assert status == 1 and report["stopped"].startswith("Blocked")
    assert [line for line in outputs.read_text().splitlines() if line.startswith("more=")] == ["more=false", "more=false"]


def test_stored_pages_are_parsed_again_without_asking_the_court(world, capsys):
    world.run(capsys)
    path = world.root / "data" / "OT2010.parquet"
    table = pq.read_table(path).to_pylist()
    for row in table:
        row["proceedings"], row["attorneys"] = "[]", None
    pq.write_table(pa.Table.from_pylist(table, schema=cases.SCHEMA), path)
    manifest = world.manifest()
    manifest["terms"]["OT2010"].update(builder=cases.BUILDER - 1, sha256=sha256_file(path))
    (world.root / MANIFEST).write_text(json.dumps(manifest))
    asked = {url: world.site.asked(url) for url in (OLD + "10-1.htm", OLD + "10-2.htm")}

    status, report = world.run(capsys)
    assert status == 0 and report["terms"][0]["reason"] == "builder changed"
    assert {url: world.site.asked(url) for url in asked} == asked
    rebuilt = world.rows(2010)
    assert all(len(json.loads(row["proceedings"])) == 8 and row["attorneys"] for row in rebuilt.values())


def test_a_listed_term_whose_file_cannot_be_read_stops_the_run(world, capsys):
    world.run(capsys)
    (world.root / "data" / "OT2010.parquet").unlink()
    manifest = world.manifest()
    manifest["terms"]["OT2010"]["builder"] = cases.BUILDER - 1
    (world.root / MANIFEST).write_text(json.dumps(manifest))
    with pytest.raises(FileNotFoundError):
        cases.cmd_run(world.args)


def test_verify_names_what_does_not_match(world, capsys):
    world.run(capsys)
    good = world.manifest()

    def verify(change):
        manifest = json.loads(json.dumps(good))
        change(manifest)
        (world.root / MANIFEST).write_text(json.dumps(manifest))
        (world.root / "README.md").write_text(cases.render_card(manifest))
        status = cases.cmd_verify(world.args)
        return status, json.loads(capsys.readouterr().out)["problems"]

    assert verify(lambda m: None) == (0, [])
    status, problems = verify(lambda m: m["terms"]["OT2010"]["dockets"].pop("10-2"))
    assert status == 1 and problems == ["OT2010: rows with a docket page the manifest does not list: ['10-2']"]
    status, problems = verify(lambda m: m["terms"]["OT2010"]["dockets"].update({"10-9": {}}))
    assert status == 1 and problems == ["OT2010: the manifest lists pages for dockets with no row: ['10-9']"]
    status, problems = verify(lambda m: m["terms"]["OT2010"].update(rows=3, sha256="0" * 64))
    assert status == 1 and problems == ["OT2010: data/OT2010.parquet has SHA-256 " + sha256_file(world.root / "data" / "OT2010.parquet")[:12] + ", manifest says 000000000000", "OT2010: 2 rows, manifest says 3"]

    (world.root / MANIFEST).write_text(json.dumps(good))
    (world.root / "README.md").write_text(cases.render_card(good) + "edited\n")
    assert cases.cmd_verify(world.args) == 1
    assert json.loads(capsys.readouterr().out)["problems"] == ["README.md is not the card the manifest renders"]


def test_verify_catches_a_complete_term_with_a_page_never_fetched(world, capsys, monkeypatch):
    monkeypatch.setattr(cases, "out_of_time", lambda deadline: len(world.site.calls) >= 1)
    world.run(capsys)
    manifest = world.manifest()
    manifest["terms"]["OT2025"]["complete"] = True
    (world.root / MANIFEST).write_text(json.dumps(manifest))
    (world.root / "README.md").write_text(cases.render_card(manifest))
    assert cases.cmd_verify(world.args) == 1
    assert json.loads(capsys.readouterr().out)["problems"] == ["OT2025: 25-1 has no docket page fetched, but the term is marked complete"]
