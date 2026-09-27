"""The listing parsers on saved real pages (September 25, 2026): counts, verbatim fields and typed values."""

from datetime import date

import pytest

from conftest import BASE, Response, page_bytes
from scotus_products.markup import main_content, parse, render, squash
from scotus_products.sources import COLLECTIONS, Listing, ListingError, current_term, group_units, iso_date, reread_url

TODAY = date(2026, 9, 25)

# (collection, fixture, page path, term, entries, files, partitions), counted on the saved pages.
PAGES = [
    ("opinions-of-the-court", "opinions_slipopinion_18", "opinions/slipopinion/18", 2018, 73, 5, 1),
    ("opinions-relating-to-orders", "opinions_relatingtoorders_25", "opinions/relatingtoorders/25", 2025, 49, 42, 1),
    ("in-chambers-opinions", "opinions_in_chambers_aspx", "opinions/in-chambers.aspx", None, 1, 1, 1),
    ("us-reports", "opinions_USReports_aspx", "opinions/USReports.aspx", None, 621, 621, 62),
    ("online-sources-cited-in-opinions", "opinions_cited_urls_24", "opinions/cited_urls/24", 2024, 67, 67, 1),
    ("media-files-cited-in-opinions", "media_media_aspx", "media/media.aspx", None, 10, 10, 7),
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
    ("press-releases", "publicinfo_press_pressreleases_aspx", "publicinfo/press/pressreleases.aspx", None, 210, 210, 25),
    ("media-advisories", "publicinfo_media_mediaadvisories_aspx", "publicinfo/media/mediaadvisories.aspx", None, 117, 117, 22),
    ("speeches", "publicinfo_speeches_speeches_aspx", "publicinfo/speeches/speeches.aspx", None, 48, 48, 12),
    ("chief-justice-year-end-reports", "publicinfo_year_end_year_endreports_aspx", "publicinfo/year-end/year-endreports.aspx", None, 26, 26, 26),
    ("reporters-guide-to-applications", "publicinfo_publicinfo_aspx", "publicinfo/publicinfo.aspx", None, 1, 1, 1),
    ("services-for-news-media", "publicinfo_publicinfo_aspx", "publicinfo/publicinfo.aspx", None, 1, 1, 1),
    ("rules-and-guidance", "filingandrules_rules_guidance_aspx", "filingandrules/rules_guidance.aspx", None, 15, 15, 2),
    ("rules-and-guidance", "ctrules_scannedrules_aspx", "ctrules/scannedrules.aspx", None, 55, 55, 52),
    ("electronic-filing-documents", "filingandrules_electronicfiling_aspx", "filingandrules/electronicfiling.aspx", None, 6, 6, 1),
    ("supreme-court-bar-documents", "filingandrules_supremecourtbar_aspx", "filingandrules/supremecourtbar.aspx", None, 3, 3, 1),
    ("about-the-court", "about_faq_aspx", "about/faq.aspx", None, 4, 4, 1),
    ("about-the-court", "about_code_of_conduct_for_justices_aspx", "about/code-of-conduct-for-justices.aspx", None, 2, 2, 2),
    ("argument-audio", "oral_arguments_argument_audio_2025", "oral_arguments/argument_audio/2025", 2025, 58, 58, 7),
    ("argument-audio", "oral_arguments_argument_audio_2017", "oral_arguments/argument_audio/2017", 2017, 63, 63, 7),
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
        if key in ("listing", "href", "link_title", "document_type", "docket"):
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


def test_argument_audio_preserves_mp3_url_case():
    _, found = parsed("argument-audio", "oral_arguments_argument_audio_2025", "oral_arguments/argument_audio/2025", 2025)
    units = group_units(found)
    unit = units["media/audio/mp3files/25a312.mp3"]
    assert unit.url == "https://www.supremecourt.gov/media/audio/mp3files/25A312.mp3"
    assert unit.partition == "2026-01"
    assert COLLECTIONS["argument-audio"].typed(unit, TODAY)["docket"] == "25A312"


def test_year_end_reports_keep_pdf_and_html_document_types():
    _, found = parsed("chief-justice-year-end-reports", "publicinfo_year_end_year_endreports_aspx", "publicinfo/year-end/year-endreports.aspx", None)
    units = group_units(found)
    assert units["publicinfo/year-end/2025year-endreport.pdf"].entries[0]["document_type"] == "pdf"
    assert units["publicinfo/year-end/2000year-endreport.aspx"].entries[0]["document_type"] == "html"


def test_opinions_extras_keep_external_urls_as_listing_text_and_media_types():
    _, found = parsed("online-sources-cited-in-opinions", "opinions_cited_urls_24", "opinions/cited_urls/24", 2024)
    source = group_units(found)["opinions/urls_cited/ot2024/24-316/24-316-1.pdf"]
    assert source.entries[0]["link_text"].startswith("https://www.uspreventiveservicestaskforce.org/")
    assert source.entries[0]["document_type"] == "pdf"
    assert COLLECTIONS["online-sources-cited-in-opinions"].typed(source, TODAY) == {"term": 2024, "date": None, "docket": "24-316", "title": source.entries[0]["link_text"]}
    _, found = parsed("media-files-cited-in-opinions", "media_media_aspx", "media/media.aspx", None)
    media = group_units(found)
    assert media["media/video/mp4files/19-5807_thedrick_edwards_video_taped_statement.mp4"].entries[0]["document_type"] == "video"
    assert media["media/audio/mp3files/19-3204_parts.mp3"].entries[0]["document_type"] == "audio"
    assert media["media/video/mp4files/19-5807_thedrick_edwards_video_taped_statement.mp4"].partition == "OT2020"


def test_publicinfo_services_pdf_is_found_by_scanning_the_page():
    _, found = parsed("services-for-news-media", "publicinfo_publicinfo_aspx", "publicinfo/publicinfo.aspx", None)
    unit = group_units(found)["publicinfo/pioservices.pdf"]
    assert unit.entries[0]["href"] == "/publicinfo/PIOServices.pdf"
    assert unit.entries[0]["document_type"] == "pdf"
    assert COLLECTIONS["services-for-news-media"].typed(unit, TODAY)["title"] == "Services for News Media"


def test_about_collection_includes_about_documents_and_excludes_visit_and_filing_faqs():
    _, found = parsed("about-the-court", "about_faq_aspx", "about/faq.aspx", None)
    units = group_units(found)
    assert set(units) == {"about/faq.aspx", "about/faq_justices.aspx", "about/faq_general.aspx", "about/faq_documents.aspx"}
    _, found = parsed("about-the-court", "about_buildingregulations_aspx", "about/buildingregulations.aspx", None)
    units = group_units(found)
    assert units["about/buildingregulations.aspx"].entries[0]["document_type"] == "html"
    assert units["about/buildingregulations.pdf"].entries[0]["document_type"] == "pdf"


def test_files_the_court_replaces_under_the_same_address_are_asked_about_on_every_run():
    replaced_in_place = {"calendars-and-lists", "granted-noted-cases-list", "journal", "argument-audio", "reporters-guide-to-applications", "services-for-news-media", "rules-and-guidance", "electronic-filing-documents", "supreme-court-bar-documents", "about-the-court"}
    assert {name for name, c in COLLECTIONS.items() if c.mutable} == replaced_in_place


@pytest.mark.parametrize("name", ["rules-and-guidance", "chief-justice-year-end-reports"])
def test_a_document_listing_does_not_take_a_linked_video_or_recording_for_a_document(name):
    page = b'<html><body><div id="pagemaindiv"><p><a href="/filingandrules/2023RulesoftheCourt.pdf">Rules of the Supreme Court (2023)</a></p><p><a href="/media/video/mp4files/tutorial2023.mp4">Tutorial (2023)</a> <a href="/media/audio/mp3files/remarks2023.mp3">Remarks (2023)</a></p></div></body></html>'
    found = COLLECTIONS[name].parse(main_content(parse(page)), BASE + "filingandrules/rules_guidance.aspx", None)
    assert [uid for uid, *_ in found] == ["filingandrules/2023rulesofthecourt.pdf"]


def test_electronic_filing_excludes_external_services_and_tutorial_apps():
    _, found = parsed("electronic-filing-documents", "filingandrules_electronicfiling_aspx", "filingandrules/electronicfiling.aspx", None)
    units = group_units(found)
    assert "" not in units
    assert not any("/elearning/" in uid for uid in units)
    assert len(units) == 6


def test_news_and_filing_documents_have_no_term_and_take_the_dates_the_listing_writes_out():
    _, found = parsed("speeches", "publicinfo_speeches_speeches_aspx", "publicinfo/speeches/speeches.aspx", None)
    speech = group_units(found)["publicinfo/speeches/remarks for the harry s. truman good neighbor award_as delivered.pdf"]
    assert speech.partition == "2025"
    assert COLLECTIONS["speeches"].typed(speech, TODAY) == {"term": None, "date": "2025-05-08", "docket": None, "title": "Remarks for the Harry S. Truman Good Neighbor Award, Kansas City, MO, May 8, 2025"}
    _, found = parsed("press-releases", "publicinfo_press_pressreleases_aspx", "publicinfo/press/pressreleases.aspx", None)
    press = group_units(found)
    assert COLLECTIONS["press-releases"].typed(press["publicinfo/press/pressreleases/pr_07-01-26"], TODAY)["date"] == "2026-07-01"
    assert all(COLLECTIONS["press-releases"].typed(u, TODAY)["date"] for u in press.values())
    for name, fixture, path in [("chief-justice-year-end-reports", "publicinfo_year_end_year_endreports_aspx", "publicinfo/year-end/year-endreports.aspx"), ("rules-and-guidance", "filingandrules_rules_guidance_aspx", "filingandrules/rules_guidance.aspx")]:
        _, found = parsed(name, fixture, path, None)
        assert {COLLECTIONS[name].typed(u, TODAY)["term"] for u in group_units(found).values()} == {None}


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


def test_listing_rereads_a_term_page_that_shows_another_term():
    # On September 27, 2026 the CDN answered argument_audio/2017 with its copy of the October Term 2025 page, and argument_audio/2017?reread=1 with the October Term 2017 page, which the fixture is.
    url = BASE + "oral_arguments/argument_audio/2017"
    answers = {url: Response(200, page_bytes("oral_arguments_argument_audio_2025")), reread_url(url): Response(200, page_bytes("oral_arguments_argument_audio_2017"))}
    listing = Listing(COLLECTIONS["argument-audio"], Pages(answers), today=TODAY)
    head, units = listing.list_all()
    assert listing.pages[url] == {"term": 2017, "entries": 63, "reread": [2025]}
    assert head["entries"] == 63 and {entry["term"] for unit in units.values() for entry in unit.entries} == {2017}
    assert {entry["listing"] for unit in units.values() for entry in unit.entries} == {url}


@pytest.mark.parametrize("reread", [None, Response(404, b""), Response(200, page_bytes("oral_arguments_argument_audio_2025"))], ids=["redirected", "failed", "another-term"])
def test_listing_takes_nothing_from_a_term_page_that_shows_another_term(reread):
    fixture = page_bytes("oral_arguments_argument_audio_2025")
    url = BASE + "oral_arguments/argument_audio/2017"
    answers = {url: Response(200, fixture), BASE + "oral_arguments/argument_audio/2025": Response(200, fixture)} | ({reread_url(url): reread} if reread else {})
    listing = Listing(COLLECTIONS["argument-audio"], Pages(answers), today=TODAY)
    head, units = listing.list_all()
    assert listing.pages[url] == {"term": 2017, "entries": 0, "shown_term": [2025]}
    assert listing.pages[BASE + "oral_arguments/argument_audio/2025"] == {"term": 2025, "entries": 58}
    assert head["entries"] == 58 and {entry["term"] for unit in units.values() for entry in unit.entries} == {2025}


def test_reread_url_adds_a_query_string():
    assert reread_url(BASE + "oral_arguments/argument_audio/2017") == BASE + "oral_arguments/argument_audio/2017?reread=1"
    assert reread_url(BASE + "x.aspx?term=2017") == BASE + "x.aspx?term=2017&reread=1"


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
