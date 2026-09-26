# tests

Offline tests: `pip install '.[test]'`, then `python -m pytest -q`. They use no network, but the PDF tests run poppler's `pdftotext`, `pdfinfo` and `pdfimages`, so poppler must be installed.

**Objective:** pin down the behavior the verbatim record depends on. Each load-bearing check was tested by planting the defect it should catch in a copy of the code and running the suite (last run September 26, 2026). Unchanged, the copy passed all 154 tests, and every planted defect failed at least one:

| Planted defect | Tests failed | One of them |
|---|---:|---|
| `pdftotext` without `-raw` | 7 | `test_raw_mode_keeps_hyphens_that_end_a_line` |
| No page counted as an image page (`IMAGE_SHARE = 2`) | 6 | `test_ocr_layer_of_a_scan_goes_to_ocr_text_only` |
| Listing text joined with added spaces | 3 | `test_field_joins_inline_text_without_adding_spaces` |
| No-break spaces collapsed like ASCII whitespace | 4 | `test_squash_collapses_only_ascii_whitespace` |
| The emptied-page check disabled | 1 | `test_a_listing_page_that_empties_stops_the_run` |
| Failed files no longer holding a partition open | 2 | `test_a_missing_file_is_recorded_as_failed_and_retried` |
| `verify` not comparing re-extracted text | 2 | `test_text_that_is_not_the_extractor_output_is_caught_by_the_deep_check_only` |
| `verify` not checking which of `text` and `ocr_text` is set | 2 | `test_ocr_in_the_text_column_is_caught` |
| The robots.txt guard disabled | 1 | `test_robots_disallowed_paths_are_never_requested` |
| Pacing interval 0 | 1 | `test_the_default_pace_is_above_the_crawl_delay` |
| Pacing that never waits | 2 | `test_requests_to_a_host_are_paced` |
| Redirects followed | 1 | `test_requests_identify_the_pipeline_and_redirects_are_not_followed` |
| `--revalidate-all` ignored when choosing the files to ask about | 3 | `test_revalidate_all_asks_about_every_stored_file_of_any_collection` |
| `--revalidate-all` ignored when choosing the partitions to open | 3 | `test_a_stored_file_that_cannot_be_fetched_again_keeps_its_row` |
| The card listing a stored file among the files not stored | 1 | `test_a_stored_file_that_failed_again_is_not_called_unstored` |
| The card giving every collection the sentence for mutable ones | 1 | `test_the_card_says_which_stored_files_are_asked_about` |
| The last listing's per-partition file counts not kept | 3 | `test_the_card_names_the_partitions_a_stopped_run_never_reached` |
| The card claiming every listed file whatever the gaps | 3 | `test_the_card_claims_every_file_only_when_no_gap_shows` |
| The card never naming the partitions no run has reached | 3 | `test_the_card_names_the_partitions_a_stopped_run_never_reached` |
| The card calling a partition with no listed files unreached | 1 | `test_a_partition_the_listing_gives_no_files_is_not_called_unreached` |

**Inputs:** real pages and PDFs from www.supremecourt.gov in [`fixtures/`](fixtures/README.md), and a stand-in site and listing in `conftest.py`.

**Files:**

- `conftest.py`: fixture loaders, a stand-in site that serves bytes by URL (404 otherwise) and counts every request, a stand-in listing, and a check around every test that fails it when a store is left open (an open store leaves its empty staging directory in the temporary directory).
- `test_sources.py`: each collection's parser on a saved page: entry, file and partition counts, and every field a substring of the text the page renders.
- `test_markup.py`: rendered text, whitespace, and link resolution.
- `test_extract.py`: raw text unchanged, hyphens that end a line kept, one form feed per page, scanned or born-digital, and the pypdf cross-check.
- `test_http.py`: pacing, robots.txt, https only, no redirects, and 403, 429 and server errors.
- `test_pipeline.py`: the sync loop: a first run, an idle run, files dropped from and restored to the listing, changed entries without a refetch, failures and retries, the emptied-page check, mutable files, `--revalidate-all`, a stored file that cannot be fetched again, budget stops, the disk floor and the writer lease.
- `test_store.py`: Parquet round trips and row groups, the local commit, and the Hub commit fence against a fake Hub.
- `test_verify.py`: each planted data defect is named.
- `test_card.py`: the card's front matter and license lines, its gaps (a stored file that failed again is not called unstored, and the partitions a stopped run never reached are named), a first sentence that claims every listed file only when no gap shows, which stored files each run asks about, and its independence from key order.
- `test_cli.py`: exit codes, the card check, and `--dataset all`.
