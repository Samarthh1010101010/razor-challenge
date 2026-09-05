"""Load a local .env into the environment. Standard library only.

`.env.example` documented this file for months before anything read it, so a key
placed there did nothing and the run silently stayed in offline mode -- the
worst kind of configuration bug, because it looks like it worked.

Real environment variables always win: a value already exported is never
overwritten, so a cloud environment's settings cannot be shadowed by a stale
file left in a checkout.
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT = Path(".env")


def load(path: Path = DEFAULT) -> list[str]:
    """Apply KEY=value lines from `path`. Returns the names it set."""
    if not path.exists():
        return []
    applied = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and not os.environ.get(key):     # exported values win
            os.environ[key] = value
            applied.append(key)
    return applied
