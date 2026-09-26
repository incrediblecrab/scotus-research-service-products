# tests

Offline tests: `pip install '.[test]'`, then `python -m pytest -q`. They use no network; the PDF tests need poppler.

**Objective:** pin down the behavior the verbatim record depends on. Each load-bearing check was tested by planting the defect it should catch in a copy of the code (33 defects, last run September 26, 2026): unchanged, the copy passed all 159 tests, and every planted defect failed at least one.

**Inputs:** real pages and PDFs in [`fixtures/`](fixtures/README.md), and a stand-in site and listing.

**Files:**

- `conftest.py`: fixture loaders, the stand-in site and listing, and a check that no test leaves a store open.
- `test_sources.py`: each parser on a saved page: counts, and every field in the page's rendered text.
- `test_markup.py`: rendered text, whitespace, links.
- `test_extract.py`: raw text unchanged, scanned or born-digital, the pypdf cross-check.
- `test_http.py`: pacing, robots.txt, redirects, error statuses.
- `test_pipeline.py`: the sync loop: failures and retries, delisting, the emptied-page check, `--revalidate-all`, budget and disk stops, the writer lease.
- `test_store.py`: Parquet round trips and both stores' commits.
- `test_verify.py`: each planted data defect is named.
- `test_card.py`: front matter, licenses, every gap named.
- `test_cli.py`: exit codes, the card check, `--dataset all`.
