# 运行记录 — v1_refresh_model Smoke Test
日期：2026-06-01  
记录用途：**销量预测技能打样**，记录一次端到端流程的全部确认项与输出，供后续技能开发复刻。

---

## 阶段 0 — 前置准备

### 0.1 特征宽表（数据源）
| 项目 | 内容 |
|---|---|
| **GCP 表** | `ingka-cn-cop-stage.ikea_da_test.archen_model_base_with_y_r4w` |
| **特征清单来源** | `10_iteration_archive/v1_refresh_model_20260601/feature_list_20260601_refresh_model.csv` |
| **筛选条件** | `is_in_refresh_model = 1` |
| **请求特征数** | 132 |
| **实际可用特征数** | 99（源表缺失 33 列，已自动跳过） |
| **缺失列** | year, week, item_qty_last1~4, item_qty_last52, item_qty_up_r1/r2, item_qty_wa_r4, item_qty_std_r4, item_qty_slope_r4, is_sto_available_isr, is_sto_isr, sto_overlap_ratio, price_log, price_bin, price_elast, price_vs_avg, product_cluster2, delivery_type, is_camp_nlp/tla/618/d11/tro/collection/national_holiday/back_to_school/spring/xmas/circular_week/cny |

> 注：缺失列不阻断流程，但应在后续迭代中补齐源表或调整特征清单。

---

## 阶段 1 — 确认预测模型基本信息

| 确认项 | 本次值 | 说明 |
|---|---|---|
| **预测 Y 变量** | `item_qty_weekly` | 本周实际销量（周度口径） |
| **时间序列列** | `week_no` | IKEA 周序号，格式 YYYYWW |
| **模型 Entity 粒度** | `(sales_channel, market_area_no_orig, hfb_code, product_cluster)` | 每个组合训练一个独立模型 |
| **样本 Key（行唯一键）** | `(sales_channel, market_area_no_orig, hfb_code, product_cluster, week_no)` | 用于日志追踪和去重 |
| **建模范围** | **筛选样本**：`sales_channel = 'Store'` AND `product_cluster = '热销商品'` | 不是全样本；仅覆盖 Store 渠道热销商品 |
| **自动发现 Entity 数** | 634 | 系统从 BQ 数据中自动发现所有组合 |
| **最少训练行数** | 95 | 低于此数的 entity 自动跳过 |

---

## 阶段 2 — 确认模型设置

### 2.1 时间窗口（In-sample vs Out-of-sample）
| 划分 | 时间范围 | 周数 |
|---|---|---|
| **训练集（In-sample）** | 202401 ~ 202552 | 约 78 周 |
| **测试集（Out-of-sample）** | 202601 ~ 202617 | 17 周 |

### 2.2 In-sample Validation
| 项目 | 本次值 |
|---|---|
| **是否开启** | ✅ 是 |
| **划分比例** | validation = 20%，实际训练 = 80% |
| **划分方式** | `random`（随机划分，不是按时间） |
| **随机种子** | 42 |

### 2.3 算法
| 项目 | 本次值 |
|---|---|
| **选择算法** | **LightGBM** |
| **默认超参（调参前兜底）** | num_leaves=31, max_depth=6, learning_rate=0.05, n_estimators=200 |

### 2.4 超参搜索（Tuning）
| 项目 | 本次值 |
|---|---|
| **是否开启** | ✅ 是 |
| **搜索方法** | `random`（随机搜索，非网格搜索） |
| **迭代次数** | 50 次 |
| **内部验证比例** | 20% |
| **主目标** | `mae_min`（最小化平均绝对误差） |
| **辅助目标** | `accuracy_strict_nonzero_max`（非零值准确率最大化） |
| **搜索空间** | num_leaves:[31,50,80,120,150] / max_depth:[5,7,10,15,-1] / learning_rate:[0.01,0.02,0.05,0.1,0.15] / min_child_samples:[8,12,20,32,50] / feature_fraction:[0.6-1.0] / bagging_fraction:[0.6-1.0] / lambda_l1:[0,0.1,1,5,10] / lambda_l2:[0,0.1,1,5,10] |

---

## 阶段 3 — 确认输出

### 3.1 本次执行场景
- `bq_local_local`：从 BigQuery 读取 → 本地执行 → 输出到本地目录（不写回 BQ）

