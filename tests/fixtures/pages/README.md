# pages

Pages from www.supremecourt.gov, saved September 25, 2026: gzipped response bodies, each named for its path with `/`, `.` and `-` written as `_`. `test_sources.py` pins each listing page's entry, file and partition counts.

Every page loads Akamai's boomerang script from `s.go-mpulse.net` with the site's beacon key, `window.BOOMR_API_key`, a public value served to every visitor. gitleaks 8.30.1, opening archives (`--max-archive-depth 1`), reports it as a generic API key: 16 findings in 15 files. It is no credential of this project; the pages stay as served. The Orders by Circuit document carries values that change per request, so a fresh copy's bytes may differ, as `sources.py` notes.

| File | Page |
|---|---|
| `casedocuments_original_jurisdiction_cases_aspx.html.gz` | https://www.supremecourt.gov/casedocuments/original_jurisdiction_cases.aspx |
| `opinions_in_chambers_aspx.html.gz` | https://www.supremecourt.gov/opinions/in-chambers.aspx |
| `opinions_relatingtoorders_25.html.gz` | https://www.supremecourt.gov/opinions/relatingtoorders/25 |
| `opinions_slipopinion_18.html.gz` | https://www.supremecourt.gov/opinions/slipopinion/18 |
| `opinions_USReports_aspx.html.gz` | https://www.supremecourt.gov/opinions/USReports.aspx |
| `oral_arguments_archived_transcripts_1968.html.gz` | https://www.supremecourt.gov/oral_arguments/archived_transcripts/1968 |
| `oral_arguments_argument_transcript_2009.html.gz` | https://www.supremecourt.gov/oral_arguments/argument_transcript/2009 |
| `oral_arguments_calendarsandlists_aspx.html.gz` | https://www.supremecourt.gov/oral_arguments/calendarsandlists.aspx |
| `oral_arguments_earlierdaycalls_aspx.html.gz` | https://www.supremecourt.gov/oral_arguments/earlierdaycalls.aspx |
| `orders_grantednotedlists_aspx.html.gz` | https://www.supremecourt.gov/orders/grantednotedlists.aspx |
| `orders_journal_aspx.html.gz` | https://www.supremecourt.gov/orders/journal.aspx |
| `orders_ordersbycircuit_09.html.gz` | https://www.supremecourt.gov/orders/ordersbycircuit/09 |
| `orders_ordersbycircuit_ordercasebycircuit_090426OrderCasesByCircuit.html.gz` | https://www.supremecourt.gov/orders/ordersbycircuit/ordercasebycircuit/090426OrderCasesByCircuit, an Orders by Circuit document rather than a listing page |
| `orders_ordersofthecourt_25.html.gz` | https://www.supremecourt.gov/orders/ordersofthecourt/25 |
| `orders_scannedjournals_aspx.html.gz` | https://www.supremecourt.gov/orders/scannedjournals.aspx |
