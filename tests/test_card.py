"""The dataset card: rendered from the manifest alone, with front matter the Hub can parse, the license each collection needs, and every gap shown."""

import copy
import time

import pytest
import yaml

from conftest import FakeListing, FakeSite, collection, unit
from scotus_products.card import COLUMN_DOCS, FAILURES_SHOWN, LICENSE, count, gigabytes, render, render_category
from scotus_products.pipeline import Context, new_manifest, sync
from scotus_products.sources import CATEGORIES, COLLECTIONS
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
    if name in ("argument-transcripts", "argument-audio", "online-sources-cited-in-opinions", "media-files-cited-in-opinions", "speeches", "original-jurisdiction-records-and-briefs"):
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
    store = LocalStore(tmp_path)
    yield store
    store.close()


def test_a_built_datasets_card_is_the_one_its_manifest_renders(built):
    card, manifest = built.read_text("README.md"), built.read_manifest()
    assert card == render(manifest)
    meta, body = front_matter(card)
    name = "original-jurisdiction-records-and-briefs"
    assert meta["configs"] == [{"config_name": name, "data_files": [{"split": "train", "path": f"{name}/data/*.parquet"}]}]
    assert f"**2 files** ({gigabytes(manifest['partitions']['OT2023']['file_bytes'] + manifest['partitions']['OT2024']['file_bytes'])}, 2 PDF pages); the listing of {manifest['seen']['at']} linked 3 files from 3 entries." in body
    assert "| born_digital | 1 |" in body and "| scanned | 1 |" in body
    assert "Cross-check of `text` against pypdf: 1 file compared;" in body


def test_the_card_does_not_depend_on_the_order_of_the_manifests_keys(built):
    manifest = built.read_manifest()
    reordered = dict(manifest, partitions=dict(reversed(list(manifest["partitions"].items()))), failures=dict(reversed(list(manifest["failures"].items()))))
    assert render(reordered) == render(manifest)


def test_the_card_names_every_gap(built):
    body = render(built.read_manifest())
    assert "This collection does not hold every file the listing links: the status below says what it holds and where the gaps are." in body and "Every document file" not in body
    assert "1 partition is not yet complete: OT2023." in body
    assert "| [pdfs/gone.pdf](https://www.supremecourt.gov/pdfs/gone.pdf) | 1 | FetchFailed: HTTP 404 for https://www.supremecourt.gov/pdfs/gone.pdf |" in body
    assert "0 files have failed 3 times." in body


def manifest_of(path):
    store = LocalStore(path)
    try:
        return store.read_manifest()
    finally:
        store.close()


@pytest.fixture
def whole(tmp_path, born_digital, scanned):
    """A dataset that holds every file its stand-in listing links."""
    units = [unit("pdfs/a.pdf"), unit("pdfs/b.pdf", partition="OT2024", term=2024)]
    store = LocalStore(tmp_path, card=render)
    ctx = Context(store=store, collection=collection(name="original-jurisdiction-records-and-briefs"), fetcher=FakeSite({units[0].url: born_digital, units[1].url: scanned}), deadline=time.monotonic() + 600, workers=2)
    sync(ctx, FakeListing(units))
    store.close()
    return manifest_of(tmp_path)


EVERY = "Every document file that the Supreme Court's website lists under"


def test_the_card_claims_every_file_only_when_no_gap_shows(whole):
    assert whole["seen"]["partitions"] == {"OT2023": 1, "OT2024": 1}
    assert EVERY in render(whole) and "does not hold every file" not in render(whole)
    failure = {"partition": "OT2023", "url": "https://www.supremecourt.gov/pdfs/c.pdf", "attempts": 3, "error": "FetchFailed: HTTP 404", "at": "2026-09-26T00:00:00Z"}
    gaps = {
        "no complete run": lambda m: m.pop("listing"),
        "a partition not complete": lambda m: m["partitions"]["OT2023"].update(complete=False),
        "a partition no run reached": lambda m: m["seen"]["partitions"].update(OT2022=1),
        "a file that failed for good": lambda m: m["failures"].update({"pdfs/c.pdf": failure}),
        "fewer listed rows than listed files": lambda m: m["seen"].update(count=3),
    }
    for gap, plant in gaps.items():
        manifest = copy.deepcopy(whole)
        plant(manifest)
        body = render(manifest)
        assert EVERY not in body and "This collection does not hold every file the listing links" in body, gap


def test_the_card_names_the_partitions_a_stopped_run_never_reached(tmp_path, born_digital):
    """The disk runs low before the first download: the partition the run was on is incomplete, and the one after it has no entry at all."""
    units = [unit("pdfs/a.pdf"), unit("pdfs/c.pdf"), unit("pdfs/b.pdf", partition="OT2024", term=2024)]
    store = LocalStore(tmp_path, card=render)
    ctx = Context(store=store, collection=collection(name="original-jurisdiction-records-and-briefs"), fetcher=FakeSite({u.url: born_digital for u in units}), deadline=time.monotonic() + 600, workers=2, min_free_bytes=1 << 60)
    record = sync(ctx, FakeListing(units))
    store.close()
    manifest = manifest_of(tmp_path)
    assert record["stopped"].startswith("LowDisk") and sorted(manifest["partitions"]) == ["OT2024"]
    body = render(manifest)
    assert "1 partition is not yet complete: OT2024." in body
    assert f"The listing of {manifest['seen']['at']} links 2 files in 1 partition that no run has reached yet, so it holds no rows: OT2023." in body
    assert EVERY not in body


