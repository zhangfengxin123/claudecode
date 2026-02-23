#!/usr/bin/env python3
"""
Direct strict capture for Pardus public cases (with or without description).

Compared with batch pipeline, this script captures case-by-case with hard
timeouts on every download to avoid whole-batch stalls.
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
import socket
import time
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

UP_API = "https://pardusai.org/v1/api/public/project/{case_id}/uploads"
DOC_API = "https://pardusai.org/v1/api/public/project/{case_id}/document"
FILE_API = "https://pardusai.org/v1/api/public/project/{case_id}/files/{name}"
ASSET_API = "https://pardusai.org/v1/api/public/project/{case_id}/assets/{name}"
RECENT_API = "https://pardusai.org/v1/api/public/projects/recent?limit=1"

HEX_RE = re.compile(r"^[a-f0-9]{32,128}$")
VIEW_RE = re.compile(r"/view/([a-f0-9]{32,128})")
ASSETS_RE = re.compile(r"const\s+ASSETS\s*=\s*(\[[\s\S]*?\])\s*;", re.M)

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE
DEBUG_HTTP = (os.getenv("PARDUS_DEBUG_HTTP") or "").strip() == "1"
PARDUS_HOST = "pardusai.org"
PARDUS_IP_HINT = (os.getenv("PARDUS_IP_HINT") or "").strip()


def _resolve_pardus_ip() -> str:
    if PARDUS_IP_HINT:
        return PARDUS_IP_HINT
    try:
        return socket.gethostbyname(PARDUS_HOST)
    except Exception:
        return ""


def _maybe_ip_fallback_url(url: str) -> Tuple[str, str]:
    split = urllib.parse.urlsplit(url)
    host = split.hostname or ""
    if host.lower() != PARDUS_HOST:
        return "", ""
    ip = _resolve_pardus_ip()
    if not ip:
        return "", ""
    netloc = ip
    if split.port:
        netloc = f"{ip}:{split.port}"
    alt = urllib.parse.urlunsplit((split.scheme, netloc, split.path, split.query, split.fragment))
    return alt, host


def _open_url_bytes(url: str, timeout: int, headers: Optional[Dict[str, str]] = None) -> Tuple[int, Optional[bytes]]:
    h = {"User-Agent": "Mozilla/5.0"}
    if headers:
        h.update(headers)

    def once(u: str, hdrs: Dict[str, str]) -> Tuple[int, Optional[bytes]]:
        req = urllib.request.Request(u, headers=hdrs)
        with urllib.request.urlopen(req, timeout=max(3, int(timeout)), context=SSL_CTX) as resp:
            st = int(resp.getcode() or 0)
            body = resp.read()
        return st, body

    try:
        return once(url, h)
    except urllib.error.HTTPError as e:
        return int(e.code or 0), None
    except urllib.error.URLError as e:
        alt_url, host = _maybe_ip_fallback_url(url)
        if alt_url:
            try:
                h2 = dict(h)
                h2["Host"] = host
                return once(alt_url, h2)
            except urllib.error.HTTPError as e2:
                return int(e2.code or 0), None
            except Exception as e2:
                if DEBUG_HTTP:
                    print(f"[open_url_fallback] error url={url[:120]} alt={alt_url[:120]} err={e2!r}")
                return 0, None
        if DEBUG_HTTP:
            print(f"[open_url] url_error url={url[:120]} err={e!r}")
        return 0, None
    except Exception as e:
        if DEBUG_HTTP:
            print(f"[open_url] generic_error url={url[:120]} err={e!r}")
        return 0, None


def utc_tag() -> str:
    return dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def h(x: Any) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def is_case_id(x: Any) -> bool:
    return isinstance(x, str) and bool(HEX_RE.match(x.lower()))


def extract_case_id_from_item(item: Dict[str, Any]) -> Optional[str]:
    cid = item.get("case_id") or item.get("id")
    if is_case_id(cid):
        return str(cid)
    url = item.get("url", "")
    if isinstance(url, str):
        m = VIEW_RE.search(url)
        if m and is_case_id(m.group(1)):
            return m.group(1)
    return None


def http_get_json(url: str, timeout: int = 20) -> Tuple[int, Any]:
    st, body = _open_url_bytes(url, timeout=timeout)
    if st != 200 or body is None:
        return st, None
    try:
        return st, json.loads(body.decode("utf-8", errors="replace"))
    except Exception as e:
        if DEBUG_HTTP:
            print(f"[http_get_json] json_decode_error url={url[:120]} err={e!r}")
        return st, None


def http_get_json_retry(url: str, retries: int = 3, timeout: int = 20) -> Tuple[int, Any]:
    last = 0
    for idx in range(max(1, retries)):
        st, payload = http_get_json(url, timeout=timeout)
        last = st
        if st == 200 and isinstance(payload, (dict, list)):
            return st, payload
        if idx < max(1, retries) - 1:
            time.sleep(0.2)
    return last, None


def wait_network_ready(max_wait_sec: int = 12) -> bool:
    deadline = time.time() + max(2, int(max_wait_sec))
    while time.time() < deadline:
        st, payload = http_get_json(RECENT_API, timeout=6)
        if st == 200 and isinstance(payload, list):
            return True
        time.sleep(0.6)
    return False


def walk_json(x: Any):
    if isinstance(x, dict):
        yield x
        for v in x.values():
            yield from walk_json(v)
    elif isinstance(x, list):
        for it in x:
            yield from walk_json(it)


def _collect_strict_ids_from_summary_obj(obj: Dict[str, Any]) -> set[str]:
    ids: set[str] = set()

    for k in ("actual_strict_case_ids", "strict_case_ids"):
        arr = obj.get(k)
        if isinstance(arr, list):
            for v in arr:
                if is_case_id(v):
                    ids.add(str(v))

    rows = obj.get("rows_strict")
    if isinstance(rows, list):
        for r in rows:
            if isinstance(r, dict) and is_case_id(r.get("case_id")):
                ids.add(str(r["case_id"]))

    rows_all = obj.get("rows_all")
    if isinstance(rows_all, list):
        for r in rows_all:
            if isinstance(r, dict) and r.get("strict_pass") is True and is_case_id(r.get("case_id")):
                ids.add(str(r["case_id"]))

    for key in ("captured", "results", "cases", "items", "records"):
        arr = obj.get(key)
        if not isinstance(arr, list):
            continue
        for r in arr:
            if not isinstance(r, dict):
                continue
            if "strict_pass" in r and r.get("strict_pass") is not True:
                continue
            cid = r.get("case_id") or r.get("id")
            if is_case_id(cid):
                ids.add(str(cid))

    return ids


def collect_existing_case_ids(runs_dir: Path, mode: str = "strict") -> set[str]:
    ids: set[str] = set()
    strict_mode = str(mode).strip().lower() != "all"
    for p in sorted(runs_dir.glob("**/summary.json")):
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        if strict_mode:
            ids |= _collect_strict_ids_from_summary_obj(obj)
        else:
            for node in walk_json(obj):
                for k, v in node.items():
                    if "case_id" in k and is_case_id(v):
                        ids.add(str(v))
    return ids


def load_cases_from_input_json(path: Path) -> List[Dict[str, str]]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    items: List[Any] = []
    if isinstance(obj, dict):
        for k in ("results", "projects", "items", "data"):
            v = obj.get(k)
            if isinstance(v, list):
                items = v
                break
    elif isinstance(obj, list):
        items = obj

    out: List[Dict[str, str]] = []
    seen: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        cid = extract_case_id_from_item(it)
        if not cid or cid in seen:
            continue
        seen.add(cid)
        out.append({"case_id": cid, "title": str(it.get("title", ""))})
    return out


def safe_name(name: str) -> str:
    return (name or "").replace("/", "_").replace("\\", "_").strip() or "unnamed"


def unique_path(base_dir: Path, name: str, seen: Dict[str, int]) -> Path:
    s = safe_name(name)
    k = s.lower()
    n = seen.get(k, 0)
    seen[k] = n + 1
    if n == 0:
        return base_dir / s
    stem = Path(s).stem
    suf = Path(s).suffix
    return base_dir / f"{stem}__dup{n}{suf}"


def looks_like_html_fallback(path: Path, original_name: str) -> bool:
    low = original_name.lower()
    # html input files are allowed to be html
    if low.endswith(".html") or low.endswith(".htm"):
        return False
    try:
        b = path.read_bytes()[:2048]
    except Exception:
        return True
    t = b.decode("utf-8", errors="ignore").lower()
    if "project not found" in t:
        return True
    if "<!doctype html" in t or "<html" in t:
        # a frequent fallback page is tiny (around 818 bytes)
        if path.stat().st_size < 10_000:
            return True
    return False


def download_file(case_id: str, upload_name: str, out_path: Path, timeout_sec: int) -> Tuple[bool, str]:
    quoted = urllib.parse.quote(upload_name, safe="")
    url = FILE_API.format(case_id=case_id, name=quoted)
    st, body = _open_url_bytes(url, timeout=max(4, int(timeout_sec)))
    if st != 200 or body is None:
        return False, f"http_{st}" if st else "url_error"
    if not body:
        return False, "empty_file"
    out_path.write_bytes(body)

    if looks_like_html_fallback(out_path, upload_name):
        return False, "html_fallback"
    return True, ""


def fetch_asset_with_retry(case_id: str, asset_path: str, timeout_sec: int = 20) -> Tuple[int, Any]:
    quoted = urllib.parse.quote(asset_path, safe="/._-")
    url = ASSET_API.format(case_id=case_id, name=quoted)
    for sec in (8, 14, timeout_sec):
        st, obj = http_get_json(url, timeout=max(4, int(sec)))
        if st == 200 and isinstance(obj, dict) and isinstance(obj.get("data"), list):
            return st, obj
    return 0, None


def collect_plot_assets(doc_payload: Dict[str, Any], ab_module: Any) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()

    def rec(x: Any) -> None:
        if isinstance(x, dict):
            t = str(x.get("type", "")).lower()
            if t == "plot":
                raw = x.get("htmlPath") or x.get("code") or ""
                if isinstance(raw, str):
                    p = ab_module.clean_rel_path(raw)
                    if p.endswith(".json") and p not in seen:
                        seen.add(p)
                        out.append(p)
            for v in x.values():
                rec(v)
        elif isinstance(x, list):
            for it in x:
                rec(it)

    rec(doc_payload.get("document", {}))
    return out


def build_index(run_dir: Path, rows: List[Dict[str, Any]]) -> None:
    ok_rows = [r for r in rows if r.get("status") == "ok"]
    fail_rows = [r for r in rows if r.get("status") != "ok"]
    strict_rows = [r for r in rows if r.get("strict_pass")]

    def row_html(r: Dict[str, Any]) -> str:
        report = r.get("full_report_html")
        report_cell = "-"
        if isinstance(report, str) and report:
            report_cell = f"<a href='{h(report)}' target='_blank' rel='noopener noreferrer'>open</a>"
        return (
            "<tr>"
            f"<td>{h(r.get('seq'))}</td>"
            f"<td><code>{h(str(r.get('case_id',''))[:12])}</code></td>"
            f"<td>{h(r.get('title',''))}</td>"
            f"<td>{h(r.get('has_description'))}</td>"
            f"<td>{h(r.get('status'))}</td>"
            f"<td>{h(r.get('strict_pass'))}</td>"
            f"<td>{h(r.get('input_complete'))}</td>"
            f"<td>{h(r.get('plot_assets_in_doc'))}</td>"
            f"<td>{h(r.get('plots_renderable'))}</td>"
            f"<td>{h(r.get('plot_assets_failed'))}</td>"
            f"<td>{h(r.get('error',''))}</td>"
            f"<td>{report_cell}</td>"
            f"<td><a href='https://pardusai.org/view/{h(r.get('case_id',''))}' target='_blank' rel='noopener noreferrer'>view</a></td>"
            "</tr>"
        )

    page = f"""<!doctype html>
