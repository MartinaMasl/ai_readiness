"""Means and maturity levels.

Every subdimension has a score from 0 to 5; no evidence at all gives 0
(see `insufficient_evidence` in the rubric rules).

All calculations use Fraction, so they are exact. A float mean such as
2.9999999999999996 would be floored to the wrong level; a Fraction
cannot have that problem. Values are turned into floats only for display.
"""

import math
from fractions import Fraction


def dimension_score(subdimension_scores: list[int]) -> Fraction:
    """Mean of the subdimension scores."""
    return Fraction(sum(subdimension_scores), len(subdimension_scores))


def overall_score(dimension_scores: list[Fraction]) -> Fraction:
    """Mean of the dimension scores."""
    return sum(dimension_scores, Fraction(0)) / len(dimension_scores)


def maturity_level(score: Fraction | int) -> int:
    """The maturity level is the score rounded down."""
    return math.floor(score)


def main_gap(subdimension_scores: list[int]) -> int | None:
    """Position of the lowest-scoring subdimension, or None if there is no gap.

    On a tie the subdimension listed first is taken. If the lowest score
    is 5, there is no gap.
    """
    lowest_score = min(subdimension_scores)
    if lowest_score == 5:
        return None
    return subdimension_scores.index(lowest_score)


def display(score: Fraction) -> str:
    """Score rounded to two decimals for display."""
    return f"{float(score):.2f}"
