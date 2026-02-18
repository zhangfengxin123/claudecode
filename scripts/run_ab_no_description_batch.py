#!/usr/bin/env python3
"""
Batch rerun Pardus public cases without description.txt and build A/B evidence packs.

Input:
  - source run with case evidence html files (used to detect description.txt cases)
  - Pardus auth token + username (from logged-in browser localStorage)

Output:
  runs/ab_no_description_batch_<timestamp>/
    index.html
    summary.json
    batch_manifest.json
    cases/
      case_001_<shortid>/
        meta.json
        original_case_summary.json
        original_input_snapshot.json
        new_uploads.json
        rerun_input_snapshot.json
        original_document.json
        new_document.json
        ab_summary.json
        case_context.json
        input_A_original_all/
        input_B_rerun_all/
        items/
          case_ab_001_<a>_vs_<b>.html
          report_full_A_capture.html
          report_full_B_capture.html
          report_full_B_zh.html
          report_full_bundle.html
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote

import requests

BASE = "https://pardusai.org"
PUB = f"{BASE}/v1/api/public/project"
PRIV = f"{BASE}/v1/api"


def utc_now_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def utc_now_tag() -> str:
    return dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def jdump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def jload(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def req_json(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json_body: Any = None,
    data: Any = None,
    files: Any = None,
    timeout: int = 120,
) -> Any:
    with requests.request(
        method=method,
        url=url,
        headers=headers,
        json=json_body,
        data=data,
        files=files,
        timeout=timeout,
    ) as r:
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {url} failed: {r.status_code} {r.text[:500]}")
        ct = r.headers.get("content-type", "")
        if "application/json" in ct:
            return r.json()
        txt = r.text
        try:
            return json.loads(txt)
        except Exception:
            return {"raw_text": txt}


def req_bytes(url: str, *, headers: Optional[Dict[str, str]] = None, timeout: int = 120) -> Tuple[bytes, str]:
    with requests.get(url, headers=headers, timeout=timeout) as r:
        if r.status_code >= 400:
            raise RuntimeError(f"GET {url} failed: {r.status_code} {r.text[:300]}")
        return r.content, r.headers.get("content-type", "application/octet-stream")


def safe_file_name(raw: str) -> str:
    out = (raw or "").replace("\\", "_").replace("/", "_").strip()
    return out or "unnamed_file"


def unique_snapshot_path(base_dir: Path, raw_name: str, seen: Dict[str, int]) -> Path:
    base = safe_file_name(raw_name)
    key = base.lower()
    n = seen.get(key, 0)
    seen[key] = n + 1
    if n == 0:
        return base_dir / base
    stem = Path(base).stem
    suf = Path(base).suffix
    return base_dir / f"{stem}__dup{n}{suf}"


def clean_rel_path(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    x = str(v).strip()
    if x.startswith("./"):
        x = x[2:]
    x = x.splitlines()[0].strip()
    return x or None


def textish(obj: Any) -> str:
    if isinstance(obj, str):
        return obj
    if not isinstance(obj, dict):
        return "" if obj is None else str(obj)
    for k in ("content", "Content", "text", "Text"):
        v = obj.get(k)
        if isinstance(v, str):
            return v
    return ""


def gather_text(node: Any) -> str:
    parts: List[str] = []

    def walk(x: Any) -> None:
        if isinstance(x, str):
            parts.append(x)
            return
        if isinstance(x, list):
            for y in x:
                walk(y)
            return
        if isinstance(x, dict):
            t = textish(x)
            if t:
                parts.append(t)
            for v in x.values():
                if isinstance(v, (dict, list)):
                    walk(v)

    walk(node)
    return " ".join(p.strip() for p in parts if p and str(p).strip()).strip()


def first_paragraph(doc: Dict[str, Any]) -> str:
    children = doc.get("document", {}).get("children", [])
    if not isinstance(children, list):
        return ""
    for n in children:
        if isinstance(n, dict) and str(n.get("type", "")).lower() == "paragraph":
            return gather_text(n)
    return ""


def h2_sections(doc: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    children = doc.get("document", {}).get("children", [])
    if not isinstance(children, list):
        return out
    for n in children:
        if not isinstance(n, dict):
            continue
        if str(n.get("type", "")).lower() == "heading" and int(n.get("level", 0) or 0) == 2:
            txt = gather_text(n)
            if txt:
                out.append(txt)
    return out


def extract_plot_asset_names(doc: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    seen = set()

    def add_name(raw_path: Optional[str]) -> None:
        p = clean_rel_path(raw_path)
        if not p:
            return
        name = Path(p).name
        if not name.endswith(".json"):
            return
        if name in seen:
            return
        seen.add(name)
        out.append(name)

    def walk(x: Any) -> None:
        if isinstance(x, list):
            for y in x:
                walk(y)
            return
        if not isinstance(x, dict):
            return

        t = str(x.get("type", "")).lower()
        if t == "plot":
            add_name(x.get("htmlPath") or x.get("code"))

        for v in x.values():
            if isinstance(v, (dict, list)):
                walk(v)

    walk(doc.get("document", {}))
    return out


def figure_meta(fig: Dict[str, Any], fallback_name: str) -> Tuple[str, List[str], int]:
    data = fig.get("data")
    if not isinstance(data, list):
        data = []
    trace_types: List[str] = []
    for tr in data:
        if isinstance(tr, dict):
            t = str(tr.get("type", "")).strip().lower()
            if t:
                trace_types.append(t)
    layout = fig.get("layout", {}) if isinstance(fig.get("layout"), dict) else {}
    title = ""
    t = layout.get("title")
    if isinstance(t, dict):
        title = str(t.get("text") or "").strip()
    elif isinstance(t, str):
        title = t.strip()
    if not title:
        title = fallback_name
    return title, trace_types, len(data)


def load_source_desc_cases(source_run: Path) -> List[Dict[str, Any]]:
    summary = jload(source_run / "summary.json")
    rows = summary.get("rows", [])
    if not isinstance(rows, list):
        raise ValueError(f"Invalid summary rows in {source_run / 'summary.json'}")

    items_dir = source_run / "items"
    desc_case_ids = set()
    for p in sorted(items_dir.glob("case_*.html")):
        t = p.read_text(encoding="utf-8", errors="ignore")
        if "description.txt" not in t.lower():
            continue
        m = re.search(r"/view/([0-9a-f]{64})", t)
        if m:
            desc_case_ids.add(m.group(1))

    selected = [r for r in rows if isinstance(r, dict) and r.get("case_id") in desc_case_ids]
    selected.sort(key=lambda x: int(x.get("idx") or 10**9))
    return selected


def build_trace_hist(entries: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    c = Counter()
    for e in entries:
        for t in e.get("trace_types", []):
            c[str(t)] += 1
    return dict(c)


def build_case_summary(
    case_id: str,
    title: str,
    uploads: Sequence[Dict[str, Any]],
    entries: Sequence[Dict[str, Any]],
    doc: Dict[str, Any],
) -> Dict[str, Any]:
    input_names = [str(x.get("name")) for x in uploads if isinstance(x, dict) and x.get("name")]
    return {
        "case_id": case_id,
        "title": title,
        "with_description": any(str(n).lower() == "description.txt" for n in input_names),
        "input_files": input_names,
        "input_data_files": [n for n in input_names if str(n).lower() != "description.txt"],
        "description_present": any(str(n).lower() == "description.txt" for n in input_names),
        "asset_count": len(entries),
        "assets": [
            {
                "name": e["name"],
                "trace_types": e.get("trace_types", []),
                "trace_count": e.get("trace_count", 0),
                "title": e.get("title", e["name"]),
            }
            for e in entries
        ],
        "h2_sections": h2_sections(doc),
        "first_paragraph": first_paragraph(doc),
    }


def build_ab_summary(
    *,
    a_case_id: str,
    b_case_id: str,
    a_title: str,
    a_uploads: Sequence[Dict[str, Any]],
    b_uploads: Sequence[Dict[str, Any]],
    a_doc: Dict[str, Any],
    b_doc: Dict[str, Any],
    a_entries: Sequence[Dict[str, Any]],
    b_entries: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    a_asset_names = [e["name"] for e in a_entries]
    b_asset_names = [e["name"] for e in b_entries]
    a_only = sorted(set(a_asset_names) - set(b_asset_names))
    b_only = sorted(set(b_asset_names) - set(a_asset_names))

    return {
        "generated_at": utc_now_iso(),
        "ab_case": {
            "template_case_id": a_case_id,
            "rerun_case_id": b_case_id,
            "title": f"{a_title} (with description vs without description)",
        },
        "A_with_description": {
            "case_id": a_case_id,
            "inputs": [x.get("name") for x in a_uploads if isinstance(x, dict)],
            "description_present": any(str(x.get("name", "")).lower() == "description.txt" for x in a_uploads if isinstance(x, dict)),
            "chart_assets_count": len(a_entries),
            "trace_type_hist": build_trace_hist(a_entries),
            "h2_sections": h2_sections(a_doc),
            "first_paragraph": first_paragraph(a_doc),
            "assets": [
                {
                    "name": e["name"],
                    "url": e["url"],
                    "trace_types": e.get("trace_types", []),
                    "trace_count": e.get("trace_count", 0),
                    "title": e.get("title", e["name"]),
                }
                for e in a_entries
            ],
        },
        "B_without_description": {
            "case_id": b_case_id,
            "inputs": [x.get("name") for x in b_uploads if isinstance(x, dict)],
            "description_present": any(str(x.get("name", "")).lower() == "description.txt" for x in b_uploads if isinstance(x, dict)),
            "chart_assets_count": len(b_entries),
            "trace_type_hist": build_trace_hist(b_entries),
            "h2_sections": h2_sections(b_doc),
            "first_paragraph": first_paragraph(b_doc),
            "assets": [
                {
                    "name": e["name"],
                    "url": e["url"],
                    "trace_types": e.get("trace_types", []),
                    "trace_count": e.get("trace_count", 0),
                    "title": e.get("title", e["name"]),
                }
                for e in b_entries
            ],
        },
        "diff": {
            "chart_assets_delta": len(b_entries) - len(a_entries),
            "sections_delta": len(h2_sections(b_doc)) - len(h2_sections(a_doc)),
            "description_removed_ok": not any(str(x.get("name", "")).lower() == "description.txt" for x in b_uploads if isinstance(x, dict)),
            "asset_name_overlap": len(set(a_asset_names) & set(b_asset_names)),
            "asset_old_only": a_only,
            "asset_new_only": b_only,
        },
    }


def render_case_ab_html(
    *,
    out_path: Path,
    a_case_id: str,
    b_case_id: str,
    a_title: str,
    a_uploads: Sequence[Dict[str, Any]],
    b_uploads: Sequence[Dict[str, Any]],
    a_entries: Sequence[Dict[str, Any]],
    b_entries: Sequence[Dict[str, Any]],
    ab_summary: Dict[str, Any],
) -> None:
    esc = html.escape
    a_json = json.dumps(a_entries, ensure_ascii=False)
    b_json = json.dumps(b_entries, ensure_ascii=False)

    a_inputs = "".join(f"<li><code>{esc(str(x.get('name')))}</code></li>" for x in a_uploads if isinstance(x, dict) and x.get("name"))
    b_inputs = "".join(f"<li><code>{esc(str(x.get('name')))}</code></li>" for x in b_uploads if isinstance(x, dict) and x.get("name"))
    a_h2 = "".join(f"<li><code>{esc(s)}</code></li>" for s in (ab_summary.get("A_with_description", {}).get("h2_sections") or []))
    b_h2 = "".join(f"<li><code>{esc(s)}</code></li>" for s in (ab_summary.get("B_without_description", {}).get("h2_sections") or []))
    diff = ab_summary.get("diff", {})

    html_doc = f"""<!doctype html>
