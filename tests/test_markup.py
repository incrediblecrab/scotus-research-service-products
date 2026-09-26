import pytest

from scotus_products.markup import field, line_of, main_content, parse, render, resolve, squash


def element(html, xpath="//div[@id='pagemaindiv']"):
    return parse(f"<html><body><div id='pagemaindiv'>{html}</div></body></html>".encode()).xpath(xpath)[0]


def test_squash_collapses_only_ascii_whitespace():
    assert squash(" a \t\n\r\f b ") == "a b"
    # The no-break space is text the page shows, not layout.
    assert squash("Volume 179\u00a0 (1900 Term)") == "Volume 179\u00a0 (1900 Term)"


def test_field_joins_inline_text_without_adding_spaces():
    # Regression: the first version put a space between every text node, which turned "(PDF)" into "( PDF )".
    cell = element("<table><tr><td><a href='a.pdf'>Opinion</a> (<b>PDF</b>)</td></tr></table>", "//td")
    assert field(cell) == "Opinion (PDF)"
    word = element("<p>Mc<i>Culloch</i> v. <span>Maryland</span></p>", "//p")
    assert field(word) == "McCulloch v. Maryland"


def test_field_turns_breaks_and_cells_into_spaces():
    row = element("<table><tr><td>6/17/19</td><td>17-1702<br>17-1703</td></tr></table>", "//tr")
    assert field(row) == "6/17/19 17-1702 17-1703"


def test_render_keeps_lines_and_cells():
    div = element("<p>First  line</p><table><tr><td>a</td><td>b</td></tr></table>")
    assert render(div) == "First line\na\tb"


def test_render_skips_scripts_and_styles():
    div = element("<p>Kept</p><script>var x = 'dropped';</script><style>p {}</style>")
    assert render(div) == "Kept"


def test_line_of_gives_the_line_that_holds_the_link():
    div = element("<p>Opinion<br>Revisions: <a href='r.pdf'>7/01/26</a><br>Other</p>")
    assert line_of(div.xpath(".//a")[0]) == "Revisions: 7/01/26"


def test_line_of_joins_the_lines_a_link_spans():
    div = element("<p>Before <a href='a.pdf'>one<br>two</a> after<br>Next</p>")
    assert line_of(div.xpath(".//a")[0]) == "Before one two after"


def test_resolve_lowercases_the_id_and_keeps_the_url():
    uid, url, page = resolve("https://www.supremecourt.gov/opinions/slipopinion/18", "/opinions/boundvolumes/587BV.pdf#page=824")
    assert (uid, url, page) == ("opinions/boundvolumes/587bv.pdf", "https://www.supremecourt.gov/opinions/boundvolumes/587BV.pdf", 824)


def test_resolve_relative_and_encoded_paths():
    uid, url, _ = resolve("https://www.supremecourt.gov/oral_arguments/calendarsandlists.aspx", "argument_calendars/Monthly%20Cal.pdf")
    assert uid == "oral_arguments/argument_calendars/monthly cal.pdf"
    assert url == "https://www.supremecourt.gov/oral_arguments/argument_calendars/Monthly%20Cal.pdf"


def test_main_content_requires_the_main_column():
    with pytest.raises(ValueError):
        main_content(parse(b"<html><body><p>No main column</p></body></html>"))


def test_parse_refuses_bytes_that_are_not_utf8():
    with pytest.raises(UnicodeDecodeError):
        parse("<html><body>caf\u00e9</body></html>".encode("latin-1"))
