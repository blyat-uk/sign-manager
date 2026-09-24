"""Unit tests for text-transform utilities.

Pure-logic tests: parse_rich_text → transform → segments_to_ass round-trips,
plus edge cases (empty input, no-op inputs, balance idempotency, apostrophes).
"""

from __future__ import annotations

import pytest

from sign_manager.model.text_transforms import TransformKind, apply


# ---------------------------------------------------------------------------
# Plain text (no inline overrides)
# ---------------------------------------------------------------------------


def test_spaces_to_breaks_plain():
    assert apply("hello world here", TransformKind.SPACES_TO_BREAKS,
                 default_bold=False, default_italic=False) == "hello\\Nworld\\Nhere"


def test_breaks_to_spaces_plain():
    assert apply("hello\\Nworld\\Nhere", TransformKind.BREAKS_TO_SPACES,
                 default_bold=False, default_italic=False) == "hello world here"


def test_uppercase_plain():
    assert apply("Hello World", TransformKind.UPPERCASE,
                 default_bold=False, default_italic=False) == "HELLO WORLD"


def test_lowercase_plain():
    assert apply("HELLO World", TransformKind.LOWERCASE,
                 default_bold=False, default_italic=False) == "hello world"


def test_title_case_plain():
    assert apply("hello world", TransformKind.TITLE_CASE,
                 default_bold=False, default_italic=False) == "Hello World"


def test_title_case_preserves_apostrophes():
    # str.title() would give "It'S Mine"; our impl must give "It's Mine".
    assert apply("it's mine", TransformKind.TITLE_CASE,
                 default_bold=False, default_italic=False) == "It's Mine"


def test_title_case_normalizes_existing_caps():
    assert apply("HELLO world", TransformKind.TITLE_CASE,
                 default_bold=False, default_italic=False) == "Hello World"


# ---------------------------------------------------------------------------
# Balance
# ---------------------------------------------------------------------------


def test_balance_splits_at_midpoint_space():
    # "one two three" — len 13, mid 6, nearest space at index 7 (between "two" and "three").
    # Wait: "one two three" — spaces at index 3 and 7. mid = 13//2 = 6. |3-6|=3, |7-6|=1 → split at 7.
    out = apply("one two three", TransformKind.BALANCE,
                default_bold=False, default_italic=False)
    assert out == "one two\\Nthree"


def test_balance_idempotent():
    once = apply("one two three four", TransformKind.BALANCE,
                 default_bold=False, default_italic=False)
    twice = apply(once, TransformKind.BALANCE,
                  default_bold=False, default_italic=False)
    assert once == twice


def test_balance_collapses_existing_breaks_first():
    # Already multi-line input should be re-flowed to a single \\N at the best split.
    out = apply("one\\Ntwo three four", TransformKind.BALANCE,
                default_bold=False, default_italic=False)
    # joined: "one two three four" (len 18, mid 9). spaces at 3, 7, 13. closest to 9 = 7.
    assert out == "one two\\Nthree four"


def test_balance_single_word_is_noop():
    assert apply("hello", TransformKind.BALANCE,
                 default_bold=False, default_italic=False) == "hello"


def test_balance_empty_is_noop():
    assert apply("", TransformKind.BALANCE,
                 default_bold=False, default_italic=False) == ""


def test_balance_prefers_comma_over_midpoint_space():
    # "one, two three" — len 14, mid 7. Spaces at 4, 8. Pure midpoint logic picks 8
    # (closer to 7), but comma-space at 4 wins because commas are preferred.
    out = apply("one, two three", TransformKind.BALANCE,
                default_bold=False, default_italic=False)
    assert out == "one,\\Ntwo three"


def test_balance_picks_comma_closest_to_midpoint():
    # "one, two, three" — len 15, mid 7. Comma-spaces at 4, 9. |9-7|=2 < |4-7|=3.
    out = apply("one, two, three", TransformKind.BALANCE,
                default_bold=False, default_italic=False)
    assert out == "one, two,\\Nthree"


def test_balance_falls_back_when_no_comma_space():
    # "hello,world" — comma not followed by space, so no comma-space candidates;
    # no plain spaces either → no-op.
    out = apply("hello,world", TransformKind.BALANCE,
                default_bold=False, default_italic=False)
    assert out == "hello,world"


def test_balance_3_splits_into_three_lines():
    # "one two three four five" — len 23. Targets at 7 and 15.
    # Spaces at 3, 7, 13, 18. Closest to 7 → 7. Then closest to 15 from {3,13,18} → 13.
    out = apply("one two three four five", TransformKind.BALANCE_3,
                default_bold=False, default_italic=False)
    assert out == "one two\\Nthree\\Nfour five"


def test_balance_3_prefers_commas():
    # "hello, world, again" — len 19. Comma-spaces at 6, 13 — exactly the splits we want.
    out = apply("hello, world, again", TransformKind.BALANCE_3,
                default_bold=False, default_italic=False)
    assert out == "hello,\\Nworld,\\Nagain"


def test_balance_3_best_effort_with_few_spaces():
    # Only one space → can produce at most one split → effectively 2 lines.
    out = apply("hello world", TransformKind.BALANCE_3,
                default_bold=False, default_italic=False)
    assert out == "hello\\Nworld"


