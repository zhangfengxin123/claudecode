#!/usr/bin/env python3
"""
Build full-report pages for A/B rerun case.

Input directory layout (example):
  runs/ab_no_description_xxx/
    meta.json
    original_document.json
    new_document.json
    items/

Outputs:
  items/report_full_A_capture.html
  items/report_full_B_capture.html
  items/report_full_A_zh.html
  items/report_full_B_zh.html
  items/report_full_bundle.html
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import ssl
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib import error, request

BASE_PUBLIC_API = "https://pardusai.org/v1/api/public/project"
DEFAULT_ENV_FILE = Path(".codex_exe/.env.llm.local")


def esc(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def get_textish(obj: Any) -> str:
    if isinstance(obj, str):
        return obj
    if not isinstance(obj, dict):
        return "" if obj is None else str(obj)
    for k in ("content", "Content", "text", "Text"):
        v = obj.get(k)
        if isinstance(v, str):
            return v
    return ""


def load_env_file(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    out: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def should_translate(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    if t.startswith("http://") or t.startswith("https://"):
        return False
    # Keep technical identifiers as-is (file names, compact codes).
    if re.fullmatch(r"[A-Za-z0-9_.:/-]{1,64}", t):
        return False
    # Translate whenever there is natural-language Latin text, including mixed zh/en.
    return bool(re.search(r"[A-Za-z]{2,}", t))


def extract_message_content(payload: Dict[str, Any]) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
                if isinstance(content, list):
                    parts: List[str] = []
                    for item in content:
                        if isinstance(item, str):
                            parts.append(item)
                            continue
                        if isinstance(item, dict):
                            txt = item.get("text")
                            if isinstance(txt, str):
                                parts.append(txt)
                    merged = "\n".join(x.strip() for x in parts if x and x.strip()).strip()
                    if merged:
                        return merged
            text = first.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""


class OpenAITranslator:
    def __init__(
        self,
        *,
        enabled: bool,
        base_url: str,
        model: str,
        api_key: str,
        timeout: int = 90,
        insecure: bool = False,
        batch_size: int = 20,
    ) -> None:
        self.enabled = enabled and bool(base_url and model and api_key)
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.insecure = insecure
        self.batch_size = max(1, batch_size)
        self.cache: Dict[str, str] = {}
        self.calls = 0
        self.failed_calls = 0

    @classmethod
    def from_env(
        cls,
        *,
        env_file: Path,
        enabled: bool,
        timeout: int,
        insecure: bool,
        batch_size: int,
    ) -> "OpenAITranslator":
        values = load_env_file(env_file)
        base_url = os.getenv("AB_TRANSLATE_BASE_URL") or values.get("AB_TRANSLATE_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL") or values.get("DEEPSEEK_BASE_URL") or ""
        model = os.getenv("AB_TRANSLATE_MODEL") or values.get("AB_TRANSLATE_MODEL") or os.getenv("DEEPSEEK_MODEL") or values.get("DEEPSEEK_MODEL") or ""
        api_key = os.getenv("AB_TRANSLATE_API_KEY") or values.get("AB_TRANSLATE_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or values.get("DEEPSEEK_API_KEY") or ""
        return cls(
            enabled=enabled,
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout=timeout,
            insecure=insecure,
            batch_size=batch_size,
        )

    def _endpoint(self) -> str:
        return self.base_url + "/chat/completions"

    @staticmethod
    def _english_word_count(text: str) -> int:
        return len(re.findall(r"[A-Za-z]{3,}", text or ""))

    def _needs_second_pass(self, src: str, dst: str) -> bool:
        src_en = self._english_word_count(src)
        dst_en = self._english_word_count(dst)
        if src_en < 3:
            return False
        # If most English words remain, try one more strict pass.
        return dst_en >= max(3, int(src_en * 0.65))

    def _call_api_once(self, texts: List[str]) -> Optional[List[str]]:
        if not self.enabled:
            return list(texts)

        payload = {
            "model": self.model,
            "temperature": 0.2,
            "max_tokens": min(6000, max(1200, sum(len(x) for x in texts) * 3)),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是专业翻译。将输入文本逐条翻译成简体中文。"
                        "输出必须是 JSON 对象，格式严格为: {\"translations\":[...]}，"
                        "数量必须与输入一致，不要输出任何额外解释。"
                        "除品牌名、产品名、文件名、缩写外，不要保留英文句子。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({"texts": texts}, ensure_ascii=False),
                },
            ],
        }

        req = request.Request(
            self._endpoint(),
            method="POST",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

        self.calls += 1
        ctx = ssl._create_unverified_context() if self.insecure else None
        try:
            with request.urlopen(req, timeout=self.timeout, context=ctx) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            content = extract_message_content(data)
            if not content:
                self.failed_calls += 1
                return None

            parsed: Optional[Dict[str, Any]] = None
            try:
                parsed = json.loads(content)
            except Exception:
                m = re.search(r"\{[\s\S]*\}", content)
                if m:
                    try:
                        parsed = json.loads(m.group(0))
                    except Exception:
                        parsed = None

            if not isinstance(parsed, dict):
                self.failed_calls += 1
                return None
            rows = parsed.get("translations")
            if not isinstance(rows, list) or len(rows) != len(texts):
                self.failed_calls += 1
                return None
            out: List[str] = []
            for i, src in enumerate(texts):
                v = rows[i]
                cand = v.strip() if isinstance(v, str) and v.strip() else src
                if self._needs_second_pass(src, cand):
                    retry = self._call_plain_once(cand)
                    if isinstance(retry, str) and retry.strip():
                        cand = retry.strip()
                out.append(cand)
            return out
        except (error.HTTPError, error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
            self.failed_calls += 1
            return None
        except Exception:
            self.failed_calls += 1
            return None

    def _call_api(self, texts: List[str]) -> List[str]:
        out = self._call_api_once(texts)
        if out is not None:
            return out
        # Fallback: split batch to minimize total untranslated content.
        if len(texts) == 1:
            one = self._call_plain_once(texts[0])
            return [one if one else texts[0]]
        mid = len(texts) // 2
        left = self._call_api(texts[:mid])
        right = self._call_api(texts[mid:])
        return left + right

    def _call_plain_once(self, text: str) -> Optional[str]:
        if not self.enabled:
            return text

        payload = {
            "model": self.model,
            "temperature": 0.2,
            "max_tokens": min(2400, max(300, len(text) * 3)),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是专业翻译。将用户输入翻译成简体中文，只输出翻译结果本身，"
                        "不要解释，不要加引号。除品牌名、产品名、文件名、缩写外，不要保留英文句子。"
                    ),
                },
                {"role": "user", "content": text},
            ],
        }
        req = request.Request(
            self._endpoint(),
            method="POST",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        self.calls += 1
        ctx = ssl._create_unverified_context() if self.insecure else None
        try:
            with request.urlopen(req, timeout=self.timeout, context=ctx) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            content = extract_message_content(data)
            if isinstance(content, str) and content.strip():
                return content.strip()
            self.failed_calls += 1
            return None
        except Exception:
            self.failed_calls += 1
            return None

    def warm_cache(self, texts: Iterable[str]) -> None:
        todo: List[str] = []
        for t in texts:
            s = str(t or "").strip()
            if not s:
                continue
            if s in self.cache:
                continue
            if not should_translate(s):
                self.cache[s] = s
                continue
            todo.append(s)

        if not todo:
            return

        # Stable order with dedup to reduce API calls.
        uniq: List[str] = []
        seen: set[str] = set()
        for t in todo:
            if t in seen:
                continue
            seen.add(t)
            uniq.append(t)

        for i in range(0, len(uniq), self.batch_size):
            batch = uniq[i : i + self.batch_size]
            translated = self._call_api(batch)
            for src, dst in zip(batch, translated):
                self.cache[src] = dst
            time.sleep(0.1)

    def translate(self, text: str) -> str:
        s = str(text or "")
        if not s.strip():
            return s
        if s in self.cache:
            return self.cache[s]
        if not should_translate(s):
            self.cache[s] = s
            return s
        self.warm_cache([s])
        return self.cache.get(s, s)

    def stats(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "cached_items": len(self.cache),
            "api_calls": self.calls,
            "failed_calls": self.failed_calls,
        }


TranslateFn = Callable[[str], str]


def apply_translate(text: str, translate: Optional[TranslateFn]) -> str:
    if not translate:
        return text
    try:
        return translate(text)
    except Exception:
        return text


def inline_html(span: Any, translate: Optional[TranslateFn] = None) -> str:
    if isinstance(span, str):
        return esc(apply_translate(span, translate))
    if not isinstance(span, dict):
        return esc(apply_translate(str(span), translate))
    text = apply_translate(get_textish(span), translate)
    t = str(span.get("type", "")).lower()
    if t == "bold":
        return f"<strong>{esc(text)}</strong>"
    if t == "italic":
        return f"<em>{esc(text)}</em>"
    return esc(text)


def inline_to_text(seq: Any) -> str:
    if not isinstance(seq, list):
        return "" if seq is None else str(seq)
    parts: List[str] = []
    for item in seq:
        if isinstance(item, str):
            parts.append(item)
            continue
        if isinstance(item, dict):
            txt = get_textish(item)
            if txt:
                parts.append(txt)
    return "".join(parts)


def join_inline(seq: Any, translate: Optional[TranslateFn] = None) -> str:
    if not isinstance(seq, list):
        return esc(apply_translate(str(seq), translate))
    return "".join(inline_html(x, translate) for x in seq)


def clean_rel_path(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    x = v.strip()
    if x.startswith("./"):
        x = x[2:]
    x = x.splitlines()[0].strip()
    return x or None


def asset_url(project_id: str, rel_path: str) -> str:
    name = Path(rel_path).name
    return f"{BASE_PUBLIC_API}/{project_id}/assets/{name}"


def render_list_items(items: Any, translate: Optional[TranslateFn] = None) -> str:
    if not isinstance(items, list):
        return ""
    rows: List[str] = []
    for item in items:
        if not isinstance(item, dict):
            rows.append(f"<li>{esc(apply_translate(str(item), translate))}</li>")
            continue
        c = item.get("content")
        if c is None:
            c = item.get("Content")
        if isinstance(c, list):
            text = inline_to_text(c)
            rows.append(f"<li>{esc(apply_translate(text, translate))}</li>")
        elif isinstance(c, str):
            rows.append(f"<li>{esc(apply_translate(c, translate))}</li>")
        else:
            rows.append(f"<li>{esc(apply_translate(str(c), translate))}</li>")
    return "".join(rows)


def render_table(node: Dict[str, Any], translate: Optional[TranslateFn] = None) -> str:
    headers = node.get("headers") if isinstance(node.get("headers"), list) else []
    rows = node.get("rows") if isinstance(node.get("rows"), list) else []
    head_html = "".join(f"<th>{esc(apply_translate(str(h), translate))}</th>" for h in headers)
    body_html: List[str] = []
    for r in rows:
        if isinstance(r, list):
            tds = "".join(f"<td>{esc(apply_translate(str(v), translate))}</td>" for v in r)
            body_html.append(f"<tr>{tds}</tr>")
    return (
        "<div class='table-wrap'><table>"
        f"<thead><tr>{head_html}</tr></thead>"
        f"<tbody>{''.join(body_html)}</tbody></table></div>"
    )


def collect_inline_strings(seq: Any) -> List[str]:
    out: List[str] = []
    if not isinstance(seq, list):
        return out
    for item in seq:
        if isinstance(item, str):
            out.append(item)
            continue
        if isinstance(item, dict):
            t = get_textish(item)
            if t:
                out.append(t)
    return out


def collect_doc_strings(doc: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    children = doc.get("document", {}).get("children", [])
    if not isinstance(children, list):
        return out

    for node in children:
        if not isinstance(node, dict):
            out.append(str(node))
            continue
        t = str(node.get("type", "")).lower()
        if t == "heading":
            txt = get_textish(node)
            if txt:
                out.append(txt)
            continue
        if t == "paragraph":
            c = node.get("children")
            if isinstance(c, list):
                text = inline_to_text(c)
                if text:
                    out.append(text)
            else:
                txt = get_textish(node)
                if txt:
                    out.append(txt)
            continue
        if t == "list":
            items = node.get("items") if isinstance(node.get("items"), list) else []
            for it in items:
                if not isinstance(it, dict):
                    out.append(str(it))
                    continue
                c = it.get("content")
                if c is None:
                    c = it.get("Content")
                if isinstance(c, list):
                    text = inline_to_text(c)
                    if text:
                        out.append(text)
                elif isinstance(c, str):
                    out.append(c)
            continue
        if t == "table":
            headers = node.get("headers") if isinstance(node.get("headers"), list) else []
            rows = node.get("rows") if isinstance(node.get("rows"), list) else []
            out.extend(str(h) for h in headers)
            for r in rows:
                if isinstance(r, list):
                    out.extend(str(v) for v in r)
            continue
        if t == "question":
            qs = node.get("questions") if isinstance(node.get("questions"), list) else []
            out.extend(str(q) for q in qs)
            continue
        if t in {"plot", "svg", "yaml", "horizontalrule"}:
            continue

        txt = get_textish(node)
        if txt:
            out.append(txt)
        c = node.get("children")
        if isinstance(c, list):
            text = inline_to_text(c)
            if text:
                out.append(text)

    return out


def load_case_fig_caches(run_dir: Path) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    Parse embedded figure arrays from case_ab_*.html:
      const A = [...];
      const B = [...];
    Returns: (a_cache, b_cache), keyed by asset filename.
    """
    items_dir = run_dir / "items"
    candidates = sorted(items_dir.glob("case_ab_*.html"))
    if not candidates:
        return {}, {}

    text = candidates[0].read_text(encoding="utf-8")

    def parse_array(var_name: str) -> List[Dict[str, Any]]:
        m = re.search(rf"\bconst\s+{re.escape(var_name)}\s*=\s*\[", text)
        if not m:
            return []

        start = m.end() - 1
        depth = 0
        in_string: Optional[str] = None
        escaped = False
        end = -1

        for i in range(start, len(text)):
            ch = text[i]
            if in_string is not None:
                if escaped:
                    escaped = False
                    continue
                if ch == "\\":
                    escaped = True
                    continue
                if ch == in_string:
                    in_string = None
                continue

            if ch in {'"', "'"}:
                in_string = ch
                continue

            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    end = i
                    break

        if end < 0:
            return []

        payload = text[start : end + 1]
        try:
            arr = json.loads(payload)
            return arr if isinstance(arr, list) else []
        except Exception:
            return []

    def to_cache(arr: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for item in arr:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            fig = item.get("fig")
            if isinstance(name, str) and isinstance(fig, dict):
                out[name] = fig
        return out

    return to_cache(parse_array("A")), to_cache(parse_array("B"))


class DocRenderer:
    def __init__(
        self,
        project_id: str,
        fig_cache: Dict[str, Dict[str, Any]],
        *,
        translate_fn: Optional[TranslateFn] = None,
    ) -> None:
        self.project_id = project_id
        self.fig_cache = fig_cache
        self.translate_fn = translate_fn
        self.fig_payload: Dict[str, Dict[str, Any]] = {}
        self.plot_counter = 0

    def _t(self, text: Any) -> str:
        return apply_translate("" if text is None else str(text), self.translate_fn)

    def _register_fig(self, asset_name: str) -> Optional[str]:
        fig = self.fig_cache.get(asset_name)
        if not isinstance(fig, dict):
            return None
        self.plot_counter += 1
        key = f"fig_{self.plot_counter}_{re.sub(r'[^a-zA-Z0-9]+', '_', asset_name)}"
        self.fig_payload[key] = fig
        return key

    def render_plot_block(self, asset_name: str, title: Optional[str] = None) -> str:
        url = asset_url(self.project_id, asset_name)
        key = self._register_fig(asset_name)
        ttl = esc(title or asset_name)
        if key:
            plot_html = f"<div id='plot-{esc(key)}' class='plot' data-fig-key='{esc(key)}'></div>"
        else:
            plot_html = "<div class='plot'><p class='muted'>该图表未在缓存中捕获，点击下方 asset 查看原始数据。</p></div>"
        return (
            "<div class='plot-card'>"
            f"<div class='plot-meta'><code>{ttl}</code></div>"
            f"{plot_html}"
            f"<a class='asset' href='{esc(url)}' target='_blank' rel='noopener noreferrer'>asset</a>"
            "</div>"
        )

    def render_node(self, node: Any, idx: int) -> str:
        if not isinstance(node, dict):
            return f"<p>{esc(self._t(node))}</p>"

        t = str(node.get("type", "")).lower()

        if t == "heading":
            lvl = int(node.get("level", 2))
            lvl = 1 if lvl < 1 else (4 if lvl > 4 else lvl)
            return f"<h{lvl}>{esc(self._t(get_textish(node)))}</h{lvl}>"

        if t == "paragraph":
            children = node.get("children")
            if isinstance(children, list):
                return f"<p>{esc(self._t(inline_to_text(children)))}</p>"
            return f"<p>{esc(self._t(get_textish(node)))}</p>"

        if t == "list":
            tag = "ol" if bool(node.get("ordered")) else "ul"
            return f"<{tag}>{render_list_items(node.get('items'), self.translate_fn)}</{tag}>"

        if t == "table":
            return render_table(node, self.translate_fn)

        if t == "question":
            qs = node.get("questions") if isinstance(node.get("questions"), list) else []
            if not qs:
                return ""
            items = "".join(f"<li>{esc(self._t(q))}</li>" for q in qs)
            return f"<details class='qblock'><summary>分析问题</summary><ul>{items}</ul></details>"

        if t == "yaml":
            meta = node.get("metadata", "")
            return f"<details class='meta'><summary>YAML Metadata</summary><pre>{esc(meta)}</pre></details>"

        if t == "horizontalrule":
            return "<hr/>"

        if t == "plot":
            path = clean_rel_path(str(node.get("htmlPath") or node.get("code") or ""))
            if not path or not path.endswith(".json"):
                return ""
            name = Path(path).name
            return self.render_plot_block(name, name)

        if t == "svg":
            path = clean_rel_path(str(node.get("svgPath") or node.get("code") or ""))
            if not path:
                return ""
            url = asset_url(self.project_id, path)
            base = Path(path).name
            return (
                "<figure class='svg-fig'>"
                f"<img src='{esc(url)}' alt='{esc(base)}' loading='lazy'/>"
                f"<figcaption><a href='{esc(url)}' target='_blank' rel='noopener noreferrer'>{esc(base)}</a></figcaption>"
                "</figure>"
            )

        content = get_textish(node)
        children = node.get("children")
        if content and t in {"text", "bold", "italic"}:
            return f"<p>{inline_html(node, self.translate_fn)}</p>"
        if content:
            return f"<p>{esc(self._t(content))}</p>"
        if isinstance(children, list):
            return f"<p>{esc(self._t(inline_to_text(children)))}</p>"
        return ""

    def render_doc_body(self, doc: Dict[str, Any]) -> str:
        children = doc.get("document", {}).get("children", [])
        if not isinstance(children, list):
            return "<p class='muted'>no document body</p>"
        chunks: List[str] = []
        idx = 0
        for n in children:
            idx += 1
            h = self.render_node(n, idx)
            if h:
                chunks.append(h)
        return "\n".join(chunks)


def page_shell(title: str, subtitle: str, body_html: str, fig_payload: Dict[str, Dict[str, Any]]) -> str:
    fig_json = json.dumps(fig_payload, ensure_ascii=False)
    return f"""<!doctype html>
<html lang='zh-CN'>
<head>
<meta charset='utf-8'/>
<meta name='viewport' content='width=device-width, initial-scale=1'/>
<title>{esc(title)}</title>
<script src='https://cdn.plot.ly/plotly-2.35.2.min.js'></script>
<style>
  :root {{ --bg:#f6f8fc; --card:#fff; --line:#e5e7eb; --text:#111827; --muted:#6b7280; }}
  body {{ margin:0; font-family: Arial, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; background:var(--bg); color:var(--text); }}
  .wrap {{ max-width: 1120px; margin: 0 auto; padding: 20px; }}
  h1 {{ margin: 0 0 8px; font-size: 30px; }}
  h2 {{ margin: 24px 0 10px; font-size: 24px; border-bottom: 1px solid var(--line); padding-bottom: 6px; }}
  h3 {{ margin: 18px 0 8px; font-size: 20px; }}
  h4 {{ margin: 14px 0 6px; font-size: 17px; }}
  p, li {{ line-height: 1.68; font-size: 15px; }}
  .sub {{ color: var(--muted); margin-bottom: 16px; }}
  .card {{ background: var(--card); border:1px solid var(--line); border-radius: 12px; padding: 16px; margin: 12px 0; }}
  .plot-card {{ background:#fff; border:1px solid var(--line); border-radius: 10px; padding:10px; margin:10px 0; }}
  .plot {{ min-height: 420px; }}
  .plot-meta {{ color:var(--muted); font-size:12px; margin-bottom:6px; }}
  .asset {{ font-size: 12px; color:#2563eb; text-decoration: none; }}
  table {{ width:100%; border-collapse: collapse; font-size:14px; }}
  th,td {{ border:1px solid var(--line); padding:8px; text-align:left; vertical-align:top; }}
  th {{ background:#f3f4f6; }}
  .table-wrap {{ overflow-x:auto; }}
  .meta, .qblock {{ margin:10px 0; }}
  pre {{ background:#0b1020; color:#e5e7eb; padding:10px; border-radius:8px; overflow:auto; }}
  .svg-fig img {{ max-width: 100%; border: 1px solid var(--line); border-radius: 8px; background: #fff; }}
  .muted {{ color: var(--muted); }}
</style>
</head>
<body>
  <div class='wrap'>
    <h1>{esc(title)}</h1>
    <p class='sub'>{esc(subtitle)}</p>
    <div class='card'>
      {body_html}
    </div>
  </div>
<script>
(function() {{
  const FIG_PAYLOAD = {fig_json};
  const nodes = Array.from(document.querySelectorAll('.plot[data-fig-key]'));
  for (const el of nodes) {{
    const key = el.getAttribute('data-fig-key');
    const fig = FIG_PAYLOAD[key];
    if (!fig || typeof fig !== 'object') {{
      el.innerHTML = `<p class='muted'>图表数据缺失，请点击下方 asset 链接查看。</p>`;
      continue;
    }}
    try {{
      const data = Array.isArray(fig.data) ? fig.data : [];
      const layout = (fig.layout && typeof fig.layout === 'object') ? fig.layout : {{}};
      Plotly.newPlot(el, data, layout, {{responsive:true, displaylogo:false}});
    }} catch (e) {{
      el.innerHTML = `<p class='muted'>图表渲染失败，请点击下方 asset 链接查看。</p>`;
    }}
  }}
}})();
</script>
</body>
</html>
"""


def build_cn_page_from_doc(
    *,
    project_id: str,
    fig_cache: Dict[str, Dict[str, Any]],
    doc: Dict[str, Any],
    title: str,
    subtitle_note: str,
    translator: OpenAITranslator,
) -> str:
    if translator.enabled:
        translator.warm_cache(collect_doc_strings(doc))
    r = DocRenderer(project_id, fig_cache, translate_fn=translator.translate if translator.enabled else None)
    body_html = r.render_doc_body(doc)
    body_html += (
        "<hr/><p class='muted'>说明：该页面保持原报告结构与图表原位。"
        + ("文本已翻译为简体中文。" if translator.enabled else "未启用翻译时保留原文。")
        + "</p>"
    )
    return page_shell(
        title=title,
        subtitle=f"来源: /view/{project_id} ｜ {subtitle_note}",
        body_html=body_html,
        fig_payload=r.fig_payload,
    )


def render_bundle_html(out_path: Path, a_case_id: str, b_case_id: str) -> None:
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
  .pane iframe{{width:100%;height:1300px;border:1px solid var(--line);border-radius:8px;background:#fff}}
  .links a{{display:inline-block;margin-right:10px;margin-bottom:8px;text-decoration:none;color:#2563eb}}
  @media (max-width:1080px){{.compare-grid{{grid-template-columns:1fr}}}}
</style>
</head>
<body><div class='wrap'>
  <h1>完整报告抓取包（A/B）</h1>
  <p class='muted'>左侧 A（含 description）与右侧 B（去 description），均为简体中文对照页。</p>
  <div class='card'><h2>入口链接</h2><div class='links'>
      <a href='./report_full_A_zh.html' target='_blank'>A 中文版</a>
      <a href='./report_full_B_zh.html' target='_blank'>B 中文版</a>
      <a href='./report_full_A_capture.html' target='_blank'>A 原文抓取页</a>
      <a href='./report_full_B_capture.html' target='_blank'>B 原文抓取页</a>
      <a href='https://pardusai.org/view/{esc(a_case_id)}' target='_blank'>A 原站页</a>
      <a href='https://pardusai.org/view/{esc(b_case_id)}' target='_blank'>B 原站页</a>
  </div></div>
  <div class='card'><h2>左右对照（中文）</h2>
    <div class='compare-grid'>
      <section class='pane'><span class='tag tag-a'>A（含 description）</span><iframe src='./report_full_A_zh.html' title='A zh report'></iframe></section>
      <section class='pane'><span class='tag tag-b'>B（去 description）</span><iframe src='./report_full_B_zh.html' title='B zh report'></iframe></section>
    </div>
  </div>
</div></body></html>
"""
    out_path.write_text(s, encoding="utf-8")


def build(run_dir: Path, *, translator: OpenAITranslator) -> None:
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    pid_a = meta.get("template_case_id")
    pid_b = meta.get("rerun_case_id")
    if not pid_a or not pid_b:
        raise ValueError("meta.json missing template_case_id/rerun_case_id")

    d_a = json.loads((run_dir / "original_document.json").read_text(encoding="utf-8"))
    d_b = json.loads((run_dir / "new_document.json").read_text(encoding="utf-8"))

    cache_a, cache_b = load_case_fig_caches(run_dir)
    out_dir = run_dir / "items"
    out_dir.mkdir(parents=True, exist_ok=True)

    renderer_a = DocRenderer(pid_a, cache_a)
    body_a = renderer_a.render_doc_body(d_a)
    html_a = page_shell(
        title="A 完整报告抓取（原文 + 原位图表）",
        subtitle=f"来源: /view/{pid_a} ｜ 字体统一：Arial",
        body_html=body_a,
        fig_payload=renderer_a.fig_payload,
    )

    renderer_b = DocRenderer(pid_b, cache_b)
    body_b = renderer_b.render_doc_body(d_b)
    html_b = page_shell(
        title="B 完整报告抓取（原文 + 原位图表）",
        subtitle=f"来源: /view/{pid_b} ｜ 字体统一：Arial",
        body_html=body_b,
        fig_payload=renderer_b.fig_payload,
    )

    html_a_zh = build_cn_page_from_doc(
        project_id=pid_a,
        fig_cache=cache_a,
        doc=d_a,
        title="A 报告（中文对照版，原位图表）",
        subtitle_note="含 description",
        translator=translator,
    )
    html_b_zh = build_cn_page_from_doc(
        project_id=pid_b,
        fig_cache=cache_b,
        doc=d_b,
        title="B 报告（中文对照版，原位图表）",
        subtitle_note="去 description",
        translator=translator,
    )

    (out_dir / "report_full_A_capture.html").write_text(html_a, encoding="utf-8")
    (out_dir / "report_full_B_capture.html").write_text(html_b, encoding="utf-8")
    (out_dir / "report_full_A_zh.html").write_text(html_a_zh, encoding="utf-8")
    (out_dir / "report_full_B_zh.html").write_text(html_b_zh, encoding="utf-8")
    render_bundle_html(out_dir / "report_full_bundle.html", str(pid_a), str(pid_b))

    idx = run_dir / "index.html"
    if idx.exists():
        s = idx.read_text(encoding="utf-8")
        add = (
            "<hr/><h2>full report capture</h2><ul>"
            "<li><a href='items/report_full_A_zh.html'>report_full_A_zh.html</a></li>"
            "<li><a href='items/report_full_B_zh.html'>report_full_B_zh.html</a></li>"
            "<li><a href='items/report_full_bundle.html'>report_full_bundle.html</a></li>"
            "<li><a href='items/report_full_A_capture.html'>report_full_A_capture.html</a></li>"
            "<li><a href='items/report_full_B_capture.html'>report_full_B_capture.html</a></li>"
            "</ul>"
        )
        if "report_full_bundle.html" not in s:
            s = s.replace("</body>", add + "</body>")
            idx.write_text(s, encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    ap.add_argument("--no-translate", action="store_true", help="Disable LLM translation and keep original text.")
    ap.add_argument("--insecure", action="store_true", help="Disable TLS cert verification for translation API.")
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument("--batch-size", type=int, default=20)
    args = ap.parse_args()

    translator = OpenAITranslator.from_env(
        env_file=args.env_file,
        enabled=not args.no_translate,
        timeout=args.timeout,
        insecure=args.insecure,
        batch_size=args.batch_size,
    )
    build(args.run_dir, translator=translator)
    print(json.dumps({"run_dir": str(args.run_dir), "translator": translator.stats()}, ensure_ascii=False))
