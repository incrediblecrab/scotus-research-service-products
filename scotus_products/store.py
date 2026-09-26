"""Where a dataset lives: a Hugging Face dataset repo, or a local directory for tests and local builds.

The repo holds data/{partition}.parquet, manifest.json (what every partition holds, and where the last listing stood) and README.md (the dataset card, rendered from the manifest). The manifest and the card are staged together and committed with the partitions they describe, so the three cannot disagree on the Hub.

Every Hub commit names its parent (parent_commit). If anything else committed since this store last read or wrote the repo, the Hub refuses the commit and the store raises Superseded instead of overwriting the other writer's work.
"""

import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from huggingface_hub import CommitOperationAdd, HfApi, HfFileSystem, hf_hub_download
from huggingface_hub.errors import EntryNotFoundError, HfHubHTTPError, RemoteEntryNotFoundError

SCHEMA = pa.schema([
    ("id", pa.string()),
    ("partition", pa.string()),
    ("url", pa.string()),
    ("term", pa.int32()),
    ("date", pa.string()),
    ("docket", pa.string()),
    ("title", pa.string()),
    ("entries", pa.large_string()),
    ("listed", pa.bool_()),
    ("media_type", pa.string()),
    ("file", pa.large_binary()),
    ("file_sha256", pa.string()),
    ("file_size", pa.int64()),
    ("etag", pa.string()),
    ("last_modified", pa.string()),
    ("pages", pa.int32()),
    ("image_pages", pa.int32()),
    ("ocr_pages", pa.int32()),
    ("text_source", pa.string()),
    ("text", pa.large_string()),
    ("ocr_text", pa.large_string()),
    ("extractor", pa.string()),
    ("xcheck_extractor", pa.string()),
    ("xcheck_equal", pa.bool_()),
    ("xcheck_equal_nfkd", pa.bool_()),
    ("xcheck_delta", pa.int64()),
    ("metadata", pa.large_string()),
    ("first_seen_at", pa.string()),
    ("fetched_at", pa.string()),
    ("checked_at", pa.string()),
    ("delisted_at", pa.string()),
])
COLUMNS = SCHEMA.names
INT_COLUMNS = ("term", "file_size", "pages", "image_pages", "ocr_pages", "xcheck_delta")
JSON_COLUMNS = ("entries", "metadata")
MANIFEST = "manifest.json"
CARD = "README.md"
# A row group closes once its rows reach this many bytes; one scanned volume can exceed it alone, and then fills a row group by itself.
ROW_GROUP_BYTES = 64 << 20
COMMIT_ATTEMPTS = 4
RETRYABLE = (408, 429, 500, 502, 503, 504)
# Measured September 23, 2026 on a scratch dataset (for the CRS datasets this pipeline is adapted from): a commit whose parent_commit is no longer the branch head answers 412 Precondition Failed.
CONFLICT = 412
log = logging.getLogger("scotus_products")


class Superseded(RuntimeError):
    """Another writer committed to the repo since this store last saw it."""


def partition_path(key):
    return f"data/{key}.parquet"


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def normalize(row):
    out = {name: row.get(name) for name in COLUMNS}
    for name in INT_COLUMNS:
        if out[name] is not None:
            out[name] = int(out[name])
    for name in JSON_COLUMNS:
        if out[name] is not None and not isinstance(out[name], str):
            out[name] = dumps(out[name])
    out["listed"] = bool(out["listed"]) if out["listed"] is not None else None
    return out


def row_weight(row):
    """What a row adds to its row group's size: the stored file and every text column."""
    return sum(len(row[name] or b"") for name in ("file", "text", "ocr_text", "entries", "metadata"))


def write_parquet(rows, path, schema=SCHEMA):
    """Rows sorted by id, zstd, content-defined chunking so a rewritten partition re-uploads only its changed chunks."""
    rows = sorted((normalize(row) for row in rows), key=lambda row: row["id"])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = pq.ParquetWriter(path, schema, compression="zstd", compression_level=9, use_content_defined_chunking=True)
    try:
        batch, size = [], 0
        for row in rows:
            batch.append(row)
            size += row_weight(row)
            if size >= ROW_GROUP_BYTES:
                writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                batch, size = [], 0
        if batch or not rows:
            writer.write_table(pa.Table.from_pylist(batch, schema=schema))
    finally:
        writer.close()
    return {
        "rows": len(rows),
        "listed": sum(1 for row in rows if row["listed"]),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "file_bytes": sum(row["file_size"] or 0 for row in rows),
        "ids": [row["id"] for row in rows],
    }


