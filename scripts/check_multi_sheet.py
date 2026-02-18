#!/usr/bin/env python3
import io
import json
import pathlib
import ssl
import urllib.parse
import urllib.request
import zipfile

summary_path = pathlib.Path('Pardus/.codex_session/runs/chart_evidence_60_combo_20260209_212840/summary.json')
rows = json.loads(summary_path.read_text())['rows']
rows = [r for r in rows if r.get('doc_status') == 200 and (r.get('chart_count') or 0) >= 5 and (r.get('upload_count') or 0) >= 1]

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def get_json(url: str, timeout: int = 10):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            if r.getcode() != 200:
                return int(r.getcode() or 0), None
            return 200, json.loads(r.read().decode('utf-8', 'replace'))
    except Exception:
        return 0, None


def get_bytes(url: str, timeout: int = 12, max_bytes: int = 4_000_000):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return int(r.getcode() or 0), r.read(max_bytes)
    except Exception:
        return 0, b''


def count_sheets_xlsx(body: bytes) -> int:
    try:
        z = zipfile.ZipFile(io.BytesIO(body))
        xml = z.read('xl/workbook.xml').decode('utf-8', 'replace')
        return xml.count('<sheet ')
    except Exception:
        return -1


excel_cases = []
multi_cases = []

print(f'high_quality_pool={len(rows)}', flush=True)
for i, r in enumerate(rows, 1):
    cid = r['case_id']
    st, uploads = get_json(f'https://pardusai.org/v1/api/public/project/{cid}/uploads')
    if st != 200 or not isinstance(uploads, list):
        continue

    excel_files = []
    for it in uploads:
        if not isinstance(it, dict):
            continue
        n = it.get('name', '')
        if not isinstance(n, str):
            continue
        low = n.lower()
        if low.endswith('.xlsx') or low.endswith('.xlsm'):
            excel_files.append(n)

    if not excel_files:
        continue

    infos = []
    ms = False
    for n in excel_files:
        q = urllib.parse.quote(n)
        stf, body = get_bytes(f'https://pardusai.org/v1/api/public/project/{cid}/files/{q}')
        sheets = count_sheets_xlsx(body) if stf == 200 else -1
        infos.append({'file': n, 'http': stf, 'sheets': sheets, 'bytes_head': len(body)})
        if sheets >= 2:
            ms = True

    item = {
        'idx': r['idx'],
        'case_id': cid,
        'title': r.get('title', ''),
        'chart_count': r.get('chart_count', 0),
        'upload_count': r.get('upload_count', 0),
        'excel_infos': infos,
        'multi_sheet': ms,
    }
    excel_cases.append(item)
    if ms:
        multi_cases.append(item)

    print(f'processed {i}/{len(rows)} -> excel_cases={len(excel_cases)} multi={len(multi_cases)}', flush=True)

out = pathlib.Path('Pardus/.codex_session/runs/multi_sheet_candidates_from60.json')
out.write_text(
    json.dumps(
        {
            'high_quality_pool_count': len(rows),
            'excel_cases_count': len(excel_cases),
            'multi_sheet_cases_count': len(multi_cases),
            'excel_cases': excel_cases,
            'multi_sheet_cases': multi_cases,
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding='utf-8',
)

print('saved', out)
print('multi_sheet_cases:')
for it in sorted(multi_cases, key=lambda x: x['chart_count'], reverse=True):
    print(it['idx'], it['case_id'][:12], it['chart_count'], it['title'][:70])
    for e in it['excel_infos']:
        print('  ', e)
