#!/usr/bin/env python3
"""
Scan Pardus recent public projects and find multi-sheet Excel candidates.

Criteria (quality):
- uploads endpoint 200
- at least one xlsx/xlsm with sheets >= 2
- document endpoint 200
- chart assets (.json/.svg via htmlPath) >= min_chart_count
"""

from __future__ import annotations

import argparse
import concurrent.futures
import io
import json
import pathlib
import re
import ssl
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

RECENT_API = "https://pardusai.org/v1/api/public/projects/recent?limit={limit}"
UP_API = "https://pardusai.org/v1/api/public/project/{case_id}/uploads"
DOC_API = "https://pardusai.org/v1/api/public/project/{case_id}/document"
FILE_API = "https://pardusai.org/v1/api/public/project/{case_id}/files/{name}"

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

CASE_RE = re.compile(r"/view/([0-9a-f]{32,128})")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def http_get(url: str, timeout: int = 12, max_bytes: Optional[int] = None) -> Tuple[int, Dict[str, str], bytes]:
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


def http_get_json(url: str, timeout: int = 12) -> Tuple[int, Any]:
    status, _, body = http_get(url, timeout=timeout)
    if status != 200:
        return status, None
    try:
        return 200, json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return 200, None


def extract_case_id(item: Dict[str, Any]) -> Optional[str]:
    for key in ("id", "case_id"):
        v = item.get(key)
        if isinstance(v, str) and len(v) >= 32:
            return v
    u = item.get("url")
    if isinstance(u, str):
        m = CASE_RE.search(u)
        if m:
            return m.group(1)
    return None


def count_sheets_xlsx(content: bytes) -> int:
    try:
        z = zipfile.ZipFile(io.BytesIO(content))
        xml = z.read("xl/workbook.xml").decode("utf-8", errors="replace")
        return xml.count("<sheet ")
    except Exception:
        return -1


def extract_chart_count(document_payload: Dict[str, Any]) -> int:
    refs: List[str] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            hp = x.get("htmlPath")
            if isinstance(hp, str):
                refs.append(hp)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for it in x:
                walk(it)

    walk(document_payload)
    uniq = []
    seen = set()
    for r in refs:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    # count render assets only
    c = 0
    for r in uniq:
        if r.endswith(".json") or r.endswith(".svg"):
            c += 1
    return c


@dataclass
class ExcelInfo:
    name: str
    http: int
    bytes_head: int
    sheets: int
    full_retry: bool = False
    http_full: int = 0
    bytes_full: int = 0
    sheets_full: int = -1


@dataclass
class CaseResult:
    idx: int
    case_id: str
    title: str
    view_url: str
    uploads_status: int
    upload_count: int
    excel_infos: List[ExcelInfo]
    has_multisheet: bool
    document_status: int = 0
    chart_count: int = 0


