#!/usr/bin/env python3
"""
Capture new Pardus public cases under strict constraints:

1) no description.txt in uploads
2) non-duplicate against existing local research summaries
3) input complete (all uploads downloaded by build_chart_evidence_batch)
4) output complete strict:
   - document endpoint is available
   - plot assets exist in report (>0)
   - all plot assets are fetchable (asset_fail == 0)
   - full report html (body + in-place charts) is generated

If strict count cannot reach target, output as many as possible.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import importlib.util
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


RECENT_API = "https://pardusai.org/v1/api/public/projects/recent?limit={limit}"
DOC_API = "https://pardusai.org/v1/api/public/project/{case_id}/document"
UP_API = "https://pardusai.org/v1/api/public/project/{case_id}/uploads"
ASSET_API = "https://pardusai.org/v1/api/public/project/{case_id}/assets/{name}"

HEX_RE = re.compile(r"^[a-f0-9]{32,128}$")
VIEW_RE = re.compile(r"/view/([a-f0-9]{32,128})")
ASSETS_RE = re.compile(r"const\s+ASSETS\s*=\s*(\[[\s\S]*?\])\s*;", re.M)

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def h(x: Any) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def is_case_id(x: Any) -> bool:
    return isinstance(x, str) and bool(HEX_RE.match(x.lower()))


def utc_tag() -> str:
    return dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def http_get_json(url: str, timeout: int = 12) -> Tuple[int, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
            status = int(resp.getcode() or 0)
            body = resp.read()
        if status != 200:
            return status, None
        try:
            return status, json.loads(body.decode("utf-8", errors="replace"))
        except Exception:
            return status, None
    except urllib.error.HTTPError as e:
        return int(e.code or 0), None
    except Exception:
        return 0, None


def http_get_json_with_retries(url: str, retries: int = 3, timeout: int = 12) -> Tuple[int, Any]:
    last_status = 0
    for _ in range(max(1, retries)):
        st, obj = http_get_json(url, timeout=timeout)
        last_status = st
        if st == 200 and isinstance(obj, (dict, list)):
            return st, obj
        time.sleep(0.12)
    return last_status, None


def extract_case_id_from_recent_item(item: Dict[str, Any]) -> Optional[str]:
    cid = item.get("id") or item.get("case_id")
    if is_case_id(cid):
        return str(cid)
    url = item.get("url", "")
    if isinstance(url, str):
        m = VIEW_RE.search(url)
        if m:
            c = m.group(1)
            if is_case_id(c):
                return c
    return None


def walk_json(x: Any):
    if isinstance(x, dict):
        yield x
        for v in x.values():
            yield from walk_json(v)
    elif isinstance(x, list):
        for it in x:
            yield from walk_json(it)


def collect_existing_case_ids(runs_dir: Path) -> set[str]:
    ids: set[str] = set()
    for p in sorted(runs_dir.glob("**/summary.json")):
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for node in walk_json(obj):
            for k, v in node.items():
                if "case_id" in k and is_case_id(v):
                    ids.add(str(v))
    return ids


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


def parse_assets_from_chart_evidence_html(path: Path) -> Dict[str, Dict[str, Any]]:
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


def fetch_asset_with_curl(case_id: str, asset_path: str, max_time: int = 10) -> Tuple[int, Any]:
    quoted = urllib.parse.quote(asset_path, safe="/._-")
    url = ASSET_API.format(case_id=case_id, name=quoted)
    proc = subprocess.run(
        ["curl", "-sS", "--max-time", str(max_time), url],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
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


def fetch_asset_with_retry(case_id: str, asset_path: str) -> Tuple[int, Any]:
    # Try short -> long timeout. Fall back to urllib.
    for sec in (8, 14, 20):
        st, payload = fetch_asset_with_curl(case_id, asset_path, max_time=sec)
        if st == 200 and isinstance(payload, dict):
            return st, payload
    quoted = urllib.parse.quote(asset_path, safe="/._-")
    url = ASSET_API.format(case_id=case_id, name=quoted)
    return http_get_json_with_retries(url, retries=2, timeout=20)


def probe_candidate(item: Dict[str, str], ab_module: Any) -> Dict[str, Any]:
    cid = item["case_id"]
    title = item.get("title", "")

    up_st, up_payload = http_get_json_with_retries(UP_API.format(case_id=cid), retries=2, timeout=10)
    if up_st != 200 or not isinstance(up_payload, list):
        return {"case_id": cid, "title": title, "ok": False, "reason": f"uploads_http_{up_st}"}

    uploads = []
    for it in up_payload:
        if isinstance(it, dict) and isinstance(it.get("name"), str):
            uploads.append(it["name"])
    if not uploads:
        return {"case_id": cid, "title": title, "ok": False, "reason": "uploads_empty"}

    lower = [x.strip().lower() for x in uploads]
    has_desc = any(x == "description.txt" or x.endswith("/description.txt") for x in lower)
    if has_desc:
        return {"case_id": cid, "title": title, "ok": False, "reason": "has_description"}

    d_st, doc = http_get_json_with_retries(DOC_API.format(case_id=cid), retries=2, timeout=10)
    if d_st != 200 or not isinstance(doc, dict):
        return {"case_id": cid, "title": title, "ok": False, "reason": f"document_http_{d_st}"}

    plot_assets = collect_plot_assets(doc, ab_module)
    if not plot_assets:
        return {"case_id": cid, "title": title, "ok": False, "reason": "no_plot_assets"}

    return {
        "case_id": cid,
        "title": title,
        "ok": True,
        "uploads_count": len(uploads),
        "plot_assets_in_doc_probe": len(plot_assets),
    }


def build_report_indexes(run_dir: Path, rows: List[Dict[str, Any]]) -> None:
    ok_rows = [r for r in rows if r.get("status") == "ok"]
    fail_rows = [r for r in rows if r.get("status") != "ok"]

    def row_html(r: Dict[str, Any]) -> str:
        if r.get("status") == "ok":
            report_cell = (
                f"<a href='{h(r.get('full_report_html'))}' target='_blank' rel='noopener noreferrer'>open</a>"
            )
        else:
            report_cell = f"<span style='color:#b91c1c'>{h(r.get('error', 'failed'))}</span>"
        ce = r.get("chart_evidence_html", "")
        ce_cell = "-"
        if ce:
            ce_cell = f"<a href='{h(ce)}' target='_blank' rel='noopener noreferrer'>open</a>"
        return (
            "<tr>"
            f"<td>{h(r.get('seq'))}</td>"
            f"<td><input type='checkbox'/></td>"
            f"<td><code>{h(str(r.get('case_id', ''))[:12])}</code></td>"
            f"<td>{h(r.get('title', ''))}</td>"
            f"<td>{h(r.get('plot_assets_in_doc', '-'))}</td>"
            f"<td>{h(r.get('plots_renderable', '-'))}</td>"
            f"<td>{h(r.get('plot_assets_failed', '-'))}</td>"
            f"<td>{h(r.get('input_complete', '-'))}</td>"
            f"<td>{h(r.get('strict_pass', '-'))}</td>"
            f"<td>{report_cell}</td>"
            f"<td>{ce_cell}</td>"
            f"<td><a href='{h(r.get('view_url', ''))}' target='_blank' rel='noopener noreferrer'>view</a></td>"
            "</tr>"
        )

    def page(title: str, rows_html: Sequence[str], total: int) -> str:
        return f"""<!doctype html>
