# workflows

**Objective:** keep every dataset current with no personal device and no person, given a commit every 60 days. The workflow is scheduled at 00:00 and 12:00 UTC, and GitHub starts some runs late, often by hours, or drops them.

**Inputs:** the Court's pages and files, the document datasets as the Hub holds them, and the job's OIDC token, which Hugging Face exchanges for a short-lived write token scoped to one dataset when that dataset lists this repository, branch `main` and `pipeline.yml` as a Trusted Publisher; otherwise the write fails. No secret is used.

**Files:**

- [`pipeline.yml`](pipeline.yml): `cases` runs `python -m scotus_products.cases run` within 60 minutes, building scotus-cases from the document datasets and the docket pages due for a check, then its `verify`; it runs before `sync`, so the two never ask the Court for pages at once, and `sync` runs whether it succeeded or not. `sync` runs `python -m scotus_products run --dataset all` within 290 minutes, keeping 10 minutes for each collection still to come, then `verify` checks each collection the run changed. `continue` starts the next run when `sync` ran out of budget while files remained, or `cases` ran out of budget, and that job fetched something. `inactivity` fails after 50 days without a commit, before GitHub disables every schedule here at 60. It makes no keepalive commit: GitHub [called](https://github.com/ddev/github-action-add-on-test/issues/46) that a Terms violation.

GitHub reports a failed scheduled run to whoever last changed its cron, by email if their settings allow. A dispatch (**Run workflow**) takes a collection, a budget, a budget for `cases` (0 skips it) and extra run arguments; with extra arguments it runs only a bounded sync test, skips verify and `cases`, and never starts another run. `.github/` has no README, which GitHub would show instead of the repository's.
