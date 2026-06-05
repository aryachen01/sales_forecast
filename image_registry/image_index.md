# Image Index — 镜像维表

> 每次 `gcloud builds submit` 成功后登记一行。
> 覆盖旧 tag 时更新对应行的 Digest / Git Hash / Build 时间 / 说明。

| Tag | Digest (前16位) | Build 时间 (HKT) | Git Hash | 对应 Config | 说明 |
|---|---|---|---|---|---|
| bgg-v001-20260601 | sha256:eb5a5883abbd279a | 2026-06-01 22:57 | 898a3e5 | config_bgg_lgbm_v001_20260601.yaml | 旧 tag，已被新 tag 取代 |
| model-refresh-v1-20260601 | sha256:dce374097368552b | 2026-06-01 23:47 (HKT) | 2ba5557 | config_bgg_lgbm_v001_20260601.yaml | gc.collect/del model/n_iter=30/GCS checkpoint sync/image_registry 新规范 |
