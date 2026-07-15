"""RTL HTML wrapper for CRM emails — Gmail-safe inline styles + table layout.

Font note: Gmail (web + app) ignores custom fonts and uses Tahoma/Arial.
Hosted @font-face + media queries work in Apple Mail, iOS Mail, and some
Android clients. Set WEB_PUBLIC_URL so Vazir loads from /static/fonts/.
"""
from __future__ import annotations

import base64
import html
import os
import re
from pathlib import Path

LAYOUT_MARKER = "<!-- enamad-email-layout -->"

_SCRIPT_DIR = Path(__file__).resolve().parent
_FONT_PATH = _SCRIPT_DIR / "static" / "fonts" / "Vazir-Regular.woff2"
_FONT_B64: str | None = None

_FONT_STACK = "Vazir,Tahoma,Arial,'Segoe UI',sans-serif"
_BLOCK = (
    f"direction:rtl;text-align:right;font-family:{_FONT_STACK};"
    "font-size:16px;line-height:1.95;color:#1f2937;"
)
_P_STYLE = f"margin:0 0 1em 0;{_BLOCK}"
_LIST_STYLE = f"margin:0 0 1em 0;padding-right:1.5em;padding-left:0;{_BLOCK}"


def _public_font_url() -> str:
    base = (os.environ.get("WEB_PUBLIC_URL") or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/static/fonts/Vazir-Regular.woff2"


def _head_styles() -> str:
    """@font-face (non-Gmail clients) + mobile spacing (Gmail keeps @media)."""
    sources: list[str] = []
    url = _public_font_url()
    if url:
        sources.append(f"url('{url}') format('woff2')")
    global _FONT_B64
    if _FONT_B64 is None:
        if _FONT_PATH.is_file():
            _FONT_B64 = base64.b64encode(_FONT_PATH.read_bytes()).decode("ascii")
        else:
            _FONT_B64 = ""
    if _FONT_B64:
        sources.append(
            f"url(data:font/woff2;base64,{_FONT_B64}) format('woff2')"
        )

    font_face = ""
    if sources:
        src = ",\n    ".join(sources)
        font_face = (
            "@font-face {\n"
            "  font-family: 'Vazir';\n"
            f"  src: {src};\n"
            "  font-weight: normal;\n"
            "  font-style: normal;\n"
            "}\n"
        )

    return f"""<style type="text/css">
{font_face}
@media only screen and (max-width: 620px) {{
  .email-content-cell {{
    padding: 20px 16px !important;
    font-size: 16px !important;
    line-height: 2 !important;
  }}
  .email-header-cell {{
    padding: 20px 16px !important;
    font-size: 17px !important;
  }}
  .benefits-box td {{
    padding: 28px 20px !important;
    line-height: 2.4 !important;
    font-size: 16px !important;
  }}
}}
</style>"""


def _looks_like_html(text: str) -> bool:
    return bool(re.search(r"<[a-z][\s\S]*>", text, re.IGNORECASE))


def _merge_rtl_into_style(style: str) -> str:
    style = (style or "").strip().rstrip(";")
    if "direction" not in style:
        style = f"{style};direction:rtl" if style else "direction:rtl"
    if "text-align" not in style:
        style = f"{style};text-align:right"
    if "font-family" not in style:
        style = f"{style};font-family:{_FONT_STACK}"
    return style


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

    for tag, sty in (("p", _P_STYLE), ("ul", _LIST_STYLE), ("ol", _LIST_STYLE), ("li", _BLOCK)):
        fragment = re.sub(
            rf"<{tag}([^>]*)>",
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
        elif "text-align" not in attrs:
            attrs += ' style="direction:rtl;text-align:right;"'
        return f"<td{attrs}>"

    fragment = re.sub(r"<td([^>]*)>", _fix_td, fragment, flags=re.I)
    return fragment


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
    return "".join(parts) or f'<p dir="rtl" style="{_P_STYLE}"></p>'


def wrap_email_html(content: str) -> str:
    cell_style = (
        f"padding:28px 32px;direction:rtl;text-align:right;"
        f"font-family:{_FONT_STACK};font-size:16px;line-height:1.95;color:#1f2937;"
    )
    return f"""<!DOCTYPE html>
<html dir="rtl" lang="fa" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
{LAYOUT_MARKER}
{_head_styles()}
</head>
<body style="margin:0;padding:0;background-color:#f4f4f5;font-family:{_FONT_STACK};">
<table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" dir="rtl" style="background-color:#f4f4f5;">
<tr>
<td align="center" style="padding:16px 8px;">
<table role="presentation" border="0" cellpadding="0" cellspacing="0" width="600" dir="rtl" style="max-width:600px;width:100%;background-color:#ffffff;border-radius:8px;">
<tr>
<td class="email-content-cell" dir="rtl" align="right" style="{cell_style}">
{content}
</td>
</tr>
</table>
</td>
</tr>
</table>
</body>
</html>"""


def prepare_email_html(body: str) -> str:
    body = (body or "").strip()
    if not body:
        return wrap_email_html(f'<p dir="rtl" style="{_P_STYLE}"></p>')
    if LAYOUT_MARKER in body:
        return body
    if _looks_like_html(body):
        content = _enhance_html_fragment(body)
    else:
        content = plain_text_to_html(body)
    return wrap_email_html(content)
