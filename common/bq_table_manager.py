from __future__ import annotations

"""BigQuery 输出表通用管理模块。

本模块用于统一处理三类重复逻辑：
1. 表结构兼容性校验（schema / partition / clustering）
2. 建表 SQL 生成与执行
3. 目标表解析与 fallback（结构不兼容时加 run_ts 后缀）

设计目标是让主流程脚本只关注业务逻辑：
- 声明每张表的 TableSpec
- 调用 resolve_bq_table 拿到可写入的最终表名
"""

from dataclasses import dataclass
from typing import Dict, List, Tuple

from google.api_core.exceptions import NotFound
from google.cloud import bigquery


def build_bq_schema(field_defs: List[Tuple[str, str]]) -> List[bigquery.SchemaField]:
    """按 (字段名, 字段类型) 定义构建 BigQuery SchemaField 列表。"""
    return [bigquery.SchemaField(name, field_type) for name, field_type in field_defs]


@dataclass(frozen=True)
class TableSpec:
    """描述一张 BigQuery 输出表的结构与治理规则。"""

    key: str
    label: str
    env_name: str
    schema: List[bigquery.SchemaField]
    partition_expr: str
    partition_field: str
    cluster_fields: List[str]


def _normalize_schema_map(schema: List[bigquery.SchemaField]) -> Dict[str, Tuple[str, str]]:
    """将 schema 标准化为可比对映射：field -> (type, mode)。"""

    return {
        field.name: (field.field_type.upper(), (field.mode or "NULLABLE").upper())
        for field in schema
    }


def bq_table_compatible(table: bigquery.Table, spec: TableSpec) -> bool:
    """判断现有 BigQuery 表是否满足 TableSpec 约束。"""

    expected_map = _normalize_schema_map(spec.schema)
    actual_map = _normalize_schema_map(list(table.schema))
    if expected_map != actual_map:
        return False

    part_field = None
    if table.time_partitioning is not None:
        part_field = table.time_partitioning.field
    if part_field != spec.partition_field:
        return False

    actual_cluster = list(table.clustering_fields or [])
    if actual_cluster != spec.cluster_fields:
        return False

    return True


def _schema_field_sql(field: bigquery.SchemaField) -> str:
    """将 SchemaField 转成 CREATE TABLE 语句中的字段片段。"""

    mode = (field.mode or "NULLABLE").upper()
    field_type = field.field_type.upper()
    if mode == "REPEATED":
        return f"{field.name} ARRAY<{field_type}>"
    if mode == "REQUIRED":
        return f"{field.name} {field_type} NOT NULL"
    return f"{field.name} {field_type}"


def create_bq_table(client: bigquery.Client, table_id: str, spec: TableSpec) -> None:
    """按 TableSpec 创建目标表（不存在时）。"""

    if len(table_id.split(".")) != 3:
        raise ValueError(f"{spec.env_name} must be full name: project.dataset.table")

    column_sql = ",\n      ".join(_schema_field_sql(field) for field in spec.schema)
    cluster_sql = ""
    if spec.cluster_fields:
        cluster_sql = f"\n    CLUSTER BY {', '.join(spec.cluster_fields)}"

    create_sql = f"""
    CREATE TABLE IF NOT EXISTS `{table_id}` (
      {column_sql}
    )
    PARTITION BY {spec.partition_expr}{cluster_sql}
    """
    client.query(create_sql).result()


def resolve_bq_table(
    client: bigquery.Client,
    requested_table_id: str,
    spec: TableSpec,
    run_ts: str,
) -> str:
    """解析最终可写入表名。

    规则：
    - 空字符串：返回空字符串（代表关闭该输出）
    - 表不存在：按 spec 创建后返回原表名
    - 表存在且兼容：返回原表名
    - 表存在但不兼容：创建 <table>_<run_ts> 并返回 fallback 表名
    """

    if not requested_table_id.strip():
        return ""
    if len(requested_table_id.split(".")) != 3:
        raise ValueError(f"{spec.env_name} must be full name: project.dataset.table")

    project_id, dataset_id, table_name = requested_table_id.split(".")
    try:
        table = client.get_table(requested_table_id)
    except NotFound:
        create_bq_table(client, requested_table_id, spec)
        print(f"[INFO] Created {spec.label}: {requested_table_id}", flush=True)
        return requested_table_id

    if bq_table_compatible(table, spec):
        print(f"[INFO] Reusing compatible {spec.label}: {requested_table_id}", flush=True)
        return requested_table_id

    fallback_table_id = f"{project_id}.{dataset_id}.{table_name}_{run_ts}"
    create_bq_table(client, fallback_table_id, spec)
    print(
        f"[WARN] Existing {spec.label} schema/partition/clustering mismatch. "
        f"Created fallback table: {fallback_table_id}",
        flush=True,
    )
    return fallback_table_id
