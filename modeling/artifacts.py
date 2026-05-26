"""建模产物持久化模块。

包含模型文件、模型元数据、预测明细、特征重要性等产物的
本地落盘与可选 GCS 上传辅助函数。
"""

from __future__ import annotations

import json
import pickle
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import pandas as pd

from common.gcs_artifact_manager import upload_file_to_gcs_by_model


def save_model_and_metadata(
    model_name: str,
    entity_id_json: str,
    item_dir: Path,
    out_dir: Path,
    run_ts: str,
    source_ref: str,
    feature_cols: List[str],
    model_params,
    model,
    model_key: str,
    algorithm_name: str,
    algorithm_version: str,
    run_tag: str,
    gcs_output_uri: str,
    project_id: str,
    config_name: str,
    store_model_meta_to_gcs: bool,
    store_model_to_gcs: bool = False,
):
    model_pkl = item_dir / f"{model_key}_model_{run_ts}.pkl"
    with model_pkl.open("wb") as f:
        pickle.dump(model, f)

    model_meta = {
        "model_name": model_name,
        "entity_id_json": entity_id_json,
        "model_type": algorithm_name,
        "model_version": algorithm_version,
        "timestamp": run_ts,
        "source_table": source_ref,
        "feature_count": len(feature_cols),
        "features": feature_cols,
        "params": model_params,
        "artifact_files": {"model_pkl_local": str(model_pkl)},
    }
    model_meta_json = item_dir / f"{model_key}_model_metadata_{run_ts}.json"
    model_meta_json.write_text(json.dumps(model_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    model_pkl_gcs = ""
    if store_model_to_gcs:
        model_pkl_gcs = upload_file_to_gcs_by_model(
            local_dir=out_dir,
            local_path=model_pkl,
            gcs_uri=gcs_output_uri,
            model_key=model_key,
            run_ts=run_ts,
            project_id=project_id,
            run_tag=run_tag,
        )

    model_meta_json_gcs = ""
    if store_model_meta_to_gcs:
        model_meta_json_gcs = upload_file_to_gcs_by_model(
            local_dir=out_dir,
            local_path=model_meta_json,
            gcs_uri=gcs_output_uri,
            model_key=model_key,
            run_ts=run_ts,
            project_id=project_id,
            run_tag=run_tag,
        )

    model_meta_row = {
        "run_id": run_ts,
        "run_ts": datetime.utcnow().isoformat() + "Z",
        "model_name": model_name,
        "entity_id_json": entity_id_json,
        "model_type": algorithm_name,
        "model_version": algorithm_version,
        "source_table": source_ref,
        "feature_count": len(feature_cols),
        "features_json": json.dumps(feature_cols, ensure_ascii=False),
        "params_json": json.dumps(model_params if isinstance(model_params, dict) else {}, ensure_ascii=False),
        "model_pkl_path": str(model_pkl),
        "model_metadata_json_path": model_meta_json_gcs if model_meta_json_gcs else str(model_meta_json),
        "config_name": config_name,
        "gcs_run_uri": f"{gcs_output_uri.rstrip('/')}/{run_tag}/",
    }

    return {
        "model_pkl": model_pkl,
        "model_pkl_gcs": model_pkl_gcs,
        "model_meta_json": model_meta_json,
        "model_meta_json_gcs": model_meta_json_gcs,
        "model_meta_row": model_meta_row,
    }


def save_predictions_csv(
    pred_all_df: pd.DataFrame,
    item_dir: Path,
    out_dir: Path,
    run_ts: str,
    run_tag: str,
    gcs_output_uri: str,
    project_id: str,
    model_key: str,
    store_pred_to_gcs: bool,
) -> Dict:
    pred_all_csv = item_dir / f"{model_key}_predictions_train_test_{run_ts}.csv"
    pred_all_df.to_csv(pred_all_csv, index=False)

    pred_all_csv_gcs = ""
    if store_pred_to_gcs:
        pred_all_csv_gcs = upload_file_to_gcs_by_model(
            local_dir=out_dir,
            local_path=pred_all_csv,
            gcs_uri=gcs_output_uri,
            model_key=model_key,
            run_ts=run_ts,
            project_id=project_id,
            run_tag=run_tag,
        )

    return {"pred_all_csv": pred_all_csv, "pred_all_csv_gcs": pred_all_csv_gcs}


def save_feature_importance_csv(
    feat_imp_df: pd.DataFrame,
    item_dir: Path,
    out_dir: Path,
    run_ts: str,
    run_tag: str,
    gcs_output_uri: str,
    project_id: str,
    model_key: str,
    store_feat_imp_to_gcs: bool,
) -> Dict:
    feat_imp_csv = item_dir / f"{model_key}_feature_importance_{run_ts}.csv"
    feat_imp_df.to_csv(feat_imp_csv, index=False)

    feat_imp_csv_gcs = ""
    if store_feat_imp_to_gcs:
        feat_imp_csv_gcs = upload_file_to_gcs_by_model(
            local_dir=out_dir,
            local_path=feat_imp_csv,
            gcs_uri=gcs_output_uri,
            model_key=model_key,
            run_ts=run_ts,
            project_id=project_id,
            run_tag=run_tag,
        )

    return {"feat_imp_csv": feat_imp_csv, "feat_imp_csv_gcs": feat_imp_csv_gcs}
