"""verify() against a dataset built by the pipeline, then tampered with one defect at a time: each check must catch its defect and only that check."""

import hashlib
import time

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from conftest import FakeListing, FakeSite, collection, unit
from scotus_products.pipeline import MAX_ATTEMPTS, Context, sync
from scotus_products.store import LocalStore, sha256_file, write_parquet
from scotus_products.verify import TOLERANCE, verify

OPINION = "opinions/23pdf/23a843_he4l.pdf"
FILING = "pdfs/recordsandbriefs/oj-page3.pdf"


@pytest.fixture
def dataset(tmp_path, born_digital, scanned):
    units = [unit(OPINION), unit(FILING)]
    site = FakeSite({units[0].url: born_digital, units[1].url: scanned})
    store = LocalStore(tmp_path)
    sync(Context(store=store, collection=collection(), fetcher=site, deadline=time.monotonic() + 600, workers=2), FakeListing(units))
    store.close()
    return tmp_path, units, site


def tamper(root, change, key="OT2023", manifest_too=True):
    """Rewrite one partition with change(rows) applied; with manifest_too, the manifest follows (sha256, rows, ids), so only the row-level checks can notice."""
    store = LocalStore(root)
    rows = store.read_partition(key)
    change(rows)
    path = root / "data" / f"{key}.parquet"
    stats = write_parquet(rows, path)
    if manifest_too:
        manifest = store.read_manifest()
        manifest["partitions"][key].update(sha256=stats["sha256"], rows=stats["rows"], ids=stats["ids"], listed=stats["listed"])
        (root / "manifest.json").write_text(__import__("json").dumps(manifest))
    store.close()


def row(rows, uid):
    return next(r for r in rows if r["id"] == uid)


def check(root, **options):
    store = LocalStore(root)
    try:
        return verify(store, **options)
    finally:
        store.close()


def test_a_fresh_dataset_passes_every_check(dataset):
    root, units, site = dataset
    report = check(root, listing=FakeListing(units), deep=True, redownload=2, fetcher=site, seed=1)
    assert report["problems"] == []
    assert report["deep"] == {"rows_checked": 2, "rows_with_problems": 0}
    assert report["redownload"]["same"] == 2 and report["live"]["missing"] == 0


def test_a_partition_file_that_is_not_the_one_the_manifest_names(dataset):
    root, _, _ = dataset
    tamper(root, lambda rows: row(rows, OPINION).update(title="Edited"), manifest_too=False)
    problems = check(root)["problems"]
    assert len(problems) == 1 and problems[0].startswith("OT2023: data/OT2023.parquet has sha256")


def test_a_partition_file_that_cannot_be_read_is_reported_not_raised(dataset):
    root, _, _ = dataset
    path = root / "data" / "OT2023.parquet"
    path.write_bytes(path.read_bytes() + b"\0")
    problems = check(root, deep=True)["problems"]
    assert any("has sha256" in p for p in problems) and any("cannot be read" in p for p in problems)


def test_text_that_is_not_the_extractor_output_is_caught_by_the_deep_check_only(dataset):
    root, _, _ = dataset

    def edit(rows):
        opinion = row(rows, OPINION)
        # One character, the kind of change a "cleanup" makes: a hyphen that ends a line, removed.
        opinion["text"] = opinion["text"].replace("-\n", "\n", 1)

    tamper(root, edit)
    assert check(root)["problems"] == []
    problems = check(root, deep=True)["problems"]
    assert problems == [f"{OPINION}: extracting the stored file again gives other text"]


def test_ocr_text_that_differs_from_the_file_is_caught_by_the_deep_check(dataset):
    root, _, _ = dataset
    tamper(root, lambda rows: row(rows, FILING).update(ocr_text=row(rows, FILING)["ocr_text"].replace("WISCONSIN", "WISCONSLN")))
    assert check(root, deep=True)["problems"] == [f"{FILING}: extracting the stored file again gives other text"]



