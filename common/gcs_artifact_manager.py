"""GCS 产物管理与上传模块。

本模块负责处理本地产物到 GCS 的路径映射和上传操作。

主要功能：
- parse_gcs_uri：解析 gs://bucket/path URI，提取 bucket 和对象前缀。
- upload_dir_to_gcs：递归上传本地目录所有文件到 GCS，按 run_tag 分桶。
- to_gcs_uri_for_local_file：将本地文件路径映射为对应的 GCS URI（不实际上传）。
- upload_file_to_gcs_by_model：上传单个文件到 GCS，与本地目录结构保持同构。

所有上传操作均按统一规则：<gcs_root>/runs/<run_tag>/<相对本地路径>

这样保证了本地和 GCS 的目录结构一致，便于溯源和对账。
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from google.cloud import storage


def parse_gcs_uri(uri: str) -> Tuple[str, str]:
    """解析 gs://bucket/path，返回 bucket 与对象前缀/路径。"""
    if not uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {uri}")
    body = uri[5:]
    parts = body.split("/", 1)
    bucket = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""
    return bucket, prefix.rstrip("/")


def upload_dir_to_gcs(
    local_dir: Path,
    gcs_uri: str,
    *,
    run_ts: str,
    project_id: str,
    run_tag: str | None = None,
) -> List[str]:
    """将 local_dir 下所有文件上传到 GCS 的本次运行目录。"""
    bucket_name, base_prefix = parse_gcs_uri(gcs_uri)
    storage_client = storage.Client(project=project_id)
    bucket = storage_client.bucket(bucket_name)

    tag = run_tag or run_ts
    uploaded = []
    for local_path in local_dir.rglob("*"):
        if local_path.is_dir():
            continue
        rel = local_path.relative_to(local_dir).as_posix()
        blob_name = f"{base_prefix}/runs/{tag}/{rel}" if base_prefix else f"runs/{tag}/{rel}"
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(str(local_path))
        uploaded.append(f"gs://{bucket_name}/{blob_name}")
    return uploaded


def to_gcs_uri_for_local_file(
    local_dir: Path,
    local_path: Path,
    gcs_uri: str,
    *,
    run_ts: str,
    run_tag: str | None = None,
) -> str:
    """将本地产物路径映射为本次运行对应的 GCS URI。"""
    bucket_name, base_prefix = parse_gcs_uri(gcs_uri)
    tag = run_tag or run_ts
    rel = local_path.relative_to(local_dir).as_posix()
    blob_name = f"{base_prefix}/runs/{tag}/{rel}" if base_prefix else f"runs/{tag}/{rel}"
    return f"gs://{bucket_name}/{blob_name}"


def upload_file_to_gcs_by_model(
    local_dir: Path,
    local_path: Path,
    gcs_uri: str,
    *,
    model_key: str,
    run_ts: str,
    project_id: str,
    run_tag: str | None = None,
) -> str:
    """上传单个文件到 GCS，并放到 <RUN_TAG>/ 目录下（与本地结构一致）。"""
    bucket_name, base_prefix = parse_gcs_uri(gcs_uri)
    storage_client = storage.Client(project=project_id)
    bucket = storage_client.bucket(bucket_name)

    tag = run_tag or run_ts
    rel = local_path.relative_to(local_dir).as_posix()
    if base_prefix:
        blob_name = f"{base_prefix}/runs/{tag}/{rel}"
    else:
        blob_name = f"runs/{tag}/{rel}"

    blob = bucket.blob(blob_name)
    blob.upload_from_filename(str(local_path))
    return f"gs://{bucket_name}/{blob_name}"


def upload_file_to_gcs_uri(local_path: Path, gcs_uri: str, project_id: str) -> str:
    """Upload a local file to an exact GCS URI (gs://bucket/blob/path)."""
    bucket_name, blob_name = parse_gcs_uri(gcs_uri)
    if not blob_name:
        raise ValueError(f"GCS URI must include a blob path: {gcs_uri}")
    storage.Client(project=project_id).bucket(bucket_name).blob(blob_name).upload_from_filename(str(local_path))
    return gcs_uri


def download_file_from_gcs_uri(gcs_uri: str, local_path: Path, project_id: str) -> None:
    """Download a file from an exact GCS URI to a local path, creating parent dirs as needed."""
    bucket_name, blob_name = parse_gcs_uri(gcs_uri)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    storage.Client(project=project_id).bucket(bucket_name).blob(blob_name).download_to_filename(str(local_path))
