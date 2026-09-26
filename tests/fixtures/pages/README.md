# pages

Pages from www.supremecourt.gov, saved on September 25, 2026: each file is a response body, gzipped. A file's name is the page's path with `/`, `.` and `-` written as `_`. `test_sources.py` counts the entries, files and partitions it expects on each listing page.

Every page loads Akamai's boomerang script from `s.go-mpulse.net` and carries the site's beacon key in `window.BOOMR_API_key`, one value served to every visitor. gitleaks 8.30.1 reports it as a generic API key (16 findings in these 15 files on September 25, 2026). It is part of each page as the server sent it, not a credential of this project, and the files are kept as served.

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
