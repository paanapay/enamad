"""RTL HTML wrapper for CRM emails — Gmail-safe inline styles + table layout.

Gmail strips @font-face; we only declare a system font stack (Vazir/Tahoma).

Critical: keep MIME lines under ~900 bytes. Overlong lines get broken by
SMTP relays and show up as mid-word spaces (س ریع) and broken tags (< strong>).
"""
from __future__ import annotations

import html
import os
import re
from pathlib import Path

LAYOUT_MARKER = "<!-- enamad-email-layout -->"

_FONT_STACK = (
    "Tahoma, Arial, 'Segoe UI', vazirmatn, Vazir, IRANSans, sans-serif"
)
_FONT_STACK_CSS = (
    "Tahoma, Arial, 'Segoe UI', vazirmatn, 'Vazir', 'IRANSans', sans-serif"
)

_BLOCK = (
    f"direction:rtl;text-align:right;font-family:{_FONT_STACK};"
    "font-size:16px;line-height:1.8;color:#1f2937;"
    "letter-spacing:0;word-spacing:normal;"
)
_P_STYLE = f"margin:0 0 1em 0;{_BLOCK}"
_LIST_STYLE = f"margin:0 0 1em 0;padding-right:1.5em;padding-left:0;{_BLOCK}"

# Soft hyphen / odd Unicode spaces that CKEditor/Word sometimes insert.
# Keep ZWNJ (U+200C) — needed for correct Persian (راه‌اندازی).
_BAD_CHARS_RE = re.compile("[\u00ad\u200b\u200e\u200f\ufeff]")


