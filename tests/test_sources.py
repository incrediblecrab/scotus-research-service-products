"""The listing parsers on saved real pages (September 25, 2026): counts, verbatim fields and typed values."""

from datetime import date

import pytest

from conftest import BASE, Response, page_bytes
from scotus_products.markup import main_content, parse, render, squash
from scotus_products.sources import COLLECTIONS, Listing, ListingError, current_term, group_units, iso_date

TODAY = date(2026, 9, 25)

# (collection, fixture, page path, term, entries, files, partitions), counted on the saved pages.
PAGES = [
    ("opinions-of-the-court", "opinions_slipopinion_18", "opinions/slipopinion/18", 2018, 73, 5, 1),
    ("opinions-relating-to-orders", "opinions_relatingtoorders_25", "opinions/relatingtoorders/25", 2025, 49, 42, 1),
    ("in-chambers-opinions", "opinions_in_chambers_aspx", "opinions/in-chambers.aspx", None, 1, 1, 1),
    ("us-reports", "opinions_USReports_aspx", "opinions/USReports.aspx", None, 621, 621, 62),
    ("argument-transcripts", "oral_arguments_argument_transcript_2009", "oral_arguments/argument_transcript/2009", 2009, 77, 77, 1),
    ("argument-transcripts", "oral_arguments_archived_transcripts_1968", "oral_arguments/archived_transcripts/1968", 1968, 151, 151, 1),
    ("calendars-and-lists", "oral_arguments_calendarsandlists_aspx", "oral_arguments/calendarsandlists.aspx", None, 169, 169, 4),
    ("calendars-and-lists", "oral_arguments_earlierdaycalls_aspx", "oral_arguments/earlierdaycalls.aspx", None, 340, 340, 9),
    ("orders-of-the-court", "orders_ordersofthecourt_25", "orders/ordersofthecourt/25", 2025, 117, 117, 1),
    ("orders-by-circuit", "orders_ordersbycircuit_09", "orders/ordersbycircuit/09", 2009, 30, 30, 1),
    ("granted-noted-cases-list", "orders_grantednotedlists_aspx", "orders/grantednotedlists.aspx", None, 20, 20, 1),
    ("journal", "orders_journal_aspx", "orders/journal.aspx", None, 33, 33, 4),
    ("journal", "orders_scannedjournals_aspx", "orders/scannedjournals.aspx", None, 104, 104, 12),
    ("original-jurisdiction-records-and-briefs", "casedocuments_original_jurisdiction_cases_aspx", "casedocuments/original_jurisdiction_cases.aspx", None, 3081, 2660, 146),
]
IDS = [f"{name}:{fixture}" for name, fixture, *_ in PAGES]


def parsed(name, fixture, path, term):
    main = main_content(parse(page_bytes(fixture)))
    return main, COLLECTIONS[name].parse(main, BASE + path, term)


@pytest.mark.parametrize("name,fixture,path,term,entries,files,partitions", PAGES, ids=IDS)
def test_counts(name, fixture, path, term, entries, files, partitions):
    _, found = parsed(name, fixture, path, term)
    units = group_units(found)
    assert (len(found), len(units), len({u.partition for u in units.values()})) == (entries, files, partitions)


def strings(entry):
    """Every string in an entry that the page renders: link text, row cells and headers, list item, line, headings."""
    for key, value in entry.items():
        if key in ("listing", "href", "link_title"):
            continue
        if isinstance(value, str):
            yield key, value
        elif isinstance(value, dict):
            for header, cell in value.items():
                yield f"{key} header", header
                yield f"{key}[{header}]", cell


@pytest.mark.parametrize("name,fixture,path,term,entries,files,partitions", PAGES, ids=IDS)
def test_every_field_is_text_the_page_renders(name, fixture, path, term, entries, files, partitions):
    main, found = parsed(name, fixture, path, term)
    page = squash(render(main))
    titles = " | ".join(squash(a.get("title")) for a in main.iter("a") if a.get("title"))
    for uid, _, entry, _ in found:
        for key, value in strings(entry):
            assert value and value in page, (uid, key, value)
        if "link_title" in entry:
            assert entry["link_title"] in titles, (uid, entry["link_title"])


def test_slip_opinions_that_link_bound_volumes_keep_their_page_anchors():
    _, found = parsed("opinions-of-the-court", "opinions_slipopinion_18", "opinions/slipopinion/18", 2018)
    units = group_units(found)
    assert sorted(units) == ["opinions/boundvolumes/586bv.pdf", "opinions/boundvolumes/587bv.pdf", "opinions/preliminaryprint/588us1pp_final.pdf",
                             "opinions/preliminaryprint/588us1pp_web.pdf", "opinions/preliminaryprint/588us2pp_final.pdf"]
    halleck = next(e for e in units["opinions/boundvolumes/587bv.pdf"].entries if e["link_text"] == "Manhattan Community Access Corp. v. Halleck")
    assert halleck["page"] == 824
    assert halleck["row"] == {"R-": "55", "Date": "6/17/19", "Docket": "17-1702", "Name": "Manhattan Community Access Corp. v. Halleck", "J.": "BK", "Citation": "587 U.S. 802"}
    assert COLLECTIONS["opinions-of-the-court"].typed(units["opinions/boundvolumes/587bv.pdf"], TODAY)["term"] == 2018