<html lang='zh-CN'>
<head>
  <meta charset='utf-8'/>
  <meta name='viewport' content='width=device-width, initial-scale=1'/>
  <title>Mixed Strict Direct Capture</title>
  <style>
    body {{ font-family: -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif; margin:22px; color:#111827; }}
    table {{ border-collapse:collapse; width:100%; font-size:13px; }}
    th,td {{ border:1px solid #e5e7eb; padding:6px 8px; text-align:left; vertical-align:top; }}
    th {{ background:#f3f4f6; position:sticky; top:0; z-index:2; }}
    code {{ background:#f3f4f6; padding:2px 6px; border-radius:6px; }}
    .wrap {{ overflow:auto; max-height:calc(100vh - 220px); border:1px solid #e5e7eb; border-radius:8px; }}
  </style>
</head>
<body>
  <h1>Mixed Strict Direct Capture</h1>
  <p>rows: <code>{len(rows)}</code> · ok: <code>{len(ok_rows)}</code> · failed: <code>{len(fail_rows)}</code> · strict: <code>{len(strict_rows)}</code></p>
  <div class='wrap'>
    <table>
      <thead><tr><th>#</th><th>case</th><th>title</th><th>has_desc</th><th>status</th><th>strict</th><th>input</th><th>assets</th><th>renderable</th><th>asset_fail</th><th>error</th><th>report</th><th>pardus</th></tr></thead>
      <tbody>{''.join(row_html(r) for r in rows)}</tbody>
    </table>
  </div>
</body>
</html>
"""
    (run_dir / "index.html").write_text(page, encoding="utf-8")
    # strict only
    page_strict = page.replace(
        "<h1>Mixed Strict Direct Capture</h1>",
        "<h1>Mixed Strict Direct Capture - Strict Only</h1>",
    ).replace("".join(row_html(r) for r in rows), "".join(row_html(r) for r in strict_rows))
    (run_dir / "index_strict_only.html").write_text(page_strict, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Direct strict capture from input case list")
    ap.add_argument("--input-json", required=True)
    ap.add_argument("--target", type=int, default=100)
    ap.add_argument("--reserve", type=int, default=240)
    ap.add_argument("--download-timeout-sec", type=int, default=60)
    ap.add_argument("--asset-timeout-sec", type=int, default=20)
    ap.add_argument("--dedupe-mode", choices=["strict", "all"], default="strict")
    ap.add_argument("--debug-first", type=int, default=0)
    ap.add_argument("--out-dir", default="")
    args = ap.parse_args()

    repo_root = Path.cwd()
    runs_dir = repo_root / "Pardus/.codex_session/runs"
    scripts_dir = repo_root / "Pardus/.codex_session/scripts"
    ab_full_py = scripts_dir / "build_ab_full_report_pages.py"

    spec = importlib.util.spec_from_file_location("ab_full", ab_full_py)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {ab_full_py}")
    ab = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ab)

    run_dir = Path(args.out_dir) if args.out_dir else (runs_dir / f"mixed_strict_direct_{utc_tag()}")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "input").mkdir(exist_ok=True)
    (run_dir / "full_reports").mkdir(exist_ok=True)

    if not wait_network_ready(max_wait_sec=15):
        print("[error] network probe failed: unable to reach pardus public API in this process.")
        print("[error] please retry command; current process has DNS/connectivity instability.")
        return

    existing = collect_existing_case_ids(runs_dir, mode=args.dedupe_mode)
    src_cases = load_cases_from_input_json(Path(args.input_json))
    pool = [c for c in src_cases if c["case_id"] not in existing]
    picked = pool[: max(args.target, args.reserve)]
    print(f"[info] run_dir={run_dir}")
    print(f"[info] source_cases={len(src_cases)} existing={len(existing)} filtered={len(pool)} picked={len(picked)}")

    rows: List[Dict[str, Any]] = []
    strict_rows: List[Dict[str, Any]] = []
    strict_ids: set[str] = set()

    seq = 0
    for c in picked:
        if len(strict_rows) >= args.target:
            break
        seq += 1
        cid = c["case_id"]
        title = c.get("title", "")
        row: Dict[str, Any] = {
            "seq": seq,
            "case_id": cid,
            "title": title,
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

        up_url = UP_API.format(case_id=cid)
        up_st, up_payload = http_get_json_retry(up_url, retries=2, timeout=15)
        if args.debug_first > 0 and seq <= args.debug_first:
            st2, p2 = http_get_json(up_url, timeout=15)
            print(
                "[debug] "
                f"seq={seq} cid={cid[:12]} len={len(cid)} "
                f"uploads_status={up_st} payload_type={type(up_payload).__name__ if up_payload is not None else 'None'} "
                f"probe2_status={st2} probe2_type={type(p2).__name__ if p2 is not None else 'None'} "
                f"url={up_url!r}"
            )
        if up_st != 200 or not isinstance(up_payload, list):
            row["error"] = f"uploads_http_{up_st}"
            rows.append(row)
            continue
        uploads = [x for x in up_payload if isinstance(x, dict) and isinstance(x.get("name"), str)]
        if not uploads:
            row["error"] = "uploads_empty"
            rows.append(row)
            continue

        names_low = [str(x.get("name", "")).strip().lower() for x in uploads]
        row["has_description"] = any(n == "description.txt" or n.endswith("/description.txt") for n in names_low)

        input_dir = run_dir / "input" / f"case_{seq:03d}_{cid[:12]}"
        input_dir.mkdir(parents=True, exist_ok=True)
        row["input_dir"] = str(input_dir.relative_to(runs_dir))
        manifest = []
        seen_names: Dict[str, int] = {}
        input_ok = True
        for up in uploads:
            name = str(up["name"])
            local_path = unique_path(input_dir, name, seen_names)
            ok, err = download_file(cid, name, local_path, timeout_sec=max(10, int(args.download_timeout_sec)))
            manifest.append(
                {
                    "name": name,
                    "local": local_path.name,
                    "ok": ok,
                    "error": err,
                    "size": local_path.stat().st_size if local_path.exists() else 0,
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

        d_st, doc = http_get_json_retry(DOC_API.format(case_id=cid), retries=3, timeout=20)
        if d_st != 200 or not isinstance(doc, dict):
            row["error"] = f"document_http_{d_st}"
            rows.append(row)
            continue

        plot_assets = collect_plot_assets(doc, ab)
        row["plot_assets_in_doc"] = len(plot_assets)
        if not plot_assets:
            row["error"] = "no_plot_assets"
            rows.append(row)
            continue

        fig_cache: Dict[str, Dict[str, Any]] = {}
        asset_fail = 0
        remote_ok = 0
        for apath in plot_assets:
            st, payload = fetch_asset_with_retry(cid, apath, timeout_sec=max(8, int(args.asset_timeout_sec)))
            if st == 200 and isinstance(payload, dict) and isinstance(payload.get("data"), list):
                key = Path(apath).name
                fig_cache[key] = payload
                fig_cache[apath] = payload
                remote_ok += 1
            else:
                asset_fail += 1

        renderer = ab.DocRenderer(cid, fig_cache, translate_fn=None)
        body = renderer.render_doc_body(doc)
        body += (
            "<hr/><p class='muted'>直抓严格报告："
            f"uploads={len(uploads)} · assets={len(plot_assets)} · renderable={len(renderer.fig_payload)} · asset_fail={asset_fail}</p>"
        )
        page = ab.page_shell(
            title=f"完整报告：{title}",
            subtitle=f"来源: /view/{cid} ｜ mixed strict direct capture",
            body_html=body,
            fig_payload=renderer.fig_payload,
        )
        report_name = f"case_{seq:03d}_{cid[:12]}_full_report.html"
        (run_dir / "full_reports" / report_name).write_text(page, encoding="utf-8")
        row["full_report_html"] = f"full_reports/{report_name}"
        row["plot_assets_failed"] = asset_fail
        row["plots_renderable"] = len(renderer.fig_payload)
        row["fig_from_remote"] = remote_ok

        strict = input_ok and asset_fail == 0 and len(renderer.fig_payload) > 0
        row["strict_pass"] = strict
        row["status"] = "ok" if strict else "failed"
        if not strict and not row["error"]:
            row["error"] = "asset_incomplete" if asset_fail > 0 else "render_empty"
        rows.append(row)
        if strict and cid not in strict_ids:
            strict_ids.add(cid)
            strict_rows.append(row)

        if seq % 10 == 0:
            print(f"[progress] processed={seq} strict={len(strict_rows)}")

    reasons: Dict[str, int] = {}
    for r in rows:
        if r.get("strict_pass"):
            continue
        k = str(r.get("error") or "failed")
        reasons[k] = reasons.get(k, 0) + 1

    final = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source": str(args.input_json),
        "target": args.target,
        "source_cases": len(src_cases),
        "filtered_cases": len(pool),
        "picked_cases": len(picked),
        "actual_strict_count": len(strict_rows),
        "actual_strict_case_ids": sorted(strict_ids),
        "strict_with_description": len([r for r in strict_rows if r.get("has_description") is True]),
        "strict_without_description": len([r for r in strict_rows if r.get("has_description") is False]),
        "failure_reasons": reasons,
        "rows_all": rows,
        "rows_strict": strict_rows,
    }
    (run_dir / "summary.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "strict_rows.json").write_text(
        json.dumps({"generated_at": final["generated_at"], "rows": strict_rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    build_index(run_dir, rows)
    print(f"[done] run_dir={run_dir}")
    print(f"[done] strict_count={len(strict_rows)} target={args.target}")
    if len(strict_rows) < args.target:
        print("[done] warning: strict count below target; delivered maximum available under current constraints.")


if __name__ == "__main__":
    main()
