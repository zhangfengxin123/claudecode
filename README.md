# codex_session（精简整理版）

本目录已完成分层清理，仅保留当前产品调研与设计前需要的核心材料。

## 目录结构

- `docs/`
  - `run_log.md`：调研过程记录
  - `capability_map.md`：能力映射
  - `failure_atlas.md`：失败模式
  - `pardus_research_playbook_v1.md`：调研手册
  - `pardus_usage_research_round1.md`：第一轮结论
- `scripts/`
  - `scan_recent_multisheet.py`：多 sheet 扫描
  - `build_chart_evidence_batch.py`：批量证据页生成
  - `build_multisheet_aggregate_dashboard.py`：聚合总览生成
  - `check_multi_sheet.py`、`light_probe_recent.py`：辅助脚本
- `research/deepresearch/`
  - 外部补充研究文档
- `runs/`
  - `multisheet_scan500_fast2_20260210_102544/`：500 条扫描结果（31 条合格样本来源）
  - `chart_evidence_multisheet31_20260210_102903/`：31 条证据页与聚合页
  - `chart_evidence_multisheet_latest`、`chart_evidence_latest`：最新证据目录软链接

## 本次清理动作

- 删除了历史试错目录：
  - 旧 `case_*` 临时案例目录
  - `chrome_remote_profile` 浏览器缓存目录
  - `runs/` 下早期 light probe / chart evidence / 中间 JSON 报告
- 将同类型文件归档到 `docs/`、`scripts/`、`research/`。

## 当前查看入口

- 核心总览页：`runs/chart_evidence_multisheet_latest/aggregate_dashboard.html`
- 样本索引页：`runs/chart_evidence_multisheet_latest/index.html`
