#!/usr/bin/env python
"""
Test script to verify unified configuration loading works correctly.

Usage:
    python test_config_loading.py
    python test_config_loading.py --config config/profiles/item_day/config_v001.yaml
"""

import sys
from pathlib import Path

# Add common module to path
sys.path.insert(0, str(Path(__file__).parent))

from common.config_loader import (
    get_model_identity,
    get_model_type_params,
    load_unified_config,
    normalize_model_key,
    resolve_active_model,
)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default="config/profiles/item_channel_ma_week/config_v001.yaml",
        help="Path to unified profile config file",
    )
    args = parser.parse_args()

    print("Testing unified configuration loading...\n")

    try:
        # Load unified config
        print(f"[1] Loading unified config: {args.config}")
        cfg = load_unified_config(args.config)
        print("✅ Config loaded successfully!\n")

        # Verify required sections
        print("[2] Verifying required sections...")
        for section in ("versioning", "scenario_profiles", "runtime", "model", "bq_tables"):
            assert section in cfg, f"Missing section: {section}"
            print(f"  ✅ {section}")
        print()

        # Resolve active model
        print("[3] Resolving active model...")
        active_key = resolve_active_model(cfg["model"])
        normalized = normalize_model_key(active_key)
        identity = get_model_identity(active_key, cfg["model"])
        print(f"  ✅ active_model={normalized} | algorithm_name={identity['algorithm_name']} | version={identity['algorithm_version']}\n")

        # Get model hyperparams
        print("[4] Getting model hyperparams...")
        params = get_model_type_params(active_key, cfg["model"])
        print(f"  ✅ {normalized} params ({len(params)} keys): {params}\n")

        # Check scenario_profiles
        print("[5] Checking scenario_profiles...")
        for name, profile in cfg["scenario_profiles"].items():
            sm = profile.get("source_mode", "?")
            print(f"  ✅ {name}: source_mode={sm}")
        print()

        # Check runtime fields
        print("[6] Checking runtime fields...")
        rt = cfg["runtime"]
        for field in ("label_column", "time_column", "features", "entity_id_columns", "time_windows"):
            val = rt.get(field)
            if isinstance(val, list):
                print(f"  ✅ {field}: {len(val)} items")
            else:
                print(f"  ✅ {field}: {val}")
        print()

        print("=" * 80)
        print("✅ ALL TESTS PASSED!")
        print("=" * 80)
        print("\nYou can now run:")
        print(f"  python main.py --scenario bq_local_local --config {args.config} --max-entities 1")

        return 0

    except Exception as e:
        print(f"❌ ERROR: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

