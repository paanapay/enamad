"""Generic RTL email layout presets with a blue accent theme.

Presets are starting points only — admins replace placeholder text, links,
and contact info after applying. No product-specific copy is baked in.
"""
from __future__ import annotations

from typing import Any

# Placeholders admins should customize after applying a preset.
_SIG = """<p style="margin-top:20px;color:#475569;font-size:14px;">
وب‌سایت: <strong>example.com</strong><br>
تلفن: <strong>۰۲۱-۱۲۳۴۵۶۷۸</strong>
</p>
<p>با احترام<br><strong>تیم پشتیبانی</strong></p>"""

EMAIL_PRESETS: list[dict[str, str]] = [
    {
        "id": "letter-header",
        "title": "۱. نامه رسمی (هدر آبی)",
        "description": "شروع استاندارد با سلام شخصی‌سازی‌شده و هدر رنگی — مناسب اولین تماس",
        "subject": "{{owner_name}} عزیز، درباره {{business_name}}",
        "body": f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 20px;border-collapse:collapse;border-radius:12px;overflow:hidden;">
<tr><td style="background:linear-gradient(135deg,#1e40af 0%,#2563eb 100%);padding:20px 24px;color:#ffffff;text-align:right;font-size:17px;font-weight:bold;">
نام مجموعه شما
</td></tr></table>

<p>سلام <strong>{{{{owner_name}}}}</strong> عزیز،</p>

<p>با احترام به کسب‌وکار <strong>{{{{business_name}}}}</strong>
(دامنه: <strong>{{{{domain}}}}</strong>)، [متن پیام خود را اینجا بنویسید —
مثلاً معرفی کوتاه مجموعه و دلیل تماس].</p>

<p>در صورت تمایل به دریافت اطلاعات بیشتر، پاسخ این ایمیل را ارسال کنید
یا از راه‌های ارتباطی زیر با ما در تماس باشید.</p>

{_SIG}""",
    },
    {
        "id": "announcement",
        "title": "۲. اطلاع‌رسانی",
        "description": "اعلام خبر، به‌روزرسانی یا پیام مهم — باکس آبی برای تیتر",
        "subject": "اطلاع‌رسانی — {{business_name}}",
        "body": f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 18px;border-collapse:collapse;">
<tr><td style="background:#dbeafe;padding:16px 20px;border-radius:10px;border-right:4px solid #2563eb;text-align:right;">
<span style="font-size:16px;font-weight:bold;color:#1e40af;">عنوان اطلاعیه</span>
</td></tr></table>

<p>{{{{owner_name}}}} عزیز،</p>

<p>به اطلاع می‌رساند [خلاصه خبر یا موضوع اطلاع‌رسانی].</p>

<p>این پیام در ارتباط با کسب‌وکار <strong>{{{{business_name}}}}</strong>
در استان <strong>{{{{province}}}}</strong> / شهر <strong>{{{{city}}}}</strong> ارسال شده است.</p>

<p>[جزئیات بیشتر، تاریخ، شرایط یا نکات مهم را اینجا بنویسید.]</p>

{_SIG}""",
    },
    {
        "id": "bullet-box",
        "title": "۳. لیست نکات (باکس آبی)",
        "description": "چند نکته یا مزیت در کادر رنگی — برای معرفی خدمات یا خلاصه پیشنهاد",
        "subject": "پیشنهاد همکاری — {{domain}}",
        "body": f"""<p>سلام <strong>{{{{owner_name}}}}</strong> عزیز،</p>

<p>در ارتباط با <strong>{{{{business_name}}}}</strong> ({{{{domain}}}})،
خلاصه نکات زیر را به اطلاع می‌رسانیم:</p>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:16px 0;background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;border-collapse:collapse;">
<tr><td style="padding:16px 20px;text-align:right;color:#1e3a8a;line-height:1.9;">
<strong>نکات کلیدی:</strong><br>
• نکته اول — [متن خود را بنویسید]<br>
• نکته دوم — [متن خود را بنویسید]<br>
• نکته سوم — [متن خود را بنویسید]<br>
• نکته چهارم — [متن خود را بنویسید]
</td></tr></table>

<p>برای هماهنگی یا دریافت جزئیات بیشتر با ما تماس بگیرید.</p>

{_SIG}""",
    },
    {
        "id": "follow-up",
        "title": "۴. پیگیری تماس",
        "description": "یادآوری پیام یا تماس قبلی — لحن رسمی و کوتاه",
        "subject": "پیگیری — {{business_name}}",
        "body": f"""<p style="color:#1e40af;font-weight:bold;font-size:16px;margin:0 0 16px;border-bottom:2px solid #bfdbfe;padding-bottom:10px;">
پیگیری تماس قبلی
</p>

<p>{{{{owner_name}}}} عزیز،</p>

<p>پیرو پیام/تماس قبلی درباره <strong>{{{{business_name}}}}</strong>
(دامنه <strong>{{{{domain}}}}</strong>)، خواستیم بدانیم آیا فرصت بررسی
[موضوع پیگیری] را داشته‌اید؟</p>

<p>در صورت نیاز به توضیح بیشتر، خوشحال می‌شویم پاسخگوی شما باشیم.</p>

<p style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:12px 16px;font-size:14px;color:#475569;">
📞 ۰۲۱-۱۲۳۴۵۶۷۸<br>
🌐 example.com
</p>

<p>با تشکر<br>تیم پشتیبانی</p>""",
    },
    {
        "id": "cta-button",
        "title": "۵. پیام کوتاه + دکمه",
        "description": "متن خلاصه با دکمه آبی اقدام — لینک و متن دکمه قابل ویرایش",
        "subject": "{{owner_name}} عزیز — {{business_name}}",
        "body": f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 16px;border-collapse:collapse;">
<tr><td style="border-right:4px solid #2563eb;padding:4px 16px 4px 0;text-align:right;">
<span style="font-size:18px;font-weight:bold;color:#1e40af;">عنوان پیام</span>
</td></tr></table>

<p>{{{{owner_name}}}} عزیز،</p>

<p>[یک یا دو جمله خلاصه درباره موضوع پیام و ارتباط با {{{{business_name}}}}.]</p>

<table role="presentation" cellpadding="0" cellspacing="0" style="margin:22px 0;">
<tr><td style="border-radius:8px;background:#2563eb;text-align:center;">
<a href="https://example.com" style="display:inline-block;padding:13px 26px;color:#ffffff;text-decoration:none;font-weight:bold;font-size:15px;">متن دکمه</a>
</td></tr></table>

<p style="color:#64748b;font-size:14px;">سؤالی دارید؟ با ما در تماس باشید.</p>

{_SIG}""",
    },
    {
        "id": "minimal",
        "title": "۶. مینیمال (خط آبی)",
        "description": "ساده و فشرده — فقط عنوان، متن و امضا",
        "subject": "پیام برای {{domain}}",
        "body": f"""<p style="border-bottom:3px solid #2563eb;padding-bottom:10px;margin:0 0 18px;font-size:17px;font-weight:bold;color:#1e40af;">
نام مجموعه شما
</p>

<p>{{{{owner_name}}}} عزیز،</p>

<p>[متن اصلی پیام — ۲ تا ۴ جمله درباره {{{{business_name}}}} یا {{{{domain}}}}.]</p>

<p style="font-size:14px;color:#64748b;margin-top:20px;">
example.com · ۰۲۱-۱۲۳۴۵۶۷۸
</p>""",
    },
]


def list_email_presets() -> list[dict[str, Any]]:
    return [
        {
            "id": p["id"],
            "title": p["title"],
            "description": p["description"],
            "subject": p["subject"],
            "body": p["body"],
        }
        for p in EMAIL_PRESETS
    ]


def get_email_preset(preset_id: str) -> dict[str, str] | None:
    for p in EMAIL_PRESETS:
        if p["id"] == preset_id:
            return dict(p)
    return None
