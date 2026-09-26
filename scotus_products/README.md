# scotus_products

The pipeline package, run as `python -m scotus_products {list,run,verify,card} --dataset NAME`, where NAME is a collection name or `all`.

**Objective:** keep each collection's Parquet partitions equal to what the Court's pages list, with every file stored as the server sent it and its text as the extractor printed it, and with one writer at a time.

**Inputs:** the listing pages and files on www.supremecourt.gov; each dataset's `manifest.json`, on the Hub or under `--local DIR`; poppler's `pdftotext`, `pdfinfo` and `pdfimages`.

**Files:**

- `cli.py`, `__main__.py`: the commands. `list` reads the listing pages only and prints what they hold. `run` syncs a collection within a time budget. `verify` checks a dataset against its manifest: `--deep` also hashes every file and extracts its text again, `--live` compares the dataset with the listing now, and `--redownload N` fetches N files again and compares them. `card` renders the dataset card.
- `sources.py`: the eleven collections: which pages list each one, how a page becomes entries and files, how files are partitioned and typed, and each collection's notes: quotations of the Court's pages, and counts dated to the day they were taken.
- `markup.py`: the text an HTML page renders, character for character, and the resolution of links to file ids and URLs.
- `extract.py`: PDF text with `pdftotext -raw`, the scanned-page test with `pdfimages -list`, and the cross-check against pypdf. For an HTML document, its rendered text.
- `http.py`: pacing per host, the robots.txt guard, bounded retries, and no redirects.
- `pipeline.py`: the sync loop. It covers the check for emptied listing pages, fetches, failures and retries, files the Court stops listing, re-validation of recent files in the collections marked mutable (calendars and lists, the granted/noted list, the journal) and of every stored file under `--revalidate-all`, the writer lease and the free-disk floor.
- `store.py`: the schema, Parquet partitions, the local store, and the Hub store with its parent-commit fence.
- `card.py`: the dataset card, rendered from the manifest alone.
- `verify.py`: the checks behind `verify`.
