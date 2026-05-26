"""
Configuration loader for gcp_python_modeling.

Unified entry point: load_unified_config() reads a single profile config file
(config/profiles/<model_line>/config_vXXX.yaml) and merges it with the system
scenario defaults (config/system/scenario_defaults.yaml).

Returned structure from load_unified_config():
  {
    "versioning":        dict   — version metadata
    "scenario_profiles": dict   — resolved scenario profiles (system + user overrides)
    "runtime":           dict   — business params (entity columns, features, time windows…)
    "model":             dict   — model hyperparams (includes "active" key)
    "bq_tables":         dict   — BQ output table names (empty dict if not specified)
  }
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml
except ImportError:
    raise ImportError("PyYAML is required. Install it with: pip install PyYAML")


# Default path for system scenario defaults (relative to config dir)
_SYSTEM_DEFAULTS_FILE = "system/scenario_defaults.yaml"


def get_config_dir() -> Path:
    """Return the config directory (gcp_python_modeling/config/)."""
    return Path(__file__).parent.parent / "config"


def _resolve_config_path(config_file: str, *, kind: str) -> Path:
    config_path = Path(config_file)
    if not config_path.is_absolute() and not config_path.exists():
        config_path = get_config_dir() / config_file
    if not config_path.exists():
        raise FileNotFoundError(f"{kind} config file not found: {config_path}")
    return config_path


def _load_yaml(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        raise ValueError(f"Config file is empty: {path}")
    if not isinstance(data, dict):
        raise ValueError(f"Config file must be a YAML mapping: {path}")
    return data


def _resolve_effective_scenario_profiles(
    system_profiles: Dict[str, Any],
    user_output: Dict[str, Any],
    user_source: Dict[str, Any],
) -> Dict[str, Any]:
    """Build resolved scenario profiles by applying user overrides onto system defaults.

    Override rules:
    - storage.local_output_dir → overridden by user output.local_output_dir (when set)
    - storage.gcs_output_uri   → overridden by user output.gcs_output_uri (when set)
    - source_table / source_filters / source_csv_uri / source_csv_local_path
        → injected from user source.<scenario_name>
    """
    effective = copy.deepcopy(system_profiles)

    user_local_dir = str(user_output.get("local_output_dir", "")).strip()
    user_gcs_uri = str(user_output.get("gcs_output_uri", "")).strip()

    for scenario_name, profile in effective.items():
        storage = profile.setdefault("storage", {})

        if user_local_dir and "local_output_dir" in storage:
            storage["local_output_dir"] = user_local_dir
        if user_gcs_uri and "gcs_output_uri" in storage:
            storage["gcs_output_uri"] = user_gcs_uri

        src = user_source.get(scenario_name, {})
        if not isinstance(src, dict):
            src = {}
        for src_key in (
            "source_table",
            "source_filters",
            "source_csv_uri",
            "source_csv_local_path",
        ):
            val = src.get(src_key)
            if val is not None and (not isinstance(val, str) or val.strip()):
                profile[src_key] = val

    return effective


# =============================================================================
# Public API
# =============================================================================


def load_unified_config(
    config_file: str,
    system_defaults_file: Optional[str] = None,
) -> Dict[str, Any]:
    """Load a unified profile config and merge with system scenario defaults.

    Args:
        config_file: Path to user profile config
                     (e.g. config/profiles/item_day/config_v001.yaml).
                     Relative paths are resolved from the config dir.
        system_defaults_file: Path to system scenario defaults.
                              Defaults to config/system/scenario_defaults.yaml.

    Returns:
        Dict with keys:
          versioning        — versioning metadata
          scenario_profiles — resolved scenario profiles (system defaults + user overrides)
          runtime           — business params (entity columns, features, time windows…)
          model             — model hyperparams (includes 'active' key)
          bq_tables         — BQ output table names (empty dict if not specified)
    """
    cfg_path = _resolve_config_path(config_file, kind="Unified config")
    cfg = _load_yaml(cfg_path)

    sys_file = system_defaults_file if system_defaults_file is not None else _SYSTEM_DEFAULTS_FILE
    sys_path = _resolve_config_path(sys_file, kind="System scenario defaults")
    sys_cfg = _load_yaml(sys_path)

    system_profiles = sys_cfg.get("scenario_profiles", {})
    if not isinstance(system_profiles, dict):
        raise ValueError(
            f"system scenario_defaults must have a 'scenario_profiles' mapping: {sys_path}"
        )

    user_output = cfg.get("output") or {}
    user_source = cfg.get("source") or {}
    runtime = cfg.get("runtime") or {}
    model = cfg.get("model") or {}
    bq_tables = cfg.get("bq_tables") or {}
    versioning = cfg.get("versioning") or {}

    if not isinstance(runtime, dict) or not runtime:
        raise ValueError(f"Unified config missing non-empty 'runtime' section: {cfg_path}")
    if not isinstance(model, dict) or not model:
        raise ValueError(f"Unified config missing non-empty 'model' section: {cfg_path}")

    effective_profiles = _resolve_effective_scenario_profiles(
        system_profiles, user_output, user_source
    )

    return {
        "versioning": versioning,
        "scenario_profiles": effective_profiles,
        "runtime": runtime,
        "model": model,
        "bq_tables": bq_tables,
    }


def normalize_model_key(model_type: str) -> str:
    """Normalize model aliases to canonical config keys."""
    normalized = str(model_type).strip().lower()
    alias_map = {
        "dt": "decision_tree",
        "decision-tree": "decision_tree",
        "decision_tree": "decision_tree",
        "lgbm": "lightgbm",
        "light_gbm": "lightgbm",
        "lightgbm": "lightgbm",
    }
    return alias_map.get(normalized, normalized)


def resolve_active_model(
    model_section: Dict[str, Any],
    override_model: Optional[str] = None,
) -> str:
    """Resolve the active model key for this run.

    Priority:
    1) override_model (CLI --model-type)
    2) model_section["active"]
    """
    raw = override_model if override_model is not None else model_section.get("active")
    if raw is None:
        raise ValueError(
            "model section missing 'active'; specify --model-type or set model.active in config"
        )
    key = normalize_model_key(str(raw))
    if key not in model_section:
        available = [k for k in model_section if k not in ("active",)]
        raise ValueError(
            f"active model '{raw}' -> '{key}' not found in model section; available={available}"
        )
    return key


def get_model_type_params(model_type: str, model_section: Dict[str, Any]) -> Dict[str, Any]:
    """Extract hyperparams for a model type from cfg["model"].

    Metadata keys (description, algorithm_name, version) are stripped from the result.
    """
    key = normalize_model_key(model_type)
    if key not in model_section:
        available = [k for k in model_section if k not in ("active",)]
        raise KeyError(
            f"Model type '{model_type}' not found in model config. Available: {available}"
        )
    params = model_section[key]
    if isinstance(params, dict):
        meta_keys = {"description", "algorithm_name", "version"}
        params = {k: v for k, v in params.items() if k not in meta_keys}
    return params


def get_model_identity(model_type: str, model_section: Dict[str, Any]) -> Dict[str, str]:
    """Return algorithm display name and version from cfg["model"]."""
    key = normalize_model_key(model_type)
    section = model_section.get(key, {}) if isinstance(model_section, dict) else {}
    if not isinstance(section, dict):
        section = {}

    default_name_map = {"decision_tree": "DT", "lightgbm": "LGBM"}
    algorithm_name = str(
        section.get("algorithm_name") or default_name_map.get(key, key.upper())
    ).strip() or default_name_map.get(key, key.upper())
    algorithm_version = str(section.get("version") or "Default").strip() or "Default"

    return {"algorithm_name": algorithm_name, "algorithm_version": algorithm_version}
