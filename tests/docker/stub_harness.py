"""A scripted stand-in for a harness, used by tests/test_run_task_docker.py.

It follows the adapter contract (spec §6): reads $HB_TASK_FILE, asks the model
at $OPENAI_BASE_URL, works in /workspace. The test's stub provider answers with
a JSON directive as the message content:

    {"write": {path: text}, "symlink": {path: target}, "then": "exit" | "again" | "sleep", "code": n}

On HTTP 429 (budget spent) it sleeps, so the toolkit has to end the rep; on any
other error status it exits 3.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request


def ask() -> dict:
    with open(os.environ["HB_TASK_FILE"], encoding="utf-8") as fh:
        instruction = fh.read()
    body = json.dumps({"model": os.environ["HB_MODEL"],
                       "messages": [{"role": "user", "content": instruction}]}).encode()
    request = urllib.request.Request(
        os.environ["OPENAI_BASE_URL"] + "/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(json.loads(response.read())["choices"][0]["message"]["content"])


def main() -> int:
    while True:
        try:
            directive = ask()
        except urllib.error.HTTPError as exc:
            print(f"stub-harness: gateway answered {exc.code}", flush=True)
            if exc.code == 429:
                time.sleep(3600)
            return 3
        for path, text in directive.get("write", {}).items():
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        for path, target in directive.get("symlink", {}).items():
            os.symlink(target, path)
        print("stub-harness: applied", sorted(directive.get("write", {})), flush=True)
        then = directive.get("then", "exit")
        if then == "again":
            continue
        if then == "sleep":
            time.sleep(3600)
        return int(directive.get("code", 0))


if __name__ == "__main__":
    sys.exit(main())
