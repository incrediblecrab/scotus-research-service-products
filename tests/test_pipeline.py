"""The sync loop against a stand-in site: what is stored, kept, marked, fetched again and refused."""

import time

import pytest

from conftest import BASE, FakeListing, FakeSite, collection, pdf_bytes, sha256, unit
from scotus_products.extract import extract_pdf
from scotus_products.pipeline import MAX_ATTEMPTS, Context, codepoints, sync
from scotus_products.sources import ListingError
from scotus_products.store import LocalStore


def run(root, listing, site, **options):
    store = LocalStore(root)
    ctx = Context(store=store, collection=options.pop("collection", collection()), fetcher=site, deadline=options.pop("deadline", time.monotonic() + 600), workers=2, **options)
    try:
        return sync(ctx, listing), store
    finally:
        store.close()


def rows(root, key="OT2023"):
    store = LocalStore(root)
    try:
        return {row["id"]: row for row in store.read_partition(key)}
    finally:
        store.close()


@pytest.fixture
def two_files(born_digital, scanned):
    opinion, filing = unit("opinions/23pdf/23a843_he4l.pdf"), unit("pdfs/recordsandbriefs/oj-page3.pdf")
    site = FakeSite({opinion.url: born_digital, filing.url: scanned})
    return [opinion, filing], site


def test_first_run_stores_every_file_with_its_bytes_and_text(tmp_path, two_files, born_digital, scanned):
    units, site = two_files
    record, store = run(tmp_path, FakeListing(units), site)
    assert record["finished"] and record["stopped"] is None and record["added"] == 2
    stored = rows(tmp_path)
    opinion = stored["opinions/23pdf/23a843_he4l.pdf"]
    assert opinion["file"] == born_digital and opinion["file_sha256"] == sha256(born_digital) and opinion["file_size"] == len(born_digital)
    assert opinion["text"] == extract_pdf(born_digital)["text"] and opinion["ocr_text"] is None and opinion["text_source"] == "born_digital"
    filing = stored["pdfs/recordsandbriefs/oj-page3.pdf"]
    assert filing["file"] == scanned and filing["text"] is None and filing["ocr_text"] == extract_pdf(scanned)["ocr_text"]
    manifest = store.read_manifest()
    assert manifest["partitions"]["OT2023"]["complete"] and manifest["partitions"]["OT2023"]["ids"] == sorted(stored)
    assert manifest["listing"]["count"] == 2


def test_second_run_fetches_nothing(tmp_path, two_files):
    units, site = two_files
    run(tmp_path, FakeListing(units), site)
    before = len(site.gets)
    record, _ = run(tmp_path, FakeListing(units), site)
    assert len(site.gets) == before and record["fetched"] == 0


def test_a_file_the_listing_drops_is_kept_and_marked_then_restored(tmp_path, two_files):
    units, site = two_files
    run(tmp_path, FakeListing(units), site)
    record, _ = run(tmp_path, FakeListing(units[:1]), site)
    assert record["delisted"] == 1
    dropped = rows(tmp_path)["pdfs/recordsandbriefs/oj-page3.pdf"]
    assert dropped["listed"] is False and dropped["delisted_at"] and dropped["file"]
    gets = len(site.gets)
    record, _ = run(tmp_path, FakeListing(units), site)
    assert record["relisted"] == 1 and len(site.gets) == gets
    back = rows(tmp_path)["pdfs/recordsandbriefs/oj-page3.pdf"]
    assert back["listed"] is True and back["delisted_at"] is None


def test_changed_entries_update_the_row_without_fetching_the_file(tmp_path, two_files):
    units, site = two_files
    run(tmp_path, FakeListing(units), site)
    renamed = unit("opinions/23pdf/23a843_he4l.pdf", link_text="Navarro v. United States")
    gets = len(site.gets)
    record, _ = run(tmp_path, FakeListing([renamed, units[1]]), site)
    assert record["updated"] == 1 and len(site.gets) == gets
    assert rows(tmp_path)["opinions/23pdf/23a843_he4l.pdf"]["title"] == "Navarro v. United States"


def test_a_stored_file_stays_in_its_partition(tmp_path, two_files):
    units, site = two_files
    run(tmp_path, FakeListing(units), site)
    moved = unit("opinions/23pdf/23a843_he4l.pdf", partition="OT2024", term=2024)
    run(tmp_path, FakeListing([moved, units[1]]), site)
    assert "opinions/23pdf/23a843_he4l.pdf" in rows(tmp_path, "OT2023")
    assert not (tmp_path / "data" / "OT2024.parquet").exists()