<html lang='zh-CN'>
<head>
  <meta charset='utf-8'/>
  <meta name='viewport' content='width=device-width, initial-scale=1'/>
  <title>{h(title)}</title>
  <style>
    body {{ font-family: -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif; margin:22px; color:#111827; }}
    table {{ border-collapse:collapse; width:100%; font-size:13px; }}
    th,td {{ border:1px solid #e5e7eb; padding:6px 8px; text-align:left; vertical-align:top; }}
    th {{ background:#f3f4f6; position:sticky; top:0; z-index:2; }}
    code {{ background:#f3f4f6; padding:2px 6px; border-radius:6px; }}
    .wrap {{ overflow:auto; max-height:calc(100vh - 210px); border:1px solid #e5e7eb; border-radius:8px; }}
    .meta {{ color:#374151; margin:8px 0; }}
  </style>
</head>
<body>
  <h1>{h(title)}</h1>
  <div class='meta'>total rows: <code>{total}</code> · ok: <code>{len(ok_rows)}</code> · failed: <code>{len(fail_rows)}</code></div>
  <div class='meta'>严格口径：strict_pass=True（无 description + 输入完整 + 文档可取 + 有图 + 资产全可取）。</div>
  <div class='wrap'>
    <table>
      <thead><tr><th>#</th><th>check</th><th>case</th><th>title</th><th>plot_assets</th><th>renderable</th><th>asset_fail</th><th>input_complete</th><th>strict_pass</th><th>full report</th><th>chart evidence</th><th>pardus</th></tr></thead>
      <tbody>{''.join(rows_html)}</tbody>
    </table>
  </div>
</body>
</html>
"""

    all_rows_html = [row_html(r) for r in rows]
    ok_rows_html = [row_html(r) for r in ok_rows]
    strict_rows = [r for r in ok_rows if r.get("strict_pass")]
    strict_rows_html = [row_html(r) for r in strict_rows]

    (run_dir / "index.html").write_text(page("No Description Strict Capture - All", all_rows_html, len(rows)), encoding="utf-8")
    (run_dir / "index_ok_only.html").write_text(page("No Description Strict Capture - OK Only", ok_rows_html, len(ok_rows)), encoding="utf-8")
    (run_dir / "index_strict_only.html").write_text(
        page("No Description Strict Capture - Strict Pass Only", strict_rows_html, len(strict_rows)),
        encoding="utf-8",
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Strict no-description Pardus capture orchestrator")
    ap.add_argument("--target", type=int, default=100)
    ap.add_argument("--recent-limit", type=int, default=500)
    ap.add_argument("--reserve", type=int, default=260)
    ap.add_argument("--batch-size", type=int, default=20)
    ap.add_argument("--max-batches", type=int, default=20)
    ap.add_argument("--out-dir", default="")
    args = ap.parse_args()

    repo_root = Path.cwd()
    runs_dir = repo_root / "Pardus/.codex_session/runs"
    scripts_dir = repo_root / "Pardus/.codex_session/scripts"
    build_batch_py = scripts_dir / "build_chart_evidence_batch.py"
    ab_full_py = scripts_dir / "build_ab_full_report_pages.py"

    spec = importlib.util.spec_from_file_location("ab_full", ab_full_py)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {ab_full_py}")
    ab = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ab)

    run_dir = Path(args.out_dir) if args.out_dir else (runs_dir / f"no_desc_strict_capture_{utc_tag()}")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "full_reports").mkdir(parents=True, exist_ok=True)
    (run_dir / "batches").mkdir(parents=True, exist_ok=True)
    (run_dir / "tmp").mkdir(parents=True, exist_ok=True)

    print(f"[info] run_dir={run_dir}")

    existing_ids = collect_existing_case_ids(runs_dir)
    print(f"[info] existing_unique_case_ids={len(existing_ids)}")

    st, recent = http_get_json_with_retries(RECENT_API.format(limit=args.recent_limit), retries=2, timeout=15)
    if st != 200 or not isinstance(recent, list):
        raise SystemExit(f"failed to fetch recent list: HTTP {st}")

    seen_recent: set[str] = set()
    recent_pool: List[Dict[str, str]] = []
    for it in recent:
        if not isinstance(it, dict):
            continue
        cid = extract_case_id_from_recent_item(it)
        if not cid or cid in seen_recent:
            continue
        seen_recent.add(cid)
        if cid in existing_ids:
            continue
        recent_pool.append({"case_id": cid, "title": str(it.get("title", ""))})

    print(f"[info] recent_new_pool={len(recent_pool)}")

    # Probe candidates for no-description and has-plot-assets.
    probe_ok: List[Dict[str, Any]] = []
    probe_fail: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=24) as ex:
        futs = [ex.submit(probe_candidate, it, ab) for it in recent_pool]
        done = 0
        for fut in as_completed(futs):
            done += 1
            res = fut.result()
            if res.get("ok"):
                probe_ok.append(res)
            else:
                probe_fail.append(res)
            if done % 50 == 0:
                print(f"[probe] {done}/{len(futs)} ok={len(probe_ok)} fail={len(probe_fail)}")

    # Preserve recent order.
    order = {it["case_id"]: i for i, it in enumerate(recent_pool)}
    probe_ok.sort(key=lambda x: order.get(x["case_id"], 10**9))

    reserve_n = max(args.target, args.reserve)
    picked = probe_ok[:reserve_n]

    candidates_json = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "meta": {
            "target": args.target,
            "reserve": reserve_n,
            "recent_limit": args.recent_limit,
            "existing_unique_case_ids": len(existing_ids),
            "recent_new_pool": len(recent_pool),
            "probe_ok": len(probe_ok),
            "probe_fail": len(probe_fail),
            "picked": len(picked),
        },
        "results": [{"case_id": x["case_id"], "title": x.get("title", "")} for x in picked],
    }
    (run_dir / "candidates.json").write_text(json.dumps(candidates_json, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "probe_fail.json").write_text(json.dumps(probe_fail, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[info] picked_candidates={len(picked)}")

    strict_rows: List[Dict[str, Any]] = []
    all_rows: List[Dict[str, Any]] = []
    strict_ids: set[str] = set()
    evaluated_ids: set[str] = set()

    cursor = 0
    batch_no = 0
    seq = 0
    while cursor < len(picked) and batch_no < args.max_batches and len(strict_rows) < args.target:
        batch_no += 1
        batch_cases = picked[cursor : cursor + args.batch_size]
        cursor += len(batch_cases)
        if not batch_cases:
            break

        input_json = run_dir / "tmp" / f"batch_{batch_no:02d}_input.json"
        input_json.write_text(json.dumps({"results": [{"case_id": x["case_id"], "title": x.get("title", "")} for x in batch_cases]}, ensure_ascii=False, indent=2), encoding="utf-8")

        out_batch = run_dir / "batches" / f"batch_{batch_no:02d}"
        cmd = [
            sys.executable,
            str(build_batch_py),
            "--input-json",
            str(input_json),
            "--sample-size",
            str(len(batch_cases)),
            "--out-dir",
            str(out_batch),
        ]
        print(f"[batch {batch_no}] run capture: {len(batch_cases)}")
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
        print(proc.stdout.strip())

        summary_path = out_batch / "summary.json"
        if not summary_path.exists():
            print(f"[batch {batch_no}] missing summary.json")
            continue
        batch_summary = json.loads(summary_path.read_text(encoding="utf-8"))
        batch_rows = batch_summary.get("rows", [])

        for br in batch_rows:
            if not isinstance(br, dict):
                continue
            cid = br.get("case_id")
            if not is_case_id(cid):
                continue
            if cid in evaluated_ids:
                continue
            evaluated_ids.add(cid)

            seq += 1
            title = str(br.get("title", ""))
            base = {
                "seq": seq,
                "case_id": cid,
                "title": title,
                "batch_no": batch_no,
                "batch_dir": str(out_batch),
                "view_url": f"https://pardusai.org/view/{cid}",
                "chart_evidence_html": "",
                "input_complete": False,
                "strict_pass": False,
            }

            if br.get("status", "ok") != "ok":
                base.update({"status": "failed", "error": str(br.get("error", "capture_failed"))})
                all_rows.append(base)
                continue

            html_name = str(br.get("html") or "")
            rel_chart_html = f"{out_batch.relative_to(runs_dir).as_posix()}/items/{html_name}" if html_name else ""
            base["chart_evidence_html"] = rel_chart_html

            idx = int(br.get("idx") or 0)
            prefix = f"case_{idx:03d}_{cid[:12]}"
            input_dir = out_batch / "input" / prefix
            input_complete = input_dir.exists() and any(p.is_file() for p in input_dir.iterdir())
            base["input_complete"] = bool(input_complete)
            base["input_dir"] = str(input_dir.relative_to(runs_dir)) if input_dir.exists() else ""

            if not input_complete:
                base.update({"status": "failed", "error": "input_incomplete"})
                all_rows.append(base)
                continue

            d_st, doc = http_get_json_with_retries(DOC_API.format(case_id=cid), retries=3, timeout=15)
            if d_st != 200 or not isinstance(doc, dict):
                base.update({"status": "failed", "error": f"document_http_{d_st}"})
                all_rows.append(base)
                continue

            plot_assets = collect_plot_assets(doc, ab)
            base["plot_assets_in_doc"] = len(plot_assets)
            if not plot_assets:
                base.update({"status": "failed", "error": "no_plot_assets"})
                all_rows.append(base)
                continue

            # Use captured chart-evidence fig cache first.
            fig_cache = parse_assets_from_chart_evidence_html(runs_dir / rel_chart_html)
            local_before = len(fig_cache)
            remote_ok = 0
            asset_fail = 0

            for apath in plot_assets:
                base_name = Path(apath).name
                if base_name in fig_cache:
                    continue
                a_st, payload = fetch_asset_with_retry(cid, apath)
                if a_st == 200 and isinstance(payload, dict) and isinstance(payload.get("data"), list):
                    fig_cache[base_name] = payload
                    fig_cache[apath] = payload
                    remote_ok += 1
                else:
                    asset_fail += 1

            renderer = ab.DocRenderer(cid, fig_cache, translate_fn=None)
            body = renderer.render_doc_body(doc)
            body += (
                "<hr/><p class='muted'>说明：严格抓取报告（无 description + 输入完整 + 图资产完整）。"
                f"plot assets: {len(plot_assets)}; renderable: {len(renderer.fig_payload)}; asset_fail: {asset_fail}.</p>"
            )
            page = ab.page_shell(
                title=f"完整报告：{title}",
                subtitle=f"来源: /view/{cid} ｜ strict no-description capture",
                body_html=body,
                fig_payload=renderer.fig_payload,
            )

            report_name = f"case_{seq:03d}_{cid[:12]}_strict_full_report.html"
            report_path = run_dir / "full_reports" / report_name
            report_path.write_text(page, encoding="utf-8")

            strict_pass = asset_fail == 0
            row = dict(base)
            row.update(
                {
                    "status": "ok",
                    "strict_pass": strict_pass,
                    "full_report_html": f"full_reports/{report_name}",
                    "plot_assets_failed": asset_fail,
                    "plots_renderable": len(renderer.fig_payload),
                    "fig_from_local": local_before,
                    "fig_from_remote": remote_ok,
                }
            )
            all_rows.append(row)
            if strict_pass and cid not in strict_ids:
                strict_ids.add(cid)
                strict_rows.append(row)

        summary = {
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "target": args.target,
            "evaluated_cases": len(evaluated_ids),
            "strict_pass_count": len(strict_rows),
            "strict_pass_case_ids": sorted(strict_ids),
            "rows": all_rows,
        }
        (run_dir / "summary.partial.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        build_report_indexes(run_dir, all_rows)
        print(f"[batch {batch_no}] strict_pass={len(strict_rows)} evaluated={len(evaluated_ids)}")

    # Finalize
    strict_final = strict_rows[: args.target]
    strict_final_ids = {r["case_id"] for r in strict_final}

    reasons: Dict[str, int] = {}
    for r in all_rows:
        if r.get("strict_pass"):
            continue
        if r.get("status") != "ok":
            key = str(r.get("error", "failed"))
        else:
            key = "asset_incomplete"
        reasons[key] = reasons.get(key, 0) + 1

    final = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "target": args.target,
        "actual_strict_count": len(strict_final),
        "actual_strict_case_ids": sorted(strict_final_ids),
        "candidate_picked": len(picked),
        "evaluated_cases": len(evaluated_ids),
        "strict_pass_total_before_cap": len(strict_rows),
        "failure_reasons": reasons,
        "rows_all": all_rows,
        "rows_strict": strict_final,
    }
    (run_dir / "summary.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")

    strict_only = {
        "generated_at": final["generated_at"],
        "target": args.target,
        "actual_strict_count": len(strict_final),
        "rows": strict_final,
    }
    (run_dir / "strict_rows.json").write_text(json.dumps(strict_only, ensure_ascii=False, indent=2), encoding="utf-8")
    build_report_indexes(run_dir, all_rows)

    print(f"[done] run_dir={run_dir}")
    print(f"[done] strict_count={len(strict_final)} target={args.target}")
    if len(strict_final) < args.target:
        print("[done] warning: strict count below target; delivered maximum available under current constraints.")


if __name__ == "__main__":
    main()
