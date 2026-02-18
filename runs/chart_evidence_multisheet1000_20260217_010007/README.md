# Pardus 多 Sheet 证据批次（20260217_010007）

## 批次说明
- 目标：按既有模板扩量抓取 Pardus 公共项目，并输出同格式证据页
- 执行时间：2026-02-17
- 扫描脚本：`Pardus/.codex_session/scripts/scan_recent_multisheet.py`
- 证据脚本：`Pardus/.codex_session/scripts/build_chart_evidence_batch.py`
- 聚合脚本：`Pardus/.codex_session/scripts/build_multisheet_aggregate_dashboard.py`

## 本轮参数
- recent_limit: `1000`
- sample_size: `1000`
- workers: `8`
- min_chart_count: `5`
- preview_bytes: `4000000`
- full_retry_threshold_mb: `25`

## 结果概览
- 扫描任务数：`500`（接口返回上限）
- 多 Sheet 样本：`38`
- 合格样本：`30`
- 证据页：`30`（`items/case_*.html`）

## 产物清单
- `summary.json`
- `index.html`
- `items/case_*.html`
- `aggregate_data.json`
- `aggregate_dashboard.html`
- `build_evidence.log`

## 入口
- 样本索引：`index.html`
- 聚合总览：`aggregate_dashboard.html`

## 与历史 31 样本对比（基于 case_id）
- 重叠：`29`
- 新增：`1`
- 历史有但本轮未命中：`2`

## 备注
- 虽然参数设置为 1000，`/recent` 当前实际返回 500 条；本轮按返回上限完成扫描。
- 输出结构与历史批次保持一致，便于后续横向比对。
