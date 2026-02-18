#!/usr/bin/env python3
"""
Build aggregated HTML dashboard for multi-sheet Pardus chart evidence.

Input:
- chart evidence directory with summary.json + items/*.html
- (optional) scan_results_qualified.json for sheet counts

Output:
- aggregate_dashboard.html in the evidence directory
- aggregate_data.json (structured intermediate)
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import html
import json
import pathlib
import re
import statistics
from typing import Any, Dict, List


def h(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


def has_cjk(text: str) -> bool:
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            return True
    return False


def title_from_fig(fig: Dict[str, Any]) -> str:
    layout = fig.get("layout")
    if not isinstance(layout, dict):
        return ""
    title = layout.get("title")
    if isinstance(title, dict):
        return str(title.get("text") or "")
    if isinstance(title, str):
        return title
    return ""


def parse_assets_from_case_html(content: str) -> List[Dict[str, Any]]:
    m = re.search(r"const ASSETS = (\[.*?\]);\s*for\s*\(const item of ASSETS\)", content, re.S)
    if not m:
        return []
    try:
        raw_assets = json.loads(m.group(1))
    except Exception:
        return []

    assets: List[Dict[str, Any]] = []
    for item in raw_assets:
        if not isinstance(item, dict):
            continue
        fig = item.get("fig")
        if not isinstance(fig, dict):
            fig = {}
        data = fig.get("data")
        if not isinstance(data, list):
            data = []
        trace_types = []
        for trace in data:
            if isinstance(trace, dict):
                trace_types.append(str(trace.get("type") or "unknown"))
            else:
                trace_types.append("unknown")

        seen = set()
        uniq_trace_types = []
        for trace_type in trace_types:
            if trace_type not in seen:
                seen.add(trace_type)
                uniq_trace_types.append(trace_type)

        assets.append(
            {
                "name": str(item.get("name") or ""),
                "title": title_from_fig(fig),
                "trace_count": len(data),
                "trace_types": uniq_trace_types,
            }
        )
    return assets


def parse_uploads_from_case_html(content: str) -> List[Dict[str, Any]]:
    # Limit regex scope to section 1 table.
    m = re.search(
        r"<h2>1\) 输入数据源（uploads/files）</h2>(.*?)<h2>2\) 图表资产清单（document -> htmlPath）</h2>",
        content,
        re.S,
    )
    if not m:
        return []
    section = m.group(1)
    row_re = re.compile(
        r"<tr><td><code>(.*?)</code></td><td>(\d+)</td><td>(\d+)</td><td>(.*?)</td><td><a href='(.*?)'",
        re.S,
    )

    rows: List[Dict[str, Any]] = []
    for row in row_re.finditer(section):
        filename = html.unescape(strip_tags(row.group(1)).strip())
        status = int(row.group(2))
        size = int(row.group(3))
        content_type = html.unescape(strip_tags(row.group(4)).strip())
        link = html.unescape(row.group(5))
        rows.append(
            {
                "file": filename,
                "http": status,
                "bytes": size,
                "content_type": content_type,
                "link": link,
            }
        )
    return rows


def file_ext(name: str) -> str:
    n = (name or "").lower().strip()
    if "." not in n:
        return "(no-ext)"
    return "." + n.rsplit(".", 1)[-1]


def pick_scan_json(runs_dir: pathlib.Path, case_ids: set[str]) -> pathlib.Path | None:
    candidates = sorted(
        runs_dir.glob("multisheet_scan*/scan_results_qualified.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    best_path = None
    best_overlap = -1
    for candidate in candidates:
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, list):
            continue
        overlap = 0
        for item in payload:
            if not isinstance(item, dict):
                continue
            if str(item.get("case_id") or "") in case_ids:
                overlap += 1
        if overlap > best_overlap:
            best_overlap = overlap
            best_path = candidate
    return best_path


def sheet_counts_from_scan_item(item: Dict[str, Any]) -> List[int]:
    out: List[int] = []
    excel_infos = item.get("excel_infos")
    if not isinstance(excel_infos, list):
        return out
    for excel in excel_infos:
        if not isinstance(excel, dict):
            continue
        s_full = excel.get("sheets_full")
        s_head = excel.get("sheets")
        if isinstance(s_full, int) and s_full > 0:
            out.append(s_full)
        elif isinstance(s_head, int) and s_head > 0:
            out.append(s_head)
    return out


def to_json_script(value: Any) -> str:
    # Prevent accidental </script> breakouts in embedded JSON.
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def build_html(
    aggregate: Dict[str, Any],
    case_rows: List[Dict[str, Any]],
    output_path: pathlib.Path,
    source_summary_path: pathlib.Path,
    source_scan_path: pathlib.Path | None,
) -> None:
    generated_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    metrics = aggregate["metrics"]
    charts = aggregate["charts"]

    table_rows = []
    for item in case_rows:
        top_trace = ", ".join(item["top_trace_types"][:4]) or "-"
        sample_titles = " | ".join(item["sample_titles"][:3]) or "-"
        sheet_info = (
            f"max={item['sheet_max']} / excel={item['excel_file_count']}"
            if item["sheet_max"] is not None
            else f"max=- / excel={item['excel_file_count']}"
        )
        table_rows.append(
            "<tr>"
            f"<td>{item['idx']}</td>"
            f"<td><code>{h(item['case_id'][:12])}</code></td>"
            f"<td>{h(item['title'])}</td>"
            f"<td>{item['chart_count']}</td>"
            f"<td>{item['upload_count']}</td>"
            f"<td>{h(sheet_info)}</td>"
            f"<td>{h(top_trace)}</td>"
            f"<td>{h(sample_titles)}</td>"
            f"<td><a href='{h(item['html'])}'>open</a> | <a href='{h(item['view_url'])}' target='_blank' rel='noopener noreferrer'>view</a></td>"
            "</tr>"
        )

    html_text = f"""<!doctype html>
