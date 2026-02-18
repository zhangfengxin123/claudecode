# Pardus Run Log

更新时间：2026-02-08
状态：Batch A（A01-A10）已完成

## 记录规则

- 每次 run 必须有：数据来源、目标问题、3 项复算、证据路径、打分。
- 低质量 run（总分 < 24）不计入有效样本。

## Run Index

| Run | 状态 | 场景 | 证据目录 | 备注 |
|---|---|---|---|---|
| A01 | completed | 基线复现（Bank Churn） | `case_c8554dc4df400246172d42369902664563c3735685e4bf4ea46c97301bd8af68` | 指标与图表一致 |
| A02 | completed | 可复现性对照（同案例重放） | `case_c8554dc4df400246172d42369902664563c3735685e4bf4ea46c97301bd8af68` | 重放一致 |
| A03 | completed | 分类（Adult Income） | `case_fb9bd5e5d0d6bac36620bf46b8252ad7213ae531e1ca283e22c87e060f37e55b` | 标签标准化后对齐 |
| A04 | completed | 运营转化（Global Ads） | `case_a16a6fd625c62226a212e5416de52a484e88d4f479fb754d729524f985181ce5` | 指标对齐优秀 |
| A05 | completed | 分类统计（BankNote） | `case_99c8c77bd8643412a79c0ada3d1dd9fd6b77f9527894a3139425205c83142933` | 部分统计字段不一致 |
| A06 | completed | 医学分类（Breast Cancer） | `case_8daf3093c3daaca832a4ad8bb074814a5755f24b37e7a9629c63d4b036ffb276` | 特征统计字段存在偏差 |
| A07 | completed | 消费信贷（BNPL） | `case_a04751b41f4ca29afdbf1f3c0e5cfb72c547251cce0a1ea75edf9b1b5265a0ea` | 三类核心图表全部对齐 |
| A08 | completed | 宏观时序（多源合并） | `case_a66a96a46ee244bd8c1160eb76fdd3b79a1747cffc35170c9e5b12a501ec178e` | 点数与相关性对齐 |
| A09 | completed | 行为实验（Power Nap vs Coffee） | `case_8c7670bf201ce709445fa3dec9ba9da3dc33b078ee249aaaf4f5688ac1f612ec` | 分组统计对齐稳定 |
| A10 | completed | 个人账单（CreditCard） | `case_dae89b508977e39879d3956315061d60551227d78d09400176670fbb53725bd0` | 存在隐式清洗口径 |

---

## Run Detail

### A01

- 日期：2026-02-08
- 数据集：`Churn_Modelling.csv`
- 数据来源：公开案例文件下载
- 目标问题：验证关键业务结论是否能从原始数据复算
- 复算校验：
  - Overall Churn：`20.37%`（raw） vs `20.37%`（报告）
  - Germany Churn：`32.44%`（raw） vs `32.44%`（图表）
  - 4 Products Churn：`100.00%`（raw） vs `100.00%`（图表）
- 异常：无
- 打分：
  - 准确性 5 / 证据完整性 5 / 可控性 4 / 可复现性 5 / 失败可解释性 4 / 输出可用性 5 / 交互效率 4 / 鲁棒性 4
  - 总分：`36/40`
- 证据：
  - `Pardus/.codex_session/case_c8554dc4df400246172d42369902664563c3735685e4bf4ea46c97301bd8af68/evidence_chain_case.html`
  - `Pardus/.codex_session/case_c8554dc4df400246172d42369902664563c3735685e4bf4ea46c97301bd8af68/verification_report.json`

### A02

- 日期：2026-02-08
- 数据集：同 A01（重放）
- 目标问题：验证同一公开案例多次抓取的一致性
- 对照结果（pre vs post）：
  - `total_trace`：`21 == 21`
  - `api_trace`：`19 == 19`
  - `asset_urls_found`：`18 == 18`
  - `churn_by_geography` 图值：一致
  - `churn_by_products` 图值：一致
- 异常：无
- 打分：
  - 准确性 5 / 证据完整性 5 / 可控性 4 / 可复现性 5 / 失败可解释性 4 / 输出可用性 4 / 交互效率 4 / 鲁棒性 4
  - 总分：`35/40`
- 证据：
  - `Pardus/.codex_session/runs/A02_pre_summary_c855.json`
  - `Pardus/.codex_session/runs/A02_pre_asset_samples_c855.json`
  - `Pardus/.codex_session/case_c8554dc4df400246172d42369902664563c3735685e4bf4ea46c97301bd8af68/summary.json`

### A03

