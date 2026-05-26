# Unified Hyperparameter Tuning Method

## Scope

This project now supports a unified tuning framework at entity level.

- Current algorithm support: `decision_tree` (implemented)
- Reserved algorithm entry: `lightgbm` / `lgbm` (placeholder)

Tuning runs before final model fit for each entity and outputs run-level records.

## Objective Priority (Lexicographic)

Default objective priority is:

1. Primary: minimize `MAE`
2. Secondary: maximize `accuracy_strict_nonzero` when MAE is tied

Tie definition:

- If absolute MAE difference is less than or equal to `mae_tie_tolerance`, it is treated as tied.

This rule enforces deterministic selection consistent with business preference.

## Parameter Precedence

When `tuning.enabled=true`:

- Static model parameters from config are treated as base defaults only.
- Tuned best parameters override configured parameters for final fit.
- Runtime logs explicitly state that configured params are ignored as final effective params.

When `tuning.enabled=false`:

- Configured model parameters are used directly.

## Search Methods

### Random Search (default)

- `method: random`
- Candidate count controlled by `n_iter`
- Randomness controlled by `random_seed`

### Grid Search (optional)

- `method: grid`
- Full cartesian product over configured search space

### Search Space

Search space is algorithm keyed:

- `tuning.search_space.decision_tree` for DecisionTreeRegressor
- If omitted, built-in default search space is used for decision tree.

## Internal Validation for Tuning

Tuning requires train/validation split per entity.

- If in-sample validation already exists in main pipeline, that split is reused.
- Otherwise, tuning creates internal split from full train data with:
  - `internal_validation_ratio`
  - `internal_split_mode` (`random` or `time_tail`)
  - `random_seed`

## Output Artifacts (Run Level)

When tuning is enabled, these files are generated under run directory:

- `tuning_trials_<RUN_TS>.csv`: all trial records by entity
- `tuning_best_params_<RUN_TS>.csv`: best params and best metrics by entity
- `effective_params_by_entity_<RUN_TS>.csv`: effective params source and payload by entity
- `effective_model_params_<RUN_TS>.json`: model_name to effective params mapping

## Runtime Config Example

```yaml
tuning:
  enabled: true
  method: random
  n_iter: 20
  random_seed: 42
  internal_validation_ratio: 0.2
  internal_split_mode: random
  objective:
    primary: mae_min
    secondary: accuracy_strict_nonzero_max
    mae_tie_tolerance: 1.0e-9
  search_space:
    decision_tree:
      max_depth: [3, 5, 8, 12, null]
      min_samples_split: [2, 5, 10, 20]
      min_samples_leaf: [1, 2, 4, 8]
      max_features: [null, "sqrt", "log2"]
      ccp_alpha: [0.0, 0.001, 0.01]
```

## Extension Notes for LightGBM

LightGBM entry is intentionally reserved in code path.

- Add candidate generator and scorer in `modeling/tuning.py`
- Keep same objective comparison and trial output schema
- No pipeline contract change is required
