import json

import pytest

import demo
from report import build_report
from rubric import load_rubric

RUBRIC = load_rubric()
DATA = RUBRIC.dimensions[0]
SUB = [s.name for s in DATA.subdimensions]


def scores(data):
    """Worked example scores for every dimension, with the given Data scores."""
    all_scores = demo.worked_example_scores(RUBRIC)
    all_scores["Data"] = dict(zip(SUB, data))
    return all_scores


def test_rubric_loads():
    assert DATA.name == "Data"
    assert len(DATA.subdimensions) == 3
    assert RUBRIC.rules.max_follow_ups >= 0 and RUBRIC.rules.max_follow_ups_total >= 0


def test_every_dimension_has_three_subdimensions():
    for dimension in RUBRIC.dimensions:
        assert len(dimension.subdimensions) == 3


def test_report_uses_rubric_texts():
    report = build_report(RUBRIC, scores([4, 2, 5]))
    data = report["dimensions"][0]
    assert data["score_display"] == "3.67"
    assert data["level"] == {"number": 3, "name": RUBRIC.scale[3].name}
    assert data["report_text"] == DATA.report_levels[3]
    assert report["overall"]["level"]["number"] == 3
    assert report["overall"]["description"] == RUBRIC.scale[3].description


def test_missing_subdimension_score_is_an_error():
    with pytest.raises(ValueError):
        incomplete = scores([4, 2, 5])
        del incomplete["Data"][SUB[1]]
        build_report(RUBRIC, incomplete)


def test_report_does_not_contain_subdimension_scores():
    report = build_report(RUBRIC, scores([4, 2, 5]))
    assert set(report["dimensions"][0]) == {"name", "score", "score_display", "level", "report_text"}
    json.dumps(report)  # the endpoint must be able to return it as JSON


def test_four_dimension_demo_matches_worked_example():
    rubric = demo.four_dimension_placeholder(RUBRIC)
    report = build_report(rubric, demo.worked_example_scores(rubric))
    assert [d["score_display"] for d in report["dimensions"]] == ["3.67", "3.33", "2.67", "0.67"]
    assert report["overall"]["score_display"] == "2.58"
    assert report["overall"]["level"]["number"] == 2
