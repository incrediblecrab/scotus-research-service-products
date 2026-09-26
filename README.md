# scotus-research-service-products

This repository builds eleven Hugging Face datasets, one per Supreme Court document collection on [www.supremecourt.gov](https://www.supremecourt.gov): Opinions of the Court, Opinions Relating to Orders, In-Chambers Opinions, U. S. Reports, Argument Transcripts, Calendars and Lists, Orders of the Court, Orders by Circuit, Granted/Noted Cases List, Journal, and Original Jurisdiction Records & Briefs. None is published yet; builds go to `local-data/`, which Git ignores.

**Objective:** a verbatim record of what the Court published. A row is one listed file: its bytes as served, its text as the extractor printed it, and the listing entries that link it. Nothing is corrected, normalized or generated; the pipeline runs no OCR or language model. Each dataset card states the rules and gaps.

**Inputs:** the collection pages and their files, requested at least 1.1 seconds apart, because robots.txt sets `Crawl-delay: 1`, by a user agent naming this repository, never via a redirect or a disallowed path.

**Files:**

- [`scotus_products/`](scotus_products/README.md): the pipeline package
- [`tests/`](tests/README.md): offline tests
- [`pyproject.toml`](pyproject.toml): pinned dependencies; PDFs also need poppler

**Try it:** `pip install .`, then `python -m scotus_products run --dataset in-chambers-opinions --local /tmp/scotus`; `python -m scotus_products verify --local /tmp/scotus --deep --live` also checks the live sources.

## License

MIT. See [`LICENSE`](LICENSE).
