"""The store: Parquet round trips and row groups, the local commit, and the Hub commit fence against a fake Hub API."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pyarrow.parquet as pq
import pytest
from huggingface_hub.errors import HfHubHTTPError

from scotus_products import store as store_module
from scotus_products.store import COLUMNS, SCHEMA, HubStore, LocalStore, Superseded, git_blob_sha1, read_parquet, sha256_file, write_parquet


def test_rows_round_trip_sorted_with_typed_and_json_columns(tmp_path):
    path = tmp_path / "p.parquet"
    stats = write_parquet([
        {"id": "b.pdf", "term": "2023", "listed": 1, "entries": [{"link_text": "Trump v. Anderson"}], "file": b"%PDF-", "file_size": 5, "text": "–"},
        {"id": "a.pdf", "listed": 0, "metadata": {"pdfinfo": {"Pages": "1"}}},
    ], path)
    assert stats["rows"] == 2 and stats["listed"] == 1 and stats["file_bytes"] == 5 and stats["ids"] == ["a.pdf", "b.pdf"]
    assert stats["sha256"] == sha256_file(path) and stats["bytes"] == path.stat().st_size
    rows = read_parquet(path)
    assert [row["id"] for row in rows] == ["a.pdf", "b.pdf"], "rows are sorted by id"
    assert list(rows[0]) == COLUMNS, "every row has every column, in schema order"
    assert rows[1]["term"] == 2023 and rows[1]["listed"] is True and rows[0]["listed"] is False
    assert rows[1]["entries"] == '[{"link_text": "Trump v. Anderson"}]' and rows[0]["metadata"] == '{"pdfinfo": {"Pages": "1"}}'
    assert rows[1]["file"] == b"%PDF-" and rows[1]["text"] == "–", "bytes and non-ASCII text come back unchanged"
    assert pq.read_schema(path).equals(SCHEMA)


def test_an_empty_partition_is_a_valid_file_with_the_schema(tmp_path):
    stats = write_parquet([], tmp_path / "empty.parquet")
    assert stats["rows"] == 0 and read_parquet(tmp_path / "empty.parquet") == []
    assert pq.read_schema(tmp_path / "empty.parquet").equals(SCHEMA)
    # The datasets library reads a file in batches the size of its first row group, and fails on one of 0 rows.
    assert pq.ParquetFile(tmp_path / "empty.parquet").metadata.num_row_groups == 0


def test_row_groups_close_at_the_byte_limit_and_a_large_row_fills_one_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "ROW_GROUP_BYTES", 100)
    rows = [{"id": f"{n:02d}", "file": b"x" * 40} for n in range(5)] + [{"id": "99", "file": b"y" * 500}]
    write_parquet(rows, tmp_path / "p.parquet")
    handle = pq.ParquetFile(tmp_path / "p.parquet")
    sizes = [handle.metadata.row_group(i).num_rows for i in range(handle.num_row_groups)]
    # 40+40+40 reaches 100 after three rows; 40+40 then the 500-byte row reach it together; nothing is left over.
    assert sizes == [3, 3] and sum(sizes) == 6
    assert [row["id"] for row in read_parquet(tmp_path / "p.parquet")] == ["00", "01", "02", "03", "04", "99"]


def test_the_local_store_commits_what_was_staged_and_renders_the_card_with_the_manifest(tmp_path):
    store = LocalStore(tmp_path / "ds", workdir=tmp_path, card=lambda manifest: f"card for {manifest['n']}\n")
    try:
        assert store.commit("nothing") is None
        stats = store.stage_partition("OT2023", [{"id": "a.pdf", "listed": True}])
        store.stage_manifest({"n": 1})
        assert store.commit("first") == "1"
        assert stats["file"] == "data/OT2023.parquet" and sha256_file(tmp_path / "ds" / "data" / "OT2023.parquet") == stats["sha256"]
        assert store.read_manifest() == {"n": 1} and store.read_text("README.md") == "card for 1\n"
        assert store.list_files() == ["README.md", "data/OT2023.parquet", "manifest.json"] and store.list_files("data/") == ["data/OT2023.parquet"]
        assert [row["id"] for row in store.read_partition("OT2023")] == ["a.pdf"] and store.read_partition("OT1999") == []
        assert store.staged == {} and store.commits == [{"message": "first", "files": ["README.md", "data/OT2023.parquet", "manifest.json"]}]
        assert list(store.file_sha256s(["data/OT2023.parquet", "data/missing.parquet"])) == ["data/OT2023.parquet"]
    finally:
        store.close()
    assert not store.dir.exists(), "close removes the scratch directory"


def test_collections_share_a_repo_under_their_prefixes_and_the_root_card_renders_from_all_their_manifests(tmp_path):
    def root_card(manifests):
        return "root: " + ", ".join(f"{name}={manifest['n']}" for name, manifest in sorted(manifests.items())) + "\n"

    for name, n in (("b", 1), ("a", 2), ("b", 3)):
        store = LocalStore(tmp_path / "repo", workdir=tmp_path, card=lambda manifest: f"card for {manifest['n']}\n", prefix=f"{name}/", root_card=root_card)
        try:
            stats = store.stage_partition("OT2023", [{"id": f"{name}.pdf", "listed": True}])
            store.stage_manifest({"collection": name, "n": n})
            assert store.commit(f"{name} {n}") == "1"
            assert stats["file"] == "data/OT2023.parquet", "the manifest names paths within the collection"
            assert store.list_files() == ["README.md", "data/OT2023.parquet", "manifest.json"] and store.list_files("data/") == ["data/OT2023.parquet"]
            assert store.read_manifest() == {"collection": name, "n": n} and store.read_text("README.md") == f"card for {n}\n"
            assert [row["id"] for row in store.read_partition("OT2023")] == [f"{name}.pdf"] and list(store.file_sha256s(["data/OT2023.parquet"])) == ["data/OT2023.parquet"]
        finally:
            store.close()
    assert (tmp_path / "repo" / "README.md").read_text() == "root: a=2, b=3\n"
    assert sorted(str(p.relative_to(tmp_path / "repo")) for p in (tmp_path / "repo").rglob("*") if p.is_file()) == [
        "README.md", "a/README.md", "a/data/OT2023.parquet", "a/manifest.json", "b/README.md", "b/data/OT2023.parquet", "b/manifest.json"]


def test_a_root_card_needs_a_prefix(tmp_path):
    with pytest.raises(ValueError, match="prefix"):
        LocalStore(tmp_path / "repo", workdir=tmp_path, root_card=lambda manifests: "")


def test_the_local_commit_copies_when_a_rename_cannot_cross_volumes(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "ds", workdir=tmp_path)
    real_replace = os.replace

    def no_cross_device(source, target):
        if str(source).startswith(str(store.dir)):
            raise OSError(18, "Invalid cross-device link")
        real_replace(source, target)

    monkeypatch.setattr(store_module.os, "replace", no_cross_device)
    try:
        store.stage_partition("OT2023", [{"id": "a.pdf"}])
        store.commit("first")
        assert [row["id"] for row in store.read_partition("OT2023")] == ["a.pdf"]
        assert not list((tmp_path / "ds" / "data").glob("*.partial"))
    finally:
        store.close()


def test_iter_rows_reads_a_partition_a_few_rows_at_a_time(tmp_path):
    write_parquet([{"id": f"{n:02d}"} for n in range(20)], tmp_path / "ds" / "data" / "OT2023.parquet")
    store = LocalStore(tmp_path / "ds", workdir=tmp_path)
    try:
        assert [row["id"] for row in store.iter_rows("data/OT2023.parquet", ["id"], batch_size=3)] == [f"{n:02d}" for n in range(20)]
    finally:
        store.close()


def http_error(status):
    response = httpx.Response(status, request=httpx.Request("POST", "https://huggingface.co/api/datasets/x/y/commit/main"))
    return HfHubHTTPError(f"{status} from the fake Hub", response=response)


class FakeApi:
    """The parts of HfApi that HubStore.commit uses. A commit on a parent that is not the head answers 412, as the Hub did when measured for the CRS datasets."""

    token = False

    def __init__(self):
        self.head, self.files, self.commits, self.attempts = "c0", {}, [], 0
        self.lose_next_response = False

    def dataset_info(self, repo_id):
        return SimpleNamespace(sha=self.head)

    def create_commit(self, repo_id, operations, commit_message, repo_type, parent_commit):
        self.attempts += 1
        if parent_commit != self.head:
            raise http_error(412)
        for operation in operations:
            self.files[operation.path_in_repo] = Path(operation.path_or_fileobj).read_bytes()
        self.commits.append(commit_message)
        self.head = f"c{len(self.commits)}"
        if self.lose_next_response:
            self.lose_next_response = False
            raise http_error(502)
        return SimpleNamespace(oid=self.head)

    def get_paths_info(self, repo_id, paths, repo_type, revision):
        return [SimpleNamespace(path=path, blob_id=git_blob_sha1(self.files[path]), lfs=None) for path in paths if path in self.files]


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module.time, "sleep", lambda seconds: None)
    api = FakeApi()
    store = HubStore("x/y", workdir=tmp_path, api=api, card=lambda manifest: f"card for {manifest}")
    yield store, api
    store.close()


class UnreachableApi(FakeApi):
    def dataset_info(self, repo_id):
        raise httpx.ConnectError("planted: the Hub cannot be reached")


def test_a_hub_that_cannot_be_reached_leaves_no_scratch_directory(tmp_path):
    with pytest.raises(httpx.ConnectError):
        HubStore("x/y", workdir=tmp_path, api=UnreachableApi())
    assert list(tmp_path.iterdir()) == []


def test_a_commit_on_a_stale_parent_is_superseded_and_the_store_writes_nothing_more(hub):
    store, api = hub
    store.stage_manifest({"n": 1})
    assert store.commit("first") == "c1" and set(api.files) == {"manifest.json", "README.md"}
    api.head = "someone-else"
    store.stage_manifest({"n": 2})
    with pytest.raises(Superseded):
        store.commit("second")
    attempts = api.attempts
    store.stage_manifest({"n": 3})
    with pytest.raises(Superseded):
        store.commit("third")
    assert api.attempts == attempts, "a superseded store does not ask the Hub again"
    assert api.commits == ["first"]


def test_a_retried_commit_that_had_landed_is_adopted_not_superseded(hub):
    store, api = hub
    api.lose_next_response = True
    store.stage_manifest({"n": 1})
    assert store.commit("first") == "c1"
    assert api.commits == ["first"] and store.revision == "c1" and store.superseded is None
    store.stage_manifest({"n": 2})
    assert store.commit("second") == "c2"


def test_a_412_whose_head_holds_a_different_manifest_is_superseded(hub):
    store, api = hub
    store.stage_manifest({"n": 1})
    real_create = api.create_commit

    def other_writer_first(*args, **kwargs):
        # Another writer's manifest lands between this store's attempts.
        api.create_commit = real_create
        api.files["manifest.json"] = b'{"n": "theirs"}\n'
        api.head = "theirs"
        raise http_error(502)

    api.create_commit = other_writer_first
    with pytest.raises(Superseded):
        store.commit("first")


def test_an_error_that_is_not_retryable_is_raised_at_once(hub):
    store, api = hub

    def forbidden(*args, **kwargs):
        api.attempts += 1
        raise http_error(403)

    api.create_commit = forbidden
    store.stage_manifest({"n": 1})
    with pytest.raises(HfHubHTTPError):
        store.commit("first")
    assert api.attempts == 1


def test_a_readme_check_answered_with_a_busy_page_is_retried(hub):
    """huggingface_hub parses the validate-yaml body before it checks the status, so a busy Hub raises JSONDecodeError before anything is uploaded; the Fed scheduled run of September 27, 2026 stopped on one."""
    store, api = hub
    real_create = api.create_commit

    def busy_first(*args, **kwargs):
        api.create_commit = real_create
        raise json.JSONDecodeError("Expecting value", "", 0)

    api.create_commit = busy_first
    store.stage_manifest({"n": 1})
    assert store.commit("first") == "c1" and api.commits == ["first"]


def test_a_dropped_connection_whose_commit_landed_is_adopted(hub):
    store, api = hub
    real_create = api.create_commit

    def lands_then_drops(*args, **kwargs):
        api.create_commit = real_create
        real_create(*args, **kwargs)
        raise httpx.ReadTimeout("planted: the response never arrived")

    api.create_commit = lands_then_drops
    store.stage_manifest({"n": 1})
    assert store.commit("first") == "c1"
    assert api.commits == ["first"] and store.superseded is None, "the retry adopted the landed commit rather than committing twice"


def test_invalid_card_metadata_is_raised_at_once(hub):
    store, api = hub

    def invalid(*args, **kwargs):
        api.attempts += 1
        raise ValueError("Invalid metadata in README.md.")

    api.create_commit = invalid
    store.stage_manifest({"n": 1})
    with pytest.raises(ValueError, match="Invalid metadata"):
        store.commit("first")
    assert api.attempts == 1, "JSONDecodeError is a ValueError, but a card the Hub rejects is not retried"


def test_git_blob_sha1_matches_git():
    assert git_blob_sha1(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    assert git_blob_sha1(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_a_hub_store_commits_under_its_prefix_and_adopts_its_own_landed_commit_there(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module.time, "sleep", lambda seconds: None)
    api = FakeApi()
    store = HubStore("x/y", workdir=tmp_path, api=api, card=lambda manifest: "card\n", prefix="c/")
    try:
        store.stage_partition("OT2023", [{"id": "a.pdf"}])
        store.stage_manifest({"collection": "c"})
        api.lose_next_response = True
        assert store.commit("first") == "c1" and api.commits == ["first"]
        assert sorted(api.files) == ["c/README.md", "c/data/OT2023.parquet", "c/manifest.json"]
    finally:
        store.close()