- 日期：2026-02-08
- 数据集：`data.csv`（Adult Income）
- 目标问题：验证收入分布与性别分布图是否与原始数据一致
- 复算校验：
  - 年龄均值：`38.6`（raw） vs `38.6`（表格）
  - 周工时均值：`40.4`（raw） vs `40.4`（表格）
  - 收入分布：标签标准化后 raw `<=50K:37155, >50K:11687` vs 图表 `<=50K:37155, >50K:11687`（一致）
  - 男性 `>50K`：标签标准化后 raw `9918` vs 图表 `9918`（一致）
- 异常：无硬错误；但收入标签存在 `<=50K.` / `>50K.`，图表侧做了合并
- 打分：
  - 准确性 5 / 证据完整性 5 / 可控性 4 / 可复现性 4 / 失败可解释性 3 / 输出可用性 4 / 交互效率 4 / 鲁棒性 4
  - 总分：`33/40`
- 证据：
  - `Pardus/.codex_session/case_fb9bd5e5d0d6bac36620bf46b8252ad7213ae531e1ca283e22c87e060f37e55b/evidence_chain_case.html`
  - `Pardus/.codex_session/case_fb9bd5e5d0d6bac36620bf46b8252ad7213ae531e1ca283e22c87e060f37e55b/data.csv`

### A04

- 日期：2026-02-08
- 数据集：`global_ads_performance_dataset.csv`
- 目标问题：验证平台/国家 ROAS 与转化率图表是否可复算
- 复算校验：
  - 平台 ROAS：Google `4.11` / Meta `6.92` / TikTok `9.54`（raw=chart）
  - 平台转化率（Conversions/Clicks）：Google `4.46` / Meta `4.59` / TikTok `4.71`（raw=chart）
  - 国家 ROAS Top3：UAE `6.96`、Germany `6.71`、Australia `6.67`（raw=chart）
- 异常：无
- 打分：
  - 准确性 5 / 证据完整性 5 / 可控性 4 / 可复现性 4 / 失败可解释性 4 / 输出可用性 5 / 交互效率 4 / 鲁棒性 4
  - 总分：`35/40`
- 证据：
  - `Pardus/.codex_session/case_a16a6fd625c62226a212e5416de52a484e88d4f479fb754d729524f985181ce5/evidence_chain_case.html`
  - `Pardus/.codex_session/case_a16a6fd625c62226a212e5416de52a484e88d4f479fb754d729524f985181ce5/global_ads_performance_dataset.csv`

### A05

- 日期：2026-02-08
- 数据集：`BankNoteAuthentication.csv`
- 目标问题：验证分类统计表中各特征的类内统计是否正确
- 复算校验：
  - Variance（Class0/1 均值与标准差）与表格一致
  - Skewness 均值一致，但标准差与表格不一致（raw `5.14/5.40` vs 表格 `4.13/2.07`）
  - Curtosis 显著不一致（raw mean `0.80/2.15` vs 表格 `1.24/5.26`）
- 异常：统计表存在字段级错误或字段映射错误风险
- 打分：
  - 准确性 3 / 证据完整性 5 / 可控性 3 / 可复现性 4 / 失败可解释性 4 / 输出可用性 3 / 交互效率 4 / 鲁棒性 3
  - 总分：`29/40`
- 证据：
  - `Pardus/.codex_session/case_99c8c77bd8643412a79c0ada3d1dd9fd6b77f9527894a3139425205c83142933/evidence_chain_case.html`
  - `Pardus/.codex_session/case_99c8c77bd8643412a79c0ada3d1dd9fd6b77f9527894a3139425205c83142933/BankNoteAuthentication.csv`

### A06

- 日期：2026-02-08
- 数据集：`data.csv`（Breast Cancer）
- 目标问题：验证诊断分布与关键特征统计是否可由原始数据复算
- 复算校验：
  - 诊断分布：raw `B:357, M:212` vs 图表 `B:357, M:212`（一致）
  - `area_mean` 分组均值：raw `B 462.79, M 978.38` vs 表格 `B 462.79, M 978.38`（一致）
  - `concave points_mean` 分组均值：raw `B 0.026, M 0.088` vs 表格 `B 0.049, M 0.161`（不一致）
- 异常：文档表格存在特征字段映射/标注错误风险
- 打分：
  - 准确性 4 / 证据完整性 5 / 可控性 4 / 可复现性 4 / 失败可解释性 4 / 输出可用性 4 / 交互效率 3 / 鲁棒性 3
  - 总分：`31/40`
- 证据：
  - `Pardus/.codex_session/case_8daf3093c3daaca832a4ad8bb074814a5755f24b37e7a9629c63d4b036ffb276/evidence_chain_case.html`
  - `Pardus/.codex_session/case_8daf3093c3daaca832a4ad8bb074814a5755f24b37e7a9629c63d4b036ffb276/verification_report.json`

### A07