def test_us_reports_volume_entry_is_verbatim():
    _, found = parsed("us-reports", "opinions_USReports_aspx", "opinions/USReports.aspx", None)
    units = group_units(found)
    volume = units["pdfs/usreports/usreports-179_pdfa.pdf"]
    # The page source has "Volume 179&nbsp;&nbsp;(1900 Term - 65764K)": both no-break spaces stay.
    assert volume.entries[0]["link_text"] == "Volume 179\u00a0\u00a0(1900 Term - 65764K)"
    assert volume.partition == "volumes-170-179"
    assert COLLECTIONS["us-reports"].typed(volume, TODAY) == {"term": 1900, "date": None, "docket": None, "title": "Volume 179\u00a0\u00a0(1900 Term - 65764K)"}
    assert units["opinions/datesofdecisions.pdf"].partition == "other"


def test_archived_transcript_row():
    _, found = parsed("argument-transcripts", "oral_arguments_archived_transcripts_1968", "oral_arguments/archived_transcripts/1968", 1968)
    unit = group_units(found)["pdfs/transcripts/1968/68-32_11-13-1968.pdf"]
    assert unit.entries[0]["row"] == {"Oral Argument": "32 Johnson v. Bennett", "Date Argued": "11/13/1968"}
    assert unit.entries[0]["group"] == "Argument Month: November 1968"
    assert COLLECTIONS["argument-transcripts"].typed(unit, TODAY) == {"term": 1968, "date": "1968-11-13", "docket": "32", "title": "Johnson v. Bennett"}


def test_original_jurisdiction_document_listed_under_several_cases_is_one_file():
    _, found = parsed("original-jurisdiction-records-and-briefs", "casedocuments_original_jurisdiction_cases_aspx", "casedocuments/original_jurisdiction_cases.aspx", None)
    units = group_units(found)
    shared = [u for u in units.values() if len({e["group"] for e in u.entries}) > 1]
    assert shared
    for u in shared:
        assert u.partition == min(f"orig-{int(e['group'].split()[1].rstrip(',')):03d}" for e in u.entries)


def test_row_whose_cells_do_not_match_the_headers_is_refused():
    html = b"<html><body><div id='pagemaindiv'><table><tr><th>Date</th><th>Docket</th><th>Name</th></tr><tr><td>6/17/19</td><td><a href='/opinions/18pdf/x.pdf'>X</a></td></tr></table></div></body></html>"
    with pytest.raises(ListingError):
        COLLECTIONS["opinions-of-the-court"].parse(main_content(parse(html)), BASE + "opinions/slipopinion/18", 2018)


def test_document_link_outside_the_opinions_table_is_refused():
    html = b"<html><body><div id='pagemaindiv'><p><a href='/opinions/18pdf/stray.pdf'>Stray</a></p></div></body></html>"
    with pytest.raises(ListingError):
        COLLECTIONS["opinions-of-the-court"].parse(main_content(parse(html)), BASE + "opinions/slipopinion/18", 2018)


class Pages:
    def __init__(self, answers):
        self.answers = answers

    def get(self, url):
        return self.answers.get(url) or Response(302, b"", {"Location": "/opinions/USReports.aspx"})


def test_listing_counts_a_redirected_term_page_as_empty():
    fixture = page_bytes("orders_ordersofthecourt_25")
    answers = {BASE + "orders/ordersofthecourt/25": Response(200, fixture)}
    listing = Listing(COLLECTIONS["orders-of-the-court"], Pages(answers), today=TODAY)
    head, units = listing.list_all()
    assert head["count"] == 117 and len(units) == 117
    assert listing.pages[BASE + "orders/ordersofthecourt/26"] == {"term": 2026, "entries": 0, "redirected": True}


def test_listing_refuses_a_single_page_that_redirects_or_fails():
    with pytest.raises(ListingError):
        Listing(COLLECTIONS["in-chambers-opinions"], Pages({}), today=TODAY).list_all()
    failing = Pages({BASE + "opinions/in-chambers.aspx": Response(500, b"")})
    with pytest.raises(ListingError):
        Listing(COLLECTIONS["in-chambers-opinions"], failing, today=TODAY).list_all()


def test_current_term_turns_in_october():
    assert current_term(date(2026, 9, 30)) == 2025
    assert current_term(date(2026, 10, 1)) == 2026


def test_iso_date():
    assert iso_date("6/17/19", TODAY) == "2019-06-17"
    assert iso_date("7/14/1922", TODAY) == "1922-07-14"
    assert iso_date("10-03-22", TODAY) == "2022-10-03"
    assert iso_date("12/31/68", TODAY) == "1968-12-31"
    assert iso_date("N/A", TODAY) is None
    assert iso_date("2/30/20", TODAY) is None
