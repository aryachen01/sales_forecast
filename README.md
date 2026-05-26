# gcp_python_modeling

批量销量预测建模框架 — Decision Tree / LightGBM，支持 BigQuery + GCS + Cloud Run。

📖 **[完整文档 → docs/INDEX.md](docs/INDEX.md)**

---

## 快速开始

```powershell
cd scripts/gcp_python_modeling
python main.py --scenario bq_local_local --config config/profiles/item_channel_ma_week/config_v001.yaml --max-entities 3
```

详细执行说明：[docs/03_execution_guide.md](docs/03_execution_guide.md)
