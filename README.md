# scotus-research-service-products

This repository builds Hugging Face datasets of the eleven Supreme Court document collections on [www.supremecourt.gov](https://www.supremecourt.gov), one dataset per column of the site's footer and one config per collection: [scotus-opinions](https://huggingface.co/datasets/incrediblecrab/scotus-opinions) (Opinions of the Court, Opinions Relating to Orders, In-Chambers Opinions, U. S. Reports), [scotus-oral-arguments](https://huggingface.co/datasets/incrediblecrab/scotus-oral-arguments) (Argument Transcripts, Calendars and Lists) and [scotus-case-documents](https://huggingface.co/datasets/incrediblecrab/scotus-case-documents) (Orders of the Court, Orders by Circuit, Granted/Noted Cases List, Journal, Original Jurisdiction Records & Briefs). Local builds go to `local-data/`, which Git ignores.

**Objective:** a verbatim record of what the Court published. A row is one listed file: its bytes as served, its text as the extractor printed it, and the listing entries that link it. Nothing is corrected, normalized or generated; the pipeline runs no OCR or language model. Each dataset card states the rules and gaps.

**Inputs:** the collection pages and their files, requested at least 1.1 seconds apart, because robots.txt sets `Crawl-delay: 1`, by a user agent naming this repository, never via a redirect or a disallowed path.

**Files:**

- [`scotus_products/`](scotus_products/README.md): the pipeline package
- [`tests/`](tests/README.md): offline tests
- [`pyproject.toml`](pyproject.toml): pinned dependencies; PDFs also need poppler

**Try it:** `pip install .`, then `python -m scotus_products run --dataset in-chambers-opinions --local /tmp/scotus`, which writes `/tmp/scotus/scotus-opinions/in-chambers-opinions/` as the Hub holds it; `python -m scotus_products verify --dataset in-chambers-opinions --local /tmp/scotus --deep --live` also checks the live sources.

## License

MIT. See [`LICENSE`](LICENSE).
