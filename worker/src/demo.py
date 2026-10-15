"""Made-up scores so the report can be checked before conversations exist.

Temporary: replaced by scores from D1 in step 3.

- "demo": the real rubric (only Data so far) with the worked example scores.
- "demo-four": four dimensions so the radar chart has four axes. People,
  Strategy and Technology reuse the Data texts as placeholders; they are
  not the rubric.
"""

from rubric import Rubric

# The worked example from the thesis.
WORKED_EXAMPLE = {
    "Data": [4, 2, 5],
    "People": [5, 3, 2],
    "Strategy": [2, 3, 3],
    "Technology": [1, 1, 0],
}


def four_dimension_placeholder(rubric: Rubric) -> Rubric:
    data = rubric.dimensions[0]
    copies = [data.model_copy(update={"name": name}) for name in ("People", "Strategy", "Technology")]
    return rubric.model_copy(update={"dimensions": [data] + copies})


def worked_example_scores(rubric: Rubric) -> dict[str, dict[str, int]]:
    return {
        dimension.name: {
            sub.name: score
            for sub, score in zip(dimension.subdimensions, WORKED_EXAMPLE[dimension.name])
        }
        for dimension in rubric.dimensions
    }
