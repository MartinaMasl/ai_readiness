"""The comparison questionnaire at the end of the study.

All content (sections, questions, options) is in comparison.json, written
by the author and expected to change; the code contains no question text.
The file is checked when the Worker starts (load_comparison), so a mistake
in it shows up at once.

Question types:
- "scale": one of the labels in "scale" (stored as its index, 0 = first)
- "choice": one of the "options" (stored as its index)
- "text": free text; may be "optional"
Answers are stored with the file's "version" (comparison_responses.content_version).
"""

import json
from pathlib import Path

import study

# comparison.json sits next to this file; wrangler.jsonc bundles it into the Worker.
COMPARISON_PATH = Path(__file__).parent / "comparison.json"
TYPES = ("scale", "choice", "text")
MAX_TEXT = 5000


def options(item: dict) -> list[str]:
    """The answer options of a scale or choice question."""
    return item["scale"] if item["type"] == "scale" else item.get("options", [])


def load_comparison(path: Path = COMPARISON_PATH) -> dict:
    """Read comparison.json and check it: a version, the sections "chatbot"
    and "questionnaire", question ids unique across the file, known types,
    and options for every scale and choice question."""
    content = json.loads(Path(path).read_text(encoding="utf-8"))
    if not content.get("version"):
        raise ValueError("comparison.json needs a version")
    section_ids = [s["id"] for s in content["sections"]]
    for fmt in study.FORMATS:
        if fmt not in section_ids:
            raise ValueError(f'comparison.json needs a section with the id "{fmt}"')
    if len(set(section_ids)) != len(section_ids):
        raise ValueError("comparison.json: section ids must be unique")
    seen = set()
    for section in content["sections"]:
        for item in section["items"]:
            if item["id"] in seen:
                raise ValueError(f"comparison.json: question id {item['id']} is used twice")
            seen.add(item["id"])
            if item["type"] not in TYPES:
                raise ValueError(f"comparison.json: {item['id']} has unknown type {item['type']!r}")
            if item["type"] in ("scale", "choice") and len(options(item)) < 2:
                raise ValueError(f"comparison.json: {item['id']} needs at least two options")
    return content


def check_answer(item: dict, value) -> str | None:
    """The answer as it is stored (an option index or the text), or None if
    there is no answer. Raises ValueError for an answer that does not fit."""
    if item["type"] == "text":
        text = (value or "").strip() if isinstance(value, str) else None
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{item['id']}: expected text")
        if text and len(text) > MAX_TEXT:
            raise ValueError(f"{item['id']}: the text is too long")
        return text or None
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < len(options(item)):
        raise ValueError(f"{item['id']}: not one of the options")
    return str(value)


def section_complete(section: dict, answers: dict) -> bool:
    """Every question that is not optional has an answer."""
    return all(item.get("optional") or item["id"] in answers for item in section["items"])


def resume_at(sections: list[dict], answers: dict) -> int:
    """Where "Continue" resumes: the first section that is not complete, or
    len(sections) if all are (ready to submit)."""
    for index, section in enumerate(sections):
        if not section_complete(section, answers):
            return index
    return len(sections)


def for_respondent(content: dict, format_order: str) -> list[dict]:
    """The sections in the respondent's order (study.comparison_sections)."""
    return study.comparison_sections(content, format_order)