def _public_font_url() -> str:
    base = (os.environ.get("WEB_PUBLIC_URL") or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/static/fonts/Vazir-Regular.woff2"


def _head_styles() -> str:
    """Client resets + optional hosted @font-face (Apple Mail; not Gmail)."""
    url = _public_font_url()
    font_face = ""
    if url:
        font_face = (
            "@font-face {\n"
            "  font-family: 'Vazir';\n"
            f"  src: url('{url}') format('woff2');\n"
            "  font-weight: normal;\n"
            "  font-style: normal;\n"
            "  mso-font-alt: 'Tahoma';\n"
            "}\n"
        )

    return f"""<style type="text/css">
{font_face}
body, table, td, a, p, li, div {{
  -webkit-text-size-adjust: 100%;
  -ms-text-size-adjust: 100%;
  font-family: {_FONT_STACK_CSS};
  letter-spacing: 0;
  word-spacing: normal;
}}
table, td {{
  mso-table-lspace: 0pt;
  mso-table-rspace: 0pt;
}}
</style>"""


def _looks_like_html(text: str) -> bool:
    return bool(re.search(r"<[a-z][\s\S]*>", text, re.IGNORECASE))


def _merge_rtl_into_style(style: str) -> str:
    style = (style or "").strip().rstrip(";")
    style = re.sub(
        r"(?i)(?:^|;)\s*font-family\s*:[^;]*",
        "",
        style,
    ).strip().strip(";").strip()
    if "direction" not in style:
        style = f"{style};direction:rtl" if style else "direction:rtl"
    if "text-align" not in style:
        style = f"{style};text-align:right"
    style = f"{style};font-family:{_FONT_STACK}"
    return style


def _open_tag_pattern(tag: str) -> str:
    """Match <tag> or <tag ...> but never <tagX> (e.g. <a> ≠ <strong>/<abbr>)."""
    return rf"<{tag}(\s[^>]*)?>"


def _enhance_html_fragment(fragment: str) -> str:
    def _open_tag(tag: str, default_style: str, m: re.Match) -> str:
        attrs = m.group(1) or ""
        if "dir=" not in attrs.lower():
            attrs += ' dir="rtl"'
        style_m = re.search(r'style="([^"]*)"', attrs, re.I)
        if style_m:
            merged = _merge_rtl_into_style(style_m.group(1))
            attrs = attrs[: style_m.start()] + f'style="{merged}"' + attrs[style_m.end() :]
        else:
            attrs += f' style="{default_style}"'
        return f"<{tag}{attrs}>"

    # Only structural tags — leave strong/b/em/br/img alone.
    for tag, sty in (
        ("p", _P_STYLE),
        ("ul", _LIST_STYLE),
        ("ol", _LIST_STYLE),
        ("li", _BLOCK),
    ):
        fragment = re.sub(
            _open_tag_pattern(tag),
            lambda m, t=tag, s=sty: _open_tag(t, s, m),
            fragment,
            flags=re.I,
        )

    def _fix_td(m: re.Match) -> str:
        attrs = m.group(1) or ""
        if "align=" not in attrs.lower():
            attrs += ' align="right"'
        if "dir=" not in attrs.lower():
            attrs += ' dir="rtl"'
        style_m = re.search(r'style="([^"]*)"', attrs, re.I)
        if style_m:
            merged = _merge_rtl_into_style(style_m.group(1))
            attrs = attrs[: style_m.start()] + f'style="{merged}"' + attrs[style_m.end() :]
        else:
            attrs += f' style="{_BLOCK}"'
        return f"<td{attrs}>"

    fragment = re.sub(r"<td(\s[^>]*)?>", _fix_td, fragment, flags=re.I)
    return fragment


def _break_long_lines(html_text: str, limit: int = 800) -> str:
    """Fold long lines without ever splitting inside a word/tag name.

    A newline inside Persian text becomes a visible space in HTML
    (سریع → سر یع). Only break after '>' or at existing whitespace.
    If a token is longer than limit (rare), leave it — quoted-printable
    will fold the MIME line safely.
    """
    out: list[str] = []
    for line in html_text.splitlines() or [html_text]:
        while len(line.encode("utf-8")) > limit:
            chunk = line
            # Budget by characters first, then walk back to a safe cut.
            approx = min(len(chunk), max(40, limit // 2))
            window = chunk[:approx]
            cut = None
            for sep in (">", " ", "\t"):
                idx = window.rfind(sep)
                if idx >= 20:
                    cut = idx + 1
                    break
            if cut is None:
                # No safe point — stop folding this line.
                break
            out.append(chunk[:cut].rstrip(" \t"))
            line = chunk[cut:].lstrip(" \t")
            if not line:
                break
        if line:
            out.append(line)
    return "\n".join(out)


def _normalize_fragment(fragment: str) -> str:
    fragment = _BAD_CHARS_RE.sub("", fragment or "")
    # Put block tags on their own lines — keeps SMTP lines short and source readable.
    fragment = re.sub(r"<(p|div|ul|ol|li|tr|td|table|h[1-6]|br)(\s|>)", r"\n<\1\2", fragment, flags=re.I)
    fragment = re.sub(r"</(p|div|ul|ol|li|tr|td|table|h[1-6])>", r"</\1>\n", fragment, flags=re.I)
    return fragment.strip()


def plain_text_to_html(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return f'<p dir="rtl" style="{_P_STYLE}"></p>'
    blocks = re.split(r"\n\s*\n", text)
    parts: list[str] = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        escaped = html.escape(block).replace("\n", "<br>\n")
        parts.append(f'<p dir="rtl" style="{_P_STYLE}">{escaped}</p>')
    return "\n".join(parts) or f'<p dir="rtl" style="{_P_STYLE}"></p>'


def wrap_email_html(content: str) -> str:
    """Minimal RTL shell: font stack + alignment."""
    cell_style = _BLOCK
    raw = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title></title>
{LAYOUT_MARKER}
{_head_styles()}
</head>
<body style="margin:0;padding:0;width:100%;font-family:{_FONT_STACK};">
<table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" dir="rtl" style="border-collapse:collapse;">
<tr>
<td dir="rtl" align="right" style="{cell_style}">
{content}
</td>
</tr>
</table>
</body>
</html>"""
    return _break_long_lines(raw)


def prepare_email_html(body: str) -> str:
    body = _BAD_CHARS_RE.sub("", (body or "").strip())
    if not body:
        return wrap_email_html(f'<p dir="rtl" style="{_P_STYLE}"></p>')
    if LAYOUT_MARKER in body:
        return _break_long_lines(body)
    if _looks_like_html(body):
        content = _enhance_html_fragment(_normalize_fragment(body))
    else:
        content = plain_text_to_html(body)
    return wrap_email_html(content)
