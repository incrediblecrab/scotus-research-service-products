# scotus_products

The pipeline package, run as `python -m scotus_products {list,run,verify,card,summarize} --dataset NAME`, where NAME is a collection or `all`; each takes `--help`.

**Objective:** keep each collection's Parquet partitions equal to what the Court's pages list, every file as served and its text as the extractor printed it, with one writer at a time.

**Inputs:** the listing pages and files on www.supremecourt.gov; each dataset's `manifest.json`, on the Hub or under `--local DIR`; poppler's `pdftotext`, `pdfinfo` and `pdfimages`.

**Files:**

- `cli.py`, `__main__.py`: the commands: `list` reads only the listing, `run` syncs within a time budget, `verify` checks a dataset, `card` renders its card, and `summarize` recounts partition summaries.
- `sources.py`: each collection's pages, parser, partitions, typed fields and notes.
- `markup.py`: an HTML page's rendered text, and links resolved to file ids.
- `extract.py`: `pdftotext -raw`, the scanned-page test, the pypdf cross-check, and HTML text.
- `http.py`: pacing, the robots.txt guard, bounded retries, no redirects.
- `pipeline.py`: the sync loop, failures and retries, delisted files, the writer lease, the free-disk floor.
- `store.py`: the schema, Parquet partitions, and the local and Hub stores.
- `card.py`: the dataset card, rendered from the manifest.
- `verify.py`: the checks behind `verify`.