### 3.2 本地输出
| 项目 | 路径 |
|---|---|
| **输出根目录** | `C:\Users\arche24\KnowledgeBase\model_predict_by_weekly\20_modeling\bq_local_runs\refresh_model\v001_20260601` |
| **本次 Run 目录** | `runs\20260601_133450_091__refresh_model__lightgbm\` |
| **Run 汇总** | `lgbm_run_summary_20260601_133450_091.json` |
| **评估指标** | `lgbm_eval_metrics_by_group_20260601_133450_091.csv` |
| **产物清单** | `lgbm_artifact_manifest_20260601_133450_091.json` |
| **全局运行注册表** | `run_registry.csv`（追加写，每次运行记录一行） |
| **配置快照** | `config_snapshot/` 目录（自动保存运行时的 yaml 副本 + SHA256） |

### 3.3 BigQuery 输出（本次 bq_local_local 未写回，以下为 bq_gcp_bq 场景时的配置）
| 用途 | BQ 表名 |
|---|---|
| 预测结果（train+validation+test 明细） | `ingka-cn-cop-stage.ikea_da_test.refresh_model_pred_train_test_detail` |
| 模型元数据（超参、版本等） | `ingka-cn-cop-stage.ikea_da_test.refresh_model_metadata` |
| 特征重要性明细 | `ingka-cn-cop-stage.ikea_da_test.refresh_model_feature_importance_detail` |
| 实体级分 split 指标 | `ingka-cn-cop-stage.ikea_da_test.refresh_model_metrics_by_split` |
| Run 级汇总指标 | `ingka-cn-cop-stage.ikea_da_test.refresh_model_run_eval_metrics` |

---

## 阶段 4 — 执行命令

### 4.1 本次 Smoke Test 命令（max-entities=1）
```powershell
cd C:\Users\arche24\Documents\Model_Project\sales_forecast

c:/Users/arche24/Documents/Model_Project/.venv/Scripts/python.exe main.py `
  --scenario bq_local_local `
  --config config/profiles/refresh_model/config_bll_lgbm_v001_20260601.yaml `
  --model-type lightgbm `
  --max-entities 1
```

### 4.2 全量执行命令（去掉 max-entities）
```powershell
c:/Users/arche24/Documents/Model_Project/.venv/Scripts/python.exe main.py `
  --scenario bq_local_local `
  --config config/profiles/refresh_model/config_bll_lgbm_v001_20260601.yaml `
  --model-type lightgbm
```

### 4.3 写回 BQ 场景（生产）
```powershell
c:/Users/arche24/Documents/Model_Project/.venv/Scripts/python.exe main.py `
  --scenario bq_gcp_bq `
  --config config/profiles/refresh_model/config_bll_lgbm_v001_20260601.yaml `
  --model-type lightgbm
```

---

## 阶段 5 — 关键日志摘录（Smoke Test）

```
[CONFIG] Loaded unified config: config/profiles/refresh_model/config_bll_lgbm_v001_20260601.yaml
[CONFIG] model_key=lightgbm, model_label=LightGBM
[START] source=ingka-cn-cop-stage.ikea_da_test.archen_model_base_with_y_r4w
[START] runtime_window=train:202401~202552, test:202601~202617; features=132
[START] entity_id_columns=["sales_channel","market_area_no_orig","hfb_code","product_cluster"]
[START] tuning=enabled=True, method=random, n_iter=50
[WARN]  Missing columns skipped: [33 列，详见阶段 0.1]
[INFO]  Using 99 features.
[INFO]  entity_discovery found 634 combinations
[LGBM]  1/1 entity={sales_channel:Store, market_area_no_orig:058, hfb_code:01, product_cluster:热销商品}
[INFO]  tuned entity best_mae=10.988967
[OK]    summary 生成完毕
[DONE]  uploaded 0 files to GCS（bq_local_local 场景不上传）
```

---

## 阶段 6 — 遗留问题与后续改进

| 编号 | 问题 | 优先级 | 改进方向 |
|---|---|---|---|
| 1 | 33 个特征在源表中缺失 | 高 | 确认这些字段是否应加入特征宽表生产流程，或从特征清单剔除 |
| 2 | requirements.txt 中 PyYAML==6.0 在 Python 3.12 下不可构建 | 中 | 已改为 6.0.2，但需同步更新 Dockerfile |
| 3 | 全量 634 entities × n_iter=50 调参耗时估算未知 | 中 | 建议先跑 max-entities=10 并计时，再估算全量时长 |
| 4 | 本次无 BQ 写回，尚未验证 bq_gcp_bq 场景 | 低 | 下次全量跑时切换至 bq_gcp_bq 场景验证 |

---

## 附：本次相关文件索引

| 文件 | 路径 |
|---|---|
| 配置文件 | `config/profiles/refresh_model/config_bll_lgbm_v001_20260601.yaml` |
| 特征清单 | `10_iteration_archive/v1_refresh_model_20260601/feature_list_20260601_refresh_model.csv` |
| 本运行记录 | `10_iteration_archive/v1_refresh_model_20260601/run_record_20260601_smoke_test.md`（本文件）|
| 技能化方案 | `docs/08_skillization_runbook.md` |
