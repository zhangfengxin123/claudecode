#!/usr/bin/env python3
"""Light probe for Pardus public recent projects.

Collects lightweight structural signals only:
- title/url/case_id
- document endpoint status and basic stats
- uploads endpoint status and file names
- chart asset counts from document json (htmlPath/src references)
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import pathlib
import re
import subprocess
from typing import Any, Dict, Iterable, List, Tuple

BASE = "https://pardusai.org"
RECENT_ENDPOINT = f"{BASE}/v1/api/public/projects/recent"
UA = "Mozilla/5.0 (compatible; pardus-light-probe/1.0)"


def _http_get_json(url: str, timeout: int = 25) -> Tuple[int, int, Any]:
    marker = b"\n__STATUS__:"
    cmd = [
        "curl",
        "-sS",
        "-L",
        "-m",
        str(timeout),
        "-H",
        f"User-Agent: {UA}",
        "-w",
        "\\n__STATUS__:%{http_code}",
        url,
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if not proc.stdout:
        return 0, 0, None

    payload = proc.stdout
    if marker not in payload:
        return 0, 0, None

    body_raw, status_raw = payload.rsplit(marker, 1)
    try:
        status = int(status_raw.decode("utf-8", errors="replace").strip())
    except ValueError:
        status = 0

    size = len(body_raw)
    try:
        data = json.loads(body_raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        data = None
    return status, size, data


def _walk(node: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _extract_case_id(url: str) -> str:
    m = re.search(r"/view/([a-f0-9]{32,128})", url)
    return m.group(1) if m else ""


def _truncate(text: str, limit: int = 56) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _h(text: Any) -> str:
    return html.escape("" if text is None else str(text))


def _extract_doc_brief(doc_payload: Any) -> Dict[str, Any]:
    headings: List[str] = []
    first_paragraph = ""
    first_table: Dict[str, Any] | None = None

    if not isinstance(doc_payload, dict):
        return {
            "headings_preview": headings,
            "first_paragraph": first_paragraph,
            "first_table": first_table,
        }

    root = doc_payload.get("document")
    for obj in _walk(root):
        typ = obj.get("type")
        if typ == "heading":
            content = obj.get("content")
            if isinstance(content, str):
                headings.append(content)

        if typ == "paragraph" and not first_paragraph:
            text_parts: List[str] = []
            children = obj.get("children")
            if isinstance(children, list):
                for c in children:
                    if isinstance(c, dict):
                        c_text = c.get("content")
                        if isinstance(c_text, str):
                            text_parts.append(c_text)
            content = obj.get("content")
            if not text_parts and isinstance(content, str):
                text_parts.append(content)
            first_paragraph = " ".join(t.strip() for t in text_parts if t.strip()).strip()

        if typ == "table" and first_table is None:
            headers = obj.get("headers")
            rows = obj.get("rows")
            if isinstance(headers, list) and isinstance(rows, list):
                first_table = {"headers": headers, "rows": rows[:5]}

    return {
        "headings_preview": headings[:20],
        "first_paragraph": first_paragraph,
        "first_table": first_table,
    }


def _download_preview_image(view_url: str, out_png: pathlib.Path, timeout: int = 8) -> Tuple[bool, str]:
    remote = f"https://image.thum.io/get/width/1400/noanimate/{view_url}"
    cmd = [
        "curl",
        "-sS",
        "-L",
        "-m",
        str(timeout),
        "-o",
        str(out_png),
        remote,
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    ok = proc.returncode == 0 and out_png.exists() and out_png.stat().st_size > 0
    return ok, remote


def probe_recent(sample_size: int, recent_limit: int) -> Dict[str, Any]:
    recent_url = f"{RECENT_ENDPOINT}?limit={recent_limit}"
    recent_status, recent_bytes, recent_payload = _http_get_json(recent_url)
    projects = recent_payload if isinstance(recent_payload, list) else []
    sampled = projects[:sample_size]

    results: List[Dict[str, Any]] = []
    for idx, item in enumerate(sampled, start=1):
        url = item.get("url", "") if isinstance(item, dict) else ""
        title = item.get("title", "") if isinstance(item, dict) else ""
        case_id = _extract_case_id(url)

        doc_url = f"{BASE}/v1/api/public/project/{case_id}/document"
        up_url = f"{BASE}/v1/api/public/project/{case_id}/uploads"

        doc_status, doc_bytes, doc_payload = _http_get_json(doc_url)
        up_status, up_bytes, up_payload = _http_get_json(up_url)

        chart_json = set()
        asset_svg = set()
        heading_count = 0
        visibility = None
        can_edit = None

        if isinstance(doc_payload, dict):
            visibility = doc_payload.get("visibility")
            can_edit = doc_payload.get("can_edit")
            doc_root = doc_payload.get("document")
            for obj in _walk(doc_root):
                html_path = obj.get("htmlPath")
                if isinstance(html_path, str):
                    if html_path.endswith(".json"):
                        chart_json.add(html_path)
                    elif html_path.endswith(".svg"):
                        asset_svg.add(html_path)

                src = obj.get("src")
                if isinstance(src, str) and src.endswith(".svg"):
                    asset_svg.add(src)

                if obj.get("type") == "heading":
                    heading_count += 1
        brief = _extract_doc_brief(doc_payload)

        uploads: List[str] = []
        if isinstance(up_payload, list):
            for f in up_payload:
                if isinstance(f, dict) and isinstance(f.get("name"), str):
                    uploads.append(f["name"])

        results.append(
            {
                "idx": idx,
                "title": title,
                "url": url,
                "case_id": case_id,
                "document_url": doc_url,
                "uploads_url": up_url,
                "document_status": doc_status,
                "document_bytes": doc_bytes,
                "uploads_status": up_status,
                "uploads_bytes": up_bytes,
                "uploads": uploads,
                "uploads_count": len(uploads),
                "chart_json_count": len(chart_json),
                "svg_asset_count": len(asset_svg),
                "chart_json_assets": sorted(chart_json),
                "svg_assets": sorted(asset_svg),
                "heading_count": heading_count,
                "headings_preview": brief.get("headings_preview", []),
                "first_paragraph": brief.get("first_paragraph", ""),
                "first_table": brief.get("first_table"),
                "visibility": visibility,
                "can_edit": can_edit,
            }
        )

    aggregate = {
        "sample_count": len(results),
        "recent_return_count": len(projects),
        "document_ok_count": sum(1 for r in results if r["document_status"] == 200),
        "uploads_ok_count": sum(1 for r in results if r["uploads_status"] == 200),
        "avg_chart_json_count": round(
            (
                sum(r["chart_json_count"] for r in results) / len(results)
                if results
                else 0.0
            ),
            2,
        ),
        "avg_uploads_count": round(
            (
                sum(r["uploads_count"] for r in results) / len(results)
                if results
                else 0.0
            ),
            2,
        ),
        "max_chart_json_count": max((r["chart_json_count"] for r in results), default=0),
    }

    return {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source_endpoint": recent_url,
        "recent_status": recent_status,
        "recent_bytes": recent_bytes,
        "recent_return_count": len(projects),
        "sample_size_requested": sample_size,
        "aggregate": aggregate,
        "results": results,
    }


def write_markdown(report: Dict[str, Any], out_path: pathlib.Path) -> None:
    lines: List[str] = []
    lines.append("# Pardus 轻抓试运行报告")
    lines.append("")
    lines.append(
        f"- 生成时间: {report.get('generated_at')}"
    )
    lines.append(
        f"- 接口: `{report.get('source_endpoint')}`"
    )
    lines.append(
        f"- recent 返回数量: `{report.get('recent_return_count')}`"
    )
    lines.append(
        f"- 本次样本数: `{report.get('aggregate', {}).get('sample_count')}`"
    )
    lines.append("")

    agg = report.get("aggregate", {})
    lines.append("## 汇总")
    lines.append("")
    lines.append(f"- 文档接口 200 数: `{agg.get('document_ok_count')}`")
    lines.append(f"- 上传接口 200 数: `{agg.get('uploads_ok_count')}`")
    lines.append(f"- 平均图表 JSON 数: `{agg.get('avg_chart_json_count')}`")
    lines.append(f"- 平均上传文件数: `{agg.get('avg_uploads_count')}`")
    lines.append(f"- 最大图表 JSON 数: `{agg.get('max_chart_json_count')}`")
    lines.append("")

    lines.append("## 样本明细")
    lines.append("")
    lines.append("| # | case_id | title | uploads | chart_json | doc | up | html |")
    lines.append("|---|---|---|---:|---:|---:|---:|---|")
    for item in report.get("results", []):
        short_case = item.get("case_id", "")[:12]
        title = _truncate(item.get("title", ""))
        html_file = item.get("html_file", "")
        html_cell = f"[view]({html_file})" if html_file else ""
        lines.append(
            "| {idx} | `{case}` | {title} | {uploads} | {charts} | {doc} | {up} | {html} |".format(
                idx=item.get("idx"),
                case=short_case,
                title=title,
                uploads=item.get("uploads_count"),
                charts=item.get("chart_json_count"),
                doc=item.get("document_status"),
                up=item.get("uploads_status"),
                html=html_cell,
            )
        )

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_html_reports(report: Dict[str, Any], out_dir: pathlib.Path, download_images: bool) -> pathlib.Path:
    items_dir = out_dir / "items_html"
    images_dir = out_dir / "images"
    items_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    for item in report.get("results", []):
        idx = int(item.get("idx", 0))
        case_id = item.get("case_id", "")
        short_case = case_id[:12] if case_id else "unknown"
        filename = f"case_{idx:03d}_{short_case}.html"
        rel_path = pathlib.Path("items_html") / filename
        item["html_file"] = rel_path.as_posix()

        img_name = f"case_{idx:03d}_{short_case}.png"
        local_img_rel = pathlib.Path("images") / img_name
        local_img_abs = images_dir / img_name
        ok_img = False
        remote_img = ""
        if item.get("url"):
            ok_img, remote_img = _download_preview_image(item["url"], local_img_abs) if download_images else (False, f"https://image.thum.io/get/width/1400/noanimate/{item['url']}")
        item["preview_image_local"] = local_img_rel.as_posix() if ok_img else ""
        item["preview_image_remote"] = remote_img

        uploads_html = "".join(
            f"<li><code>{_h(name)}</code></li>" for name in item.get("uploads", [])
        ) or "<li><em>none</em></li>"
        charts_html = "".join(
            f"<li><code>{_h(name)}</code></li>" for name in item.get("chart_json_assets", [])
        ) or "<li><em>none</em></li>"
        svg_html = "".join(
            f"<li><code>{_h(name)}</code></li>" for name in item.get("svg_assets", [])
        ) or "<li><em>none</em></li>"
        headings_html = "".join(
            f"<li>{_h(hd)}</li>" for hd in item.get("headings_preview", [])[:10]
        ) or "<li><em>none</em></li>"

        table_html = "<p><em>none</em></p>"
        ft = item.get("first_table")
        if isinstance(ft, dict) and isinstance(ft.get("headers"), list) and isinstance(ft.get("rows"), list):
            ths = "".join(f"<th>{_h(hd)}</th>" for hd in ft["headers"])
            trs = []
            for row in ft["rows"]:
                if isinstance(row, list):
                    tds = "".join(f"<td>{_h(v)}</td>" for v in row)
                    trs.append(f"<tr>{tds}</tr>")
            table_html = (
                "<div style='overflow:auto'><table style='border-collapse:collapse; width:100%'>"
                f"<thead><tr>{ths}</tr></thead><tbody>{''.join(trs)}</tbody></table></div>"
            )

        img_src = "../" + item["preview_image_local"] if item.get("preview_image_local") else item.get("preview_image_remote", "")
        img_onerror = ""
        if item.get("preview_image_local") and item.get("preview_image_remote"):
            img_onerror = f" onerror=\"this.onerror=null;this.src='{_h(item.get('preview_image_remote'))}'\""

        page = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Pardus Probe #{idx}</title>
  <style>
    body {{ font-family: -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif; margin: 24px; color: #1f2937; }}
    h1 {{ margin: 0 0 12px; font-size: 22px; }}
    .meta {{ color: #6b7280; margin-bottom: 18px; }}
    .grid {{ display: grid; grid-template-columns: 180px 1fr; gap: 8px 12px; margin: 14px 0 20px; }}
    .k {{ color: #6b7280; }}
    code {{ background: #f3f4f6; padding: 2px 6px; border-radius: 5px; }}
    a {{ color: #2563eb; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .block {{ border: 1px solid #e5e7eb; border-radius: 10px; padding: 14px; margin: 14px 0; }}
    ul {{ margin: 8px 0 0 18px; }}
    table td, table th {{ border:1px solid #e5e7eb; padding:6px 8px; font-size:13px; }}
  </style>
</head>
<body>
  <h1>#{idx} {_h(item.get("title", ""))}</h1>
  <div class="meta">case_id: <code>{_h(case_id)}</code></div>
  <div class="grid">
    <div class="k">view_url</div><div><a href="{_h(item.get("url", ""))}" target="_blank" rel="noopener noreferrer">{_h(item.get("url", ""))}</a></div>
    <div class="k">document</div><div><code>{_h(item.get("document_status"))}</code> · <a href="{_h(item.get("document_url", ""))}" target="_blank" rel="noopener noreferrer">open api</a></div>
    <div class="k">uploads</div><div><code>{_h(item.get("uploads_status"))}</code> · <a href="{_h(item.get("uploads_url", ""))}" target="_blank" rel="noopener noreferrer">open api</a></div>
    <div class="k">chart_json_count</div><div><code>{_h(item.get("chart_json_count"))}</code></div>
    <div class="k">svg_asset_count</div><div><code>{_h(item.get("svg_asset_count"))}</code></div>
    <div class="k">heading_count</div><div><code>{_h(item.get("heading_count"))}</code></div>
    <div class="k">uploads_count</div><div><code>{_h(item.get("uploads_count"))}</code></div>
    <div class="k">visibility</div><div><code>{_h(item.get("visibility"))}</code></div>
  </div>
  <div class="block">
    <strong>Preview Image</strong>
    <div style="margin-top:10px">
      <img src="{_h(img_src)}"{img_onerror} alt="preview" style="max-width:100%; border:1px solid #e5e7eb; border-radius:8px" />
    </div>
  </div>
  <div class="block">
    <strong>First Paragraph (from document)</strong>
    <p style="line-height:1.7; margin-top:10px">{_h(item.get("first_paragraph", "")) or "<em>none</em>"}</p>
  </div>
  <div class="block">
    <strong>Headings Preview</strong>
    <ul>{headings_html}</ul>
  </div>
  <div class="block">
    <strong>First Table Preview</strong>
    {table_html}
  </div>
  <div class="block">
    <strong>Uploads</strong>
    <ul>{uploads_html}</ul>
  </div>
  <div class="block">
    <strong>Chart JSON Assets</strong>
    <ul>{charts_html}</ul>
  </div>
  <div class="block">
    <strong>SVG Assets</strong>
    <ul>{svg_html}</ul>
  </div>
</body>
</html>
"""
        (items_dir / filename).write_text(page, encoding="utf-8")

    rows = []
    for item in report.get("results", []):
        thumb_src = item.get("preview_image_local") or item.get("preview_image_remote") or ""
        thumb_html = "no"
        if thumb_src:
            thumb_onerror = ""
            if item.get("preview_image_local") and item.get("preview_image_remote"):
                thumb_onerror = f" onerror=\"this.onerror=null;this.src='{_h(item.get('preview_image_remote'))}'\""
            thumb_html = (
                f"<img src=\"{_h(thumb_src)}\"{thumb_onerror} alt=\"thumb\" "
                f"style=\"width:220px; max-width:100%; border:1px solid #e5e7eb; border-radius:6px\" />"
            )
        rows.append(
            f"<tr>"
            f"<td>{_h(item.get('idx'))}</td>"
            f"<td><code>{_h((item.get('case_id') or '')[:12])}</code></td>"
            f"<td>{_h(_truncate(item.get('title', ''), 80))}</td>"
            f"<td>{_h(item.get('uploads_count'))}</td>"
            f"<td>{_h(item.get('chart_json_count'))}</td>"
            f"<td>{_h(item.get('document_status'))}</td>"
            f"<td>{_h(item.get('uploads_status'))}</td>"
            f"<td>{thumb_html}</td>"
            f"<td><a href=\"{_h(item.get('html_file'))}\">open</a></td>"
            f"</tr>"
        )

    agg = report.get("aggregate", {})
    index_page = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Pardus Light Probe Index</title>
  <style>
    body {{ font-family: -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif; margin: 24px; color: #1f2937; }}
    h1 {{ margin: 0 0 10px; }}
    .meta {{ color: #6b7280; margin-bottom: 16px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #e5e7eb; padding: 8px 10px; text-align: left; font-size: 14px; vertical-align: top; }}
    th {{ background: #f9fafb; }}
    code {{ background: #f3f4f6; padding: 2px 6px; border-radius: 5px; }}
    a {{ color: #2563eb; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <h1>Pardus 轻抓索引</h1>
  <div class="meta">
    generated_at: <code>{_h(report.get('generated_at'))}</code> ·
    source: <code>{_h(report.get('source_endpoint'))}</code> ·
    sample: <code>{_h(agg.get('sample_count'))}</code> ·
    document_200: <code>{_h(agg.get('document_ok_count'))}</code> ·
    uploads_200: <code>{_h(agg.get('uploads_ok_count'))}</code>
  </div>
  <table>
    <thead>
      <tr><th>#</th><th>case</th><th>title</th><th>uploads</th><th>charts</th><th>doc</th><th>up</th><th>preview</th><th>html</th></tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
</body>
</html>
"""
    index_path = out_dir / "light_probe_index.html"
    index_path.write_text(index_page, encoding="utf-8")
    report["index_html"] = index_path.name
    return index_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Pardus recent light probe")
    parser.add_argument("--sample-size", type=int, default=10, help="How many recent items to probe")
    parser.add_argument(
        "--recent-limit",
        type=int,
        default=500,
        help="Recent list size requested from API",
    )
    parser.add_argument(
        "--download-preview-images",
        action="store_true",
        help="Download preview images for each case using external screenshot service",
    )
    parser.add_argument("--out-dir", type=str, required=True, help="Output directory")
    args = parser.parse_args()

    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = probe_recent(
        sample_size=max(1, args.sample_size),
        recent_limit=max(1, args.recent_limit),
    )
    index_path = write_html_reports(report, out_dir, download_images=args.download_preview_images)
    json_path = out_dir / "light_probe.json"
    md_path = out_dir / "light_probe.md"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(report, md_path)

    print(f"saved: {json_path}")
    print(f"saved: {md_path}")
    print(f"saved: {index_path}")


if __name__ == "__main__":
    main()
