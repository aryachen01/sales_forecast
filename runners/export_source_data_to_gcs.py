"""
导出建模源数据到 GCS（使用个人账号执行，绕过 Cloud Run 服务账号权限问题）。

使用方式：
  python export_source_data_to_gcs.py

执行完成后，脚本会打印 GCS URI，
直接复制该 URI 写入 profile config 的
  source.gcs_gcp_gcs.source_csv_uri
再触发 Cloud Run：
  gcloud run jobs execute gcp-python-modeling-demo \
    --region europe-west4 --wait \
    --args=--scenario=gcs_gcp_gcs \
    --args=--max-entities=1
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from google.cloud import bigquery, storage

# ── 配置区（按需修改）────────────────────────────────────────────────
PROJECT_ID = "ingka-cn-cop-stage"

SOURCE_TABLE = (
    "ingka-cn-cop-stage.ikea_da_temp.temp_store_ma582_sampled_items_modeling_202605"
)

# 要导出的商品列表（与主脚本 ITEMS_TO_PROCESS 保持一致）
ITEMS_TO_PROCESS = [
    "00334701",
    "00504294",
    "00609192",
    "30534416",
    "30617609",
    "40277464",
    "70540873",
    "80307591",
]

# 时间窗（与主脚本保持一致）
TRAIN_START = "2024-01-01"
TEST_END    = "2026-04-30"

# 导出到 GCS 的目标路径（bucket 与路径前缀）
GCS_BUCKET  = "ocp_model_archive"
GCS_PREFIX  = "gcp_python_modeling/source_data"

# 本地临时 CSV 路径
LOCAL_CSV   = Path("/tmp/source_export.csv")
# ─────────────────────────────────────────────────────────────────────

# SQL 语句：拉取全量特征宽表中指定商品+时间范围的数据
ITEMS_IN = ", ".join(f"'{i}'" for i in ITEMS_TO_PROCESS)

SQL = f"""
SELECT
  item_no,
  day_date,
  item_qty,
  -- 滞后与滚动特征
  lag_qty_7,
  lag_qty_14,
  lag_qty_28,
  rolling_std_14,
  rolling_std_28,
  nonzero_rate_28,
  nonzero_days_7,
  -- 日期类特征
  is_workday,
  is_holiday,
  is_dayoff,
  is_holiday_special,
  day_name_long,
  holiday_name,
  holiday_tag,
  -- 促销与活动特征
  is_service_discount,
  is_order_manjian,
  is_order_manzhe,
  is_item_combo,
  discount_ratio,
  discount_ratio_recode,
  is_camp_start_week,
  is_camp_end_week,
  is_in_scin,
  -- 同期基线特征
  same_wd_median_4,
  same_wd_median_8,
  same_wd_median_13,
  -- 天气特征
  is_sunny,
  tavg
FROM `{SOURCE_TABLE}`
WHERE item_no IN ({ITEMS_IN})
  AND day_date BETWEEN '{TRAIN_START}' AND '{TEST_END}'
ORDER BY item_no, day_date
"""


def main() -> None:
    print(f"[1/3] 从 BigQuery 读取数据...")
    print(f"      表：{SOURCE_TABLE}")
    print(f"      商品数：{len(ITEMS_TO_PROCESS)}")
    print(f"      时间范围：{TRAIN_START} ~ {TEST_END}")

    bq_client = bigquery.Client(project=PROJECT_ID)
    df = bq_client.query(SQL).to_dataframe()
    print(f"      读取行数：{len(df):,}")

    print(f"\n[2/3] 保存 CSV 到本地临时文件...")
    LOCAL_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(LOCAL_CSV, index=False)
    print(f"      本地路径：{LOCAL_CSV}  ({LOCAL_CSV.stat().st_size / 1024:.1f} KB)")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    gcs_object = f"{GCS_PREFIX}/source_{ts}.csv"
    gcs_uri = f"gs://{GCS_BUCKET}/{gcs_object}"

    print(f"\n[3/3] 上传到 GCS...")
    print(f"      目标：{gcs_uri}")
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(GCS_BUCKET)
    blob = bucket.blob(gcs_object)
    blob.upload_from_filename(str(LOCAL_CSV))
    print(f"      上传完成。")

    print(f"""
────────────────────────────────────────────────────
✓ 导出成功！GCS URI：
  {gcs_uri}

下一步 - 将 URI 写入 profile config：

  source:
    gcs_gcp_gcs:
      source_csv_uri: "{gcs_uri}"

再触发 Cloud Run（复制以下命令执行）：

  gcloud run jobs execute gcp-python-modeling-demo \\
    --region europe-west4 --wait \\
    --args=--scenario=gcs_gcp_gcs \\
    --args=--max-entities=1
────────────────────────────────────────────────────
""")


if __name__ == "__main__":
    main()
