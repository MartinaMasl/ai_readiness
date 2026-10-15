from fractions import Fraction

import pytest

import scoring

# Worked example from the thesis.
WORKED_EXAMPLE = {
    "Data": ([4, 2, 5], "3.67", 3),
    "People": ([5, 3, 2], "3.33", 3),
    "Strategy": ([2, 3, 3], "2.67", 2),
    "Technology": ([1, 1, 0], "0.67", 0),
}


@pytest.mark.parametrize("name", WORKED_EXAMPLE)
def test_worked_example_dimensions(name):
    sub_scores, expected_display, expected_level = WORKED_EXAMPLE[name]
    score = scoring.dimension_score(sub_scores)
    assert scoring.display(score) == expected_display
    assert scoring.maturity_level(score) == expected_level


def test_worked_example_overall():
    dimension_scores = [scoring.dimension_score(s) for s, _, _ in WORKED_EXAMPLE.values()]
    overall = scoring.overall_score(dimension_scores)
    assert overall == Fraction(31, 12)  # full precision is kept
    assert scoring.display(overall) == "2.58"
    assert scoring.maturity_level(overall) == 2


def test_no_evidence_counts_as_zero():
    # No evidence at all gives 0 (rubric rule insufficient_evidence), so it
    # lowers the mean: 4, 0, 5 gives 3, not 4.5.
    assert scoring.dimension_score([4, 0, 5]) == Fraction(3)
    assert scoring.maturity_level(scoring.dimension_score([4, 0, 5])) == 3


def test_level_is_floor_not_rounding():
    assert scoring.maturity_level(Fraction(29, 10)) == 2
    assert scoring.maturity_level(Fraction(3)) == 3


def test_floor_is_exact_for_whole_number_means():
    # 10/3 + 8/3 + 3 + 3 = 12, mean 3: stays level 3, not 2.
    dims = [Fraction(10, 3), Fraction(8, 3), Fraction(3), Fraction(3)]
    assert scoring.maturity_level(scoring.overall_score(dims)) == 3


def test_main_gap_is_lowest():
    assert scoring.main_gap([4, 2, 5]) == 1


def test_main_gap_tie_takes_first_listed():
    assert scoring.main_gap([3, 1, 1]) == 1
    assert scoring.main_gap([2, 2, 2]) == 0


def test_no_gap_at_level_5():
    assert scoring.main_gap([5, 5, 5]) is None
