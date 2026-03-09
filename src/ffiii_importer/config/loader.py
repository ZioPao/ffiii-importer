from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import AppSettings, BankMappingsFile


def load_settings(path: Path) -> AppSettings:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"Settings file not found: {path}")
    except yaml.YAMLError as e:
        raise SystemExit(f"Invalid YAML in settings file: {e}")

    try:
        return AppSettings.model_validate(raw)
    except ValidationError as e:
        raise SystemExit(f"Invalid settings configuration:\n{e}")


def load_bank_mappings(path: Path) -> BankMappingsFile:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"Bank mappings file not found: {path}")
    except yaml.YAMLError as e:
        raise SystemExit(f"Invalid YAML in bank mappings file: {e}")

    try:
        return BankMappingsFile.model_validate(raw)
    except ValidationError as e:
        raise SystemExit(f"Invalid bank mappings configuration:\n{e}")