AUDIO, VIDEO = "media/audio/mp3files/25-466.mp3", "media/video/mp4files/scott_v_harris.mp4"


@pytest.fixture
def media(tmp_path):
    units = [unit(AUDIO, document_type="audio"), unit(VIDEO, document_type="video")]
    site = FakeSite({units[0].url: b"ID3\x04\x00\x00\x00\x00\x00\x00\xff\xfb\x90\x64" + bytes(400), units[1].url: bytes.fromhex("00000014667479706d70343200000200") + bytes(400)})
    store = LocalStore(tmp_path)
    sync(Context(store=store, collection=collection(), fetcher=site, deadline=time.monotonic() + 600, workers=2), FakeListing(units))
    store.close()
    return tmp_path


def test_audio_and_video_whose_bytes_are_no_longer_audio_or_video_are_caught_by_the_deep_check_only(media):
    assert check(media, deep=True)["deep"] == {"rows_checked": 2, "rows_with_problems": 0}
    page = b"<!DOCTYPE html><html><body>Error</body></html>"

    def swap(rows):
        for uid in (AUDIO, VIDEO):
            row(rows, uid).update(file=page, file_sha256=hashlib.sha256(page).hexdigest(), file_size=len(page))

    tamper(media, swap)
    assert check(media)["problems"] == []
    problems = check(media, deep=True)["problems"]
    assert len(problems) == 2 and problems[0].startswith(f"{AUDIO}: the response does not look like MP3 audio") and problems[1].startswith(f"{VIDEO}: the response does not look like an MP4 file")


def test_a_video_row_that_claims_text_is_caught_by_the_deep_check(media):
    tamper(media, lambda rows: row(rows, VIDEO).update(text_source="born_digital", text="Officer Scott", extractor="pdftotext"))
    assert f"{VIDEO}: checking the stored file again gives other fields" in check(media, deep=True)["problems"]

def test_bytes_that_do_not_match_their_hash_are_caught(dataset):
    root, _, _ = dataset
    tamper(root, lambda rows: row(rows, OPINION).update(file=row(rows, OPINION)["file"] + b"\n"))
    problems = check(root, deep=True)["problems"]
    assert f"{OPINION}: the stored bytes do not match file_sha256 and file_size" in problems


def test_ocr_in_the_text_column_is_caught(dataset):
    root, _, _ = dataset
    tamper(root, lambda rows: row(rows, FILING).update(text=row(rows, FILING)["ocr_text"]))
    assert any(f"{FILING} is scanned with text set" in p for p in check(root)["problems"])


def test_born_digital_text_moved_to_ocr_text_is_caught(dataset):
    root, _, _ = dataset
    tamper(root, lambda rows: row(rows, OPINION).update(ocr_text=row(rows, OPINION)["text"], text=None))
    assert any(f"{OPINION} is born_digital with text null and ocr_text set" in p for p in check(root)["problems"])


def test_rows_that_are_not_the_manifests(dataset):
    root, _, _ = dataset
    tamper(root, lambda rows: rows.remove(row(rows, FILING)), manifest_too=False)
    manifest_path = root / "manifest.json"
    manifest = __import__("json").loads(manifest_path.read_text())
    manifest["partitions"]["OT2023"]["sha256"] = sha256_file(root / "data" / "OT2023.parquet")
    manifest_path.write_text(__import__("json").dumps(manifest))
    problems = check(root)["problems"]
    assert "OT2023: 1 rows, the manifest says 2" in problems and "OT2023: the ids differ from the manifest's" in problems


def test_listed_files_that_are_neither_stored_nor_failed(dataset):
    root, units, _ = dataset
    extra = [unit(f"opinions/23pdf/new-{n}.pdf") for n in range(TOLERANCE + 1)]
    report = check(root, listing=FakeListing(units + extra))
    assert report["live"]["missing"] == TOLERANCE + 1
    assert any("neither stored nor recorded as failed" in p for p in report["problems"])
    # Within the tolerance, a document posted since the last run is not a problem.
    assert check(root, listing=FakeListing(units + extra[:TOLERANCE]))["problems"] == []


