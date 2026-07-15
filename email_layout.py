"""RTL HTML wrapper for CRM emails (Vazir font, right-aligned Persian text)."""
from __future__ import annotations

import base64
import html
import re
from pathlib import Path

LAYOUT_MARKER = "<!-- enamad-email-layout -->"
_FONT_B64: str | None = None

_SCRIPT_DIR = Path(__file__).resolve().parent
_FONT_PATH = _SCRIPT_DIR / "static" / "fonts" / "Vazir-Regular.woff2"


def _font_face_css() -> str:
    global _FONT_B64
    if _FONT_B64 is None:
        if _FONT_PATH.is_file():
            _FONT_B64 = base64.b64encode(_FONT_PATH.read_bytes()).decode("ascii")
        else:
            _FONT_B64 = ""
    if not _FONT_B64:
        return ""
    return (
        "@font-face {\n"
        "  font-family: 'Vazir';\n"
        f"  src: url(data:font/woff2;charset=utf-8;base64,{_FONT_B64}) format('woff2');\n"
        "  font-weight: normal;\n"
        "  font-style: normal;\n"
        "}\n"
    )


def _looks_like_html(text: str) -> bool:
    return bool(re.search(r"<[a-z][\s\S]*>", text, re.IGNORECASE))


def plain_text_to_html(text: str) -> str:
    """Turn plain-text template body into simple HTML paragraphs."""
    text = (text or "").strip()
    if not text:
        return "<p></p>"
    blocks = re.split(r"\n\s*\n", text)
    parts: list[str] = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        escaped = html.escape(block).replace("\n", "<br>\n")
        parts.append(f'<p style="margin:0 0 1em 0;">{escaped}</p>')
    return "".join(parts) or "<p></p>"


def wrap_email_html(content: str) -> str:
    """Wrap email fragment in a full RTL HTML document with Vazir."""
    font_css = _font_face_css()
    return f"""<!DOCTYPE html>
<html dir="rtl" lang="fa">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{LAYOUT_MARKER}
<style>
{font_css}
body {{
  margin: 0;
  padding: 0;
  background: #f4f4f5;
  direction: rtl;
  text-align: right;
}}
.email-wrap {{
  max-width: 600px;
  margin: 24px auto;
  padding: 24px 28px;
  background: #ffffff;
  font-family: Vazir, Tahoma, 'Segoe UI', Arial, sans-serif;
  font-size: 15px;
  line-height: 1.9;
  color: #1f2937;
  direction: rtl;
  text-align: right;
}}
.email-wrap p {{ margin: 0 0 1em 0; }}
.email-wrap ul, .email-wrap ol {{
  padding-right: 1.5em;
  padding-left: 0;
  margin: 0 0 1em 0;
}}
.email-wrap a {{ color: #2563eb; }}
</style>
</head>
<body>
<div class="email-wrap">
{content}
</div>
</body>
</html>"""


def prepare_email_html(body: str) -> str:
    """Normalize template body to a styled RTL HTML email."""
    body = (body or "").strip()
    if not body:
        return wrap_email_html("<p></p>")
    if LAYOUT_MARKER in body:
        return body
    if _looks_like_html(body):
        content = body
    else:
        content = plain_text_to_html(body)
    return wrap_email_html(content)