<html lang='zh-CN'>
<head>
<meta charset='utf-8'/>
<meta name='viewport' content='width=device-width, initial-scale=1'/>
<title>A/B Visual Comparison - {a_case_id[:6]} vs {b_case_id[:6]}</title>
<script src='https://cdn.plot.ly/plotly-2.35.2.min.js'></script>
<style>
  :root {{ --bg:#f5f7fb; --card:#fff; --line:#e5e7eb; --text:#111827; --muted:#6b7280; --a:#0ea5e9; --b:#f97316; }}
  body {{ font-family: Arial, -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,sans-serif; margin: 18px; color:var(--text); background:var(--bg); }}
  h1,h2,h3,h4 {{ margin:8px 0; }}
  .muted {{ color:var(--muted); }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px; margin:12px 0; }}
  .grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
  .charts {{ display:grid; grid-template-columns:1fr; gap:10px; }}
  .plot {{ min-height:380px; }}
  .label {{ display:inline-block; font-size:12px; border:1px solid var(--line); border-radius:999px; padding:2px 8px; margin-right:6px; color:#334155; background:#f8fafc; }}
  .a-head {{ border-left:4px solid var(--a); padding-left:8px; }}
  .b-head {{ border-left:4px solid var(--b); padding-left:8px; }}
  table {{ border-collapse:collapse; width:100%; font-size:13px; }}
  th,td {{ border:1px solid var(--line); padding:8px; text-align:left; vertical-align:top; }}
  th {{ background:#f3f4f6; }}
  .pair-rows {{ display:grid; grid-template-columns:1fr; gap:12px; }}
  .pair-row {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
  .plot-shell {{ border:1px solid var(--line); border-radius:10px; padding:10px; background:#fff; }}
  .side-tag {{ display:inline-block; font-size:11px; font-weight:700; border-radius:999px; padding:2px 8px; margin-bottom:6px; }}
  .side-a {{ background:#e0f2fe; color:#075985; }}
  .side-b {{ background:#ffedd5; color:#9a3412; }}
  .empty {{ min-height:220px; display:flex; align-items:center; justify-content:center; border:1px dashed var(--line); border-radius:8px; color:var(--muted); background:#fafafa; }}
  .pair-title {{ font-weight:700; margin-bottom:6px; }}
  @media (max-width:1100px) {{ .grid2,.pair-row {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
  <h1>A/B 可视化对照：保留 description vs 去掉 description</h1>
  <p class='muted'>A（原始）<a href='https://pardusai.org/view/{esc(a_case_id)}' target='_blank'>/view/{esc(a_case_id)}</a> ｜ B（重跑）<a href='https://pardusai.org/view/{esc(b_case_id)}' target='_blank'>/view/{esc(b_case_id)}</a></p>

  <div class='card'>
    <h2>核心差异（结构化）</h2>
    <table>
      <thead><tr><th>metric</th><th>value</th></tr></thead>
      <tbody>
        <tr><td><code>description_removed_ok</code></td><td>{esc(str(diff.get('description_removed_ok')))}</td></tr>
        <tr><td><code>A_chart_assets</code></td><td>{len(a_entries)}</td></tr>
        <tr><td><code>B_chart_assets</code></td><td>{len(b_entries)}</td></tr>
        <tr><td><code>chart_assets_delta(B-A)</code></td><td>{len(b_entries)-len(a_entries)}</td></tr>
        <tr><td><code>A_h2_sections</code></td><td>{len(ab_summary.get('A_with_description', {}).get('h2_sections', []))}</td></tr>
        <tr><td><code>B_h2_sections</code></td><td>{len(ab_summary.get('B_without_description', {}).get('h2_sections', []))}</td></tr>
      </tbody>
    </table>
  </div>

  <div class='grid2'>
    <div class='card'>
      <h3 class='a-head'>A 输入与章节</h3>
      <h4>输入文件</h4><ul>{a_inputs}</ul>
      <h4>H2 章节</h4><ul>{a_h2}</ul>
    </div>
    <div class='card'>
      <h3 class='b-head'>B 输入与章节</h3>
      <h4>输入文件</h4><ul>{b_inputs}</ul>
      <h4>H2 章节</h4><ul>{b_h2}</ul>
    </div>
  </div>

  <div class='card'>
    <h2>图表逐项对照（A 左 / B 右）</h2>
    <p class='muted'>按输出顺序逐行对照。缺失项显示空位，便于快速观察变化。</p>
    <div id='pair-rows' class='pair-rows'></div>
  </div>

<script>
const A = {a_json};
const B = {b_json};
function plotInto(el, item) {{
  if (!item) {{ el.innerHTML = `<div class="empty">该位置无图表</div>`; return; }}
  if (!item.fig) {{ el.innerHTML = `<div class="empty">asset load failed</div>`; return; }}
  try {{
    const fig = item.fig || {{}};
    const data = Array.isArray(fig.data) ? fig.data : [];
    const layout = (fig.layout && typeof fig.layout === 'object') ? fig.layout : {{}};
    Plotly.newPlot(el, data, layout, {{responsive:true, displaylogo:false}});
  }} catch (e) {{
    el.innerHTML = `<div class="empty">render error</div>`;
  }}
}}
function makeCard(item, side) {{
  const wrap = document.createElement('article');
  wrap.className = 'plot-shell';
  const labels = item ? (item.trace_types || []).map(t => `<span class="label">${{t}}</span>`).join('') : '';
  const title = item ? (item.title || item.name || '(untitled)') : '无对应图表';
  const asset = item ? `<div class='muted'><code>${{item.name || ''}}</code></div>` : `<div class='muted'>—</div>`;
  const link = item ? `<div class='muted'><a href='${{item.url || '#'}}' target='_blank'>asset</a></div>` : '';
  wrap.innerHTML = `
    <span class='side-tag ${{side === 'A' ? 'side-a':'side-b'}}'>${{side}}</span>
    <div class='pair-title'>${{title}}</div>
    ${{asset}}
    <div>${{labels || '<span class="label">none</span>'}}</div>
    <div class='plot'></div>
    ${{link}}
  `;
  return wrap;
}}
function renderPairs(left, right) {{
  const mount = document.getElementById('pair-rows');
  const total = Math.max(left.length, right.length);
  for (let i = 0; i < total; i += 1) {{
    const row = document.createElement('div');
    row.className = 'pair-row';
    const aCard = makeCard(left[i] || null, 'A');
    const bCard = makeCard(right[i] || null, 'B');
    row.appendChild(aCard);
    row.appendChild(bCard);
    mount.appendChild(row);
    plotInto(aCard.querySelector('.plot'), left[i] || null);
    plotInto(bCard.querySelector('.plot'), right[i] || null);
  }}
}}
renderPairs(A, B);
</script>
</body>
</html>"""
    out_path.write_text(html_doc, encoding="utf-8")


def render_bundle_html(out_path: Path, a_case_id: str, b_case_id: str, case_ab_html_name: str) -> None:
    esc = html.escape
    s = f"""<!doctype html>
<html lang='zh-CN'>
<head>
<meta charset='utf-8'/>
<meta name='viewport' content='width=device-width, initial-scale=1'/>
<title>AB Full Report Bundle</title>
<style>
  :root{{--bg:#f6f8fc;--card:#fff;--line:#e5e7eb;--text:#111827;--muted:#6b7280;--a:#0ea5e9;--b:#f97316}}
  body{{margin:0;background:var(--bg);color:var(--text);font-family:Arial,-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif}}
  .wrap{{max-width:1280px;margin:0 auto;padding:20px}}
  h1{{margin:0 0 8px;font-size:30px}} h2{{margin:20px 0 10px;font-size:24px}}
  .muted{{color:var(--muted)}} .card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin:12px 0}}
  .compare-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
  .tag{{display:inline-block;font-size:11px;font-weight:700;border-radius:999px;padding:2px 8px;margin-bottom:8px}}
  .tag-a{{background:#e0f2fe;color:#075985}} .tag-b{{background:#ffedd5;color:#9a3412}}
  .pane{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:8px}}
  .pane iframe,.full-iframe{{width:100%;border:1px solid var(--line);border-radius:8px;background:#fff}}
  .pane iframe{{height:1200px}} .full-iframe{{height:1800px}}
  .links a{{display:inline-block;margin-right:10px;margin-bottom:8px;text-decoration:none;color:#2563eb}}
  @media (max-width:1080px){{.compare-grid{{grid-template-columns:1fr}}}}
</style>
</head>
<body><div class='wrap'>
  <h1>完整报告抓取包（A/B）</h1>
  <p class='muted'>左 A（含 description）与右 B（去 description）对比。优先展示简体中文对照页。</p>
  <div class='card'><h2>可点击入口</h2><div class='links'>
      <a href='./report_full_A_zh.html' target='_blank'>A 中文版</a>
      <a href='./report_full_B_zh.html' target='_blank'>B 中文版</a>
      <a href='./report_full_A_capture.html' target='_blank'>A 原文抓取</a>
      <a href='./report_full_B_capture.html' target='_blank'>B 原文抓取</a>
      <a href='https://pardusai.org/view/{esc(a_case_id)}' target='_blank'>A 原站页</a>
      <a href='https://pardusai.org/view/{esc(b_case_id)}' target='_blank'>B 原站页</a>
  </div></div>
  <div class='card'><h2>报告左右对比</h2>
    <div class='compare-grid'>
      <section class='pane'><span class='tag tag-a'>A</span><iframe src='./report_full_A_zh.html' title='A report'></iframe></section>
      <section class='pane'><span class='tag tag-b'>B</span><iframe src='./report_full_B_zh.html' title='B report'></iframe></section>
    </div>
  </div>
  <div class='card'><h2>图表结果全量对照</h2>
    <div class='links'><a href='./{esc(case_ab_html_name)}' target='_blank'>打开图表对照页</a></div>
    <iframe class='full-iframe' src='./{esc(case_ab_html_name)}' title='A/B chart full compare'></iframe>
  </div>
</div></body></html>
"""
    out_path.write_text(s, encoding="utf-8")


def process_one_case(
    *,
    row: Dict[str, Any],
    case_index: int,
    output_cases_dir: Path,
    token: str,
    username: str,
    language: str,
    web_search: bool,
    out_format: str,
    poll_interval: int,
    timeout_seconds: int,
    page_builder_script: Path,
) -> Dict[str, Any]:
    a_case_id = str(row.get("case_id"))
    a_title = str(row.get("title") or f"case_{case_index:03d}")
    short = a_case_id[:12]
    case_dir = output_cases_dir / f"case_{case_index:03d}_{short}"
    items_dir = case_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)

    auth_headers = {"authorization": f"Bearer {token}"}
    started = time.time()
    log: Dict[str, Any] = {"case_index": case_index, "template_case_id": a_case_id, "title": a_title, "started_at": utc_now_iso()}

    a_uploads = req_json("GET", f"{PUB}/{a_case_id}/uploads")
    if not isinstance(a_uploads, list):
        raise RuntimeError(f"Unexpected uploads payload for {a_case_id}")
    data_uploads = [u for u in a_uploads if isinstance(u, dict) and str(u.get("name", "")).lower() != "description.txt"]
    if len(data_uploads) == len(a_uploads):
        raise RuntimeError(f"Case {a_case_id} has no description.txt in uploads; expected description case")
    if not data_uploads:
        raise RuntimeError(f"Case {a_case_id} has no data files after removing description.txt")

    # Save ALL original inputs (including description.txt) so input evidence is complete.
    a_input_dir = case_dir / "input_A_original_all"
    a_input_dir.mkdir(parents=True, exist_ok=True)
    a_input_snapshot: List[Dict[str, Any]] = []
    files_payload: List[Tuple[str, Tuple[str, bytes, str]]] = []
    seen_a: Dict[str, int] = {}
    for idx, u in enumerate(a_uploads):
        if not isinstance(u, dict) or not u.get("name"):
            continue
        name = str(u.get("name"))
        file_url = f"{PUB}/{a_case_id}/files/{quote(name, safe='')}"
        content, ctype = req_bytes(file_url)
        local_path = unique_snapshot_path(a_input_dir, name, seen_a)
        local_path.write_bytes(content)
        is_desc = name.lower() == "description.txt"
        a_input_snapshot.append(
            {
                "idx": idx,
                "name": name,
                "is_description": is_desc,
                "content_type": ctype or "application/octet-stream",
                "bytes": len(content),
                "source_url": file_url,
                "local_path": str(local_path),
            }
        )
        if not is_desc:
            files_payload.append(("file", (name, content, ctype or "application/octet-stream")))

    expected_data_count = len([x for x in a_input_snapshot if not x.get("is_description")])
    if expected_data_count != len(files_payload):
        raise RuntimeError(
            f"Input snapshot mismatch for {a_case_id}: expected_data_count={expected_data_count}, payload_count={len(files_payload)}"
        )

    create_payload = {
        "title": f"AB_NO_DESC_{short}_{utc_now_tag()}",
        "visibility": "public",
        "description": "",
    }
    created = req_json("POST", f"{PRIV}/create_project", headers={**auth_headers, "content-type": "application/json"}, json_body=create_payload)
    b_case_id = str(created.get("project_id") or "")
    if not b_case_id:
        raise RuntimeError(f"create_project returned invalid response: {created}")
    log["rerun_case_id"] = b_case_id

    upload_resp = req_json(
        "POST",
        f"{PRIV}/upload_file/{username}/{b_case_id}",
        headers=auth_headers,
        data={"language": language, "web_search": str(web_search).lower(), "format": out_format},
        files=files_payload,
        timeout=600,
    )
    log["upload_response"] = upload_resp

    queue_history: List[Dict[str, Any]] = []
    deadline = time.time() + timeout_seconds
    final_status = "unknown"
    last_queue: Dict[str, Any] = {}
    while time.time() < deadline:
        q = req_json("GET", f"{PRIV}/queue/status/{b_case_id}", headers=auth_headers)
        if isinstance(q, dict):
            last_queue = q
            final_status = str(q.get("status", "")).lower()
            queue_history.append(
                {
                    "at": utc_now_iso(),
                    "status": final_status,
                    "tasks_ahead": q.get("tasks_ahead"),
                    "estimated_wait_minutes": q.get("estimated_wait_minutes"),
                }
            )
        if final_status in {"completed", "failed", "error", "cancelled"}:
            break
        time.sleep(poll_interval)

    log["queue_history"] = queue_history
    log["final_status"] = final_status
    log["last_queue"] = last_queue
    if final_status != "completed":
        raise RuntimeError(f"Rerun case {b_case_id} not completed, final_status={final_status}")

    a_doc = req_json("GET", f"{PUB}/{a_case_id}/document")
    b_doc = req_json("GET", f"{PUB}/{b_case_id}/document")
    b_uploads = req_json("GET", f"{PUB}/{b_case_id}/uploads")
    if not isinstance(b_uploads, list):
        b_uploads = []

    # Save rerun inputs so B-side input evidence is also complete.
    b_input_dir = case_dir / "input_B_rerun_all"
    b_input_dir.mkdir(parents=True, exist_ok=True)
    b_input_snapshot: List[Dict[str, Any]] = []
    seen_b: Dict[str, int] = {}
    for idx, u in enumerate(b_uploads):
        if not isinstance(u, dict) or not u.get("name"):
            continue
        name = str(u.get("name"))
        file_url = f"{PUB}/{b_case_id}/files/{quote(name, safe='')}"
        content, ctype = req_bytes(file_url)
        local_path = unique_snapshot_path(b_input_dir, name, seen_b)
        local_path.write_bytes(content)
        b_input_snapshot.append(
            {
                "idx": idx,
                "name": name,
                "is_description": name.lower() == "description.txt",
                "content_type": ctype or "application/octet-stream",
                "bytes": len(content),
                "source_url": file_url,
                "local_path": str(local_path),
            }
        )

    a_asset_names = extract_plot_asset_names(a_doc)
    b_asset_names = extract_plot_asset_names(b_doc)

    def collect_entries(case_id: str, names: Sequence[str]) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for i, raw_name in enumerate(names):
            # Some documents may carry non-string asset refs; normalize eagerly.
            name = str(raw_name)
            url = f"{PUB}/{case_id}/assets/{quote(name, safe='')}"
            entry: Dict[str, Any] = {
                "idx": i,
                "name": name,
                "url": url,
                "title": name,
                "trace_types": [],
                "trace_count": 0,
                "error": None,
                "fig": None,
            }
            try:
                fig = req_json("GET", url)
                if not isinstance(fig, dict):
                    raise RuntimeError("asset payload is not JSON object")
                title, trace_types, trace_count = figure_meta(fig, name)
                entry["title"] = title
                entry["trace_types"] = trace_types
                entry["trace_count"] = trace_count
                entry["fig"] = fig
            except Exception as e:
                entry["error"] = str(e)
            entries.append(entry)
        return entries

    a_entries = collect_entries(a_case_id, a_asset_names)
    b_entries = collect_entries(b_case_id, b_asset_names)

    original_case_summary = build_case_summary(a_case_id, a_title, a_uploads, a_entries, a_doc)
    ab_summary = build_ab_summary(
        a_case_id=a_case_id,
        b_case_id=b_case_id,
        a_title=a_title,
        a_uploads=a_uploads,
        b_uploads=b_uploads,
        a_doc=a_doc,
        b_doc=b_doc,
        a_entries=a_entries,
        b_entries=b_entries,
    )

    jdump(case_dir / "meta.json", {
        "run_id": case_dir.name,
        "generated_at": utc_now_iso(),
        "template_case_id": a_case_id,
        "rerun_case_id": b_case_id,
        "template_view_url": f"{BASE}/view/{a_case_id}",
        "rerun_view_url": f"{BASE}/view/{b_case_id}",
        "note": "Rerun uploaded only data files and excluded description.txt. Full inputs for A/B are snapshotted locally.",
    })
    jdump(case_dir / "original_case_summary.json", original_case_summary)
    jdump(case_dir / "original_input_snapshot.json", a_input_snapshot)
    jdump(case_dir / "new_uploads.json", b_uploads)
    jdump(case_dir / "rerun_input_snapshot.json", b_input_snapshot)
    jdump(case_dir / "original_document.json", a_doc)
    jdump(case_dir / "new_document.json", b_doc)
    jdump(case_dir / "ab_summary.json", ab_summary)
    jdump(case_dir / "case_context.json", {"source_row": row, "queue_log": log})

    case_ab_name = f"case_ab_001_{a_case_id[:12]}_vs_{b_case_id[:12]}.html"
    render_case_ab_html(
        out_path=items_dir / case_ab_name,
        a_case_id=a_case_id,
        b_case_id=b_case_id,
        a_title=a_title,
        a_uploads=a_uploads,
        b_uploads=b_uploads,
        a_entries=a_entries,
        b_entries=b_entries,
        ab_summary=ab_summary,
    )

    subprocess.run(
        [sys.executable, str(page_builder_script), "--run-dir", str(case_dir)],
        check=True,
    )

    render_bundle_html(
        out_path=items_dir / "report_full_bundle.html",
        a_case_id=a_case_id,
        b_case_id=b_case_id,
        case_ab_html_name=case_ab_name,
    )

    elapsed = round(time.time() - started, 2)
    return {
        "case_index": case_index,
        "title": a_title,
        "template_case_id": a_case_id,
        "rerun_case_id": b_case_id,
        "status": "ok",
        "elapsed_sec": elapsed,
        "case_dir": str(case_dir),
        "a_chart_count": len(a_entries),
        "b_chart_count": len(b_entries),
        "description_removed_ok": bool(ab_summary.get("diff", {}).get("description_removed_ok")),
    }


def build_batch_index(batch_dir: Path, rows: Sequence[Dict[str, Any]]) -> None:
    esc = html.escape
    tr = []
    for r in rows:
        link = "#"
        case_dir_raw = r.get("case_dir")
        if isinstance(case_dir_raw, str) and case_dir_raw.strip():
            cdir = Path(case_dir_raw.strip())
            if cdir.exists():
                try:
                    rel = cdir.relative_to(batch_dir)
                    link = f"{rel.as_posix()}/items/report_full_bundle.html"
                except Exception:
                    link = cdir.as_posix()
        tr.append(
            "<tr>"
            f"<td>{r.get('case_index')}</td>"
            f"<td><code>{esc(str(r.get('template_case_id',''))[:12])}</code></td>"
            f"<td><code>{esc(str(r.get('rerun_case_id',''))[:12])}</code></td>"
            f"<td>{esc(str(r.get('title','')))}</td>"
            f"<td>{esc(str(r.get('status','')))}</td>"
            f"<td>{r.get('a_chart_count','-')} / {r.get('b_chart_count','-')}</td>"
            f"<td>{r.get('elapsed_sec','-')}</td>"
            f"<td><a href='{esc(link)}' target='_blank'>open</a></td>"
            "</tr>"
        )
    ok = sum(1 for r in rows if r.get("status") == "ok")
    fail = len(rows) - ok
    doc = f"""<!doctype html>
<html lang='zh-CN'><head><meta charset='utf-8'/><meta name='viewport' content='width=device-width, initial-scale=1'/>
<title>AB Batch Index</title>
<style>
body{{font-family:Arial,sans-serif;margin:20px;background:#f6f8fc;color:#111827}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px;margin-bottom:12px}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #e5e7eb;padding:8px;font-size:13px}}th{{background:#f3f4f6}}
code{{background:#f3f4f6;padding:2px 6px;border-radius:6px}}
</style></head><body>
<div class='card'><h1>AB 批量重跑索引</h1><p>成功 {ok} / 失败 {fail} / 总数 {len(rows)}</p></div>
<div class='card'><table><thead><tr><th>#</th><th>A</th><th>B</th><th>title</th><th>status</th><th>charts A/B</th><th>sec</th><th>bundle</th></tr></thead>
<tbody>{''.join(tr)}</tbody></table></div>
</body></html>"""
    (batch_dir / "index.html").write_text(doc, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Batch rerun description-cases without description.txt")
    ap.add_argument("--source-run", type=Path, required=True)
    ap.add_argument("--output-root", type=Path, required=True)
    ap.add_argument("--token", default=os.getenv("PARDUS_AUTH_TOKEN", ""))
    ap.add_argument("--username", default=os.getenv("PARDUS_USERNAME", ""))
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--max-cases", type=int, default=12)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--language", default="en")
    ap.add_argument("--format", default="slide")
    ap.add_argument("--web-search", action="store_true")
    ap.add_argument("--poll-interval", type=int, default=20)
    ap.add_argument("--timeout-seconds", type=int, default=1800)
    args = ap.parse_args()

    if not args.token or not args.username:
        raise SystemExit("Missing --token/--username (or env PARDUS_AUTH_TOKEN/PARDUS_USERNAME)")

    source_run = args.source_run.resolve()
    out_root = args.output_root.resolve()
    run_id = f"ab_no_description_batch_{utc_now_tag()}"
    batch_dir = out_root / run_id
    cases_dir = batch_dir / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)

    selected = load_source_desc_cases(source_run)
    selected = selected[args.offset : args.offset + args.max_cases]
    if not selected:
        raise SystemExit("No description cases selected.")

    manifest = {
        "generated_at": utc_now_iso(),
        "run_id": run_id,
        "source_run": str(source_run),
        "selected_count": len(selected),
        "batch_size": args.batch_size,
        "cases": selected,
    }
    jdump(batch_dir / "batch_manifest.json", manifest)

    builder_script = Path(__file__).with_name("build_ab_full_report_pages.py")
    if not builder_script.exists():
        raise SystemExit(f"Missing script: {builder_script}")

    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    stop_reason: Optional[str] = None

    for i, row in enumerate(selected, start=1):
        try:
            res = process_one_case(
                row=row,
                case_index=i,
                output_cases_dir=cases_dir,
                token=args.token,
                username=args.username,
                language=args.language,
                web_search=args.web_search,
                out_format=args.format,
                poll_interval=args.poll_interval,
                timeout_seconds=args.timeout_seconds,
                page_builder_script=builder_script,
            )
            results.append(res)
        except Exception as e:
            msg = str(e)
            err = {
                "case_index": i,
                "title": row.get("title"),
                "template_case_id": row.get("case_id"),
                "status": "failed",
                "error": msg,
            }
            results.append(err)
            errors.append(err)

            # Hard stop: account-level creation quota reached.
            if "monthly public project limit" in msg.lower():
                stop_reason = "monthly_public_project_limit_reached"
                for j, rest in enumerate(selected[i:], start=i + 1):
                    skipped = {
                        "case_index": j,
                        "title": rest.get("title"),
                        "template_case_id": rest.get("case_id"),
                        "status": "skipped",
                        "error": stop_reason,
                    }
                    results.append(skipped)
                    errors.append(skipped)
                jdump(batch_dir / "progress.json", {"generated_at": utc_now_iso(), "results": results, "errors": errors, "stop_reason": stop_reason})
                break

        jdump(batch_dir / "progress.json", {"generated_at": utc_now_iso(), "results": results, "errors": errors, "stop_reason": stop_reason})

        # batch checkpoint marker
        if args.batch_size > 0 and i % args.batch_size == 0:
            jdump(batch_dir / f"batch_checkpoint_{i:03d}.json", {"generated_at": utc_now_iso(), "processed": i, "results": results, "errors": errors})

    summary = {
        "generated_at": utc_now_iso(),
        "run_id": run_id,
        "source_run": str(source_run),
        "selected_count": len(selected),
        "success_count": sum(1 for x in results if x.get("status") == "ok"),
        "failed_count": sum(1 for x in results if x.get("status") != "ok"),
        "stop_reason": stop_reason,
        "results": results,
        "errors": errors,
    }
    jdump(batch_dir / "summary.json", summary)
    build_batch_index(batch_dir, results)
    print(str(batch_dir))


if __name__ == "__main__":
    main()