def test_files_that_failed_every_attempt_are_reported_apart(dataset):
    root, units, _ = dataset
    dead = [unit(f"opinions/15pdf/dead-{n}.pdf") for n in range(TOLERANCE + 1)]
    manifest_path = root / "manifest.json"
    manifest = __import__("json").loads(manifest_path.read_text())
    for u in dead:
        manifest["failures"][u.id] = {"partition": "OT2023", "url": u.url, "attempts": MAX_ATTEMPTS, "error": "FetchFailed: HTTP 404", "at": "2026-09-26T00:00:00Z"}
    manifest["listing"]["partitions"]["OT2023"] += len(dead)
    manifest_path.write_text(__import__("json").dumps(manifest))
    report = check(root, listing=FakeListing(units + dead))
    assert report["problems"] == [] and report["live"]["missing_but_failed"] == TOLERANCE + 1 and report["failed_max_attempts"] == TOLERANCE + 1 and report["failures"] == TOLERANCE + 1


def test_rows_marked_listed_that_the_site_no_longer_lists(dataset):
    root, units, _ = dataset
    tamper(root, lambda rows: rows.extend(dict(row(rows, OPINION), id=f"opinions/23pdf/gone-{n}.pdf") for n in range(TOLERANCE + 1)))
    report = check(root, listing=FakeListing(units))
    assert any("marked listed but the site no longer lists them" in p for p in report["problems"])


def test_redownload_notices_a_file_the_site_changed(dataset):
    root, units, site = dataset
    site.files[units[0].url] = site.files[units[0].url] + b"%%EOF\n"
    report = check(root, redownload=2, fetcher=site, seed=1)
    assert report["redownload"]["differ"] == 1
    assert f"redownload: {OPINION}: the site now serves other bytes" in report["problems"]


def test_an_id_in_two_partitions(dataset):
    root, _, _ = dataset
    store = LocalStore(root)
    rows = store.read_partition("OT2023")
    stats = write_parquet([dict(row(rows, OPINION), partition="OT2024")], root / "data" / "OT2024.parquet")
    manifest = store.read_manifest()
    manifest["partitions"]["OT2024"] = dict(manifest["partitions"]["OT2023"], sha256=stats["sha256"], rows=1, listed=1, ids=stats["ids"], file="data/OT2024.parquet")
    (root / "manifest.json").write_text(__import__("json").dumps(manifest))
    store.close()
    assert any("ids in more than one row" in p for p in check(root)["problems"])


def test_a_file_the_manifest_does_not_name(dataset):
    root, _, _ = dataset
    (root / "data" / "stray.parquet").write_bytes((root / "data" / "OT2023.parquet").read_bytes())
    assert "data/stray.parquet is not in the manifest" in check(root)["problems"]


def test_a_partition_file_with_other_columns(dataset):
    # The dataset viewer takes a config's columns from its first file, so a partition written with other columns fails the whole config while every row still reads.
    root, _, _ = dataset
    path = root / "data" / "OT2023.parquet"
    table = pq.read_table(path)
    table = table.rename_columns(["name" if column == "title" else column for column in table.column_names])
    table = table.set_column(table.column_names.index("pages"), "pages", table.column("pages").cast(pa.int64()))
    pq.write_table(table, path)
    manifest_path = root / "manifest.json"
    manifest = __import__("json").loads(manifest_path.read_text())
    manifest["partitions"]["OT2023"]["sha256"] = sha256_file(path)
    manifest_path.write_text(__import__("json").dumps(manifest))
    assert check(root)["problems"] == [
        "OT2023: data/OT2023.parquet has no column title",
        "OT2023: data/OT2023.parquet has a column name the schema does not have",
        "OT2023: data/OT2023.parquet has pages as int64, the schema says int32",
    ]
