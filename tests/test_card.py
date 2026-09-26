"""The dataset card: rendered from the manifest alone, with front matter the Hub can parse, the license each collection needs, and every gap shown."""

import time

import pytest
import yaml

from conftest import FakeListing, FakeSite, collection, unit
from scotus_products.card import COLUMN_DOCS, FAILURES_SHOWN, LICENSE, count, gigabytes, render
from scotus_products.pipeline import Context, new_manifest, sync
from scotus_products.sources import COLLECTIONS
from scotus_products.store import SCHEMA, LocalStore


def front_matter(card):
    head, rest = card.split("---\n", 2)[1:]
    return yaml.safe_load(head), rest


@pytest.mark.parametrize("name", list(COLLECTIONS))
def test_every_collection_renders_a_card_the_hub_can_parse(name):
    meta, body = front_matter(render(new_manifest(COLLECTIONS[name])))
    assert meta["pretty_name"] == f"Supreme Court of the United States: {COLLECTIONS[name].title}"
    assert meta["language"] == ["en"] and meta["size_categories"] == ["n<1K"]
    assert "configs" not in meta, "no data files to point at before the first partition"
    assert "The first run has not read the listing yet." in body and "None" not in body
    for note in COLLECTIONS[name].notes:
        assert f"- {note}\n" in body, "every collection note is in its card, unchanged"


@pytest.mark.parametrize("name", list(COLLECTIONS))
def test_the_license_is_only_claimed_where_it_holds(name):
    meta, body = front_matter(render(new_manifest(COLLECTIONS[name])))
    if name in ("argument-transcripts", "original-jurisdiction-records-and-briefs"):
        assert meta["license"] == "unknown" and "license_name" not in meta
        assert LICENSE[name] in body and "makes no claim" in LICENSE[name]
    else:
        assert meta == meta | {"license": "other", "license_name": "us-government-works", "license_link": "https://www.copyright.gov/title17/92chap1.html#105"}
        assert LICENSE["default"] in body


def test_the_schema_table_documents_every_column_once():
    assert list(COLUMN_DOCS) == SCHEMA.names
    body = render(new_manifest(COLLECTIONS["journal"]))
    for column in SCHEMA:
        assert body.count(f"| `{column.name}` | {column.type} |") == 1


@pytest.fixture
def built(tmp_path, born_digital, scanned):
    """A dataset of the Original Jurisdiction collection, from a stand-in site where one of three listed files is missing."""
    units = [unit("pdfs/a.pdf"), unit("pdfs/b.pdf", partition="OT2024", term=2024), unit("pdfs/gone.pdf")]
    site = FakeSite({units[0].url: born_digital, units[1].url: scanned})
    store = LocalStore(tmp_path, card=render)
    ctx = Context(store=store, collection=collection(name="original-jurisdiction-records-and-briefs"), fetcher=site, deadline=time.monotonic() + 600, workers=2)
    sync(ctx, FakeListing(units))
    store.close()
    return LocalStore(tmp_path)


def test_a_built_datasets_card_is_the_one_its_manifest_renders(built):
    card, manifest = built.read_text("README.md"), built.read_manifest()
    assert card == render(manifest)
    meta, body = front_matter(card)
    assert meta["configs"] == [{"config_name": "default", "data_files": [{"split": "train", "path": "data/*.parquet"}]}]
    assert f"**2 files** ({gigabytes(manifest['partitions']['OT2023']['file_bytes'] + manifest['partitions']['OT2024']['file_bytes'])}, 2 PDF pages); the listing of {manifest['seen']['at']} linked 3 files from 3 entries." in body
    assert "| born_digital | 1 |" in body and "| scanned | 1 |" in body
    assert "Cross-check of `text` against pypdf: 1 file compared;" in body


def test_the_card_does_not_depend_on_the_order_of_the_manifests_keys(built):
    manifest = built.read_manifest()
    reordered = dict(manifest, partitions=dict(reversed(list(manifest["partitions"].items()))), failures=dict(reversed(list(manifest["failures"].items()))))
    assert render(reordered) == render(manifest)


def test_the_card_names_every_gap(built):
    body = render(built.read_manifest())
    assert "1 partition is not yet complete: OT2023." in body
    assert "| [pdfs/gone.pdf](https://www.supremecourt.gov/pdfs/gone.pdf) | 1 | FetchFailed: HTTP 404 for https://www.supremecourt.gov/pdfs/gone.pdf |" in body
    assert "0 files have failed 3 times." in body


def test_a_long_failure_list_is_cut_with_a_count_of_the_rest(built):
    manifest = built.read_manifest()
    manifest["failures"] = {f"pdfs/{n:03d}.pdf": {"partition": "OT2023", "url": f"https://www.supremecourt.gov/pdfs/{n:03d}.pdf", "attempts": 3, "error": "FetchFailed: HTTP 404 | planted", "at": "2026-09-26T00:00:00Z"} for n in range(FAILURES_SHOWN + 5)}
    body = render(manifest)
    assert body.count("| 3 | FetchFailed: HTTP 404 / planted |") == FAILURES_SHOWN, "a | in an error would break the table"
    assert "| ... 5 more in `manifest.json` | | |" in body and f"{FAILURES_SHOWN + 5} files have failed 3 times." in body


def test_a_stored_file_that_failed_again_is_not_called_unstored(built):
    manifest = built.read_manifest()
    manifest["failures"]["pdfs/a.pdf"] = {"partition": "OT2023", "url": "https://www.supremecourt.gov/pdfs/a.pdf", "attempts": 1, "error": "FetchFailed: HTTP 503 for https://www.supremecourt.gov/pdfs/a.pdf", "at": "2026-09-26T00:00:00Z"}
    body = render(manifest)
    assert "[pdfs/a.pdf]" not in body and "| [pdfs/gone.pdf]" in body
    assert "The last attempt to fetch 1 stored file again failed, so its row holds the version fetched earlier: pdfs/a.pdf." in body


def test_the_card_says_which_stored_files_are_asked_about(built):
    mutable, fixed = render(new_manifest(COLLECTIONS["journal"])), render(built.read_manifest())
    assert "Every run asks the server (HEAD) whether a stored file of the previous term or a later one" in mutable and "only when it is run with `--revalidate-all`" not in mutable
    assert "A run asks the server whether stored files changed only when it is run with `--revalidate-all`" in fixed and "Every run asks the server" not in fixed


def test_counts_and_sizes_read_as_english():
    assert count(1, "file") == "1 file" and count(2, "file") == "2 files" and count(1_234, "entry", "entries") == "1,234 entries"
    assert gigabytes(41_945) == "42 KB" and gigabytes(58_000_000) == "58.0 MB" and gigabytes(46_000_000_000) == "46.00 GB"
