import copy
import json

import pytest

from rubric import RUBRIC_PATH, load_rubric

CONTENT = json.loads(RUBRIC_PATH.read_text(encoding="utf-8"))


def broken(tmp_path, change):
    content = copy.deepcopy(CONTENT)
    change(content)
    path = tmp_path / "rubric.json"
    path.write_text(json.dumps(content))
    return path


def first_sub(content):
    return content["dimensions"][0]["subdimensions"][0]


@pytest.mark.parametrize("change", [
    lambda c: c["rules"].update(new_rule="A rule the code does not know."),       # would be ignored
    lambda c: c["rules"].pop("question_style"),                                   # a rule missing
    lambda c: first_sub(c).update(opening_questoin="A misspelt key."),            # would be ignored
    lambda c: first_sub(c).update(examples=[{"type": "clarification", "respondent": "What?"}]),  # no answer
    lambda c: first_sub(c).update(examples=[{"type": "follow_up", "avoid": "A", "better": "B"}]),  # no why
    lambda c: first_sub(c).update(examples=[{"type": "hint", "respondent": "A", "chatbot": "B"}]),  # unknown type
])
def test_a_broken_rubric_is_refused_instead_of_partly_ignored(tmp_path, change):
    with pytest.raises(ValueError):
        load_rubric(broken(tmp_path, change))


def test_the_current_rubric_loads_with_its_examples():
    rubric = load_rubric()
    examples = [e for d in rubric.dimensions for s in d.subdimensions for e in s.examples]
    assert examples and {e.type for e in examples} <= {"clarification", "follow_up"}