def probe_case(
    idx: int,
    case_id: str,
    title: str,
    view_url: str,
    preview_bytes: int,
    full_retry_threshold_mb: int,
    timeout_uploads: int,
    timeout_file: int,
    timeout_doc: int,
) -> CaseResult:
    up_url = UP_API.format(case_id=case_id)
    up_status, up_payload = http_get_json(up_url, timeout=timeout_uploads)

    excel_infos: List[ExcelInfo] = []
    has_multisheet = False
    upload_count = 0

    if up_status == 200 and isinstance(up_payload, list):
        upload_count = len(up_payload)
        for it in up_payload:
            if not isinstance(it, dict):
                continue
            name = it.get("name")
            if not isinstance(name, str):
                continue
            low = name.lower()
            if not (low.endswith(".xlsx") or low.endswith(".xlsm")):
                continue

            qname = urllib.parse.quote(name)
            f_url = FILE_API.format(case_id=case_id, name=qname)
            st, headers, body = http_get(f_url, timeout=timeout_file, max_bytes=preview_bytes)
            sheets = count_sheets_xlsx(body) if st == 200 else -1

            info = ExcelInfo(name=name, http=st, bytes_head=len(body), sheets=sheets)

            # if truncated/uncertain, retry full download for reasonably sized files
            content_length = 0
            try:
                content_length = int(headers.get("content-length", "0") or 0)
            except Exception:
                content_length = 0

            # Full retry only when size is known and reasonably bounded.
            # This avoids long stalls on unknown-size large files.
            should_retry_full = (
                st == 200
                and sheets < 0
                and content_length > 0
                and content_length <= full_retry_threshold_mb * 1024 * 1024
            )

            if should_retry_full:
                info.full_retry = True
                st2, _, body2 = http_get(f_url, timeout=max(timeout_file, 30), max_bytes=None)
                info.http_full = st2
                info.bytes_full = len(body2)
                info.sheets_full = count_sheets_xlsx(body2) if st2 == 200 else -1
                if info.sheets_full >= 2:
                    has_multisheet = True
            if info.sheets >= 2:
                has_multisheet = True

            excel_infos.append(info)

    doc_status = 0
    chart_count = 0
    if has_multisheet:
        d_status, d_payload = http_get_json(DOC_API.format(case_id=case_id), timeout=timeout_doc)
        doc_status = d_status
        if d_status == 200 and isinstance(d_payload, dict):
            chart_count = extract_chart_count(d_payload)

    return CaseResult(
        idx=idx,
        case_id=case_id,
        title=title,
        view_url=view_url,
        uploads_status=up_status,
        upload_count=upload_count,
        excel_infos=excel_infos,
        has_multisheet=has_multisheet,
        document_status=doc_status,
        chart_count=chart_count,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Scan recent Pardus projects for multi-sheet excel candidates")
    ap.add_argument("--recent-limit", type=int, default=500)
    ap.add_argument("--sample-size", type=int, default=500)
    ap.add_argument("--start-index", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--preview-bytes", type=int, default=4_000_000)
    ap.add_argument("--full-retry-threshold-mb", type=int, default=30)
    ap.add_argument("--min-chart-count", type=int, default=5)
    ap.add_argument("--timeout-uploads", type=int, default=10)
    ap.add_argument("--timeout-file", type=int, default=14)
    ap.add_argument("--timeout-doc", type=int, default=10)
    ap.add_argument("--out-dir", default="")
    args = ap.parse_args()

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = pathlib.Path(args.out_dir or f"Pardus/.codex_session/runs/multisheet_scan_{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)

    recent_url = RECENT_API.format(limit=args.recent_limit)
    st, payload = http_get_json(recent_url, timeout=15)
    if st != 200 or not isinstance(payload, list):
        raise SystemExit(f"failed to load recent list: HTTP={st}")

    start = max(args.start_index, 0)
    items = payload[start : start + args.sample_size]

    tasks: List[Tuple[int, str, str, str]] = []
    for i, it in enumerate(items, 1):
        if not isinstance(it, dict):
            continue
        cid = extract_case_id(it)
        if not cid:
            continue
        title = it.get("title") if isinstance(it.get("title"), str) else ""
        view_url = it.get("url") if isinstance(it.get("url"), str) else f"https://pardusai.org/view/{cid}"
        tasks.append((start + i, cid, title, view_url))

    print(f"[{now_iso()}] recent={len(payload)} tasks={len(tasks)} workers={args.workers}")

    results: List[CaseResult] = []
    done = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        fut_map = {
            ex.submit(
                probe_case,
                idx,
                cid,
                title,
                view_url,
                args.preview_bytes,
                args.full_retry_threshold_mb,
                args.timeout_uploads,
                args.timeout_file,
                args.timeout_doc,
            ): (idx, cid)
            for idx, cid, title, view_url in tasks
        }

        for fut in concurrent.futures.as_completed(fut_map):
            done += 1
            idx, cid = fut_map[fut]
            try:
                res = fut.result()
                results.append(res)
            except Exception as e:
                results.append(
                    CaseResult(
                        idx=idx,
                        case_id=cid,
                        title="",
                        view_url=f"https://pardusai.org/view/{cid}",
                        uploads_status=0,
                        upload_count=0,
                        excel_infos=[],
                        has_multisheet=False,
                        document_status=0,
                        chart_count=0,
                    )
                )
                print(f"[{now_iso()}] ERR idx={idx} case={cid[:12]} err={e}")

            if done % 20 == 0 or done == len(tasks):
                ms = sum(1 for r in results if r.has_multisheet)
                hq = sum(
                    1
                    for r in results
                    if r.has_multisheet
                    and r.uploads_status == 200
                    and r.document_status == 200
                    and r.chart_count >= args.min_chart_count
                )
                print(f"[{now_iso()}] progress {done}/{len(tasks)} multisheet={ms} qualified={hq}")

    # sort by idx
    results.sort(key=lambda x: x.idx)

    multisheet = [r for r in results if r.has_multisheet]
    qualified = [
        r
        for r in multisheet
        if r.uploads_status == 200 and r.document_status == 200 and r.chart_count >= args.min_chart_count
    ]

    # prepare serializable payload
    results_dict = [
        {
            **{k: v for k, v in asdict(r).items() if k != "excel_infos"},
            "excel_infos": [asdict(x) for x in r.excel_infos],
        }
        for r in results
    ]

    multisheet_dict = [d for d in results_dict if d["has_multisheet"]]
    qualified_dict = [
        d
        for d in multisheet_dict
        if d["uploads_status"] == 200 and d["document_status"] == 200 and d["chart_count"] >= args.min_chart_count
    ]

    summary = {
        "generated_at": now_iso(),
        "recent_limit": args.recent_limit,
        "sample_size": args.sample_size,
        "start_index": args.start_index,
        "workers": args.workers,
        "min_chart_count": args.min_chart_count,
        "total_tasks": len(tasks),
        "multisheet_count": len(multisheet),
        "qualified_count": len(qualified),
        "preview_bytes": args.preview_bytes,
        "full_retry_threshold_mb": args.full_retry_threshold_mb,
    }

    (out_dir / "scan_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "scan_results_all.json").write_text(json.dumps(results_dict, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "scan_results_multisheet.json").write_text(
        json.dumps(multisheet_dict, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "scan_results_qualified.json").write_text(
        json.dumps(qualified_dict, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Build lightweight selected input for evidence generator
    selected_for_evidence = [
        {"idx": i + 1, "case_id": q["case_id"], "title": q.get("title", "")}
        for i, q in enumerate(sorted(qualified_dict, key=lambda x: x.get("chart_count", 0), reverse=True))
    ]
    (out_dir / "selected_for_evidence.json").write_text(
        json.dumps({"results": selected_for_evidence}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("saved:", out_dir / "scan_summary.json")
    print("saved:", out_dir / "scan_results_qualified.json")
    print("saved:", out_dir / "selected_for_evidence.json")


if __name__ == "__main__":
    main()
