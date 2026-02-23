#!/usr/bin/env python3
"""
Backfill incomplete no-description Pardus cases by case_id list.

Workflow:
1) Read incomplete list from no_desc_global_completeness_*.json
2) Run build_chart_evidence_batch.py in batches for target case_ids
3) Rebuild full report pages with in-place charts
4) Validate strict completeness per case:
   - no description.txt in uploads
   - input snapshot exists
   - document available
   - has plot assets
   - all plot assets fetchable
   - full report html generated
5) Emit summary + index html
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import importlib.util
import json
import re
import ssl
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib import error, parse, request

UP_API = "https://pardusai.org/v1/api/public/project/{case_id}/uploads"
DOC_API = "https://pardusai.org/v1/api/public/project/{case_id}/document"
ASSET_API = "https://pardusai.org/v1/api/public/project/{case_id}/assets/{name}"

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

ASSETS_RE = re.compile(r"const\s+ASSETS\s*=\s*(\[[\s\S]*?\])\s*;", re.M)


def h(x: Any) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def utc_tag() -> str:
    return dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def http_get_json(url: str, timeout: int = 20) -> Tuple[int, Any]:
    req = request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
            st = int(resp.getcode() or 0)
            body = resp.read()
        if st != 200:
            return st, None
        try:
            return st, json.loads(body.decode("utf-8", errors="replace"))
        except Exception:
            return st, None
    except error.HTTPError as e:
        return int(e.code or 0), None
    except Exception:
        return 0, None


def http_get_json_retry(url: str, retries: int = 3, timeout: int = 20) -> Tuple[int, Any]:
    last = 0
    for _ in range(max(1, retries)):
        st, obj = http_get_json(url, timeout=timeout)
        last = st
        if st == 200 and isinstance(obj, (dict, list)):
            return st, obj
    return last, None


def parse_assets_from_chart_html(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    m = ASSETS_RE.search(text)
    if not m:
        return {}
    try:
        arr = json.loads(m.group(1))
    except Exception:
        return {}
    if not isinstance(arr, list):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for it in arr:
        if not isinstance(it, dict):
            continue
        name = it.get("name")
        fig = it.get("fig")
        if isinstance(name, str) and isinstance(fig, dict) and isinstance(fig.get("data"), list):
            out[name] = fig
            out[Path(name).name] = fig
    return out


def collect_plot_assets(doc_payload: Dict[str, Any], ab_module: Any) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()

    def rec(x: Any) -> None:
        if isinstance(x, dict):
            t = str(x.get("type", "")).lower()
            if t == "plot":
                raw = x.get("htmlPath") or x.get("code") or ""
                if isinstance(raw, str):
                    path = ab_module.clean_rel_path(raw)
                    if path.endswith(".json") and path not in seen:
                        seen.add(path)
                        out.append(path)
            for v in x.values():
                rec(v)
        elif isinstance(x, list):
            for it in x:
                rec(it)

    rec(doc_payload.get("document", {}))
    return out


def fetch_asset(case_id: str, asset_path: str, timeout_sec: int = 12) -> Tuple[int, Any]:
    quoted = parse.quote(asset_path, safe="/._-")
    url = ASSET_API.format(case_id=case_id, name=quoted)
    # keep same transport style with prior scripts
    proc = subprocess.run(
        ["curl", "-sS", "--max-time", str(timeout_sec), url],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        return 0, None
    try:
        obj = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except Exception:
        return 0, None
    if isinstance(obj, dict) and isinstance(obj.get("data"), list):
        return 200, obj
    return 0, None


def build_index(run_dir: Path, rows: Sequence[Dict[str, Any]]) -> None:
    def tr(r: Dict[str, Any]) -> str:
        status = r.get("status", "")
        status_cell = f"<span style='color:#065f46'>{h(status)}</span>" if status == "ok" else f"<span style='color:#b91c1c'>{h(status)}</span>"
        report = r.get("full_report_html")
        report_cell = f"<a href='{h(report)}' target='_blank' rel='noopener noreferrer'>open</a>" if isinstance(report, str) and report else "-"
        chart = r.get("chart_evidence_html")
        chart_cell = f"<a href='{h(chart)}' target='_blank' rel='noopener noreferrer'>open</a>" if isinstance(chart, str) and chart else "-"
        return (
            "<tr>"
            f"<td>{h(r.get('seq'))}</td>"
            f"<td><code>{h(str(r.get('case_id',''))[:12])}</code></td>"
            f"<td>{h(r.get('title',''))}</td>"
            f"<td>{status_cell}</td>"
            f"<td>{h(r.get('strict_pass'))}</td>"
            f"<td>{h(r.get('input_complete'))}</td>"
            f"<td>{h(r.get('plot_assets_in_doc'))}</td>"
            f"<td>{h(r.get('plots_renderable'))}</td>"
            f"<td>{h(r.get('plot_assets_failed'))}</td>"
            f"<td>{h(r.get('error',''))}</td>"
            f"<td>{report_cell}</td>"
            f"<td>{chart_cell}</td>"
            f"<td><a href='https://pardusai.org/view/{h(r.get('case_id',''))}' target='_blank' rel='noopener noreferrer'>view</a></td>"
            "</tr>"
        )

    ok = [x for x in rows if x.get("status") == "ok"]
    strict = [x for x in rows if x.get("strict_pass") is True]
    page = f"""<!doctype html>
