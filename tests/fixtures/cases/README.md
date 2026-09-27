# cases fixtures

Docket and argument audio pages from www.supremecourt.gov, saved September 27, 2026, as served, for `test_cases.py`. `dk_23-1197.html` is the page as a build of scotus-cases stored it in its `docket_html` column; a docket page's bytes change whenever the Court adds an entry, so a fresh copy may differ.

Every page but the not-found page loads Akamai's boomerang script with the site's beacon key, `window.BOOMR_API_key`, a public value served to every visitor. gitleaks 8.30.1 reports it as a generic API key: 8 findings in 7 files (`docketfiles_05-1.htm` holds two). It is no credential of this project; the pages stay as served.

| File | Page | What it covers |
|---|---|---|
| `dk_25-1.html` | https://www.supremecourt.gov/docket/docketfiles/html/public/25-1.html | the current docket format |
| `dk_23-1197.html` | https://www.supremecourt.gov/docket/docketfiles/html/public/23-1197.html | a granted case: the Questions Presented link, and entries with several documents |
| `dk_25A1.html` | https://www.supremecourt.gov/docket/docketfiles/html/public/25A1.html | an application |
| `dk_22O141.html` | https://www.supremecourt.gov/docket/docketfiles/html/public/22O141.html | an original case, No. 141, Orig. |
| `docketfiles_05-1.htm` | https://www.supremecourt.gov/docketfiles/05-1.htm | the old docket format |
| `dk_16-1.html` | https://www.supremecourt.gov/docket/docketfiles/html/public/16-1.html | the site's not-found page, served with status 404 |
| `oral_arguments_argument_audio_2024.html` | https://www.supremecourt.gov/oral_arguments/argument_audio/2024 | a term's argument audio listing |
| `audio_case.html` | https://www.supremecourt.gov/oral_arguments/audio/2024/22-7466 | a case's argument audio page, which names its MP3 |
