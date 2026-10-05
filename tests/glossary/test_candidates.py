"""Proposing glossary terms from a book's index and its recurring names."""

from __future__ import annotations

from lxml import etree

from glossary.candidates import candidates, index_headings, recurring_names

INDEX = etree.fromstring(
    """<html xmlns="http://www.w3.org/1999/xhtml"><body>
    <p>Index</p>
    <p>Entries in <i>italics</i> refer to figures.</p>
    <ul>
      <li>14K triad, <a href="#a">93</a></li>
      <li>Along (blogger), <a href="#b">163</a></li>
      <li>animal lovers, scams targeting, <a href="#c">127</a>–<a href="#d">8</a></li>
      <li>Antonopoulos, Georgios, <a href="#e">26</a></li>
      <li>scam compounds, <a href="#f">2</a>
        <ul><li>raids on, <a href="#g">5</a></li></ul>
      </li>
      <li>KK Park <i>see</i> Myawaddy</li>
    </ul>
    </body></html>"""
)


def test_index_headings_are_cleaned_into_terms() -> None:
    assert index_headings(INDEX) == [
        "14K triad",
        "Along",
        "animal lovers",
        "Georgios Antonopoulos",
        "scam compounds",
        "raids on",
        "KK Park",
    ]


TEXT = (
    "The scam compound near Sihanoukville was raided. Scam compounds spread.12 However, "
    "the Sihanoukville police came. However late, Georgios Antonopoulos wrote about "
    "the 14K triad and the 14K triad's reach in August. The KK Park scam compound. "
    "However. On ‘Fraud Today’ and fraud, fraud, in Sihanoukville’s August."
)


def test_recurring_names_skip_sentence_openers() -> None:
    names = recurring_names(TEXT, min_count=2)
    assert "Sihanoukville" in names  # "Sihanoukville’s" counts, trimmed
    assert "However" not in names  # only ever opens a sentence
    assert "Fraud" not in names  # the book says "fraud" more often
    assert "August" not in names


def test_candidates_are_counted_in_the_text_and_ranked() -> None:
    found = dict(candidates([INDEX], TEXT, known={"kk park"}, min_count=2))
    assert found["scam compound"] == 3  # the index's plural, made singular
    assert found["Sihanoukville"] == 3
    assert found["14K triad"] == 2
    assert "KK Park" not in found  # already in the glossary
    assert "Along" not in found  # in the index, but not in the text twice
    assert list(found)[0] in ("scam compound", "Sihanoukville")
    assert "raids on" not in found and "animal lovers" not in found


def test_a_plural_heading_becomes_the_singular_the_book_uses() -> None:
    from glossary.candidates import _singular

    text = "One rate, two rates; a business, many businesses; the news."
    assert _singular("rates", text) == "rate"
    assert _singular("businesses", text) == "business"
    assert _singular("news", text) == "news"


def test_a_word_found_mostly_inside_a_longer_name_is_left_out() -> None:
    text = "Voice of Democracy said. Voice of Democracy wrote. Voice of Democracy again. Democracy."
    found = dict(candidates([], text, known=set(), min_count=2))
    assert "Voice of Democracy" in found and "Democracy" not in found


def test_nationality_words_and_section_headings_are_left_out() -> None:
    from glossary.candidates import _nationality_words

    names = ["China", "Chinese", "Cambodia", "Cambodian", "Thailand", "Thai", "Philippines",
             "Philippine", "Taiwan", "Taiwanese", "Japan", "Jordan", "Duterte", "Russian"]
    assert _nationality_words(names) == {
        "Chinese", "Cambodian", "Thai", "Philippine", "Taiwanese",
    }
    text = "Chapter One. In Chapter Two, the Chapter Three. Introduction here, Introduction there."
    assert dict(candidates([], text, known=set(), min_count=2)) == {}
