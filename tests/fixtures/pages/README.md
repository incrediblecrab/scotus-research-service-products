# pages

Pages from www.supremecourt.gov, saved September 25 and September 27, 2026: gzipped response bodies, each named for its path with `/`, `.` and `-` written as `_`. `test_sources.py` pins each listing page's entry, file and partition counts.

Every page loads Akamai's boomerang script from `s.go-mpulse.net` with the site's beacon key, `window.BOOMR_API_key`, a public value served to every visitor. gitleaks 8.30.1, opening archives (`--max-archive-depth 1`), reports it as a generic API key: 59 findings in 58 files (the Orders by Circuit document holds two). It is no credential of this project; the pages stay as served. The Orders by Circuit document carries values that change per request, so a fresh copy's bytes may differ, as `sources.py` notes.

| File | Page |
|---|---|
| `about_buildingregulations_aspx.html.gz` | https://www.supremecourt.gov/about/buildingregulations.aspx |
| `about_code_of_conduct_for_justices_aspx.html.gz` | https://www.supremecourt.gov/about/code-of-conduct-for-justices.aspx |
| `about_courtatwork_aspx.html.gz` | https://www.supremecourt.gov/about/courtatwork.aspx |
| `about_courtbuilding_aspx.html.gz` | https://www.supremecourt.gov/about/courtbuilding.aspx |
| `about_faq_aspx.html.gz` | https://www.supremecourt.gov/about/faq.aspx |
| `about_faq_documents_aspx.html.gz` | https://www.supremecourt.gov/about/faq_documents.aspx |
| `about_faq_general_aspx.html.gz` | https://www.supremecourt.gov/about/faq_general.aspx |
| `about_faq_justices_aspx.html.gz` | https://www.supremecourt.gov/about/faq_justices.aspx |
| `about_historyandtraditions_aspx.html.gz` | https://www.supremecourt.gov/about/historyandtraditions.aspx |
| `about_justices_aspx.html.gz` | https://www.supremecourt.gov/about/justices.aspx |
| `casedocuments_original_jurisdiction_cases_aspx.html.gz` | https://www.supremecourt.gov/casedocuments/original_jurisdiction_cases.aspx |
| `ctrules_scannedrules_aspx.html.gz` | https://www.supremecourt.gov/ctrules/scannedrules.aspx |
| `filingandrules_electronicfiling_aspx.html.gz` | https://www.supremecourt.gov/filingandrules/electronicfiling.aspx |
| `filingandrules_rules_guidance_aspx.html.gz` | https://www.supremecourt.gov/filingandrules/rules_guidance.aspx |
| `filingandrules_supremecourtbar_aspx.html.gz` | https://www.supremecourt.gov/filingandrules/supremecourtbar.aspx |
| `media_media_aspx.html.gz` | https://www.supremecourt.gov/media/media.aspx |
| `opinions_cited_urls_05.html.gz` through `opinions_cited_urls_25.html.gz` | https://www.supremecourt.gov/opinions/cited_urls/05 through https://www.supremecourt.gov/opinions/cited_urls/25 |
| `opinions_in_chambers_aspx.html.gz` | https://www.supremecourt.gov/opinions/in-chambers.aspx |
| `opinions_relatingtoorders_25.html.gz` | https://www.supremecourt.gov/opinions/relatingtoorders/25 |
| `opinions_slipopinion_18.html.gz` | https://www.supremecourt.gov/opinions/slipopinion/18 |
| `opinions_USReports_aspx.html.gz` | https://www.supremecourt.gov/opinions/USReports.aspx |
| `oral_arguments_archived_transcripts_1968.html.gz` | https://www.supremecourt.gov/oral_arguments/archived_transcripts/1968 |
| `oral_arguments_argument_audio_2017.html.gz` | https://www.supremecourt.gov/oral_arguments/argument_audio/2017 as the origin answered it at `?reread=1` on September 27, 2026, when the CDN answered the plain address with its copy of the October Term 2025 page (see `sources.reread_url`) |
| `oral_arguments_argument_audio_2025.html.gz` | https://www.supremecourt.gov/oral_arguments/argument_audio/2025 |
| `oral_arguments_argument_transcript_2009.html.gz` | https://www.supremecourt.gov/oral_arguments/argument_transcript/2009 |
| `oral_arguments_calendarsandlists_aspx.html.gz` | https://www.supremecourt.gov/oral_arguments/calendarsandlists.aspx |
| `oral_arguments_earlierdaycalls_aspx.html.gz` | https://www.supremecourt.gov/oral_arguments/earlierdaycalls.aspx |
| `orders_grantednotedlists_aspx.html.gz` | https://www.supremecourt.gov/orders/grantednotedlists.aspx |
| `orders_journal_aspx.html.gz` | https://www.supremecourt.gov/orders/journal.aspx |
| `orders_ordersbycircuit_09.html.gz` | https://www.supremecourt.gov/orders/ordersbycircuit/09 |
| `orders_ordersbycircuit_ordercasebycircuit_090426OrderCasesByCircuit.html.gz` | https://www.supremecourt.gov/orders/ordersbycircuit/ordercasebycircuit/090426OrderCasesByCircuit, an Orders by Circuit document rather than a listing page |
| `orders_ordersofthecourt_25.html.gz` | https://www.supremecourt.gov/orders/ordersofthecourt/25 |
| `orders_scannedjournals_aspx.html.gz` | https://www.supremecourt.gov/orders/scannedjournals.aspx |
| `publicinfo_media_mediaadvisories_aspx.html.gz` | https://www.supremecourt.gov/publicinfo/media/mediaadvisories.aspx |
| `publicinfo_press_pressreleases_aspx.html.gz` | https://www.supremecourt.gov/publicinfo/press/pressreleases.aspx |
| `publicinfo_publicinfo_aspx.html.gz` | https://www.supremecourt.gov/publicinfo/publicinfo.aspx |
| `publicinfo_speeches_speeches_aspx.html.gz` | https://www.supremecourt.gov/publicinfo/speeches/speeches.aspx |
| `publicinfo_year_end_year_endreports_aspx.html.gz` | https://www.supremecourt.gov/publicinfo/year-end/year-endreports.aspx |
