#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def file_url(path: Path) -> str:
    return f"file://{path.resolve()}"


def first_existing(paths: list[Path]) -> Path | None:
    for p in paths:
        if p.exists():
            return p
    return None


def build_case_block(index: int, row: dict[str, Any]) -> str:
    case_dir = Path(row["case_dir"])
    items = case_dir / "items"

    a_zh = items / "report_full_A_zh.html"
    b_zh = items / "report_full_B_zh.html"
    bundle = items / "report_full_bundle.html"
    a_cap = items / "report_full_A_capture.html"
    b_cap = items / "report_full_B_capture.html"
    ab_html = first_existing(sorted(items.glob("case_ab_*.html")))

    ab_summary_path = case_dir / "ab_summary.json"
    if ab_summary_path.exists():
        ab = read_json(ab_summary_path)
        a_inputs = ab.get("A_with_description", {}).get("inputs", [])
        b_inputs = ab.get("B_without_description", {}).get("inputs", [])
        a_h2 = ab.get("A_with_description", {}).get("h2_sections", [])
        b_h2 = ab.get("B_without_description", {}).get("h2_sections", [])
    else:
        a_inputs, b_inputs, a_h2, b_h2 = [], [], [], []

    a_id = row.get("template_case_id", "")
    b_id = row.get("rerun_case_id", "")
    a_view = f"https://pardusai.org/view/{a_id}" if a_id else ""
    b_view = f"https://pardusai.org/view/{b_id}" if b_id else ""

    def links() -> str:
        items_list = []
        if bundle.exists():
            items_list.append(
                f"<a href='{esc(file_url(bundle))}' target='_blank'>总页</a>"
            )
        if ab_html and ab_html.exists():
            items_list.append(
                f"<a href='{esc(file_url(ab_html))}' target='_blank'>图表对照页</a>"
            )
        if a_cap.exists():
            items_list.append(
                f"<a href='{esc(file_url(a_cap))}' target='_blank'>A原文抓取</a>"
            )
        if b_cap.exists():
            items_list.append(
                f"<a href='{esc(file_url(b_cap))}' target='_blank'>B原文抓取</a>"
            )
        if a_view:
            items_list.append(f"<a href='{esc(a_view)}' target='_blank'>A原站</a>")
        if b_view:
            items_list.append(f"<a href='{esc(b_view)}' target='_blank'>B原站</a>")
        return " | ".join(items_list)

    def list_html(values: list[str]) -> str:
        if not values:
            return "<span class='muted'>-</span>"
        return "<ul>" + "".join(f"<li>{esc(v)}</li>" for v in values) + "</ul>"

    a_iframe = (
        f"<iframe loading='lazy' src='{esc(file_url(a_zh))}' title='A {index}'></iframe>"
        if a_zh.exists()
        else "<div class='empty'>缺少 A 中文页</div>"
    )
    b_iframe = (
        f"<iframe loading='lazy' src='{esc(file_url(b_zh))}' title='B {index}'></iframe>"
        if b_zh.exists()
        else "<div class='empty'>缺少 B 中文页</div>"
    )

    return f"""
    <details class='case-card' open>
      <summary>
        <span class='num'>#{index:02d}</span>
        <span class='title'>{esc(row.get("title", "(untitled)"))}</span>
        <span class='meta'>charts {esc(row.get("a_chart_count", "-"))} / {esc(row.get("b_chart_count", "-"))}</span>
      </summary>
      <div class='case-body'>
        <div class='stats'>
          <table>
            <tr><th>template_case_id</th><td><code>{esc(a_id)}</code></td></tr>
            <tr><th>rerun_case_id</th><td><code>{esc(b_id)}</code></td></tr>
            <tr><th>description_removed_ok</th><td>{esc(row.get("description_removed_ok"))}</td></tr>
            <tr><th>links</th><td>{links()}</td></tr>
          </table>
        </div>
        <div class='io-grid'>
          <section>
            <h4>A 输入（含 description）</h4>
            {list_html(a_inputs)}
            <h4>A 章节（H2）</h4>
            {list_html(a_h2)}
          </section>
          <section>
            <h4>B 输入（去 description）</h4>
            {list_html(b_inputs)}
            <h4>B 章节（H2）</h4>
            {list_html(b_h2)}
          </section>
        </div>
        <div class='compare-grid'>
          <section class='pane'>
            <div class='pane-title tag-a'>A（含 description）中文报告</div>
            {a_iframe}
          </section>
          <section class='pane'>
            <div class='pane-title tag-b'>B（去 description）中文报告</div>
            {b_iframe}
          </section>
        </div>
      </div>
    </details>
    """


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build one-page zh compare gallery from existing AB outputs."
    )
    parser.add_argument(
        "--merged-dir",
        default="Pardus/.codex_session/runs/ab_no_description_retry_merged_latest",
        help="Merged directory with summary.json",
    )
    parser.add_argument(
        "--output",
        default="index_zh_compare_gallery.html",
        help="Output HTML name under merged dir",
    )
    args = parser.parse_args()

    merged = Path(args.merged_dir)
    summary_path = merged / "summary.json"
    if not summary_path.exists():
        raise SystemExit(f"summary.json not found: {summary_path}")

    summary = read_json(summary_path)
    rows = summary.get("results", [])
    built_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    blocks = [build_case_block(i, row) for i, row in enumerate(rows, start=1)]
    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>AB 中文全量对照总览</title>
  <style>
    :root {{
      --bg:#f6f8fc; --card:#fff; --line:#e5e7eb; --text:#111827; --muted:#6b7280;
      --a-bg:#e0f2fe; --a-text:#075985; --b-bg:#ffedd5; --b-text:#9a3412;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; padding:20px; background:var(--bg); color:var(--text); font-family:Arial,-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif; }}
    .container {{ max-width:1400px; margin:0 auto; }}
    .header {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; margin-bottom:14px; }}
    h1 {{ margin:0 0 8px; font-size:28px; }}
    .muted {{ color:var(--muted); }}
    .case-card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; margin-bottom:14px; overflow:hidden; }}
    .case-card > summary {{ cursor:pointer; list-style:none; padding:14px 16px; display:flex; gap:12px; align-items:center; border-bottom:1px solid var(--line); }}
    .case-card > summary::-webkit-details-marker {{ display:none; }}
    .num {{ font-weight:700; color:#2563eb; min-width:42px; }}
    .title {{ font-weight:700; flex:1; }}
    .meta {{ color:var(--muted); font-size:13px; }}
    .case-body {{ padding:14px; }}
    table {{ width:100%; border-collapse:collapse; }}
    th, td {{ border:1px solid var(--line); padding:8px; text-align:left; vertical-align:top; font-size:13px; }}
    th {{ width:220px; background:#f3f4f6; }}
    code {{ background:#f3f4f6; padding:2px 6px; border-radius:6px; }}
    a {{ color:#2563eb; text-decoration:none; }}
    .io-grid {{ margin:12px 0; display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
    .io-grid section {{ border:1px solid var(--line); border-radius:10px; padding:10px; background:#fff; }}
    .io-grid h4 {{ margin:0 0 8px; }}
    ul {{ margin:8px 0 0 18px; }}
    .compare-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
    .pane {{ border:1px solid var(--line); border-radius:10px; padding:10px; background:#fff; }}
    .pane-title {{ font-size:12px; font-weight:700; display:inline-block; border-radius:999px; padding:3px 10px; margin-bottom:8px; }}
    .tag-a {{ background:var(--a-bg); color:var(--a-text); }}
    .tag-b {{ background:var(--b-bg); color:var(--b-text); }}
    iframe {{ width:100%; height:980px; border:1px solid var(--line); border-radius:8px; background:#fff; }}
    .empty {{ min-height:120px; display:flex; align-items:center; justify-content:center; border:1px dashed var(--line); color:var(--muted); border-radius:8px; }}
    @media (max-width:1200px) {{
      .compare-grid, .io-grid {{ grid-template-columns:1fr; }}
      iframe {{ height:760px; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <section class="header">
      <h1>AB 中文全量对照总览（有 description vs 无 description）</h1>
      <div class="muted">生成时间：{esc(built_at)} ｜ 样本数：{esc(len(rows))} ｜ 基于已有结果离线生成（不重跑）</div>
      <div class="muted">入口索引：<code>{esc(str((merged / "index.html").resolve()))}</code></div>
    </section>
    {"".join(blocks)}
  </div>
</body>
</html>"""

    out = merged / args.output
    out.write_text(html_text, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
