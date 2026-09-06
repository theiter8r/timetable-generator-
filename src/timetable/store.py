"""Persistence: the whole configuration is one JSON file on disk."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .models import Config
from .onboarding import OnboardingState

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("TIMETABLE_DATA_DIR", ROOT / "data"))
CONFIG_PATH = DATA_DIR / "config.json"
SAMPLE_PATH = DATA_DIR / "sample_config.json"
# Where the setup wizard is up to. Kept out of config.json so that the config
# stays purely the thing the solver reads, and so that resetting one does not
# silently discard the other.
ONBOARDING_PATH = DATA_DIR / "onboarding.json"


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


def _write_json(payload: dict, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    # Write via a temp file so an interrupted save can't truncate a good file.
    # The temp name carries the writer's id because two requests really can be
    # writing at once -- the browser loads the config and the wizard state in
    # parallel, and on a first run both of them seed the file. A shared temp
    # name makes that race a crash; a unique one makes it harmless, with
    # last-write-wins on the atomic replace.
    tmp = target.with_name(f"{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2) + "\n")
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)


def save_config(config: Config, path: Path | None = None) -> None:
    _write_json(config.model_dump(mode="json"), path or CONFIG_PATH)


def reset_config(path: Path | None = None) -> Config:
    """Throw away edits and go back to the shipped sample dataset."""
    config = load_sample()
    save_config(config, path)
    return config


# --- setup wizard progress ---------------------------------------------


def load_onboarding(path: Path | None = None) -> OnboardingState:
    """Where the setup wizard left off. A missing file means "never started"."""
    target = path or ONBOARDING_PATH
    if not target.exists():
        return OnboardingState()
    try:
        return OnboardingState.model_validate_json(target.read_text())
    except ValueError:
        # Progress is not worth crashing the app over; start the wizard again.
        return OnboardingState()


def save_onboarding(state: OnboardingState, path: Path | None = None) -> OnboardingState:
    state.touch()
    _write_json(state.model_dump(mode="json"), path or ONBOARDING_PATH)
    return state
