"""Respondents and their personal links.

Only a pseudonymous code (e.g. R01), the role and the format order are
stored. Keep the list that connects codes to names yourself, outside the
database and outside this repository. Codes starting with "T" are test
respondents (for the author and her supervisor).

The format order (which assessment comes first) alternates within each
role, so both orders are used about equally among technical and among
business respondents. Test codes are balanced separately, so they never
shift the balance of the real respondents.

Run from the worker/ folder; add --remote --url https://<worker address>
for the live site:
    uv run python scripts/add_respondent.py R07 technical            new respondent, order alternates
    uv run python scripts/add_respondent.py T03 business questionnaire_first   order given
    uv run python scripts/add_respondent.py --list                   all respondents, order, status, links
    uv run python scripts/add_respondent.py --assign-orders          propose orders for respondents without one
    uv run python scripts/add_respondent.py --assign-orders --save   ... and save them
    uv run python scripts/add_respondent.py --set-order R03 questionnaire_first
    uv run python scripts/add_respondent.py --reset T01              test respondents only: start again
"""

import re
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import study  # noqa: E402
from show_log import query, quoted  # noqa: E402

# The same two roles are enforced by the database (migrations/0003_respondents.sql).
ROLES = ["technical", "business"]
LOCAL_URL = "http://localhost:8787"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def link(base_url: str, token: str) -> str:
    return f"{base_url.rstrip('/')}/?r={token}"


def is_test(code: str) -> bool:
    return code.startswith("T")


def respondents(remote: bool) -> list[dict]:
    return query("SELECT code, role, token, format_order, consent_at, feedback_submitted_at "
                 "FROM respondents ORDER BY code", remote)


def order_for(code: str, role: str, existing: list[dict]) -> str:
    """Alternate within the role; test and real respondents are balanced separately."""
    same_pool = [r["format_order"] for r in existing
                 if r["role"] == role and is_test(r["code"]) == is_test(code) and r["format_order"]]
    return study.next_format_order(same_pool)


def check_order(order: str) -> None:
    if order not in study.ORDERS:
        sys.exit(f"The order must be one of: {', '.join(study.ORDERS)}.")


def create(code: str, role: str, order: str | None, remote: bool, base_url: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,20}", code):
        sys.exit("The code may contain only letters, digits, - and _ (at most 20), e.g. R01.")
    if role not in ROLES:
        sys.exit(f"The role must be one of: {', '.join(ROLES)}.")
    order = order or order_for(code, role, respondents(remote))
    check_order(order)
    token = secrets.token_urlsafe(16)  # 22 random characters
    query("INSERT INTO respondents (code, role, token, created_at, format_order) VALUES "
          f"({quoted(code)}, {quoted(role)}, {quoted(token)}, {quoted(now())}, {quoted(order)})", remote)
    print(f"{code} ({role}, {order}): {link(base_url, token)}")


def list_all(remote: bool, base_url: str) -> None:
    rows = query("SELECT r.code, r.role, r.token, r.format_order, r.consent_at, r.feedback_submitted_at, "
                 "c.format, c.finished_at FROM respondents r "
                 "LEFT JOIN conversations c ON c.respondent_code = r.code AND c.excluded = 0 "
                 "ORDER BY r.code", remote)
    people = {}
    for row in rows:
        person = people.setdefault(row["code"], dict(row, done={}))
        if row["format"]:
            person["done"][row["format"]] = "finished" if row["finished_at"] else "started"
    for p in people.values():
        status = ", ".join(f"{fmt} {state}" for fmt, state in p["done"].items()) or "nothing started"
        if p["feedback_submitted_at"]:
            status += ", feedback submitted"
        print(f"{p['code']:6} {p['role']:10} {p['format_order'] or '(no order)':20} "
              f"{'consent' if p['consent_at'] else 'no consent':11} {status:40} {link(base_url, p['token'])}")


def assign_orders(remote: bool, save: bool) -> None:
    """Propose an order for every respondent without one: if they already
    started an assessment, the format they started first; otherwise
    alternate within the role."""
    existing = respondents(remote)
    started = query("SELECT respondent_code, format, MIN(created_at) AS first_start FROM conversations "
                    "WHERE respondent_code IS NOT NULL AND excluded = 0 GROUP BY respondent_code, format", remote)
    first_format = {}
    for row in sorted(started, key=lambda r: r["first_start"]):
        first_format.setdefault(row["respondent_code"], row["format"])

    proposals = []
    for person in existing:
        if person["format_order"]:
            continue
        if person["code"] in first_format:
            order, why = f"{first_format[person['code']]}_first", f"already started the {first_format[person['code']]}"
        else:
            order, why = order_for(person["code"], person["role"], existing), "alternating within the role"
        person["format_order"] = order  # counts for the next respondent of the same role
        proposals.append((person["code"], person["role"], order, why))

    if not proposals:
        print("Every respondent already has an order.")
        return
    for code, role, order, why in proposals:
        print(f"{code:6} {role:10} {order:20} ({why})")
    if save:
        for code, _, order, _ in proposals:
            query(f"UPDATE respondents SET format_order = {quoted(order)} WHERE code = {quoted(code)}", remote)
        print("Saved.")
    else:
        print("Not saved. Run again with --save to save these orders.")


def set_order(code: str, order: str, remote: bool) -> None:
    check_order(order)
    query(f"UPDATE respondents SET format_order = {quoted(order)} WHERE code = {quoted(code)}", remote)
    print(f"{code}: {order}")


def reset(code: str, remote: bool) -> None:
    """Let a test respondent start the study again. Nothing is deleted: their
    assessments and feedback answers are marked excluded, and consent and
    the feedback submission are cleared."""
    if not is_test(code):
        sys.exit("Only test respondents (codes starting with T) can be reset.")
    query(f"UPDATE conversations SET excluded = 1 WHERE respondent_code = {quoted(code)}", remote)
    query(f"UPDATE comparison_responses SET excluded = 1 WHERE respondent_code = {quoted(code)}", remote)
    query("UPDATE respondents SET consent_at = NULL, feedback_submitted_at = NULL "
          f"WHERE code = {quoted(code)}", remote)
    print(f"{code}: reset. Earlier rows are kept and marked excluded.")


def main() -> None:
    args = sys.argv[1:]
    remote = "--remote" in args
    base_url = args[args.index("--url") + 1] if "--url" in args else (None if remote else LOCAL_URL)
    words = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] != "--url")]

    if "--list" in args or ("--remote" in args and not words and "--assign-orders" not in args):
        if remote and base_url is None:
            sys.exit("For --remote, give the address of the live site with --url https://...")
        list_all(remote, base_url)
    elif "--assign-orders" in args:
        assign_orders(remote, "--save" in args)
    elif "--set-order" in args and len(words) == 2:
        set_order(words[0], words[1], remote)
    elif "--reset" in args and len(words) == 1:
        reset(words[0], remote)
    elif len(words) in (2, 3) and not any(a in args for a in ("--set-order", "--reset")):
        if remote and base_url is None:
            sys.exit("For --remote, give the address of the live site with --url https://...")
        create(words[0], words[1], words[2] if len(words) == 3 else None, remote, base_url)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
