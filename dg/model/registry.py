"""Model version registry with weights-file provenance hash."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Tuple

import yaml

from dg import config


def load_weights(path: Path | None = None) -> Dict[str, Any]:
    path = path or config.WEIGHTS_PATH
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "weights" not in data:
        raise ValueError(f"Invalid weights file: {path}")
    return data


def weights_hash(path: Path | None = None) -> str:
    path = path or config.WEIGHTS_PATH
    content = path.read_bytes()
    return hashlib.sha256(content).hexdigest()[:10]


def model_version(path: Path | None = None) -> str:
    data = load_weights(path)
    base = data.get("version", "rule_v1")
    from dg.model.markets import markets_model_tag

    return f"{base}_{weights_hash(path)}+{markets_model_tag()}"


def load_config(path: Path | None = None) -> Tuple[str, Dict[str, Any]]:
    data = load_weights(path)
    return model_version(path), data
