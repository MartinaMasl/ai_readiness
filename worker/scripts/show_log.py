"""Print the whole log of one conversation, in the format from CLAUDE.md:
the header from `logs` plus one entry per scored subdimension from
`subdimension_results`, in conversation order.

Run from the worker/ folder:
    uv run python scripts/show_log.py <conversation id>            local database
    uv run python scripts/show_log.py <conversation id> --remote   live database
    uv run python scripts/show_log.py --list [--remote]            the latest conversations
"""

import json
import shutil
import subprocess
import sys


def query(sql: str, remote: bool) -> list[dict]:
    """Run a SELECT on the D1 database with pywrangler and return the rows."""
    command = [shutil.which("pywrangler") or "pywrangler", "d1", "execute", "ai-readiness",
               "--remote" if remote else "--local", "--json", "--command", sql]
    output = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    # pywrangler prints an INFO line first; the JSON starts at the first line beginning with "[".
    start = next(i for i, line in enumerate(output.splitlines()) if line.startswith("["))
    return json.loads("\n".join(output.splitlines()[start:]))[0]["results"]


def quoted(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--remote"]
    remote = "--remote" in sys.argv
    if not args:
        sys.exit(__doc__)

    if args[0] == "--list":
        rows = query("SELECT id, created_at, json_extract(state, '$.done') AS done "
                     "FROM conversations ORDER BY created_at DESC LIMIT 20", remote)
        for row in rows:
            print(row["id"], row["created_at"], "finished" if row["done"] else "not finished")
        return

    conversation_id = args[0]
    headers = query(f"SELECT log FROM logs WHERE conversation_id = {quoted(conversation_id)}", remote)
    if not headers:
        sys.exit(f"No conversation {conversation_id}")
    log = json.loads(headers[0]["log"])
    rows = query("SELECT entry FROM subdimension_results "
                 f"WHERE conversation_id = {quoted(conversation_id)} ORDER BY position", remote)
    log["subdimensions"] = [json.loads(row["entry"]) for row in rows]
    print(json.dumps(log, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
