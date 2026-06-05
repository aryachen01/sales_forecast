# Run Log — 运行记录

> 每次 `gcloud run jobs execute` 后登记一行，结果出来后更新状态和备注。

| Run ID | Image Tag | Digest (前16位) | 执行时间 (HKT) | 状态 | 备注 |
|---|---|---|---|---|---|
| 20260601_125615_967 | bgg-v001-20260601 | sha256:0a86742cf23d123 | 2026-06-01 17:30 | OOM (9/18) | 4Gi 内存不足，signal 9 |
| 20260601_153232_841 | bgg-v001-20260601 | sha256:0a86742cf23d123 | 2026-06-01 ~19:30 | OOM (13/18) | 16Gi，无 gc 优化，signal 9 |
| (待填 run_ts) | model-refresh-v1-20260601 | sha256:dce374097368552b | 2026-06-01 23:50 | 运行中 | 32Gi/8CPU + gc.collect + del model + n_iter=30，execution=refresh-model-bgg-v001-20260601-xtd7w |
