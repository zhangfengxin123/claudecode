#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import html
import importlib.util
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

HEX_RE = re.compile(r"^[a-f0-9]{32,128}$")

UP_API = "https://pardusai.org/v1/api/public/project/{cid}/uploads"
DOC_API = "https://pardusai.org/v1/api/public/project/{cid}/document"
FILE_API = "https://pardusai.org/v1/api/public/project/{cid}/files/{name}"
ASSET_API = "https://pardusai.org/v1/api/public/project/{cid}/assets/{name}"


def utc_tag() -> str:
    return dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def h(x: Any) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def make_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def walk_json(x: Any):
    if isinstance(x, dict):
        yield x
        for v in x.values():
            yield from walk_json(v)
    elif isinstance(x, list):
        for i in x:
            yield from walk_json(i)


def collect_strict_ids(runs_dir: Path) -> set[str]:
    ids: set[str] = set()
    for p in runs_dir.glob("**/summary.json"):
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        for k in ("actual_strict_case_ids", "strict_case_ids"):
            arr = obj.get(k)
            if isinstance(arr, list):
                for v in arr:
                    if isinstance(v, str) and HEX_RE.match(v):
                        ids.add(v)
        arr = obj.get("rows_strict")
        if isinstance(arr, list):
            for r in arr:
                if isinstance(r, dict):
                    cid = r.get("case_id")
                    if isinstance(cid, str) and HEX_RE.match(cid):
                        ids.add(cid)
        arr = obj.get("rows_all")
        if isinstance(arr, list):
            for r in arr:
                if isinstance(r, dict) and r.get("strict_pass") is True:
                    cid = r.get("case_id")
                    if isinstance(cid, str) and HEX_RE.match(cid):
                        ids.add(cid)
    return ids


def get_json(url: str, ctx: ssl.SSLContext, timeout: int = 12, retries: int = 4) -> Tuple[int, Any]:
    last = (0, None)
    for _ in range(max(1, retries)):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=max(3, int(timeout)), context=ctx) as resp:
                st = int(resp.getcode() or 0)
                body = resp.read()
            if st != 200:
                last = (st, None)
            else:
                try:
                    return st, json.loads(body.decode("utf-8", errors="replace"))
                except Exception:
                    last = (st, None)
        except urllib.error.HTTPError as e:
            last = (int(e.code or 0), None)
        except Exception:
            last = (0, None)
        time.sleep(0.25)
    return last


def download_file(case_id: str, upload_name: str, out_path: Path, ctx: ssl.SSLContext, timeout: int) -> Tuple[bool, str]:
    quoted = urllib.parse.quote(upload_name, safe="")
    req = urllib.request.Request(FILE_API.format(cid=case_id, name=quoted), headers={"User-Agent": "Mozilla/5.0"})
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=max(5, int(timeout)), context=ctx) as resp:
                body = resp.read()
            if not body:
                time.sleep(0.25)
                continue
            out_path.write_bytes(body)
            low = upload_name.lower()
            txt = body[:2048].decode("utf-8", errors="ignore").lower()
            if not (low.endswith(".html") or low.endswith(".htm")):
                if "project not found" in txt:
                    return False, "html_fallback"
                if ("<!doctype html" in txt or "<html" in txt) and len(body) < 10000:
                    return False, "html_fallback"
            return True, ""
        except urllib.error.HTTPError as e:
            return False, f"http_{int(e.code or 0)}"
        except Exception:
            time.sleep(0.3)
    return False, "download_error"


