# tests

Offline tests: `pip install '.[test]'`, then `python -m pytest -q`. They use no network; the PDF tests need poppler.

**Objective:** pin down the behavior the verbatim record depends on. Each load-bearing check was tested by planting the defect it should catch in a copy of the code (33 defects, last run September 26, 2026): unchanged, the copy passed all 159 tests, and every planted defect failed at least one. On September 27, 2026, five more were planted in the category layout (the store's prefix, the path a landed commit is looked for at, configs for collections without rows, the category-card check in `verify`, and a category card rendered without the staged manifest), and each failed at least one of the 166 tests. Later that day four more were planted in the Actions path of `run` (keeping the first dataset's Trusted Publishing resource, setting it off Actions, one reserve too many, and never reporting a collection as unfinished), and each failed `test_cli.py`; the suite then had 167 tests. Later that day eight were planted in the News Media, Filing & Rules and Argument Audio collections (lower-casing the MP3 address, following the eLearning tutorials, taking every Year-End Report for a PDF, choosing the extractor by the address's suffix, comparing an HTML page in a PDF collection by its bytes, accepting only three MP3 frame headers, reading a speech's year into `term`, and ignoring a written-out Date Posted), and each failed at least one test; the suite now has 223 tests.

**Inputs:** real pages and PDFs in [`fixtures/`](fixtures/README.md), and a stand-in site and listing.

**Files:**

- `conftest.py`: fixture loaders, the stand-in site and listing, and a check that no test leaves a store open.
- `test_sources.py`: each parser on a saved page: counts, and every field in the page's rendered text; for Argument Audio, Year-End Reports, filing pages and news, the file's address, its document type and the typed columns.
- `test_markup.py`: rendered text, whitespace, links.
- `test_extract.py`: raw text unchanged, scanned or born-digital, the pypdf cross-check, and what is taken for MP3 audio.
- `test_http.py`: pacing, robots.txt, redirects, error statuses.
- `test_pipeline.py`: the sync loop: failures and retries, delisting, the emptied-page check, `--revalidate-all`, budget and disk stops, the writer lease, and listings that mix PDFs, HTML pages and audio.
- `test_store.py`: Parquet round trips, both stores' commits, and collections sharing a repo under their prefixes.
- `test_verify.py`: each planted data defect is named.
- `test_card.py`: front matter, licenses, every gap named, and the category card's configs.
- `test_cli.py`: exit codes, the card check, `--dataset all`, and on Actions: the Trusted Publishing resource per dataset, the reserve per collection still to come, and the step outputs.
