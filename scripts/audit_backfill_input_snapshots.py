#!/usr/bin/env python3
"""
Audit and backfill local input snapshots for historical Pardus research runs.

What it does:
1) Scan all non-symlink run dirs under Pardus/.codex_session/runs that contain summary.json
2) Extract case ids from summary rows/results
3) Download full uploads/files payload for each unique case id into shared cache
4) Materialize per-run input_snapshot directories from cache (copy mode)
5) Emit per-run manifest.json + one global audit report JSON
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import ssl
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE = "https://pardusai.org"
UP_API = BASE + "/v1/api/public/project/{case_id}/uploads"
FILE_API = BASE + "/v1/api/public/project/{case_id}/files/{name}"


def now_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def safe_file_name(raw: str) -> str:
    out = (raw or "").replace("\\", "_").replace("/", "_").strip()
    return out or "unnamed_file"


def unique_path(base_dir: Path, raw_name: str, seen: Dict[str, int]) -> Path:
    base = safe_file_name(raw_name)
    key = base.lower()
    n = seen.get(key, 0)
    seen[key] = n + 1
    if n == 0:
        return base_dir / base
    stem = Path(base).stem
    suf = Path(base).suffix
    return base_dir / f"{stem}__dup{n}{suf}"


def req_get(url: str, *, timeout: int, ctx: ssl.SSLContext) -> Tuple[int, Dict[str, str], bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return int(r.getcode() or 0), {k.lower(): v for k, v in dict(r.headers).items()}, r.read()
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except Exception:
            body = b""
        return int(e.code or 0), {}, body
    except Exception:
        return 0, {}, b""


def req_json(url: str, *, timeout: int, ctx: ssl.SSLContext) -> Tuple[int, Any]:
    st, _, body = req_get(url, timeout=timeout, ctx=ctx)
    if st != 200:
        return st, None
    try:
        return st, json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return st, None


def extract_cases_from_summary(summary: Dict[str, Any]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []

    def add(case_id: Optional[str], title: Optional[str]) -> None:
        if not isinstance(case_id, str) or not case_id:
            return
        out.append({"case_id": case_id, "title": title or ""})

    rows = summary.get("rows")
    if isinstance(rows, list):
        for it in rows:
            if not isinstance(it, dict):
                continue
            add(it.get("case_id"), it.get("title"))

    results = summary.get("results")
    if isinstance(results, list):
        for it in results:
            if not isinstance(it, dict):
                continue
            # template/rerun both belong to research provenance
            add(it.get("template_case_id"), it.get("title"))
            add(it.get("rerun_case_id"), it.get("title"))
            add(it.get("case_id"), it.get("title"))

    # dedupe keep first title
    dedup: Dict[str, Dict[str, str]] = {}
    for it in out:
        cid = it["case_id"]
        if cid not in dedup:
            dedup[cid] = it
    return list(dedup.values())


def load_runs(runs_root: Path) -> List[Dict[str, Any]]:
    runs: List[Dict[str, Any]] = []
    for p in sorted(runs_root.iterdir()):
        if not p.is_dir() or p.is_symlink():
            continue
        summary_path = p / "summary.json"
        if not summary_path.exists():
            continue
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(summary, dict):
            continue
        cases = extract_cases_from_summary(summary)
        runs.append({"run_dir": p, "run_name": p.name, "summary_path": summary_path, "cases": cases})
    return runs


def ensure_case_cache(
    *,
    case_id: str,
    title_hint: str,
    cache_root: Path,
    ctx: ssl.SSLContext,
    timeout_uploads: int,
    timeout_file: int,
) -> Dict[str, Any]:
    short = case_id[:12]
    case_dir = cache_root / f"{short}_{safe_file_name(title_hint) or 'untitled'}"
    meta_path = case_dir / "manifest.json"
    case_dir.mkdir(parents=True, exist_ok=True)

    if meta_path.exists():
        try:
            old = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(old, dict) and old.get("status") == "ok":
                files = old.get("files")
                if isinstance(files, list) and files:
                    all_present = True
                    for f in files:
                        if not isinstance(f, dict):
                            all_present = False
                            break
                        p = Path(str(f.get("cached_path") or ""))
                        if int(f.get("status") or 0) != 200 or (not p.exists()) or p.stat().st_size <= 0:
                            all_present = False
                            break
                    if all_present:
                        old["reused_from_cache_manifest"] = True
                        return old
        except Exception:
            pass

    st_up, up_payload = req_json(UP_API.format(case_id=case_id), timeout=timeout_uploads, ctx=ctx)
    if st_up != 200 or not isinstance(up_payload, list):
        rec = {
            "case_id": case_id,
            "title_hint": title_hint,
            "status": "failed",
            "uploads_status": st_up,
            "error": "uploads endpoint unavailable",
            "files": [],
            "generated_at": now_iso(),
        }
        meta_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        return rec

    uploads = [it for it in up_payload if isinstance(it, dict) and isinstance(it.get("name"), str)]
    seen: Dict[str, int] = {}
    files: List[Dict[str, Any]] = []
    all_ok = True
    for idx, it in enumerate(uploads):
        raw_name = str(it.get("name"))
        enc_name = urllib.parse.quote(raw_name, safe="")
        src_url = FILE_API.format(case_id=case_id, name=enc_name)
        out_path = unique_path(case_dir, raw_name, seen)

        downloaded = False
        if out_path.exists() and out_path.stat().st_size > 0:
            body = out_path.read_bytes()
            st_file = 200
        else:
            st_file, _, body = req_get(src_url, timeout=timeout_file, ctx=ctx)
            if st_file == 200:
                out_path.write_bytes(body)
                downloaded = True

        ok = st_file == 200
        all_ok = all_ok and ok
        files.append(
            {
                "idx": idx,
                "name": raw_name,
                "status": st_file,
                "size_bytes": len(body) if ok else 0,
                "source_url": src_url,
                "cached_path": str(out_path),
                "downloaded_now": downloaded,
            }
        )

    rec = {
        "case_id": case_id,
        "title_hint": title_hint,
        "status": "ok" if all_ok else "partial_failed",
        "uploads_status": st_up,
        "uploads_count": len(uploads),
        "files_ok_count": sum(1 for f in files if f["status"] == 200),
        "files": files,
        "generated_at": now_iso(),
    }
    meta_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return rec


def copy_case_inputs_from_cache(
    *,
    run_dir: Path,
    case_id: str,
    title: str,
    cache_manifest: Dict[str, Any],
) -> Dict[str, Any]:
    input_root = run_dir / "input_snapshot"
    input_root.mkdir(parents=True, exist_ok=True)
    case_out = input_root / f"{case_id[:12]}_{safe_file_name(title) or 'untitled'}"
    case_out.mkdir(parents=True, exist_ok=True)

    copied = 0
    failed = 0
    file_rows: List[Dict[str, Any]] = []
    for f in cache_manifest.get("files", []):
        if not isinstance(f, dict):
            continue
        src = Path(str(f.get("cached_path") or ""))
        name = str(f.get("name") or src.name)
        dst = case_out / safe_file_name(name)
        ok = bool(f.get("status") == 200 and src.exists() and src.is_file())
        if ok:
            if not dst.exists() or dst.stat().st_size == 0:
                shutil.copy2(src, dst)
            copied += 1
        else:
            failed += 1
        file_rows.append(
            {
                "name": name,
                "status": int(f.get("status") or 0),
                "cached_path": str(src),
                "run_snapshot_path": str(dst),
                "ok": ok,
            }
        )

    case_manifest = {
        "case_id": case_id,
        "title": title,
        "source_cache_manifest": cache_manifest.get("generated_at"),
        "status": "ok" if failed == 0 else "partial_failed",
        "copied_files": copied,
        "failed_files": failed,
        "files": file_rows,
        "generated_at": now_iso(),
    }
    (case_out / "manifest.json").write_text(json.dumps(case_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return case_manifest


def main() -> None:
    ap = argparse.ArgumentParser(description="Audit and backfill historical input snapshots")
    ap.add_argument("--runs-root", default="Pardus/.codex_session/runs")
    ap.add_argument("--cache-root", default="Pardus/.codex_session/datasets/public_inputs_cache")
    ap.add_argument("--report-out", default="")
    ap.add_argument("--timeout-uploads", type=int, default=45)
    ap.add_argument("--timeout-file", type=int, default=120)
    ap.add_argument("--start-case", type=int, default=0, help="Start index in sorted unique case ids")
    ap.add_argument("--max-cases", type=int, default=0, help="Max cases to process, 0 means all")
    args = ap.parse_args()

    runs_root = Path(args.runs_root)
    cache_root = Path(args.cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    runs = load_runs(runs_root)
    case_title_map: Dict[str, str] = {}
    run_case_map: Dict[str, List[Dict[str, str]]] = {}
    for r in runs:
        run_case_map[r["run_name"]] = r["cases"]
        for it in r["cases"]:
            cid = it["case_id"]
            if cid not in case_title_map:
                case_title_map[cid] = it.get("title", "")

    case_cache_manifest: Dict[str, Dict[str, Any]] = {}
    ordered_cases = sorted(case_title_map.items(), key=lambda x: x[0])
    start = max(0, int(args.start_case))
    if args.max_cases and args.max_cases > 0:
        ordered_cases = ordered_cases[start : start + int(args.max_cases)]
    else:
        ordered_cases = ordered_cases[start:]

    for i, (cid, title) in enumerate(ordered_cases, start=1):
        print(f"[cache] {i}/{len(ordered_cases)} {cid[:12]} {title}", flush=True)
        rec = ensure_case_cache(
            case_id=cid,
            title_hint=title,
            cache_root=cache_root,
            ctx=ctx,
            timeout_uploads=args.timeout_uploads,
            timeout_file=args.timeout_file,
        )
        case_cache_manifest[cid] = rec

    run_reports: List[Dict[str, Any]] = []
    for r in runs:
        run_dir: Path = r["run_dir"]
        run_name: str = r["run_name"]
        case_reports: List[Dict[str, Any]] = []
        for it in run_case_map.get(run_name, []):
            cid = it["case_id"]
            title = it.get("title", "")
            cache_rec = case_cache_manifest.get(cid)
            if cache_rec is None:
                # Case not in this batch window; load from existing cache manifest if present.
                short = cid[:12]
                cache_rec = {"status": "failed", "files": [], "generated_at": now_iso()}
                for d in cache_root.glob(f"{short}_*"):
                    mp = d / "manifest.json"
                    if mp.exists():
                        try:
                            cache_rec = json.loads(mp.read_text(encoding="utf-8"))
                        except Exception:
                            pass
                        break
            cr = copy_case_inputs_from_cache(run_dir=run_dir, case_id=cid, title=title, cache_manifest=cache_rec)
            case_reports.append(cr)

        rr = {
            "run_name": run_name,
            "run_dir": str(run_dir),
            "case_count": len(case_reports),
            "ok_cases": sum(1 for c in case_reports if c.get("status") == "ok"),
            "partial_failed_cases": sum(1 for c in case_reports if c.get("status") != "ok"),
            "generated_at": now_iso(),
            "cases": case_reports,
        }
        (run_dir / "input_snapshot" / "manifest.json").write_text(
            json.dumps(rr, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        run_reports.append(rr)

    report = {
        "generated_at": now_iso(),
        "runs_root": str(runs_root),
        "cache_root": str(cache_root),
        "run_count": len(runs),
        "unique_case_count": len(case_title_map),
        "cache_ok_cases": sum(1 for c in case_cache_manifest.values() if c.get("status") == "ok"),
        "cache_partial_failed_cases": sum(1 for c in case_cache_manifest.values() if c.get("status") != "ok"),
        "runs": [
            {
                "run_name": r["run_name"],
                "case_count": r["case_count"],
                "ok_cases": r["ok_cases"],
                "partial_failed_cases": r["partial_failed_cases"],
            }
            for r in run_reports
        ],
    }

    if args.report_out:
        report_path = Path(args.report_out)
    else:
        tag = dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        report_path = runs_root / f"input_backfill_audit_{tag}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("runs:", len(runs))
    print("unique_cases:", len(case_title_map))
    print("cache_ok:", report["cache_ok_cases"])
    print("cache_partial_failed:", report["cache_partial_failed_cases"])
    print("report:", report_path)


if __name__ == "__main__":
    main()