def read_parquet(path, columns=None):
    return pq.read_table(path, columns=columns).to_pylist()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_blob_sha1(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def dir_bytes(path):
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except FileNotFoundError:
                pass
    return total


class _Staging:
    """Scratch space for one run. Tracks the largest footprint it ever reached, which is the local-disk bound beyond the dataset itself. card, if given, renders README.md from each staged manifest."""

    def __init__(self, workdir=None, card=None):
        self.dir = Path(tempfile.mkdtemp(prefix="scotus-products-", dir=workdir))
        self.card = card
        self.staged = {}
        self.peak_bytes = 0

    def measure(self):
        self.peak_bytes = max(self.peak_bytes, dir_bytes(self.dir))

    def _stage(self, repo_path, text):
        local = self.dir / "stage" / repo_path
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(text)
        self.staged[repo_path] = local
        return local

    def stage_partition(self, key, rows):
        repo_path = partition_path(key)
        local = self.dir / "stage" / repo_path
        stats = write_parquet(rows, local)
        self.staged[repo_path] = local
        self.measure()
        return dict(stats, file=repo_path)

    def stage_manifest(self, manifest):
        text = json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
        self._stage(MANIFEST, text)
        if self.card:
            # From the manifest as stored, not the dict in memory, so a reader who renders the stored manifest gets this card.
            self._stage(CARD, self.card(json.loads(text)))

    def clear(self):
        for local in self.staged.values():
            local.unlink(missing_ok=True)
        self.staged = {}

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class LocalStore(_Staging):
    def __init__(self, root, workdir=None, card=None):
        super().__init__(workdir, card)
        self.root = Path(root)
        self.commits = []

    def read_manifest(self):
        text = self.read_text(MANIFEST)
        return json.loads(text) if text else None

    def read_partition(self, key, columns=None):
        path = self.root / partition_path(key)
        return read_parquet(path, columns) if path.exists() else []

    def iter_rows(self, repo_path, columns=None, batch_size=8):
        """Rows of one partition file a few at a time, so a check over a partition of scanned volumes never holds it all."""
        handle = pq.ParquetFile(self.root / repo_path)
        for batch in handle.iter_batches(batch_size=batch_size, columns=columns):
            yield from batch.to_pylist()

    def read_table(self, repo_path, columns):
        return pq.read_table(self.root / repo_path, columns=columns)

    def read_columns(self, repo_path, columns):
        return self.read_table(repo_path, columns).to_pydict()

    def file_sha256s(self, repo_paths):
        return {repo_path: sha256_file(self.root / repo_path) for repo_path in repo_paths if (self.root / repo_path).exists()}

    def list_files(self, prefix=""):
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*") if p.is_file() and str(p.relative_to(self.root)).startswith(prefix))

    def read_text(self, repo_path):
        path = self.root / repo_path
        return path.read_text() if path.exists() else None

    def commit(self, message):
        if not self.staged:
            return None
        for repo_path, local in self.staged.items():
            target = self.root / repo_path
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                # A rename on the same volume: atomic, and no second copy of a partition of scans on disk.
                os.replace(local, target)
            except OSError:
                partial = target.with_name(target.name + ".partial")
                shutil.copyfile(local, partial)
                os.replace(partial, target)
        self.commits.append({"message": message, "files": sorted(self.staged)})
        self.clear()
        return str(len(self.commits))


class HubStore(_Staging):
    """token=False reads anonymously (the datasets are public)."""

    def __init__(self, repo_id, workdir=None, token=None, card=None, api=None):
        self.repo_id = repo_id
        self.api = api or HfApi(token=token)
        # Before the scratch directory exists, so a Hub that cannot be reached leaves nothing behind.
        info = self.api.dataset_info(repo_id)
        self.revision = info.sha
        super().__init__(workdir, card)
        self.superseded = None

    def _download(self, repo_path):
        try:
            local = hf_hub_download(self.repo_id, repo_path, repo_type="dataset", revision=self.revision, local_dir=self.dir / "download", token=self.api.token)
        except (EntryNotFoundError, RemoteEntryNotFoundError):
            return None
        self.measure()
        return Path(local)

    def read_manifest(self):
        text = self.read_text(MANIFEST)
        return json.loads(text) if text else None

    def read_partition(self, key, columns=None):
        local = self._download(partition_path(key))
        if local is None:
            return []
        try:
            return read_parquet(local, columns)
        finally:
            local.unlink(missing_ok=True)

    def iter_rows(self, repo_path, columns=None, batch_size=8):
        fs = HfFileSystem(token=self.api.token)
        with fs.open(f"datasets/{self.repo_id}@{self.revision}/{repo_path}", "rb") as handle:
            for batch in pq.ParquetFile(handle).iter_batches(batch_size=batch_size, columns=columns):
                yield from batch.to_pylist()

    def read_table(self, repo_path, columns):
        """Reads only the named columns, by HTTP range requests, so verification never downloads the files."""
        fs = HfFileSystem(token=self.api.token)
        with fs.open(f"datasets/{self.repo_id}@{self.revision}/{repo_path}", "rb") as handle:
            return pq.read_table(handle, columns=columns)

    def read_columns(self, repo_path, columns):
        return self.read_table(repo_path, columns).to_pydict()

    def file_sha256s(self, repo_paths):
        out = {}
        paths = list(repo_paths)
        for start in range(0, len(paths), 100):
            for info in self.api.get_paths_info(self.repo_id, paths[start:start + 100], repo_type="dataset", revision=self.revision):
                lfs = getattr(info, "lfs", None)
                if lfs is not None:
                    out[info.path] = lfs.sha256
        return out

    def list_files(self, prefix=""):
        return sorted(path for path in self.api.list_repo_files(self.repo_id, repo_type="dataset", revision=self.revision) if path.startswith(prefix))

    def read_text(self, repo_path):
        local = self._download(repo_path)
        if local is None:
            return None
        try:
            return local.read_text()
        finally:
            local.unlink(missing_ok=True)

    def commit(self, message):
        """One atomic commit of everything staged, on top of the last commit this store saw. Retries rate limits and server errors; a retry that finds its own manifest already at the head counts as landed."""
        if not self.staged:
            return None
        if self.superseded:
            raise self.superseded
        for attempt in range(COMMIT_ATTEMPTS):
            operations = [CommitOperationAdd(path_in_repo=repo_path, path_or_fileobj=str(local)) for repo_path, local in sorted(self.staged.items())]
            try:
                oid = self.api.create_commit(self.repo_id, operations=operations, commit_message=message, repo_type="dataset", parent_commit=self.revision).oid
                break
            except HfHubHTTPError as error:
                status = getattr(error.response, "status_code", None)
                if status == CONFLICT:
                    oid = self._landed()
                    if oid:
                        log.warning("commit %r had already landed as %s", message[:60], oid[:12])
                        break
                    self.superseded = Superseded(f"{self.repo_id} has a commit this run did not write (its last commit was {self.revision[:12]})")
                    raise self.superseded from None
                if attempt == COMMIT_ATTEMPTS - 1 or status not in RETRYABLE:
                    raise
                log.warning("commit attempt %d failed with HTTP %s; retrying", attempt + 1, status)
                time.sleep(60 * (attempt + 1))
        self.revision = oid
        self.clear()
        return oid

    def _landed(self):
        """The head commit if it already holds exactly the manifest staged here, else None."""
        staged = self.staged.get(MANIFEST)
        if staged is None:
            return None
        head = self.api.dataset_info(self.repo_id).sha
        data = staged.read_bytes()
        for info in self.api.get_paths_info(self.repo_id, [MANIFEST], repo_type="dataset", revision=head):
            lfs = getattr(info, "lfs", None)
            same = lfs.sha256 == hashlib.sha256(data).hexdigest() if lfs else getattr(info, "blob_id", None) == git_blob_sha1(data)
            if same:
                return head
        return None
