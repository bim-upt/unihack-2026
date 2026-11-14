from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"   # project root


def load_env_file(path: Path | str | None = None, override: bool = False) -> int:
    """Load KEY=VALUE lines into os.environ. Returns how many were set."""
    p = Path(path or os.getenv("TRANSIT_ENV_FILE") or DEFAULT_ENV_FILE)
    if not p.is_file():
        return 0
    count = 0
    for raw in p.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and (override or key not in os.environ):
            os.environ[key] = value
            count += 1
    return count