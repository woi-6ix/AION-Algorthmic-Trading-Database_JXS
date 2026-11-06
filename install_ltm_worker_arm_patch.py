"""Backup-first patch to let the existing PAPER worker accept AION LTM's authenticated arm.

Never changes the broker URL, position limits, account checks, or other CLI behavior.
"""
from __future__ import annotations

import ast
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKER = ROOT / "aion_alpaca_paper_algo.py"
OLD_GUARD = "        if not sys.stdin.isatty():raise RuntimeError('Arm requires an interactive terminal')"
NEW_GUARD = '''        if not sys.stdin.isatty():
            # Only a server-side, authenticated AION LTM action may arm over a pipe.
            # This does not bypass the existing account/order/market checks.
            expected = os.environ.get('AION_CONTROL_TOKEN', '').strip()
            approval = os.environ.get('AION_LTM_ARM_APPROVAL', '')
            if len(expected) < 24 or not secrets.compare_digest(approval, expected):
                raise RuntimeError('Arm requires interactive terminal or authenticated AION LTM approval')'''


def patched_source(before: str) -> str:
    if "AION_LTM_ARM_APPROVAL" in before:
        return before
    if before.count(OLD_GUARD) != 1:
        raise RuntimeError("Worker ARM guard differs from supported version. No files changed.")
    if before.count("import secrets\n") > 0:
        imports = before
    elif "import re\n" in before:
        imports = before.replace("import re\n", "import re\nimport secrets\n", 1)
    else:
        raise RuntimeError("Worker imports differ from supported version. No files changed.")
    after = imports.replace(OLD_GUARD, NEW_GUARD, 1)
    ast.parse(after)
    return after


def main():
    if not WORKER.is_file():
        raise SystemExit(f"Worker not found: {WORKER}")
    before = WORKER.read_text(encoding="utf-8")
    after = patched_source(before)
    if after == before:
        print("Worker already supports authenticated AION LTM arm; no change needed.")
        return
    backup = WORKER.with_suffix(WORKER.suffix + "." + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".bak")
    if backup.exists():
        raise SystemExit(f"Backup collision: {backup}")
    backup.write_text(before, encoding="utf-8")
    try:
        WORKER.write_text(after, encoding="utf-8")
    except Exception:
        WORKER.write_text(before, encoding="utf-8")
        raise
    print(f"Patched PAPER worker. Backup: {backup.name}")


if __name__ == "__main__":
    main()