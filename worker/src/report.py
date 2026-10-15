"""Report data as JSON.

build_report() turns subdimension scores into everything the report page
shows. All calculation happens here; result.html only displays the values.
Subdimension scores are not included, because the respondent must not
see them as numbers.
"""

from rubric import Rubric
import scoring

# Scores given to the report: {dimension name: {subdimension name: score}}.
# Every subdimension of the rubric must have a score.
Scores = dict[str, dict[str, int]]


def _level(rubric: Rubric, score) -> dict:
    level = scoring.maturity_level(score)
    return {"number": level, "name": rubric.scale[level].name}


def build_report(rubric: Rubric, scores: Scores) -> dict:
    dimensions = []
    dimension_scores = []
    for dimension in rubric.dimensions:
        given = scores.get(dimension.name, {})
        missing = [sub.name for sub in dimension.subdimensions if sub.name not in given]
        if missing:
            raise ValueError(f"{dimension.name}: no score for {', '.join(missing)}")
        sub_scores = [given[sub.name] for sub in dimension.subdimensions]

        score = scoring.dimension_score(sub_scores)
        dimension_scores.append(score)
        level = _level(rubric, score)

        gap = None
        gap_index = scoring.main_gap(sub_scores)
        if gap_index is not None:
            sub = dimension.subdimensions[gap_index]
            gap = {"subdimension": sub.name, "text": sub.gap[sub_scores[gap_index]]}

        dimensions.append(
            {
                "name": dimension.name,
                # Full precision for the radar chart, two decimals for the text.
                "score": float(score),
                "score_display": scoring.display(score),
                "level": level,
                "report_text": dimension.report_levels[level["number"]],
                "gap": gap,
            }
        )

    overall = scoring.overall_score(dimension_scores)
    overall_level = _level(rubric, overall)
    return {
        "overall": {
            "score_display": scoring.display(overall),
            "level": overall_level,
            "description": rubric.scale[overall_level["number"]].description,
        },
        "dimensions": dimensions,
    }
