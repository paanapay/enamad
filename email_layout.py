"""RTL HTML wrapper for CRM emails — Gmail-safe inline styles + table layout.

Gmail (web/app) strips @font-face and never loads remote/custom fonts.
What works in Gmail is only a system font stack: if Vazir/IRANSans is
installed on the device it is used; otherwise Tahoma (good Persian glyphs).

We therefore:
  - never embed fonts as base64 (bloats the message; Gmail ignores it anyway)
  - optionally keep a hosted @font-face URL for Apple Mail / iOS Mail
  - put a Roocket-style font stack on body/td/p with !important
"""
from __future__ import annotations

import html
import os
import re
from pathlib import Path

LAYOUT_MARKER = "<!-- enamad-email-layout -->"

# Match Persian marketing emails that render well across clients.
# Gmail will pick the first family that exists on the device, else Tahoma.
_FONT_STACK = (
    "vazirmatn, Vazir, iranyekanBakh, IRANSans, 'B Yekan', "
    "Tahoma, Arial, sans-serif"
)
_FONT_STACK_CSS = (
    "vazirmatn, 'Vazir', 'iranyekanBakh', 'IRANSans', 'B Yekan', "
    "Tahoma, Arial, sans-serif"
)

_BLOCK = (
    f"direction:rtl;text-align:right;"
    f"font-family:{_FONT_STACK} !important;"
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
    """Client resets + optional hosted @font-face (Apple Mail only; not Gmail)."""
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
}}
body, table, td, a, p, li, div {{
  font-family: {_FONT_STACK_CSS} !important;
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
    # Always force our email font stack (CKEditor / paste may set something else).
    style = re.sub(
        r"(?i)(?:^|;)\s*font-family\s*:[^;]*",
        "",
        style,
    ).strip().strip(";").strip()
    if "direction" not in style:
        style = f"{style};direction:rtl" if style else "direction:rtl"
    if "text-align" not in style:
        style = f"{style};text-align:right"
    style = f"{style};font-family:{_FONT_STACK} !important"
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

    for tag, sty in (
        ("p", _P_STYLE),
        ("div", _BLOCK),
        ("span", _BLOCK),
        ("a", _BLOCK),
        ("h1", _BLOCK),
        ("h2", _BLOCK),
        ("h3", _BLOCK),
        ("ul", _LIST_STYLE),
        ("ol", _LIST_STYLE),
        ("li", _BLOCK),
    ):
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
        else:
            attrs += f' style="{_BLOCK}"'
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
    """Minimal RTL shell: font stack + alignment (no card chrome)."""
    cell_style = _BLOCK
    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title></title>
{LAYOUT_MARKER}
{_head_styles()}
</head>
<body style="margin:0;padding:0;height:100% !important;width:100% !important;font-family:{_FONT_STACK} !important;">
<table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" dir="rtl" style="border-collapse:collapse;">
<tr>
<td dir="rtl" align="right" style="{cell_style}">
{content}
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