def test_balance_3_idempotent():
    once = apply("one two three four five", TransformKind.BALANCE_3,
                 default_bold=False, default_italic=False)
    twice = apply(once, TransformKind.BALANCE_3,
                  default_bold=False, default_italic=False)
    assert once == twice


def test_balance_idempotent_with_comma():
    once = apply("one, two three four", TransformKind.BALANCE,
                 default_bold=False, default_italic=False)
    twice = apply(once, TransformKind.BALANCE,
                  default_bold=False, default_italic=False)
    assert once == twice


def test_balance_tiebreak_prefers_left():
    # "abc de fgh" — len 10, mid 5. spaces at 3 and 6. |3-5|=2, |6-5|=1 → 6 wins.
    # Use a string where tie-break matters: "abcd ef ghij" — len 12, mid 6.
    # spaces at 4 and 7. |4-6|=2, |7-6|=1 → 7. Still asymmetric.
    # True tie: "abc def ghi" — len 11, mid 5. spaces at 3 and 7. |3-5|=2, |7-5|=2 → tie → prefer left (3).
    out = apply("abc def ghi", TransformKind.BALANCE,
                default_bold=False, default_italic=False)
    assert out == "abc\\Ndef ghi"


# ---------------------------------------------------------------------------
# Edge cases (no-ops)
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty():
    for kind in TransformKind:
        assert apply("", kind, default_bold=False, default_italic=False) == ""


def test_spaces_to_breaks_no_spaces_is_noop():
    assert apply("helloworld", TransformKind.SPACES_TO_BREAKS,
                 default_bold=False, default_italic=False) == "helloworld"


def test_breaks_to_spaces_no_breaks_is_noop():
    assert apply("hello world", TransformKind.BREAKS_TO_SPACES,
                 default_bold=False, default_italic=False) == "hello world"


# ---------------------------------------------------------------------------
# Rich text — bold/italic boundaries preserved
# ---------------------------------------------------------------------------


def test_uppercase_preserves_bold_boundaries():
    # segments_to_ass only emits closing tags on transition; trailing {\\b0} is
    # dropped during round-trips (pre-existing behavior of the helpers).
    assert apply("hello {\\b1}world{\\b0}", TransformKind.UPPERCASE,
                 default_bold=False, default_italic=False) == "HELLO {\\b1}WORLD"


def test_lowercase_preserves_italic_boundaries():
    assert apply("HELLO {\\i1}WORLD{\\i0}", TransformKind.LOWERCASE,
                 default_bold=False, default_italic=False) == "hello {\\i1}world"


def test_title_case_preserves_bold_boundaries():
    assert apply("hello {\\b1}world{\\b0}", TransformKind.TITLE_CASE,
                 default_bold=False, default_italic=False) == "Hello {\\b1}World"


def test_spaces_to_breaks_preserves_bold_boundaries():
    # "hello world" with "world" bold; space lives in the unformatted segment.
    assert apply("hello {\\b1}world{\\b0}", TransformKind.SPACES_TO_BREAKS,
                 default_bold=False, default_italic=False) == "hello\\N{\\b1}world"


def test_breaks_to_spaces_preserves_bold_boundaries():
    assert apply("hello\\N{\\b1}world{\\b0}", TransformKind.BREAKS_TO_SPACES,
                 default_bold=False, default_italic=False) == "hello {\\b1}world"


def test_balance_preserves_formatting_across_split():
    # "one two {\\b1}three four{\\b0}" — joined plain "one two three four" len 18, mid 9.
    # spaces at 3, 7, 13. closest to mid = 7 (|7-9|=2). Split moves \\N into the unbold segment.
    out = apply("one two {\\b1}three four{\\b0}", TransformKind.BALANCE,
                default_bold=False, default_italic=False)
    assert out == "one two\\N{\\b1}three four"


def test_balance_split_inside_bold_segment():
    # The midpoint space lies inside the bold run.
    # "a {\\b1}bb cc{\\b0} d" — joined "a bb cc d" len 9, mid 4. spaces at 1, 4, 7.
    # |1-4|=3, |4-4|=0 → split at 4 (the space between "bb" and "cc", which is inside the bold run).
    out = apply("a {\\b1}bb cc{\\b0} d", TransformKind.BALANCE,
                default_bold=False, default_italic=False)
    assert out == "a {\\b1}bb\\Ncc{\\b0} d"


# ---------------------------------------------------------------------------
# Default bold/italic (style-level defaults flow through)
# ---------------------------------------------------------------------------


def test_default_bold_true_round_trips_clean():
    # When the style is bold by default, a plain-uppercase round-trip should
    # not emit spurious {\\b0}{\\b1} blocks.
    assert apply("hello world", TransformKind.UPPERCASE,
                 default_bold=True, default_italic=False) == "HELLO WORLD"


def test_default_italic_true_with_inline_unitalic():
    # Style italic by default; an inline {\\i0}...{\\i1} block must survive uppercasing.
    # Trailing {\\i1} is dropped on serialize (no following segment); the i0 inside survives.
    assert apply("hello {\\i0}world{\\i1}", TransformKind.UPPERCASE,
                 default_bold=False, default_italic=True) == "HELLO {\\i0}WORLD"
