#!/usr/bin/env python3
"""
Build rich Pardus evidence pages for a batch of public projects.

Goal:
- show REAL chart assets rendered from Pardus /assets/*.json (Plotly)
- save FULL input source files from /uploads + /files/* to local snapshot
- render lightweight previews from those saved inputs
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import pathlib
import ssl
import textwrap
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

RECENT_API = "https://pardusai.org/v1/api/public/projects/recent?limit={limit}"
DOC_API = "https://pardusai.org/v1/api/public/project/{case_id}/document"
UP_API = "https://pardusai.org/v1/api/public/project/{case_id}/uploads"
ASSET_API = "https://pardusai.org/v1/api/public/project/{case_id}/assets/{name}"
FILE_API = "https://pardusai.org/v1/api/public/project/{case_id}/files/{name}"

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def h(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def truncate(s: str, n: int = 120) -> str:
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def http_get(
    url: str, timeout: int = 25, max_bytes: int | None = None
) -> Tuple[int, Dict[str, str], bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
            status = int(resp.getcode() or 0)
            headers = {k.lower(): v for k, v in dict(resp.headers).items()}
            body = resp.read(max_bytes) if isinstance(max_bytes, int) and max_bytes > 0 else resp.read()
            return status, headers, body
    except urllib.error.HTTPError as e:
        try:
            body = e.read() or b""
        except Exception:
            body = b""
        return int(e.code or 0), {}, body
    except Exception:
        return 0, {}, b""


def http_get_json(url: str, timeout: int = 25) -> Tuple[int, Dict[str, str], Any]:
    status, headers, body = http_get(url, timeout=timeout)
    if status != 200:
        return status, headers, None
    try:
        return status, headers, json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return status, headers, None


def extract_asset_refs(document_payload: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    chart_json: List[str] = []
    svg_assets: List[str] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            hp = x.get("htmlPath")
            if isinstance(hp, str):
                if hp.endswith(".json"):
                    chart_json.append(hp)
                elif hp.endswith(".svg"):
                    svg_assets.append(hp)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for it in x:
                walk(it)

    walk(document_payload)
    # keep order while dedup
    def uniq(items: List[str]) -> List[str]:
        seen = set()
        out = []
        for it in items:
            if it not in seen:
                seen.add(it)
                out.append(it)
        return out

    return uniq(chart_json), uniq(svg_assets)


def normalize_asset_name(path_ref: str) -> str:
    return path_ref.strip().lstrip("./").lstrip("/")


def detect_text_preview(filename: str, body: bytes, content_type: str) -> Tuple[str, str]:
    """Return (kind, html_snippet)."""
    name = filename.lower()

    # binary quick check
    if b"\x00" in body[:4096]:
        return "binary", "<em>binary content; preview skipped</em>"

    text = body.decode("utf-8", errors="replace")
    snippet = text[:20000]

    if name.endswith(".csv"):
        lines = snippet.splitlines()
        reader = csv.reader(lines)
        rows = []
        for i, row in enumerate(reader):
            if i > 8:
                break
            rows.append(row)
        if not rows:
            return "csv", "<em>empty csv</em>"
        head = rows[0]
        body_rows = rows[1:]
        table = ["<table><thead><tr>"]
        for c in head:
            table.append(f"<th>{h(c)}</th>")
        table.append("</tr></thead><tbody>")
        for r in body_rows:
            table.append("<tr>")
            for c in r:
                table.append(f"<td>{h(c)}</td>")
            table.append("</tr>")
        table.append("</tbody></table>")
        return "csv", "".join(table)

    if (
        name.endswith(".txt")
        or name.endswith(".md")
        or name.endswith(".json")
        or "text" in content_type
        or "json" in content_type
    ):
        return "text", f"<pre>{h(snippet[:6000])}</pre>"

    if name.endswith(".pdf") or "pdf" in content_type:
        return "pdf", "<em>pdf file; inline preview skipped</em>"

    return "text", f"<pre>{h(snippet[:4000])}</pre>"


@dataclass
class UploadEvidence:
    name: str
    url: str
    status: int
    size: int
    content_type: str
    preview_kind: str
    is_truncated: bool
    preview_html: str
    local_snapshot: str


def safe_file_name(raw: str) -> str:
    out = (raw or "").replace("\\", "_").replace("/", "_").strip()
    return out or "unnamed_file"


def unique_snapshot_path(base_dir: pathlib.Path, raw_name: str, seen: Dict[str, int]) -> pathlib.Path:
    base = safe_file_name(raw_name)
    key = base.lower()
    n = seen.get(key, 0)
    seen[key] = n + 1
    if n == 0:
        return base_dir / base
    stem = pathlib.Path(base).stem
    suf = pathlib.Path(base).suffix
    return base_dir / f"{stem}__dup{n}{suf}"


@dataclass
class AssetEvidence:
    name: str
    url: str
    status: int
    kind: str  # plotly_json / svg / other
    title: str
    trace_count: int
    trace_types: str
    payload: Any


def summarize_plotly_payload(payload: Any) -> Tuple[str, int, str]:
    if not isinstance(payload, dict):
        return "", 0, ""
    title = ""
    layout = payload.get("layout")
    if isinstance(layout, dict):
        t = layout.get("title")
        if isinstance(t, dict):
            title = str(t.get("text") or "")
        elif isinstance(t, str):
            title = t
    data = payload.get("data")
    if not isinstance(data, list):
        return title, 0, ""
    types = []
    for tr in data:
        if isinstance(tr, dict):
            types.append(str(tr.get("type") or "unknown"))
    uniq = []
    seen = set()
    for t in types:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return title, len(data), ", ".join(uniq)


def build_case_html(
    case: Dict[str, Any],
    out_dir: pathlib.Path,
    case_dir: pathlib.Path,
    *,
    allow_empty_uploads: bool = False,
) -> str:
    case_id = case["case_id"]
    title = case.get("title") or "(untitled)"

    doc_url = DOC_API.format(case_id=case_id)
    up_url = UP_API.format(case_id=case_id)

    doc_status, _, doc_payload = http_get_json(doc_url)
    up_status, _, up_payload = http_get_json(up_url)
    if up_status != 200 or not isinstance(up_payload, list):
        raise RuntimeError(f"uploads endpoint unavailable: case={case_id} status={up_status}")

    uploads: List[str] = []
    for it in up_payload:
        if isinstance(it, dict) and isinstance(it.get("name"), str):
            uploads.append(it["name"])
    if not uploads and not allow_empty_uploads:
        raise RuntimeError(f"uploads empty: case={case_id}; set --allow-empty-uploads to bypass")

    chart_refs: List[str] = []
    svg_refs: List[str] = []
    if isinstance(doc_payload, dict):
        chart_refs, svg_refs = extract_asset_refs(doc_payload)

    upload_evidence: List[UploadEvidence] = []
    input_snapshot_dir = out_dir / "input" / f"case_{case['idx']:03d}_{case_id[:12]}"
    input_snapshot_dir.mkdir(parents=True, exist_ok=True)
    seen_names: Dict[str, int] = {}
    for name in uploads:
        qname = urllib.parse.quote(name)
        furl = FILE_API.format(case_id=case_id, name=qname)
        st, headers, body = http_get(furl, timeout=60)
        ctype = headers.get("content-type", "")
        local_snapshot = "-"
        if st != 200:
            raise RuntimeError(f"required input file download failed: case={case_id} file={name} http={st}")
        local_path = unique_snapshot_path(input_snapshot_dir, name, seen_names)
        local_path.write_bytes(body)
        try:
            local_snapshot = str(local_path.relative_to(out_dir))
        except Exception:
            local_snapshot = str(local_path)
        max_preview_bytes = 200_000
        preview_blob = body[:max_preview_bytes]
        kind, preview_html = detect_text_preview(name, preview_blob, ctype)
        upload_evidence.append(
            UploadEvidence(
                name=name,
                url=furl,
                status=st,
                size=len(body),
                content_type=ctype,
                preview_kind=kind,
                is_truncated=(len(body) > max_preview_bytes),
                preview_html=preview_html,
                local_snapshot=local_snapshot,
            )
        )

    asset_evidence: List[AssetEvidence] = []
    # json charts
    for ref in chart_refs:
        nm = normalize_asset_name(ref)
        aurl = ASSET_API.format(case_id=case_id, name=urllib.parse.quote(nm))
        st, _, payload = http_get_json(aurl, timeout=30)
        chart_title, trace_count, trace_types = summarize_plotly_payload(payload)
        kind = "plotly_json" if (st == 200 and isinstance(payload, dict) and isinstance(payload.get("data"), list)) else "json"
        asset_evidence.append(
            AssetEvidence(
                name=nm,
                url=aurl,
                status=st,
                kind=kind,
                title=chart_title,
                trace_count=trace_count,
                trace_types=trace_types,
                payload=payload if st == 200 else None,
            )
        )

    # svg assets
    for ref in svg_refs:
        nm = normalize_asset_name(ref)
        aurl = ASSET_API.format(case_id=case_id, name=urllib.parse.quote(nm))
        st, _, body = http_get(aurl, timeout=30)
        payload = body.decode("utf-8", errors="replace") if st == 200 else ""
        asset_evidence.append(
            AssetEvidence(
                name=nm,
                url=aurl,
                status=st,
                kind="svg",
                title=nm,
                trace_count=0,
                trace_types="svg",
                payload=payload,
            )
        )

    # summary table rows
    asset_rows = []
    for a in asset_evidence:
        asset_rows.append(
            "<tr>"
            f"<td><code>{h(a.name)}</code></td>"
            f"<td>{h(a.kind)}</td>"
            f"<td>{h(a.status)}</td>"
            f"<td>{h(a.title or '-')}</td>"
            f"<td>{h(a.trace_count)}</td>"
            f"<td>{h(a.trace_types or '-')}</td>"
            f"<td><a href='{h(a.url)}' target='_blank' rel='noopener noreferrer'>open</a></td>"
            "</tr>"
        )

    upload_rows = []
    for u in upload_evidence:
        upload_rows.append(
            "<tr>"
            f"<td><code>{h(u.name)}</code></td>"
            f"<td>{h(u.status)}</td>"
            f"<td>{h(u.size)}</td>"
            f"<td>{h(u.content_type or '-')}</td>"
            f"<td><code>{h(u.local_snapshot)}</code></td>"
            f"<td><a href='{h(u.url)}' target='_blank' rel='noopener noreferrer'>open</a></td>"
            "</tr>"
        )

    # build plot blocks
    render_cards = []
    js_assets = []
    chart_idx = 0
    for a in asset_evidence:
        if a.status != 200:
            render_cards.append(
                f"<article class='chart-card'><h4>{h(a.name)}</h4><p class='err'>asset fetch failed: HTTP {h(a.status)}</p></article>"
            )
            continue
        if a.kind == "svg":
            render_cards.append(
                f"<article class='chart-card'><h4>{h(a.name)}</h4><img src='{h(a.url)}' alt='{h(a.name)}' style='max-width:100%; border:1px solid #e5e7eb; border-radius:8px'/></article>"
            )
            continue

        div_id = f"chart-{chart_idx}"
        chart_idx += 1
        render_cards.append(
            "<article class='chart-card'>"
            f"<h4>{h(a.title or a.name)}</h4>"
            f"<div class='muted code'>{h(a.name)}</div>"
            f"<div id='{h(div_id)}' class='plot'></div>"
            f"<p class='muted code'>{h(a.url)}</p>"
            "</article>"
        )
        js_assets.append({"div": div_id, "name": a.name, "fig": a.payload})

    upload_preview_blocks = []
    for u in upload_evidence:
        upload_preview_blocks.append(
            "<details>"
            f"<summary><code>{h(u.name)}</code> · HTTP {h(u.status)} · {h(u.preview_kind or 'unknown')}"
            f"{' · head-only preview' if u.is_truncated else ''}</summary>"
            f"{u.preview_html}"
            "</details>"
        )

    page = f"""<!doctype html>