<html lang='zh-CN'>
<head>
  <meta charset='utf-8'/>
  <meta name='viewport' content='width=device-width, initial-scale=1'/>
  <title>Pardus 多 Sheet 样本聚合总览</title>
  <script src='https://cdn.plot.ly/plotly-2.35.2.min.js'></script>
  <style>
    :root {{
      --bg: #f5f7fb;
      --card: #ffffff;
      --line: #e5e7eb;
      --text: #111827;
      --muted: #6b7280;
      --accent: #2563eb;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Helvetica, Arial, sans-serif;
      color: var(--text);
      background: var(--bg);
    }}
    .wrap {{
      max-width: 1480px;
      margin: 0 auto;
      padding: 18px;
    }}
    h1, h2, h3 {{ margin: 0 0 10px 0; }}
    .muted {{ color: var(--muted); }}
    .small {{ font-size: 12px; }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px;
      margin-bottom: 14px;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 10px;
    }}
    .metric {{
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px;
      background: #fff;
    }}
    .metric .k {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 4px;
    }}
    .metric .v {{
      font-weight: 700;
      font-size: 22px;
    }}
    .grid2 {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }}
    .plot {{
      min-height: 360px;
    }}
    table {{
      border-collapse: collapse;
      width: 100%;
      font-size: 13px;
      background: #fff;
    }}
    th, td {{
      border: 1px solid var(--line);
      padding: 8px;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      background: #f3f4f6;
      position: sticky;
      top: 0;
      z-index: 2;
    }}
    code {{
      background: #f3f4f6;
      border-radius: 6px;
      padding: 1px 6px;
    }}
    a {{
      color: var(--accent);
      text-decoration: none;
    }}
    .table-wrap {{
      max-height: 65vh;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 10px;
    }}
    @media (max-width: 980px) {{
      .grid2 {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class='wrap'>
    <div class='card'>
      <h1>Pardus 多 Sheet 样本聚合总览</h1>
      <div class='muted'>
        generated_at: <code>{h(generated_at)}</code> |
        source_summary: <code>{h(str(source_summary_path))}</code> |
        source_scan: <code>{h(str(source_scan_path) if source_scan_path else "not_found")}</code>
      </div>
      <div class='small muted' style='margin-top:6px;'>
        说明：本页基于 31 个 case 证据页自动聚合，不重新抓取新样本。
      </div>
      <div style='margin-top:8px;'>
        <a href='index.html'>返回样本索引</a>
      </div>
    </div>

    <div class='card'>
      <h2>核心指标</h2>
      <div class='metrics'>
        <div class='metric'><div class='k'>样本 case 数</div><div class='v'>{metrics['case_count']}</div></div>
        <div class='metric'><div class='k'>总图表数</div><div class='v'>{metrics['total_charts']}</div></div>
        <div class='metric'><div class='k'>平均图表/Case</div><div class='v'>{metrics['avg_charts']}</div></div>
        <div class='metric'><div class='k'>中位图表/Case</div><div class='v'>{metrics['median_charts']}</div></div>
        <div class='metric'><div class='k'>最大图表数</div><div class='v'>{metrics['max_charts']}</div></div>
        <div class='metric'><div class='k'>最小图表数</div><div class='v'>{metrics['min_charts']}</div></div>
        <div class='metric'><div class='k'>总上传文件数</div><div class='v'>{metrics['total_upload_files']}</div></div>
        <div class='metric'><div class='k'>多 Sheet Excel 数</div><div class='v'>{metrics['excel_files_count']}</div></div>
      </div>
      <div class='small muted' style='margin-top:8px;'>
        语言分布（按标题粗分）：中文 {metrics['title_cjk_count']} / 非中文 {metrics['title_non_cjk_count']}
      </div>
    </div>

    <div class='grid2'>
      <div class='card'>
        <h3>图表数量分布（每个 Case）</h3>
        <div id='plot-chart-count' class='plot'></div>
      </div>
      <div class='card'>
        <h3>Sheet 最大值 vs 图表数量</h3>
        <div id='plot-sheet-vs-chart' class='plot'></div>
      </div>
    </div>

    <div class='grid2'>
      <div class='card'>
        <h3>Trace Type 频次 Top 20</h3>
        <div id='plot-trace-types' class='plot'></div>
      </div>
      <div class='card'>
        <h3>上传文件扩展名分布 Top 20</h3>
        <div id='plot-file-ext' class='plot'></div>
      </div>
    </div>

    <div class='card'>
      <h2>31 条样本明细（可直达证据页）</h2>
      <div class='table-wrap'>
        <table>
          <thead>
            <tr>
              <th>#</th><th>case_id</th><th>title</th><th>charts</th><th>uploads</th>
              <th>sheet</th><th>top trace types</th><th>sample chart titles</th><th>links</th>
            </tr>
          </thead>
          <tbody>
            {''.join(table_rows)}
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <script>
    const CHARTS = {to_json_script(charts)};

    Plotly.newPlot('plot-chart-count', [{{
      type: 'histogram',
      x: CHARTS.chart_counts,
      marker: {{color: '#2563eb'}},
      nbinsx: 12
    }}], {{
      xaxis: {{title: 'charts per case'}},
      yaxis: {{title: 'count of cases'}},
      margin: {{l: 50, r: 20, t: 20, b: 50}},
      paper_bgcolor: '#fff',
      plot_bgcolor: '#fff'
    }}, {{displaylogo:false, responsive:true}});

    Plotly.newPlot('plot-sheet-vs-chart', [{{
      type: 'scatter',
      mode: 'markers+text',
      x: CHARTS.sheet_max,
      y: CHARTS.chart_counts,
      text: CHARTS.case_short,
      textposition: 'top center',
      marker: {{size: 9, color: '#16a34a'}},
      hovertemplate: '%{{customdata}}<br>sheet_max=%{{x}}<br>charts=%{{y}}<extra></extra>',
      customdata: CHARTS.case_title
    }}], {{
      xaxis: {{title: 'max sheet count (per case)'}},
      yaxis: {{title: 'chart count'}},
      margin: {{l: 50, r: 20, t: 20, b: 50}},
      paper_bgcolor: '#fff',
      plot_bgcolor: '#fff'
    }}, {{displaylogo:false, responsive:true}});

    Plotly.newPlot('plot-trace-types', [{{
      type: 'bar',
      x: CHARTS.trace_type_names,
      y: CHARTS.trace_type_values,
      marker: {{color: '#7c3aed'}}
    }}], {{
      xaxis: {{title: 'trace type', tickangle: -28}},
      yaxis: {{title: 'frequency'}},
      margin: {{l: 50, r: 20, t: 20, b: 90}},
      paper_bgcolor: '#fff',
      plot_bgcolor: '#fff'
    }}, {{displaylogo:false, responsive:true}});

    Plotly.newPlot('plot-file-ext', [{{
      type: 'bar',
      x: CHARTS.file_ext_names,
      y: CHARTS.file_ext_values,
      marker: {{color: '#ea580c'}}
    }}], {{
      xaxis: {{title: 'file extension', tickangle: -28}},
      yaxis: {{title: 'frequency'}},
      margin: {{l: 50, r: 20, t: 20, b: 90}},
      paper_bgcolor: '#fff',
      plot_bgcolor: '#fff'
    }}, {{displaylogo:false, responsive:true}});
  </script>
</body>
</html>
"""

    output_path.write_text(html_text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build aggregate dashboard from multisheet evidence pages")
    parser.add_argument(
        "--evidence-dir",
        default="Pardus/.codex_session/runs/chart_evidence_multisheet_latest",
        help="Directory that contains summary.json and items/*.html",
    )
    parser.add_argument(
        "--scan-json",
        default="",
        help="Optional scan_results_qualified.json path for sheet counts",
    )
    parser.add_argument(
        "--output",
        default="aggregate_dashboard.html",
        help="Output HTML filename (inside evidence dir if relative)",
    )
    parser.add_argument(
        "--write-json",
        default="aggregate_data.json",
        help="Output JSON filename (inside evidence dir if relative)",
    )
    args = parser.parse_args()

    evidence_dir = pathlib.Path(args.evidence_dir).resolve()
    summary_path = evidence_dir / "summary.json"
    if not summary_path.exists():
        raise SystemExit(f"summary not found: {summary_path}")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    rows = summary.get("rows")
    if not isinstance(rows, list):
        raise SystemExit("invalid summary.json: rows missing")

    case_ids = {str(it.get("case_id") or "") for it in rows if isinstance(it, dict)}
    runs_dir = evidence_dir.parent
    scan_path = pathlib.Path(args.scan_json).resolve() if args.scan_json else pick_scan_json(runs_dir, case_ids)

    scan_map: Dict[str, Dict[str, Any]] = {}
    if scan_path and scan_path.exists():
        try:
            payload = json.loads(scan_path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                for item in payload:
                    if isinstance(item, dict):
                        cid = str(item.get("case_id") or "")
                        if cid:
                            scan_map[cid] = item
        except Exception:
            scan_map = {}

    case_rows: List[Dict[str, Any]] = []
    global_trace_counter: collections.Counter[str] = collections.Counter()
    global_ext_counter: collections.Counter[str] = collections.Counter()
    chart_counts: List[int] = []
    sheet_max_list: List[int] = []
    total_upload_files = 0
    title_cjk_count = 0
    title_non_cjk_count = 0
    total_excel_files = 0

    for item in rows:
        if not isinstance(item, dict):
            continue
        idx = int(item.get("idx") or 0)
        case_id = str(item.get("case_id") or "")
        title = str(item.get("title") or "")
        chart_count = int(item.get("chart_count") or 0)
        upload_count = int(item.get("upload_count") or 0)
        html_rel = str(item.get("html") or "")
        html_path = evidence_dir / "items" / html_rel
        view_url = f"https://pardusai.org/view/{case_id}"

        content = ""
        if html_path.exists():
            content = html_path.read_text(encoding="utf-8", errors="replace")

        assets = parse_assets_from_case_html(content)
        uploads = parse_uploads_from_case_html(content)

        trace_counter = collections.Counter()
        sample_titles: List[str] = []
        for asset in assets:
            trace_types = asset.get("trace_types") or []
            if isinstance(trace_types, list):
                for trace_type in trace_types:
                    trace_counter[str(trace_type)] += 1
                    global_trace_counter[str(trace_type)] += 1
            title_text = str(asset.get("title") or "").strip()
            if title_text:
                sample_titles.append(title_text)

        for upload in uploads:
            ext = file_ext(str(upload.get("file") or ""))
            global_ext_counter[ext] += 1

        scan_item = scan_map.get(case_id, {})
        sheet_counts = sheet_counts_from_scan_item(scan_item) if isinstance(scan_item, dict) else []
        excel_infos = scan_item.get("excel_infos") if isinstance(scan_item, dict) else []
        excel_file_count = len(excel_infos) if isinstance(excel_infos, list) else 0
        total_excel_files += excel_file_count
        sheet_max = max(sheet_counts) if sheet_counts else None

        chart_counts.append(chart_count)
        if isinstance(sheet_max, int):
            sheet_max_list.append(sheet_max)
        total_upload_files += upload_count
        if has_cjk(title):
            title_cjk_count += 1
        else:
            title_non_cjk_count += 1

        case_rows.append(
            {
                "idx": idx,
                "case_id": case_id,
                "title": title,
                "chart_count": chart_count,
                "upload_count": upload_count,
                "sheet_counts": sheet_counts,
                "sheet_max": sheet_max,
                "excel_file_count": excel_file_count,
                "top_trace_types": [k for k, _ in trace_counter.most_common(8)],
                "sample_titles": sample_titles[:8],
                "html": f"items/{html_rel}",
                "view_url": view_url,
            }
        )

    case_rows.sort(key=lambda x: (-x["chart_count"], x["idx"]))

    trace_top = global_trace_counter.most_common(20)
    ext_top = global_ext_counter.most_common(20)

    avg_charts = round(statistics.mean(chart_counts), 2) if chart_counts else 0
    median_charts = round(statistics.median(chart_counts), 2) if chart_counts else 0
    min_charts = min(chart_counts) if chart_counts else 0
    max_charts = max(chart_counts) if chart_counts else 0

    aggregate = {
        "generated_at": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "source_summary": str(summary_path),
        "source_scan": str(scan_path) if scan_path else "",
        "metrics": {
            "case_count": len(case_rows),
            "total_charts": int(sum(chart_counts)),
            "avg_charts": avg_charts,
            "median_charts": median_charts,
            "min_charts": min_charts,
            "max_charts": max_charts,
            "total_upload_files": int(total_upload_files),
            "excel_files_count": int(total_excel_files),
            "title_cjk_count": int(title_cjk_count),
            "title_non_cjk_count": int(title_non_cjk_count),
        },
        "charts": {
            "chart_counts": [it["chart_count"] for it in case_rows],
            "sheet_max": [it["sheet_max"] if isinstance(it["sheet_max"], int) else 0 for it in case_rows],
            "case_short": [it["case_id"][:6] for it in case_rows],
            "case_title": [it["title"] for it in case_rows],
            "trace_type_names": [k for k, _ in trace_top],
            "trace_type_values": [v for _, v in trace_top],
            "file_ext_names": [k for k, _ in ext_top],
            "file_ext_values": [v for _, v in ext_top],
        },
        "cases": case_rows,
    }

    output_path = pathlib.Path(args.output)
    if not output_path.is_absolute():
        output_path = evidence_dir / output_path
    json_path = pathlib.Path(args.write_json)
    if not json_path.is_absolute():
        json_path = evidence_dir / json_path

    json_path.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8")
    build_html(aggregate, case_rows, output_path, summary_path, scan_path)

    # Add a quick link in index.html if exists.
    index_path = evidence_dir / "index.html"
    if index_path.exists():
        txt = index_path.read_text(encoding="utf-8", errors="replace")
        marker = "aggregate_dashboard.html"
        if marker not in txt:
            insert_html = (
                "\n  <p><a href='aggregate_dashboard.html'>open 聚合总览（31条多sheet）</a> | "
                "<a href='aggregate_data.json'>aggregate_data.json</a></p>\n"
            )
            txt = txt.replace("</body>", f"{insert_html}</body>")
            index_path.write_text(txt, encoding="utf-8")

    print(f"written: {output_path}")
    print(f"written: {json_path}")
    if scan_path:
        print(f"scan_json: {scan_path}")


if __name__ == "__main__":
    main()