<html lang='zh-CN'>
<head>
  <meta charset='utf-8'/>
  <meta name='viewport' content='width=device-width, initial-scale=1'/>
  <title>No-desc Backfill Index</title>
  <style>
    body {{ font-family: -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif; margin: 22px; color:#111827; }}
    table {{ border-collapse: collapse; width:100%; font-size:13px; }}
    th,td {{ border:1px solid #e5e7eb; padding:6px 8px; text-align:left; vertical-align:top; }}
    th {{ background:#f3f4f6; position:sticky; top:0; z-index:2; }}
    code {{ background:#f3f4f6; padding:2px 6px; border-radius:6px; }}
    .wrap {{ overflow:auto; max-height: calc(100vh - 200px); border:1px solid #e5e7eb; border-radius:8px; }}
  </style>
</head>
<body>
  <h1>No-description 回补结果</h1>
  <p>generated_at: <code>{h(dt.datetime.now().isoformat(timespec='seconds'))}</code></p>
  <p>total: <code>{len(rows)}</code> · ok: <code>{len(ok)}</code> · strict_pass: <code>{len(strict)}</code></p>
  <div class='wrap'>
    <table>
      <thead><tr><th>#</th><th>case</th><th>title</th><th>status</th><th>strict</th><th>input</th><th>assets</th><th>renderable</th><th>asset_fail</th><th>error</th><th>full report</th><th>chart evidence</th><th>pardus</th></tr></thead>
      <tbody>{''.join(tr(x) for x in rows)}</tbody>
    </table>
  </div>
</body>
</html>
"""
    (run_dir / "index.html").write_text(page, encoding="utf-8")


def load_incomplete_cases(path: Path) -> List[Dict[str, str]]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    need = obj.get("need_backfill", [])
    out: List[Dict[str, str]] = []
    for it in need:
        if not isinstance(it, dict):
            continue
        cid = it.get("case_id")
        if not isinstance(cid, str) or not cid:
            continue
        out.append({"case_id": cid, "title": str(it.get("title", ""))})
    # de-dup keep order
    seen = set()
    dedup = []
    for it in out:
        if it["case_id"] in seen:
            continue
        seen.add(it["case_id"])
        dedup.append(it)
    return dedup


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill no-description incomplete cases")
    ap.add_argument("--incomplete-json", required=True)
    ap.add_argument("--batch-size", type=int, default=20)
    ap.add_argument("--out-dir", default="")
    args = ap.parse_args()

    repo = Path.cwd()
    runs = repo / "Pardus/.codex_session/runs"
    scripts = repo / "Pardus/.codex_session/scripts"
    build_batch_py = scripts / "build_chart_evidence_batch.py"
    ab_full_py = scripts / "build_ab_full_report_pages.py"

    spec = importlib.util.spec_from_file_location("ab_full", ab_full_py)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {ab_full_py}")
    ab = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ab)

    run_dir = Path(args.out_dir) if args.out_dir else (runs / f"no_desc_backfill_{utc_tag()}")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "batches").mkdir(exist_ok=True)
    (run_dir / "full_reports").mkdir(exist_ok=True)
    (run_dir / "tmp").mkdir(exist_ok=True)

    cases = load_incomplete_cases(Path(args.incomplete_json))
    print(f"[info] incomplete_cases={len(cases)}")

    rows: List[Dict[str, Any]] = []
    seq = 0
    for i in range(0, len(cases), args.batch_size):
        batch_no = i // args.batch_size + 1
        batch = cases[i : i + args.batch_size]
        out_batch = run_dir / "batches" / f"batch_{batch_no:02d}"
        out_batch.mkdir(parents=True, exist_ok=True)
        in_json = run_dir / "tmp" / f"batch_{batch_no:02d}.json"
        in_json.write_text(json.dumps({"results": batch}, ensure_ascii=False, indent=2), encoding="utf-8")

        cmd = [
            sys.executable,
            str(build_batch_py),
            "--input-json",
            str(in_json),
            "--sample-size",
            str(len(batch)),
            "--out-dir",
            str(out_batch),
        ]
        print(f"[batch {batch_no}] capture {len(batch)}")
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
        if proc.stdout:
            print(proc.stdout.strip())

        summary = out_batch / "summary.json"
        if not summary.exists():
            for it in batch:
                seq += 1
                rows.append(
                    {
                        "seq": seq,
                        "case_id": it["case_id"],
                        "title": it.get("title", ""),
                        "status": "failed",
                        "error": "batch_summary_missing",
                        "strict_pass": False,
                        "input_complete": False,
                        "plot_assets_in_doc": 0,
                        "plots_renderable": 0,
                        "plot_assets_failed": None,
                    }
                )
            continue

        bobj = json.loads(summary.read_text(encoding="utf-8"))
        for br in bobj.get("rows", []):
            if not isinstance(br, dict):
                continue
            cid = br.get("case_id")
            if not isinstance(cid, str) or not cid:
                continue
            seq += 1
            base: Dict[str, Any] = {
                "seq": seq,
                "case_id": cid,
                "title": br.get("title", ""),
                "status": br.get("status", "failed"),
                "strict_pass": False,
                "input_complete": False,
                "plot_assets_in_doc": 0,
                "plots_renderable": 0,
                "plot_assets_failed": None,
                "error": br.get("error"),
                "chart_evidence_html": "",
                "full_report_html": "",
            }

            html_name = br.get("html") or ""
            if isinstance(html_name, str) and html_name:
                base["chart_evidence_html"] = f"{(out_batch.relative_to(runs)).as_posix()}/items/{html_name}"

            idx = int(br.get("idx") or 0)
            prefix = f"case_{idx:03d}_{cid[:12]}"
            input_dir = out_batch / "input" / prefix
            input_complete = input_dir.exists() and any(x.is_file() for x in input_dir.iterdir())
            base["input_complete"] = bool(input_complete)
            base["input_dir"] = str(input_dir.relative_to(runs)) if input_dir.exists() else ""

            # hard stop if capture itself failed
            if br.get("status") != "ok":
                rows.append(base)
                continue

            # ensure no description
            up_st, uploads = http_get_json_retry(UP_API.format(case_id=cid), retries=2, timeout=12)
            if up_st != 200 or not isinstance(uploads, list):
                base["status"] = "failed"
                base["error"] = f"uploads_http_{up_st}"
                rows.append(base)
                continue
            names = [str(x.get("name", "")).strip().lower() for x in uploads if isinstance(x, dict)]
            has_desc = any(n == "description.txt" or n.endswith("/description.txt") for n in names)
            if has_desc:
                base["status"] = "failed"
                base["error"] = "has_description"
                rows.append(base)
                continue

            d_st, doc = http_get_json_retry(DOC_API.format(case_id=cid), retries=3, timeout=15)
            if d_st != 200 or not isinstance(doc, dict):
                base["status"] = "failed"
                base["error"] = f"document_http_{d_st}"
                rows.append(base)
                continue

            plot_assets = collect_plot_assets(doc, ab)
            base["plot_assets_in_doc"] = len(plot_assets)
            if not plot_assets:
                base["status"] = "failed"
                base["error"] = "no_plot_assets"
                rows.append(base)
                continue

            fig_cache = parse_assets_from_chart_html(runs / str(base["chart_evidence_html"]))
            local_before = len(fig_cache)
            asset_fail = 0
            remote_ok = 0
            for apath in plot_assets:
                key = Path(apath).name
                if key in fig_cache:
                    continue
                st, payload = fetch_asset(cid, apath, timeout_sec=16)
                if st == 200 and isinstance(payload, dict) and isinstance(payload.get("data"), list):
                    fig_cache[key] = payload
                    fig_cache[apath] = payload
                    remote_ok += 1
                else:
                    asset_fail += 1

            renderer = ab.DocRenderer(cid, fig_cache, translate_fn=None)
            body = renderer.render_doc_body(doc)
            body += (
                "<hr/><p class='muted'>回补报告："
                f"plot assets={len(plot_assets)} · renderable={len(renderer.fig_payload)} · asset_fail={asset_fail}</p>"
            )
            page = ab.page_shell(
                title=f"回补完整报告：{base['title']}",
                subtitle=f"来源: /view/{cid}",
                body_html=body,
                fig_payload=renderer.fig_payload,
            )
            rname = f"case_{seq:03d}_{cid[:12]}_backfill_full_report.html"
            rpath = run_dir / "full_reports" / rname
            rpath.write_text(page, encoding="utf-8")

            strict = bool(input_complete) and asset_fail == 0 and len(renderer.fig_payload) > 0
            base.update(
                {
                    "status": "ok" if strict else "failed",
                    "strict_pass": strict,
                    "error": "" if strict else ("asset_incomplete" if asset_fail > 0 else "render_empty"),
                    "plots_renderable": len(renderer.fig_payload),
                    "plot_assets_failed": asset_fail,
                    "fig_from_local": local_before,
                    "fig_from_remote": remote_ok,
                    "full_report_html": f"full_reports/{rname}",
                }
            )
            rows.append(base)

    # summary
    strict_rows = [r for r in rows if r.get("strict_pass") is True]
    fail_rows = [r for r in rows if r.get("strict_pass") is not True]
    reasons: Dict[str, int] = {}
    for r in fail_rows:
        e = str(r.get("error") or "failed")
        reasons[e] = reasons.get(e, 0) + 1
    report = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "input_count": len(cases),
        "processed_count": len(rows),
        "strict_pass_count": len(strict_rows),
        "failure_reasons": reasons,
        "rows": rows,
        "rows_strict": strict_rows,
    }
    (run_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    build_index(run_dir, rows)
    print(f"[done] run_dir={run_dir}")
    print(f"[done] strict_pass={len(strict_rows)} / {len(rows)}")


if __name__ == "__main__":
    main()

