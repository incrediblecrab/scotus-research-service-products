# scotus_products

The pipeline package, run as `python -m scotus_products {list,run,verify,card,summarize} --dataset NAME`, where NAME is a collection or `all`; each takes `--help`.

**Objective:** keep each collection's Parquet partitions equal to what the Court's pages list, every file as served and its text as the extractor printed it, with one writer at a time.

**Inputs:** the listing pages and files on www.supremecourt.gov, and for `cases.py` the docket and argument audio pages; each collection's `{collection}/manifest.json` in its category's repo, on the Hub or under `--local DIR/scotus-{category}`; poppler's `pdftotext`, `pdfinfo` and `pdfimages`.

**Files:**

- `cli.py`, `__main__.py`: the commands: `list` reads only the listing, `run` syncs within a time budget (`--total-budget-minutes` bounds a `--dataset all` run and keeps `--reserve-minutes` for each collection still to come; on GitHub Actions it names each dataset for Trusted Publishing before writing to it and writes step outputs for the workflow), `verify` checks a dataset, `card` renders its card, and `summarize` recounts partition summaries.
- `sources.py`: each collection's pages, parser, partitions, typed fields, notes, and the footer column (category) whose dataset holds it.
- `markup.py`: an HTML page's rendered text, and links resolved to file ids.
- `extract.py`: `pdftotext -raw`, the scanned-page test, the pypdf cross-check, HTML text, and the checks that an MP3 is audio and an MP4 is video (neither gets text).
- `http.py`: pacing, the robots.txt guard, bounded retries, no redirects.
- `pipeline.py`: the sync loop, failures and retries, delisted files, the writer lease, the free-disk floor.
- `store.py`: the schema, Parquet partitions, and the local and Hub stores, which keep each collection under its own directory of its category's repo.
- `card.py`: the collection's card, rendered from its manifest, and the category's card (the repo's README.md, one config per collection), rendered from all of them.
- `verify.py`: the checks behind `verify`.
- `cases.py`: the builder of scotus-cases, run as `python -m scotus_products.cases {run,verify,card}`. It reads the opinion, transcript and granted/noted list rows of the document datasets and keys them by docket, so that `141-Orig`, `141orig` and `22O141` are all `141, Orig.`; gives each case the pages of its own opinion where the listings link one bound volume or preliminary print of the United States Reports at the page each opinion starts on, ending them before the next linked page or at a page of orders, a Reporter's Note or the index; gives a case that no listing gives an opinion of the Court or in chambers the pages of the Reports whose caption names it, and a consolidated case the opinion whose first pages name it as decided with the lead case; asks for each docket page that is due, at the address the docket's number and format make likelier first and conditionally where it was found before; links each case's MP3 from the argument audio pages; and commits one term at a time, carrying over from the last run every page it could not reach.
