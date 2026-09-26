# scotus-research-service-products

This repository builds eleven Hugging Face datasets, one for each collection of documents the Supreme Court of the United States publishes on [www.supremecourt.gov](https://www.supremecourt.gov). A row is one file that the collection's pages link: the file's bytes as the server sent them, its text as the extractor printed it, and every listing entry that links it. Each dataset card says how much the dataset holds and which files could not be stored.

| Collection | The Court's page | Hub dataset | Partitions |
|---|---|---|---|
| Opinions of the Court | [opinions/slipopinion](https://www.supremecourt.gov/opinions/slipopinion/) | `incrediblecrab/scotus-opinions-of-the-court` | October Term |
| Opinions Relating to Orders | [opinions/relatingtoorders](https://www.supremecourt.gov/opinions/relatingtoorders/) | `incrediblecrab/scotus-opinions-relating-to-orders` | October Term |
| In-Chambers Opinions | [opinions/in-chambers.aspx](https://www.supremecourt.gov/opinions/in-chambers.aspx) | `incrediblecrab/scotus-in-chambers-opinions` | October Term |
| U. S. Reports | [opinions/USReports.aspx](https://www.supremecourt.gov/opinions/USReports.aspx) | `incrediblecrab/scotus-us-reports` | volumes |
| Argument Transcripts | [oral_arguments/argument_transcript](https://www.supremecourt.gov/oral_arguments/argument_transcript/) | `incrediblecrab/scotus-argument-transcripts` | October Term |
| Calendars and Lists | [oral_arguments/calendarsandlists.aspx](https://www.supremecourt.gov/oral_arguments/calendarsandlists.aspx) | `incrediblecrab/scotus-calendars-and-lists` | October Term |
| Orders of the Court | [orders/ordersofthecourt](https://www.supremecourt.gov/orders/ordersofthecourt/) | `incrediblecrab/scotus-orders-of-the-court` | October Term |
| Orders by Circuit | [orders/ordersbycircuit](https://www.supremecourt.gov/orders/ordersbycircuit/) | `incrediblecrab/scotus-orders-by-circuit` | October Term |
| Granted/Noted Cases List | [orders/grantednotedlists.aspx](https://www.supremecourt.gov/orders/grantednotedlists.aspx) | `incrediblecrab/scotus-granted-noted-cases-list` | one (`all`) |
| Journal | [orders/journal.aspx](https://www.supremecourt.gov/orders/journal.aspx) | `incrediblecrab/scotus-journal` | decade |
| Original Jurisdiction Records & Briefs | [casedocuments/original_jurisdiction_cases.aspx](https://www.supremecourt.gov/casedocuments/original_jurisdiction_cases.aspx) | `incrediblecrab/scotus-original-jurisdiction-records-and-briefs` | case |

None of the Hub datasets is published yet. The build so far is local, in `local-data/`, which Git ignores.

**Objective:** a verbatim record of what the Court published. For legal text, a corrected or tidied copy is a different document, so nothing is corrected, normalized or generated. The pipeline runs no OCR, and no language model touches any text.

- `file` is the file byte for byte, with its SHA-256.
- `text` is the text layer of a born-digital PDF exactly as `pdftotext -raw` prints it, or the text an HTML page renders.
- `ocr_text` holds the text layer of a PDF that has text on a page one image covers at least half of. The pipeline takes such text for OCR by whoever scanned the page, so it is not verbatim, and it never goes in `text`.
- Listing fields keep the page's characters. The only change is that each run of ASCII whitespace becomes one space.

`verify --deep` hashes every stored file again and extracts its text again, and reports any row whose text differs by even one character.

**Inputs:** the collection pages on www.supremecourt.gov and the files they link. Requests to the site are at least 1.1 seconds apart (its robots.txt sets `Crawl-delay: 1`) and carry a user agent that names this repository. The client never follows a redirect and never requests a path that robots.txt disallows.

**Files:**

- [`scotus_products/`](scotus_products/README.md): the pipeline package
- [`tests/`](tests/README.md): offline tests
- `pyproject.toml`: exact dependency versions. Reading PDFs also needs poppler's `pdftotext`, `pdfinfo` and `pdfimages`.
- `LICENSE`: MIT, for the code. The documents carry their own status, which each dataset card states.

**Try it:** with Python 3.14 and poppler installed, run `pip install .`, then:

- `python -m scotus_products list --dataset all` reads every listing page and prints each collection's counts per partition. It fetches no document.
- `python -m scotus_products run --dataset in-chambers-opinions --local /tmp/scotus` builds the smallest collection, which held one file on September 25, 2026.
- `python -m scotus_products verify --dataset in-chambers-opinions --local /tmp/scotus --deep --live` checks that build against its manifest, its files and the listing.
