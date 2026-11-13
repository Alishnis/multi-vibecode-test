#!/usr/bin/env python3
"""UserPromptSubmit hook: adds the team contract to the agent's context.

Claude Code runs this script every time the user sends a prompt. Whatever the
script prints is added to what the agent sees, next to the prompt.

It uses only Python's built-in libraries, so teammates install nothing.

Rule: this script must never stop the user's prompt. If anything goes wrong
(no me.json, server down), it exits with code 0. It then prints one short note
for the agent, so the agent knows it is working without the contract instead
of silently guessing. The technical reason goes to stderr, which Claude Code
shows only in debug mode.
"""

import json
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # the coord folder
try:
    from tc_http import urlopen
except ImportError:  # an older install without coord/tc_http.py
    from urllib.request import urlopen

ME_FILE = Path(__file__).resolve().parent.parent / "me.json"  # coord/me.json
TIMEOUT_SECONDS = 3


def give_up(reason: str, note: str) -> None:
    print(f"contract hook skipped: {reason}", file=sys.stderr)
    print(f"[Team Contract] {note}")
    sys.exit(0)


def main() -> None:
    try:
        me = json.loads(ME_FILE.read_text())
        room, server = me["room"], me["server"].rstrip("/")
    except FileNotFoundError:
        give_up(
            f"{ME_FILE} not found",
            "This project uses a shared team contract, but it is not set up on this "
            "computer (coord/me.json is missing). Tell the user once that they can "
            "join with the Team Contract installer; until then, teammates' decisions "
            "are unknown.",
        )
    except (json.JSONDecodeError, KeyError, AttributeError) as error:
        give_up(
            f"{ME_FILE} is not valid: {error!r}",
            "The team contract could not be loaded because coord/me.json is not "
            "valid. Tell the user to rerun the Team Contract installer.",
        )

    query = {"room": room, "name": me.get("name", "")}  # the name marks this person as active
    url = f"{server}/contract?" + urllib.parse.urlencode(query)
    try:
        with urlopen(url, timeout=TIMEOUT_SECONDS) as response:
            contract = json.load(response)
    except Exception as error:  # server down, still waking up, wrong address, bad reply
        give_up(
            f"could not reach {url}: {error!r}",
            "The team contract could not be loaded for this prompt: the server did "
            "not answer. Decisions made by teammates are unknown right now. Before "
            "choosing a framework, database, API shape, field name or port, tell "
            "the user that the contract is unavailable and ask whether to continue.",
        )

    print(contract["text"])
    if contract["decisions"]:
        print(
            "These decisions apply to this project. When a request contradicts one "
            "of them, the requested decision is first submitted with the "
            "team-contract record_decision tool, so that the conflict is logged for "
            "the whole team, and then the conflict is raised with the user before "
            "any code is written."
        )
    # Repeated on every prompt, because agents forget instructions given once.
    print(
        "Before a plan that touches shared parts of the project, use team-contract "
        "check_plan. A contradiction is raised with the user before code is written. "
        "A choice the team has not made yet is decided within the user's task and "
        "recorded; request_decision is only for questions the user wants the whole "
        "team to answer. Questions listed above as open are not guessed."
    )
    print(
        "New project decisions made in this session (framework, library, database, "
        "API endpoint, data field name, port, convention) are recorded with the "
        "team-contract record_decision tool before the code for them is written, "
        "one call per decision, so that teammates' agents see them."
    )


if __name__ == "__main__":
    main()