def test_a_missing_file_is_recorded_as_failed_and_retried(tmp_path, two_files):
    units, site = two_files
    dead = unit("opinions/15pdf/14-10186_k53l.pdf")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        record, store = run(tmp_path, FakeListing(units + [dead]), site)
        failure = store.read_manifest()["failures"][dead.id]
        assert failure["attempts"] == attempt and "404" in failure["error"]
        assert store.read_manifest()["partitions"]["OT2023"]["complete"] is (attempt == MAX_ATTEMPTS)
    gets = site.gets.count(dead.url)
    run(tmp_path, FakeListing(units + [dead]), site)
    assert site.gets.count(dead.url) == gets, "a file that failed MAX_ATTEMPTS times waits RETRY_AFTER_HOURS"


def test_a_listing_page_that_empties_stops_the_run(tmp_path, two_files):
    units, site = two_files
    run(tmp_path, FakeListing(units), site)
    empty = FakeListing([], pages={BASE + "test/": {"term": None, "entries": 0}})
    record, store = run(tmp_path, empty, site)
    assert record["finished"] is False and record["stopped"].startswith("ListingError")
    assert all(row["listed"] for row in rows(tmp_path).values())


def test_a_redirected_term_page_may_be_empty(tmp_path, two_files):
    units, site = two_files
    pages = {BASE + "test/": {"term": None, "entries": 2}, BASE + "test/26": {"term": 2026, "entries": 0, "redirected": True}}
    record, _ = run(tmp_path, FakeListing(units, pages=pages), site)
    assert record["finished"]


def test_a_term_page_that_shows_another_term_stops_the_run_where_it_had_entries(tmp_path, two_files):
    units, site = two_files
    run(tmp_path, FakeListing(units, pages={BASE + "test/17": {"term": 2017, "entries": 2}, BASE + "test/26": {"term": 2026, "entries": 0}}), site)
    wrong = {BASE + "test/17": {"term": 2017, "entries": 0, "shown_term": [2025]}, BASE + "test/26": {"term": 2026, "entries": 0}}
    record, _ = run(tmp_path, FakeListing([], pages=wrong), site)
    assert record["finished"] is False and record["stopped"].startswith("ListingError: " + BASE + "test/17 shows Term Year 2025, not 2017, so it lists nothing; the last complete listing found 2 entries there")
    assert all(row["listed"] for row in rows(tmp_path).values())
    # A page that listed nothing before may show another term: nothing is lost by counting it empty.
    wrong_empty = {BASE + "test/17": {"term": 2017, "entries": 2}, BASE + "test/26": {"term": 2026, "entries": 0, "shown_term": [2025]}}
    record, _ = run(tmp_path, FakeListing(units, pages=wrong_empty), site)
    assert record["finished"]


def test_a_mutable_file_is_asked_about_and_fetched_again_only_when_it_changed(tmp_path, born_digital):
    calendar = unit("oral_arguments/2025termcourtcalendar.pdf", partition="OT2025", term=2025)
    site = FakeSite({calendar.url: born_digital})
    mutable = collection(mutable=True)
    run(tmp_path, FakeListing([calendar]), site, collection=mutable)
    gets = len(site.gets)
    record, _ = run(tmp_path, FakeListing([calendar]), site, collection=mutable)
    assert record["checked"] == 1 and len(site.gets) == gets and len(site.heads) == 1
    amended = pdf_bytes("daycall.pdf")
    site.files[calendar.url] = amended
    record, _ = run(tmp_path, FakeListing([calendar]), site, collection=mutable)
    assert record["replaced"] == 1
    row = rows(tmp_path, "OT2025")[calendar.id]
    assert row["file"] == amended and row["file_sha256"] == sha256(amended) and row["text"] == extract_pdf(amended)["text"]


def test_an_old_mutable_file_is_asked_about_only_under_revalidate_all(tmp_path, born_digital):
    calendar = unit("oral_arguments/1990termcourtcalendar.pdf", partition="OT1990", term=1990)
    site = FakeSite({calendar.url: born_digital})
    run(tmp_path, FakeListing([calendar]), site, collection=collection(mutable=True))
    run(tmp_path, FakeListing([calendar]), site, collection=collection(mutable=True))
    assert site.heads == []
    record, _ = run(tmp_path, FakeListing([calendar]), site, collection=collection(mutable=True), revalidate_all=True)
    assert record["checked"] == 1 and site.heads == [calendar.url]


def test_revalidate_all_asks_about_every_stored_file_of_any_collection(tmp_path, two_files):
    units, site = two_files
    run(tmp_path, FakeListing(units), site)
    run(tmp_path, FakeListing(units), site)
    assert site.heads == []
    gets = len(site.gets)
    record, _ = run(tmp_path, FakeListing(units), site, revalidate_all=True)
    assert record["checked"] == 2 and len(site.heads) == 2 and len(site.gets) == gets
    replaced = pdf_bytes("daycall.pdf")
    site.files[units[0].url] = replaced
    record, _ = run(tmp_path, FakeListing(units), site, revalidate_all=True)
    assert record["replaced"] == 1 and record["checked"] == 1
    row = rows(tmp_path)[units[0].id]
    assert row["file"] == replaced and row["file_sha256"] == sha256(replaced) and row["text"] == extract_pdf(replaced)["text"]


