import json

import pytest

import demo
from report import build_report
from rubric import load_rubric

RUBRIC = load_rubric()
DATA = RUBRIC.dimensions[0]
SUB = [s.name for s in DATA.subdimensions]


def test_rubric_loads():
    assert RUBRIC.rules.max_follow_ups == 2
    assert DATA.name == "Data"
    assert len(DATA.subdimensions) == 3


def test_every_subdimension_has_gap_sentences_for_levels_0_to_4():
    for dimension in RUBRIC.dimensions:
        for sub in dimension.subdimensions:
            assert sorted(sub.gap) == [0, 1, 2, 3, 4]


def test_report_uses_rubric_texts():
    report = build_report(RUBRIC, {"Data": {SUB[0]: 4, SUB[1]: 2, SUB[2]: 5}})
    data = report["dimensions"][0]
    assert data["score_display"] == "3.67"
    assert data["level"] == {"number": 3, "name": RUBRIC.scale[3].name}
    assert data["report_text"] == DATA.report_levels[3]
    assert data["gap"] == {"subdimension": SUB[1], "text": DATA.subdimensions[1].gap[2]}
    assert report["overall"]["level"]["number"] == 3
    assert report["overall"]["description"] == RUBRIC.scale[3].description


def test_gap_tie_in_report():
    report = build_report(RUBRIC, {"Data": {SUB[0]: 3, SUB[1]: 1, SUB[2]: 1}})
    assert report["dimensions"][0]["gap"]["subdimension"] == SUB[1]


def test_no_gap_when_all_at_level_5():
    report = build_report(RUBRIC, {"Data": {SUB[0]: 5, SUB[1]: 5, SUB[2]: 5}})
    assert report["dimensions"][0]["gap"] is None


def test_missing_subdimension_score_is_an_error():
    with pytest.raises(ValueError):
        build_report(RUBRIC, {"Data": {SUB[0]: 4, SUB[2]: 5}})


def test_report_does_not_contain_subdimension_scores():
    report = build_report(RUBRIC, {"Data": {SUB[0]: 4, SUB[1]: 2, SUB[2]: 5}})
    assert set(report["dimensions"][0]["gap"]) == {"subdimension", "text"}
    json.dumps(report)  # the endpoint must be able to return it as JSON


def test_four_dimension_demo_matches_worked_example():
    rubric = demo.four_dimension_placeholder(RUBRIC)
    report = build_report(rubric, demo.worked_example_scores(rubric))
    assert [d["score_display"] for d in report["dimensions"]] == ["3.67", "3.33", "2.67", "0.67"]
    assert report["overall"]["score_display"] == "2.58"
    assert report["overall"]["level"]["number"] == 2
