from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from sklearn.tree import DecisionTreeRegressor

from modeling.preprocess import prepare_train_test_features


@dataclass(frozen=True)
class TuningObjective:
    primary: str = "mae_min"
    secondary: str = "accuracy_strict_nonzero_max"
    mae_tie_tolerance: float = 1e-9


@dataclass(frozen=True)
class TuningConfig:
    enabled: bool = False
    method: str = "random"  # random | grid
    n_iter: int = 20
    random_seed: int = 42
    internal_validation_ratio: float = 0.2
    internal_split_mode: str = "random"  # random | time_tail
    objective: TuningObjective = TuningObjective()
    search_space: Dict[str, List[object]] | None = None


def resolve_algorithm_key(algorithm_name: str) -> str:
    normalized = str(algorithm_name).strip().lower()
    if normalized in {"dt", "decision_tree", "decision-tree"}:
        return "decision_tree"
    if normalized in {"lgbm", "lightgbm", "light_gbm"}:
        return "lightgbm"
    return normalized


def default_search_space_for(algorithm_key: str) -> Dict[str, List[object]]:
    if algorithm_key == "decision_tree":
        return {
            "max_depth": [4, 5, 6, 8, 10, 12, None],
            "min_samples_split": [8, 12, 16, 24, 32],
            "min_samples_leaf": [4, 8, 12, 16],
            "max_features": ["sqrt", "log2", None],
        }
    if algorithm_key == "lightgbm":
        # Placeholder for future extension.
        return {
            "num_leaves": [31, 63, 127],
            "max_depth": [5, 7, 9, -1],
            "learning_rate": [0.03, 0.05, 0.08],
            "n_estimators": [200, 400, 800],
        }
    raise ValueError(f"Unsupported algorithm for tuning: {algorithm_key}")


def _nonzero_strict_accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    mask = np.abs(y_true) > 1e-12
    if not np.any(mask):
        return float("nan")
    y = y_true[mask]
    p = y_pred[mask]
    ratio = p / y
    strict = (ratio >= 0.8) & (ratio <= 1.2)
    return float(strict.mean() * 100.0)


def _evaluate_decision_tree(
    params: Dict[str, object],
    df_train: pd.DataFrame,
    df_val: pd.DataFrame,
    feature_cols: List[str],
) -> Dict[str, float]:
    x_train, y_train, x_val, y_val, _, _ = prepare_train_test_features(df_train, df_val, feature_cols)
    model = DecisionTreeRegressor(**params)
    model.fit(x_train, y_train)
    y_pred = model.predict(x_val)

    err = y_pred - y_val
    mae = float(np.mean(np.abs(err)))
    strict_nonzero = _nonzero_strict_accuracy(y_val.astype(float), y_pred.astype(float))
    return {
        "mae": mae,
        "accuracy_strict_nonzero_pct": strict_nonzero,
    }


def _evaluate_lightgbm(
    params: Dict[str, object],
    df_train: pd.DataFrame,
    df_val: pd.DataFrame,
    feature_cols: List[str],
) -> Dict[str, float]:
    try:
        from lightgbm import LGBMRegressor  # type: ignore[reportMissingImports]
    except ImportError as exc:
        raise ImportError(
            "LightGBM tuning is selected but lightgbm package is not installed. "
            "Install it with: pip install lightgbm"
        ) from exc

    x_train, y_train, x_val, y_val, _, _ = prepare_train_test_features(df_train, df_val, feature_cols)
    model = LGBMRegressor(**params)
    model.fit(x_train, y_train)
    y_pred = model.predict(x_val)
    del model  # 立即释放 booster 内存，避免 tuning 循环中残留累积

    err = y_pred - y_val
    mae = float(np.mean(np.abs(err)))
    strict_nonzero = _nonzero_strict_accuracy(y_val.astype(float), y_pred.astype(float))
    return {
        "mae": mae,
        "accuracy_strict_nonzero_pct": strict_nonzero,
    }


def _build_trial_candidates(
    *,
    method: str,
    search_space: Dict[str, List[object]],
    n_iter: int,
    random_seed: int,
) -> List[Dict[str, object]]:
    keys = list(search_space.keys())
    values = [search_space[k] for k in keys]
    if not keys:
        return []

    method_norm = str(method).strip().lower()
    if method_norm == "grid":
        combos = [dict(zip(keys, combo)) for combo in itertools.product(*values)]
        return combos

    if method_norm != "random":
        raise ValueError("tuning.method must be one of: random, grid")

    rng = random.Random(random_seed)
    trials: List[Dict[str, object]] = []
    for _ in range(max(1, n_iter)):
        sampled = {k: rng.choice(search_space[k]) for k in keys}
        trials.append(sampled)
    return trials


def _better_trial(
    cand: Dict[str, float],
    best: Dict[str, float],
    objective: TuningObjective,
) -> bool:
    cand_mae = float(cand.get("mae", math.inf))
    best_mae = float(best.get("mae", math.inf))
    tol = float(objective.mae_tie_tolerance)

    if cand_mae < best_mae - tol:
        return True
    if abs(cand_mae - best_mae) <= tol:
        cand_sec = float(cand.get("accuracy_strict_nonzero_pct", float("nan")))
        best_sec = float(best.get("accuracy_strict_nonzero_pct", float("nan")))
        if np.isnan(best_sec) and not np.isnan(cand_sec):
            return True
        if np.isnan(cand_sec):
            return False
        return cand_sec > best_sec
    return False


def tune_entity_params(
    *,
    algorithm_key: str,
    base_params: Dict[str, object],
    tuning_cfg: TuningConfig,
    df_train: pd.DataFrame,
    df_val: pd.DataFrame,
    feature_cols: List[str],
) -> Tuple[Dict[str, object], Dict[str, float], List[Dict[str, object]]]:
    if not tuning_cfg.enabled:
        raise ValueError("tune_entity_params called when tuning is disabled")

    if len(df_train) < 2 or len(df_val) < 1:
        raise ValueError("insufficient rows for tuning")

    if algorithm_key not in {"decision_tree", "lightgbm"}:
        raise ValueError(f"Unsupported algorithm for tuning: {algorithm_key}")

    objective = tuning_cfg.objective
    search_space = tuning_cfg.search_space or default_search_space_for(algorithm_key)
    candidates = _build_trial_candidates(
        method=tuning_cfg.method,
        search_space=search_space,
        n_iter=tuning_cfg.n_iter,
        random_seed=tuning_cfg.random_seed,
    )
    if not candidates:
        raise ValueError("no tuning candidates generated")

    best_params: Dict[str, object] = {}
    best_metrics: Dict[str, float] = {"mae": math.inf, "accuracy_strict_nonzero_pct": float("nan")}
    trial_rows: List[Dict[str, object]] = []

    for idx, cand in enumerate(candidates, start=1):
        trial_params = {**base_params, **cand}
        if algorithm_key == "decision_tree":
            metrics = _evaluate_decision_tree(trial_params, df_train, df_val, feature_cols)
        else:
            metrics = _evaluate_lightgbm(trial_params, df_train, df_val, feature_cols)
        trial_rows.append(
            {
                "trial_no": idx,
                "algorithm_key": algorithm_key,
                "params_json": str(trial_params),
                "mae": metrics["mae"],
                "accuracy_strict_nonzero_pct": metrics["accuracy_strict_nonzero_pct"],
            }
        )
        if _better_trial(metrics, best_metrics, objective):
            best_metrics = metrics
            best_params = trial_params

    if not best_params:
        raise RuntimeError("failed to identify best tuning params")

    return best_params, best_metrics, trial_rows