<html lang='zh-CN'>
<head>
  <meta charset='utf-8'/>
  <meta name='viewport' content='width=device-width, initial-scale=1'/>
  <title>Pardus Evidence {h(case_id[:12])}</title>
  <script src='https://cdn.plot.ly/plotly-2.35.2.min.js'></script>
  <style>
    body {{ font-family: -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif; margin: 20px; color:#1f2937; background:#f8fafc; }}
    h1,h2,h3 {{ margin: 10px 0; }}
    .muted {{ color:#6b7280; }}
    .code {{ font-family: ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
    .card {{ background:#fff; border:1px solid #e5e7eb; border-radius:10px; padding:14px; margin:12px 0; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
    th,td {{ border:1px solid #e5e7eb; padding:8px; vertical-align: top; text-align:left; }}
    th {{ background:#f3f4f6; }}
    .charts {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(460px, 1fr)); gap:12px; }}
    .chart-card {{ background:#fff; border:1px solid #e5e7eb; border-radius:10px; padding:10px; }}
    .plot {{ min-height: 420px; }}
    details {{ margin: 8px 0; }}
    pre {{ white-space: pre-wrap; word-break: break-word; background:#0b1020; color:#e5e7eb; padding:10px; border-radius:8px; max-height: 420px; overflow:auto; }}
    .err {{ color:#b91c1c; }}
  </style>
</head>
<body>
  <h1>{h(title)}</h1>
  <div class='muted'>case_id: <span class='code'>{h(case_id)}</span></div>
  <div class='muted'>view: <a href='https://pardusai.org/view/{h(case_id)}' target='_blank' rel='noopener noreferrer'>https://pardusai.org/view/{h(case_id)}</a></div>

  <div class='card'>
    <h2>1) 输入数据源（uploads/files）</h2>
    <p class='muted'>这部分是 Pardus 接收到的原始输入文件清单。每个文件已完整下载到本地快照目录。</p>
    <p class='muted code'>snapshot_dir: {h(str(input_snapshot_dir.relative_to(out_dir)))}</p>
    <table>
      <thead><tr><th>file</th><th>http</th><th>bytes</th><th>content-type</th><th>local snapshot</th><th>link</th></tr></thead>
      <tbody>{''.join(upload_rows) if upload_rows else '<tr><td colspan="6">no uploads</td></tr>'}</tbody>
    </table>
    <h3>文件内容预览</h3>
    {''.join(upload_preview_blocks) if upload_preview_blocks else '<p class="muted">no preview</p>'}
  </div>

  <div class='card'>
    <h2>2) 图表资产清单（document -> htmlPath）</h2>
    <p class='muted'>这部分是 Pardus 在报告里引用的全部图表/流程图资产。</p>
    <table>
      <thead><tr><th>asset</th><th>kind</th><th>http</th><th>chart title</th><th>traces</th><th>trace types</th><th>url</th></tr></thead>
      <tbody>{''.join(asset_rows) if asset_rows else '<tr><td colspan="7">no assets</td></tr>'}</tbody>
    </table>
  </div>

  <div class='card'>
    <h2>3) 全部图表渲染（真实资产）</h2>
    <div class='charts'>
      {''.join(render_cards) if render_cards else '<p class="muted">no renderable charts</p>'}
    </div>
  </div>

  <script>
    const ASSETS = {json.dumps(js_assets, ensure_ascii=False)};
    for (const item of ASSETS) {{
      const el = document.getElementById(item.div);
      if (!el) continue;
      try {{
        const fig = item.fig || {{}};
        const data = Array.isArray(fig.data) ? fig.data : [];
        const layout = (fig.layout && typeof fig.layout === 'object') ? fig.layout : {{}};
        const config = {{responsive: true, displaylogo: false}};
        Plotly.newPlot(el, data, layout, config);
      }} catch (e) {{
        el.innerHTML = `<pre>render error: ${{String(e)}}</pre>`;
      }}
    }}
  </script>
</body>
</html>
"""

    out_name = f"case_{case['idx']:03d}_{case_id[:12]}.html"
    (case_dir / out_name).write_text(page, encoding="utf-8")

    return out_name


def load_cases(args: argparse.Namespace) -> List[Dict[str, Any]]:
    if args.input_json:
        obj = json.loads(pathlib.Path(args.input_json).read_text(encoding="utf-8"))
        if isinstance(obj, dict) and isinstance(obj.get("results"), list):
            start = max(int(args.start_index), 0)
            subset = obj["results"][start : start + args.sample_size]
            return [
                {
                    "idx": start + i + 1,
                    "case_id": it.get("case_id"),
                    "title": it.get("title", ""),
                }
                for i, it in enumerate(subset)
                if isinstance(it, dict) and it.get("case_id")
            ]

    st, _, payload = http_get_json(RECENT_API.format(limit=args.recent_limit))
    if st != 200 or not isinstance(payload, list):
        raise SystemExit(f"failed to fetch recent list: HTTP {st}")

    out = []
    start = max(int(args.start_index), 0)
    subset = payload[start : start + args.sample_size]
    for i, it in enumerate(subset):
        if not isinstance(it, dict):
            continue
        cid = it.get("id") or it.get("case_id")
        if not isinstance(cid, str):
            u = it.get("url")
            if isinstance(u, str) and "/view/" in u:
                try:
                    cid = u.rstrip("/").split("/view/")[-1]
                except Exception:
                    cid = None
        if not isinstance(cid, str):
            continue
        out.append({"idx": start + i + 1, "case_id": cid, "title": it.get("title", "")})
    return out


def build_index(cases: List[Dict[str, Any]], out_dir: pathlib.Path, rows: List[Dict[str, Any]]) -> None:
    tr = []
    for r in rows:
        html_cell = (
            f"<a href='items/{h(r['html'])}'>open</a>"
            if r.get("html")
            else f"<span style='color:#b91c1c'>{h(r.get('error', 'failed'))}</span>"
        )
        tr.append(
            "<tr>"
            f"<td>{h(r['idx'])}</td>"
            f"<td><code>{h(r['case_id'][:12])}</code></td>"
            f"<td>{h(truncate(r.get('title',''), 90))}</td>"
            f"<td>{h(r.get('status', 'ok'))}</td>"
            f"<td>{h(r.get('chart_count', 0))}</td>"
            f"<td>{h(r.get('upload_count', 0))}</td>"
            f"<td>{html_cell}</td>"
            "</tr>"
        )

    page = f"""<!doctype html>
<html lang='zh-CN'>
<head>
  <meta charset='utf-8'/>
  <meta name='viewport' content='width=device-width, initial-scale=1'/>
  <title>Pardus Charts Evidence Index</title>
  <style>
    body {{ font-family: -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif; margin: 22px; }}
    table {{ border-collapse: collapse; width:100%; }}
    th,td {{ border:1px solid #e5e7eb; padding:8px; text-align:left; }}
    th {{ background:#f3f4f6; }}
    code {{ background:#f3f4f6; padding:2px 6px; border-radius:6px; }}
  </style>
</head>
<body>
  <h1>Pardus 图表证据索引</h1>
  <p>generated_at: <code>{h(dt.datetime.now().isoformat(timespec='seconds'))}</code></p>
  <p>sample_size: <code>{h(len(rows))}</code></p>
  <table>
    <thead><tr><th>#</th><th>case</th><th>title</th><th>status</th><th>charts</th><th>uploads</th><th>html</th></tr></thead>
    <tbody>{''.join(tr)}</tbody>
  </table>
</body>
</html>
"""
    (out_dir / "index.html").write_text(page, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build full chart evidence html for Pardus public cases")
    ap.add_argument("--sample-size", type=int, default=10)
    ap.add_argument("--recent-limit", type=int, default=500)
    ap.add_argument("--start-index", type=int, default=0)
    ap.add_argument("--input-json", default="")
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--allow-empty-uploads", action="store_true")
    args = ap.parse_args()

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = pathlib.Path(args.out_dir or f"Pardus/.codex_session/runs/chart_evidence_{ts}")
    items_dir = out_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)

    cases = load_cases(args)
    rows: List[Dict[str, Any]] = []

    for c in cases:
        cid = c["case_id"]
        # pre-fetch minimal counts for index
        dst, _, dpl = http_get_json(DOC_API.format(case_id=cid))
        ust, _, upl = http_get_json(UP_API.format(case_id=cid))
        charts, svgs = extract_asset_refs(dpl) if isinstance(dpl, dict) else ([], [])
        try:
            html_name = build_case_html(c, out_dir, items_dir, allow_empty_uploads=args.allow_empty_uploads)
            rows.append(
                {
                    "idx": c["idx"],
                    "case_id": cid,
                    "title": c.get("title", ""),
                    "chart_count": len(charts) + len(svgs),
                    "upload_count": len(upl) if isinstance(upl, list) else 0,
                    "doc_status": dst,
                    "up_status": ust,
                    "html": html_name,
                    "status": "ok",
                }
            )
        except Exception as e:
            rows.append(
                {
                    "idx": c["idx"],
                    "case_id": cid,
                    "title": c.get("title", ""),
                    "chart_count": len(charts) + len(svgs),
                    "upload_count": len(upl) if isinstance(upl, list) else 0,
                    "doc_status": dst,
                    "up_status": ust,
                    "html": "",
                    "status": "failed",
                    "error": str(e),
                }
            )

    report = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "sample_size": len(rows),
        "rows": rows,
    }
    (out_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    build_index(cases, out_dir, rows)

    # latest symlink
    latest = out_dir.parent / "chart_evidence_latest"
    try:
        if latest.exists() or latest.is_symlink():
            latest.unlink()
        latest.symlink_to(out_dir.name)
    except Exception:
        pass

    print(f"saved: {out_dir / 'summary.json'}")
    print(f"saved: {out_dir / 'index.html'}")


if __name__ == "__main__":
    main()
