"""Create a personal link for a respondent, or list the existing ones.

Only a pseudonymous code (e.g. R01) and the role are stored. Keep the list
that connects codes to names yourself, outside the database and outside
this repository.

Run from the worker/ folder:
    uv run python scripts/add_respondent.py R01 technical                 local database
    uv run python scripts/add_respondent.py R02 business --remote --url https://<your worker>.workers.dev
    uv run python scripts/add_respondent.py --list [--remote --url ...]   all respondents and their links
"""

import re
import secrets
import sys
from datetime import datetime, timezone

from show_log import query, quoted

# The same two roles are enforced by the database (migrations/0003_respondents.sql).
ROLES = ["technical", "business"]
LOCAL_URL = "http://localhost:8787"


def link(base_url: str, token: str) -> str:
    return f"{base_url.rstrip('/')}/?r={token}"


def main() -> None:
    args = sys.argv[1:]
    remote = "--remote" in args
    base_url = args[args.index("--url") + 1] if "--url" in args else (None if remote else LOCAL_URL)
    words = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] != "--url")]
    if remote and base_url is None:
        sys.exit("For --remote, give the address of the live site with --url https://...")

    if "--list" in args:
        rows = query("SELECT r.code, r.role, r.token, r.created_at, COUNT(c.id) AS conversations "
                     "FROM respondents r LEFT JOIN conversations c ON c.respondent_code = r.code "
                     "GROUP BY r.code ORDER BY r.code", remote)
        for row in rows:
            print(f"{row['code']:8} {row['role']:10} {row['conversations']} conversation(s)  {link(base_url, row['token'])}")
        return

    if len(words) != 2:
        sys.exit(__doc__)
    code, role = words
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,20}", code):
        sys.exit("The code may contain only letters, digits, - and _ (at most 20), e.g. R01.")
    if role not in ROLES:
        sys.exit(f"The role must be one of: {', '.join(ROLES)}.")

    token = secrets.token_urlsafe(16)  # 22 random characters
    query("INSERT INTO respondents (code, role, token, created_at) "
          f"VALUES ({quoted(code)}, {quoted(role)}, {quoted(token)}, "
          f"{quoted(datetime.now(timezone.utc).isoformat())})", remote)
    print(f"{code} ({role}): {link(base_url, token)}")


if __name__ == "__main__":
    main()