def build_index(run_dir: Path, rows: List[Dict[str, Any]]) -> None:
    ok_rows = [r for r in rows if r.get("status") == "ok"]
    strict_rows = [r for r in rows if r.get("strict_pass")]

    def row_html(r: Dict[str, Any]) -> str:
        rep = r.get("full_report_html")
        rep_cell = "-"
        if isinstance(rep, str) and rep:
            rep_cell = f"<a href='{h(rep)}' target='_blank' rel='noopener noreferrer'>open</a>"
        return (
            "<tr>"
            f"<td>{h(r.get('seq'))}</td>"
            f"<td><code>{h(str(r.get('case_id',''))[:12])}</code></td>"
            f"<td>{h(r.get('has_description'))}</td>"
            f"<td>{h(r.get('status'))}</td>"
            f"<td>{h(r.get('strict_pass'))}</td>"
            f"<td>{h(r.get('input_complete'))}</td>"
            f"<td>{h(r.get('plot_assets_in_doc'))}</td>"
            f"<td>{h(r.get('plots_renderable'))}</td>"
            f"<td>{h(r.get('plot_assets_failed'))}</td>"
            f"<td>{h(r.get('error',''))}</td>"
            f"<td>{rep_cell}</td>"
            f"<td><a href='https://pardusai.org/view/{h(r.get('case_id',''))}' target='_blank' rel='noopener noreferrer'>view</a></td>"
            "</tr>"
        )

    page = f"""<!doctype html>
<html lang='zh-CN'><head><meta charset='utf-8'/><meta name='viewport' content='width=device-width,initial-scale=1'/>
<title>Retry Recover Capture</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;margin:20px;color:#111827}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{border:1px solid #e5e7eb;padding:6px 8px;text-align:left;vertical-align:top}}
th{{background:#f3f4f6;position:sticky;top:0}}
code{{background:#f3f4f6;padding:2px 6px;border-radius:6px}}
.wrap{{overflow:auto;max-height:calc(100vh - 220px);border:1px solid #e5e7eb;border-radius:8px}}
</style></head><body>
<h1>Retry Recover Capture</h1>
<p>rows=<code>{len(rows)}</code> · ok=<code>{len(ok_rows)}</code> · strict=<code>{len(strict_rows)}</code></p>
<div class='wrap'>
<table><thead><tr>
<th>#</th><th>case</th><th>desc</th><th>status</th><th>strict</th><th>input</th><th>assets</th><th>renderable</th><th>asset_fail</th><th>error</th><th>report</th><th>pardus</th>
</tr></thead><tbody>{''.join(row_html(r) for r in rows)}</tbody></table>
</div></body></html>"""
    (run_dir / "index.html").write_text(page, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Recapture recoverable uploads_http_0 cases")
    ap.add_argument("--probe-json", required=True)
    ap.add_argument("--max-cases", type=int, default=40)
    ap.add_argument("--download-timeout-sec", type=int, default=25)
    ap.add_argument("--asset-timeout-sec", type=int, default=14)
    ap.add_argument("--out-dir", default="")
    args = ap.parse_args()

    repo_root = Path.cwd()
    runs_dir = repo_root / "Pardus/.codex_session/runs"
    run_dir = Path(args.out_dir) if args.out_dir else (runs_dir / f"retry_recover_capture_{utc_tag()}")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "input").mkdir(exist_ok=True)
    (run_dir / "full_reports").mkdir(exist_ok=True)

    ctx = make_ctx()

    ab_full_py = repo_root / "Pardus/.codex_session/scripts/build_ab_full_report_pages.py"
    spec = importlib.util.spec_from_file_location("ab_full", ab_full_py)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {ab_full_py}")
    ab = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ab)

    strict_existing = collect_strict_ids(runs_dir)
    probe = json.loads(Path(args.probe_json).read_text(encoding="utf-8"))
    raw = probe.get("recoverable_case_ids", [])
    cids = [x for x in raw if isinstance(x, str) and HEX_RE.match(x) and x not in strict_existing]
    cids = cids[: max(1, int(args.max_cases))]

    print(f"[info] run_dir={run_dir}")
    print(f"[info] recoverable_all={len(raw)} strict_existing={len(strict_existing)} picked={len(cids)}")

    rows: List[Dict[str, Any]] = []
    strict_rows: List[Dict[str, Any]] = []
    strict_ids: set[str] = set()

    for i, cid in enumerate(cids, 1):
        row: Dict[str, Any] = {
            "seq": i,
            "case_id": cid,
            "status": "failed",
            "strict_pass": False,
            "input_complete": False,
            "has_description": None,
            "plot_assets_in_doc": 0,
            "plots_renderable": 0,
            "plot_assets_failed": None,
            "error": "",
            "full_report_html": "",
            "input_dir": "",
        }

        st, up_payload = get_json(UP_API.format(cid=cid), ctx=ctx, timeout=12, retries=5)
        if st != 200 or not isinstance(up_payload, list):
            row["error"] = f"uploads_http_{st}"
            rows.append(row)
            continue
        uploads = [u for u in up_payload if isinstance(u, dict) and isinstance(u.get("name"), str)]
        if not uploads:
            row["error"] = "uploads_empty"
            rows.append(row)
            continue

        lowers = [u["name"].strip().lower() for u in uploads]
        row["has_description"] = any(x == "description.txt" or x.endswith("/description.txt") for x in lowers)

        input_dir = run_dir / "input" / f"case_{i:03d}_{cid[:12]}"
        input_dir.mkdir(parents=True, exist_ok=True)
        row["input_dir"] = str(input_dir.relative_to(runs_dir))

        seen_names: Dict[str, int] = {}
        input_ok = True
        manifest = []
        for up in uploads:
            name = up["name"]
            k = name.lower()
            n = seen_names.get(k, 0)
            seen_names[k] = n + 1
            p = Path(name.replace("/", "_").replace("\\", "_"))
            local = input_dir / (f"{p.stem}__dup{n}{p.suffix}" if n else p.name)
            ok, err = download_file(cid, name, local, ctx=ctx, timeout=args.download_timeout_sec)
            manifest.append(
                {
                    "name": name,
                    "local": local.name,
                    "ok": ok,
                    "error": err,
                    "size": local.stat().st_size if local.exists() else 0,
                }
            )
            if not ok:
                input_ok = False
        (input_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        row["input_complete"] = input_ok
        if not input_ok:
            row["error"] = "input_incomplete"
            rows.append(row)
            continue

        st, doc = get_json(DOC_API.format(cid=cid), ctx=ctx, timeout=14, retries=5)
        if st != 200 or not isinstance(doc, dict):
            row["error"] = f"document_http_{st}"
            rows.append(row)
            continue

        plot_assets: List[str] = []
        seen_assets = set()

        def rec(x: Any) -> None:
            if isinstance(x, dict):
                if str(x.get("type", "")).lower() == "plot":
                    raw = x.get("htmlPath") or x.get("code") or ""
                    if isinstance(raw, str):
                        p2 = ab.clean_rel_path(raw)
                        if p2.endswith(".json") and p2 not in seen_assets:
                            seen_assets.add(p2)
                            plot_assets.append(p2)
                for v in x.values():
                    rec(v)
            elif isinstance(x, list):
                for it in x:
                    rec(it)

        rec(doc.get("document", {}))
        row["plot_assets_in_doc"] = len(plot_assets)
        if not plot_assets:
            row["error"] = "no_plot_assets"
            rows.append(row)
            continue

        fig_cache: Dict[str, Dict[str, Any]] = {}
        asset_fail = 0
        for apath in plot_assets:
            quoted = urllib.parse.quote(apath, safe="/._-")
            st, payload = get_json(ASSET_API.format(cid=cid, name=quoted), ctx=ctx, timeout=args.asset_timeout_sec, retries=4)
            if st == 200 and isinstance(payload, dict) and isinstance(payload.get("data"), list):
                fig_cache[apath] = payload
                fig_cache[Path(apath).name] = payload
            else:
                asset_fail += 1

        renderer = ab.DocRenderer(cid, fig_cache, translate_fn=None)
        body = renderer.render_doc_body(doc)
        body += (
            "<hr/><p class='muted'>retry recover strict: "
            f"uploads={len(uploads)} · assets={len(plot_assets)} · renderable={len(renderer.fig_payload)} · asset_fail={asset_fail}"
            "</p>"
        )
        page = ab.page_shell(
            title=f"完整报告：retry_{cid[:12]}",
            subtitle=f"来源: /view/{cid} ｜ retry recover capture",
            body_html=body,
            fig_payload=renderer.fig_payload,
        )
        report_name = f"case_{i:03d}_{cid[:12]}_full_report.html"
        (run_dir / "full_reports" / report_name).write_text(page, encoding="utf-8")
        row["full_report_html"] = f"full_reports/{report_name}"
        row["plot_assets_failed"] = asset_fail
        row["plots_renderable"] = len(renderer.fig_payload)

        strict = input_ok and asset_fail == 0 and len(renderer.fig_payload) > 0
        row["strict_pass"] = strict
        row["status"] = "ok" if strict else "failed"
        if not strict and not row["error"]:
            row["error"] = "asset_incomplete" if asset_fail > 0 else "render_empty"

        rows.append(row)
        if strict and cid not in strict_ids:
            strict_ids.add(cid)
            strict_rows.append(row)

        if i % 10 == 0:
            print(f"[progress] processed={i}/{len(cids)} strict={len(strict_rows)}")

    reasons: Dict[str, int] = {}
    for r in rows:
        if r.get("strict_pass"):
            continue
        k = str(r.get("error") or "failed")
        reasons[k] = reasons.get(k, 0) + 1

    summary = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source": str(args.probe_json),
        "target": len(cids),
        "source_cases": len(cids),
        "filtered_cases": len(cids),
        "picked_cases": len(cids),
        "actual_strict_count": len(strict_rows),
        "actual_strict_case_ids": sorted(strict_ids),
        "strict_with_description": len([r for r in strict_rows if r.get("has_description") is True]),
        "strict_without_description": len([r for r in strict_rows if r.get("has_description") is False]),
        "failure_reasons": reasons,
        "rows_all": rows,
        "rows_strict": strict_rows,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "strict_rows.json").write_text(json.dumps({"rows": strict_rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    build_index(run_dir, rows)

    print(f"[done] run_dir={run_dir}")
    print(f"[done] strict_count={len(strict_rows)} target={len(cids)}")
    print(f"[done] failure_reasons={reasons}")


if __name__ == "__main__":
    main()

