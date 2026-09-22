"""Shared paths, config loading, and the metric/status envelope every script uses.

Guardrail: every metric is logged with its source and timestamp so the report
never reads as more certain than the underlying free/manual tool supports.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

SEO_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = SEO_DIR / "config"
DATA_DIR = SEO_DIR / "data"
SNAPSHOT_DIR = SEO_DIR / "snapshots"
REPORT_DIR = SEO_DIR / "reports"
DRAFT_DIR = SEO_DIR / "drafts"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def today() -> str:
    return dt.date.today().isoformat()


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open() as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_config(name: str) -> Any:
    return load_json(CONFIG_DIR / f"{name}.json")


def metric(value: Any, source: str, **extra: Any) -> dict:
    """Wrap a value with where it came from and when."""
    return {"value": value, "source": source, "ts": now_iso(), **extra}


def step_result(status: str, source: str, data: Any = None, note: str | None = None) -> dict:
    """Uniform result for one collection step.

    status: "ok" | "partial" | "skipped" | "error" | "needs_human"
    """
    out = {"status": status, "source": source, "ts": now_iso(), "data": data}
    if note:
        out["note"] = note
    return out


def env(name: str) -> str | None:
    val = os.environ.get(name, "").strip()
    return val or None
