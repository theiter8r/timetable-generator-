"""Persistence: the whole configuration is one JSON file on disk."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .models import Config

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("TIMETABLE_DATA_DIR", ROOT / "data"))
CONFIG_PATH = DATA_DIR / "config.json"
SAMPLE_PATH = DATA_DIR / "sample_config.json"


def load_sample() -> Config:
    return Config.model_validate_json(SAMPLE_PATH.read_text())


def load_config(path: Path | None = None) -> Config:
    """Load the working config, seeding it from the sample on first run."""
    target = path or CONFIG_PATH
    if not target.exists():
        config = load_sample()
        save_config(config, target)
        return config
    return Config.model_validate_json(target.read_text())


def save_config(config: Config, path: Path | None = None) -> None:
    target = path or CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config.model_dump(mode="json"), indent=2)
    # Write via a temp file so an interrupted save can't truncate a good config.
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(payload + "\n")
    tmp.replace(target)


def reset_config(path: Path | None = None) -> Config:
    """Throw away edits and go back to the shipped sample dataset."""
    config = load_sample()
    save_config(config, path)
    return config
