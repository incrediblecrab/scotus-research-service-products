# pdfs

Three PDFs from www.supremecourt.gov, one for each kind of text the extractor has to tell apart.

| File | Source | Kind |
|---|---|---|
| `inchambers.pdf` | https://www.supremecourt.gov/opinions/23pdf/23a843_he4l.pdf, the one file the In-Chambers Opinions page lists (41,945 bytes) | Born-digital. `pdftotext -raw` prints 7 lines that end in a hyphen; the default mode prints none, because it removes the hyphen and joins the word. |
| `daycall.pdf` | https://www.supremecourt.gov/oral_arguments/daycall/DayCall_10-07-24.pdf, "Day Call, Monday, October 7, 2024" (9,839 bytes) | Born-digital, but it draws spaces in invisible render mode (mode 3), as an OCR layer does. No image covers the page, so it is not a scan. |
| `oj-page3.pdf` | Page 3 of https://www.supremecourt.gov/pdfs/recordsandbriefs/1000370832/1000370832_001.pdf, No. 1, Orig., "Motion for Leave to File Bill of Complaint", filed 7/14/1922, cut out with `pdfseparate -f 3 -l 3` to keep the fixture small (35,215 bytes) | Scanned. The page is one image with an OCR text layer, which goes to `ocr_text`. |