def test_a_stored_file_that_cannot_be_fetched_again_keeps_its_row(tmp_path, two_files, born_digital):
    units, site = two_files
    run(tmp_path, FakeListing(units), site)
    del site.files[units[0].url]
    record, store = run(tmp_path, FakeListing(units), site, revalidate_all=True)
    assert record["failed"] == 1
    assert rows(tmp_path)[units[0].id]["file"] == born_digital
    manifest = store.read_manifest()
    assert units[0].id in manifest["failures"] and manifest["partitions"]["OT2023"]["failed"] == 0


def test_fetching_the_same_bytes_again_keeps_the_stored_stamps(tmp_path, two_files):
    units, site = two_files
    run(tmp_path, FakeListing(units), site)
    before = rows(tmp_path)["opinions/23pdf/23a843_he4l.pdf"]
    time.sleep(1.1)
    record, _ = run(tmp_path, FakeListing(units), site, refetch=True)
    assert record["unchanged"] == 2
    after = rows(tmp_path)["opinions/23pdf/23a843_he4l.pdf"]
    assert after["fetched_at"] == before["fetched_at"] and after["first_seen_at"] == before["first_seen_at"] and after["checked_at"] > before["checked_at"]


def test_a_run_out_of_time_stops_and_the_next_one_resumes(tmp_path, two_files):
    units, site = two_files
    record, store = run(tmp_path, FakeListing(units), site, deadline=time.monotonic() - 1)
    assert record["stopped"] == "budget" and record["fetched"] == 0
    assert store.read_manifest()["listing"] is None
    record, store = run(tmp_path, FakeListing(units), site)
    assert record["added"] == 2 and store.read_manifest()["listing"]["count"] == 2


def test_max_units_leaves_the_partition_incomplete(tmp_path, two_files):
    units, site = two_files
    record, store = run(tmp_path, FakeListing(units), site, max_units=1)
    assert record["added"] == 1 and store.read_manifest()["partitions"]["OT2023"]["complete"] is False


def test_low_disk_stops_before_downloading(tmp_path, two_files):
    units, site = two_files
    record, _ = run(tmp_path, FakeListing(units), site, min_free_bytes=1 << 60)
    assert record["stopped"].startswith("LowDisk") and site.gets == []


def test_another_writers_fresh_lease_defers_the_run(tmp_path, two_files):
    units, site = two_files
    _, store = run(tmp_path, FakeListing(units), site)
    record, _ = run(tmp_path, FakeListing(units), site, writer="github-actions")
    assert record["stopped"] == "deferred"


def test_a_response_that_is_not_a_pdf_is_a_failure_not_a_row(tmp_path, born_digital):
    page = unit("opinions/23pdf/error.pdf")
    site = FakeSite({page.url: b"<!DOCTYPE html><html><body>Error</body></html>"})
    record, store = run(tmp_path, FakeListing([page]), site)
    assert record["failed"] == 1 and "NotAPdf" in store.read_manifest()["failures"][page.id]["error"]
    assert rows(tmp_path) == {}


def test_listing_error_from_the_listing_stops_the_run(tmp_path, two_files):
    units, site = two_files

    class Broken(FakeListing):
        def list_all(self):
            raise ListingError("a row of 2 cells under 3 headers")

    record, _ = run(tmp_path, Broken(units), site)
    assert record["stopped"].startswith("ListingError") and site.gets == []


def test_code_points_with_no_standard_meaning_are_counted_in_rows_and_characters():
    rows = [{"text": "Of\ue405ce and Net\ue406ix"}, {"text": "plane 15 \U000f0001 and 16 \U00100001, index \ufffd \ufffd"}, {"text": "fi as letters, \ufb01 as a ligature"}, {"text": None}, {"text": ""}, {}]
    assert codepoints(rows) == {"private_use_rows": 2, "private_use": 4, "replacement_rows": 1, "replacement": 2}


def test_a_pdf_collection_takes_a_file_whose_address_has_no_pdf_suffix_for_a_pdf(tmp_path, born_digital):
    volume = unit("opinions/boundvolumes/502bv")
    record, _ = run(tmp_path, FakeListing([volume]), FakeSite({volume.url: born_digital}))
    assert record["added"] == 1 and rows(tmp_path)[volume.id]["media_type"] == "application/pdf"