def test_a_partition_the_listing_gives_no_files_is_not_called_unreached(whole):
    manifest = copy.deepcopy(whole)
    manifest["seen"]["partitions"]["OT2022"] = 0
    body = render(manifest)
    assert "no run has reached" not in body and EVERY in body
    manifest["seen"]["partitions"].update({f"OT19{n:02d}": 2 for n in range(FAILURES_SHOWN + 1)})
    assert f"links {2 * (FAILURES_SHOWN + 1)} files in {FAILURES_SHOWN + 1} partitions that no run has reached yet, so they hold no rows: OT1900, OT1901" in render(manifest)
    assert f"OT19{FAILURES_SHOWN - 1:02d} ...." in render(manifest)


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


def test_the_card_counts_the_code_points_that_do_not_say_what_the_page_shows(built):
    manifest = built.read_manifest()
    assert [entry["codepoints"] for entry in manifest["partitions"].values()] == [{"private_use_rows": 0, "private_use": 0, "replacement_rows": 0, "replacement": 0}] * 2
    assert "No row of `text` holds a Private Use Area code point or U+FFFD." in render(manifest)
    manifest["partitions"]["OT2023"]["codepoints"] = {"private_use_rows": 2, "private_use": 5, "replacement_rows": 0, "replacement": 0}
    assert "2 rows of `text` hold 5 Private Use Area code points, and none holds U+FFFD." in render(manifest)
    manifest["partitions"]["OT2023"]["codepoints"] = {"private_use_rows": 0, "private_use": 0, "replacement_rows": 1, "replacement": 1}
    assert "No row of `text` holds a Private Use Area code point, and 1 row holds 1 U+FFFD character." in render(manifest)
    manifest["partitions"]["OT2023"]["codepoints"] = {"private_use_rows": 2, "private_use": 5, "replacement_rows": 1, "replacement": 1}
    assert "2 rows of `text` hold 5 Private Use Area code points, and 1 row holds 1 U+FFFD character." in render(manifest)
    del manifest["partitions"]["OT2024"]["codepoints"]
    body = render(manifest)
    assert "These characters are not yet counted in 1 partition, whose summary a run wrote before the pipeline counted them." in body and "Private Use Area code points, and" not in body


def test_the_card_says_where_text_is_not_what_the_page_prints(built):
    body = render(built.read_manifest())
    assert "exactly as printed" not in body
    assert "A PDF can declare what a run of its glyphs says (ActualText), and pdftotext prints the declaration in place of the glyphs" in body
    assert "`text` is plain text and marks no type style, such as italics or underlining, and a letter set in small capitals is whatever character the file maps it to" in body
    assert "pypdf reads the same text layer, so agreeing does not show that `text` matches the page" in body


@pytest.mark.parametrize("category", list(CATEGORIES))
def test_a_category_card_has_a_config_only_for_collections_with_rows_and_a_line_for_every_collection(category, built):
    members = [name for name, c in COLLECTIONS.items() if c.category == category]
    stored = dict(built.read_manifest(), collection=members[-1])
    manifests = {members[-1]: stored, **{name: new_manifest(COLLECTIONS[name]) for name in members[:-1]}}
    meta, body = front_matter(render_category(category, manifests))
    assert meta["pretty_name"] == f"Supreme Court of the United States: {CATEGORIES[category][0]}"
    assert meta["configs"] == [{"config_name": members[-1], "data_files": [{"split": "train", "path": f"{members[-1]}/data/*.parquet"}]}]
    for name in members:
        assert body.count(f"| [`{name}`]({name}/README.md) |") == 1
    assert body.count("no run has read the listing yet") == len(members) - 1
    assert f"| 2 | {gigabytes(stored['partitions']['OT2023']['file_bytes'] + stored['partitions']['OT2024']['file_bytes'])} | 2 of 3 |" in body
    assert f'load_dataset("{COLLECTIONS[members[-1]].repo_id}", "{members[-1]}", split="train")' in body
    unknown = any(name in LICENSE for name in members)
    assert meta["license"] == ("unknown" if unknown else "other")
    others = [c for c in CATEGORIES if c != category]
    assert all(f"(https://huggingface.co/datasets/incrediblecrab/scotus-{c})" in body for c in others) and f"datasets/incrediblecrab/scotus-{category})" not in body


def test_a_category_card_before_any_run_has_no_configs():
    manifests = {name: new_manifest(c) for name, c in COLLECTIONS.items() if c.category == "opinions"}
    meta, body = front_matter(render_category("opinions", manifests))
    assert "configs" not in meta and body.count("no run has read the listing yet") == 6