- 日期：2026-02-08
- 数据集：`bnpl_dataset.csv`
- 目标问题：验证还款状态、信用分层违约率、渠道份额三类核心图表可复算
- 复算校验：
  - 还款状态分布：raw `Paid On Time 37612 / Late 8009 / Defaulted 4379` vs 饼图一致
  - 信用分层违约率：raw `Excellent 2.0204%, Good 2.9491%, Fair 9.5760%, Poor 14.2919%` vs 图表一致
  - 渠道份额：raw `Klarna 12545, Afterpay 12536, Sezzle 12501, Affirm 12418` vs 图表一致
- 异常：无
- 打分：
  - 准确性 5 / 证据完整性 5 / 可控性 4 / 可复现性 5 / 失败可解释性 4 / 输出可用性 5 / 交互效率 4 / 鲁棒性 4
  - 总分：`36/40`
- 证据：
  - `Pardus/.codex_session/case_a04751b41f4ca29afdbf1f3c0e5cfb72c547251cce0a1ea75edf9b1b5265a0ea/evidence_chain_case.html`
  - `Pardus/.codex_session/case_a04751b41f4ca29afdbf1f3c0e5cfb72c547251cce0a1ea75edf9b1b5265a0ea/verification_report.json`

### A08

- 日期：2026-02-08
- 数据集：`DBAA.csv + SOFR30DAYAVG.csv + USEPUINDXD.csv`
- 目标问题：验证多源日期对齐、散点点数与相关性热力图是否可复算
- 复算校验：
  - DBAA-SOFR 日期交集点数：raw `1246` vs 散点图 `1246`（一致）
  - 三源交集点数：raw `1246` vs `3d_scatter` traces `1246`（一致）
  - 相关性：raw `DBAA-SOFR 0.880109, DBAA-EPU 0.270746, SOFR-EPU 0.153980` vs 热力图一致
- 异常：无
- 打分：
  - 准确性 5 / 证据完整性 5 / 可控性 4 / 可复现性 5 / 失败可解释性 4 / 输出可用性 4 / 交互效率 4 / 鲁棒性 4
  - 总分：`35/40`
- 证据：
  - `Pardus/.codex_session/case_a66a96a46ee244bd8c1160eb76fdd3b79a1747cffc35170c9e5b12a501ec178e/evidence_chain_case.html`
  - `Pardus/.codex_session/case_a66a96a46ee244bd8c1160eb76fdd3b79a1747cffc35170c9e5b12a501ec178e/verification_report.json`

### A09

- 日期：2026-02-08
- 数据集：`power_nap_vs_coffee_effectiveness_dataset.csv`
- 目标问题：验证干预效果、分职业拆分与样本规模统计是否可复算
- 复算校验：
  - 干预均值提升：raw `Power Nap 17.0418, Coffee 10.9272` vs 图表一致
  - 样本数：raw `Power Nap 239, Coffee 261` vs 表格一致
  - 分职业均值：raw 与 occupation 图表一致（Freelancer/Student/Working Professional）
- 异常：无
- 打分：
  - 准确性 5 / 证据完整性 5 / 可控性 4 / 可复现性 5 / 失败可解释性 4 / 输出可用性 5 / 交互效率 4 / 鲁棒性 4
  - 总分：`36/40`
- 证据：
  - `Pardus/.codex_session/case_8c7670bf201ce709445fa3dec9ba9da3dc33b078ee249aaaf4f5688ac1f612ec/evidence_chain_case.html`
  - `Pardus/.codex_session/case_8c7670bf201ce709445fa3dec9ba9da3dc33b078ee249aaaf4f5688ac1f612ec/verification_report.json`

### A10

- 日期：2026-02-08
- 数据集：`CreditCard1.csv`
- 目标问题：验证交易汇总与月度趋势图的统计口径
- 复算校验：
  - 总交易笔数：raw `110`（排除首行后） vs 表格 `110`（一致）
  - Total Spent Debits：raw `19,191.52`（排除首行后） vs 表格 `19,191.52`（一致）
  - 月度趋势：raw `2025-11 9272.98, 2025-12 9392.59, 2026-01 17502.06, 2026-02 418.00`（`abs(amount)` + 排除首行）vs 图表一致
- 异常：
  - 存在隐式预处理：首行交易（`02/02/2026, -28.67`）被排除
  - 月度趋势采用 `abs(amount)` 聚合口径，而非仅 debit 聚合
- 打分：
  - 准确性 4 / 证据完整性 5 / 可控性 3 / 可复现性 4 / 失败可解释性 4 / 输出可用性 4 / 交互效率 4 / 鲁棒性 4
  - 总分：`32/40`
- 证据：
  - `Pardus/.codex_session/case_dae89b508977e39879d3956315061d60551227d78d09400176670fbb53725bd0/evidence_chain_case.html`
  - `Pardus/.codex_session/case_dae89b508977e39879d3956315061d60551227d78d09400176670fbb53725bd0/verification_report.json`