def test_a_listing_that_mixes_pdfs_and_pages_extracts_each_by_its_document_type_and_compares_a_page_by_its_text(tmp_path, born_digital):
    guide, rules = unit("filingandrules/guide.aspx", document_type="html"), unit("filingandrules/rules.pdf", document_type="pdf")
    page = b'<html><body><input name="token" value="1"><div id="pagemaindiv"><p>Guide for Counsel</p></div></body></html>'
    site = FakeSite({guide.url: page, rules.url: born_digital})
    record, _ = run(tmp_path, FakeListing([guide, rules]), site)
    stored = rows(tmp_path)
    assert record["added"] == 2 and stored[guide.id]["media_type"] == "text/html" and stored[rules.id]["media_type"] == "application/pdf"
    site.files[guide.url] = page.replace(b'value="1"', b'value="2"')
    record, _ = run(tmp_path, FakeListing([guide, rules]), site, refetch=True)
    assert record["unchanged"] == 2 and rows(tmp_path)[guide.id]["file"] == page


def test_argument_audio_is_stored_as_served_with_no_text_and_a_response_that_is_not_audio_is_a_failure(tmp_path):
    audio, broken = unit("media/audio/mp3files/25-466.mp3", document_type="audio"), unit("media/audio/mp3files/25-467.mp3", document_type="audio")
    mp3 = b"ID3\x04\x00\x00\x00\x00\x00\x00\xff\xfb\x90\x64" + bytes(400)
    site = FakeSite({audio.url: mp3, broken.url: b"<!DOCTYPE html><html><body>Error</body></html>"})
    record, store = run(tmp_path, FakeListing([audio, broken]), site)
    row = rows(tmp_path)[audio.id]
    assert record["added"] == 1 and record["failed"] == 1 and broken.id in store.read_manifest()["failures"]
    assert row["file"] == mp3 and row["media_type"] == "audio/mpeg" and row["text_source"] == "no_text" and row["text"] is None and row["pages"] is None


def test_video_is_stored_as_served_with_no_text_and_a_response_that_is_not_mp4_is_a_failure(tmp_path):
    video, broken = unit("media/video/mp4files/scott_v_harris.mp4", document_type="video"), unit("media/video/mp4files/kelly_v_california.mp4", document_type="video")
    mp4 = bytes.fromhex("00000014667479706d70343200000200") + bytes(400)
    site = FakeSite({video.url: mp4, broken.url: b"<!DOCTYPE html><html><body>Error</body></html>"})
    record, store = run(tmp_path, FakeListing([video, broken]), site)
    row = rows(tmp_path)[video.id]
    assert record["added"] == 1 and record["failed"] == 1 and broken.id in store.read_manifest()["failures"]
    assert row["file"] == mp4 and row["media_type"] == "video/mp4" and row["text_source"] == "no_text" and row["text"] is None and row["pages"] is None



class DeployStampedSite(FakeSite):
    """Sends a page with only a Last-Modified, the same for every page and every version of it, as www.supremecourt.gov did for two About pages on September 27, 2026."""

    def _response(self, url, body):
        response = super()._response(url, body)
        response.headers = {name: value for name, value in response.headers.items() if name not in ("etag", "content-length")}
        return response


def test_a_mutable_page_is_read_again_on_every_run_because_its_headers_cannot_show_an_edit(tmp_path):
    page = unit("about/justices.aspx", partition="pages", term=None, document_type="html")
    first = b'<html><body><div id="pagemaindiv"><p>Chief Justice John G. Roberts, Jr.</p></div></body></html>'
    site = DeployStampedSite({page.url: first})
    run(tmp_path, FakeListing([page]), site, collection=collection(mutable=True))
    record, _ = run(tmp_path, FakeListing([page]), site, collection=collection(mutable=True))
    assert record["unchanged"] == 1 and site.heads == [] and site.gets == [page.url] * 2
    site.files[page.url] = first.replace(b"</p>", b"</p><p>Associate Justice Clarence Thomas</p>")
    record, _ = run(tmp_path, FakeListing([page]), site, collection=collection(mutable=True))
    assert record["replaced"] == 1 and "Associate Justice Clarence Thomas" in rows(tmp_path, "pages")[page.id]["text"]

def test_a_mutable_file_of_no_term_is_asked_about_on_every_run(tmp_path, born_digital):
    guide = unit("filingandrules/guidetofilingpaidcases.pdf", partition="current", term=None)
    site = FakeSite({guide.url: born_digital})
    run(tmp_path, FakeListing([guide]), site, collection=collection(mutable=True))
    for heads in (1, 2):
        record, _ = run(tmp_path, FakeListing([guide]), site, collection=collection(mutable=True))
        assert record["checked"] == 1 and site.heads == [guide.url] * heads
    record, _ = run(tmp_path, FakeListing([guide]), site, collection=collection())
    assert record["checked"] == 0 and len(site.heads) == 2, "a collection that is not mutable does not ask"
