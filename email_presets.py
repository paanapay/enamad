"""Ready-made blue-themed email bodies for CRM templates (RTL Persian)."""
from __future__ import annotations

from typing import Any

# Brand defaults used inside presets; admins can edit after applying.
_BRAND = "پاناپال"
_SITE = "panapal.ir"
_PHONE = "۰۲۱۷۷۶۱۹۹۸۰"

EMAIL_PRESETS: list[dict[str, str]] = [
    {
        "id": "gateway-promo",
        "title": "تبلیغ درگاه پرداخت",
        "description": "معرفی درگاه اینترنتی بعد از دریافت اینماد — هدر آبی، لیست مزایا",
        "subject": "{{owner_name}} عزیز، دریافت درگاه پرداخت برای {{domain}}",
        "body": f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 20px;border-collapse:collapse;border-radius:12px;overflow:hidden;">
<tr><td style="background:linear-gradient(135deg,#1e40af 0%,#2563eb 100%);padding:22px 24px;color:#ffffff;text-align:right;font-size:18px;font-weight:bold;line-height:1.6;">
{_BRAND} — درگاه پرداخت اینترنتی
</td></tr></table>

<p>سلام <strong>{{{{owner_name}}}}</strong> عزیز،</p>

<p>با توجه به اینکه وب‌سایت <strong>{{{{domain}}}}</strong> موفق به دریافت اینماد شده است، اکنون امکان دریافت <strong>درگاه پرداخت اینترنتی</strong> برای کسب‌وکار شما فراهم است.</p>

<p>{_BRAND} با ارائه خدمات پرداخت آنلاین، این امکان را فراهم می‌کند تا بدون مراجعه حضوری و در کوتاه‌ترین زمان، درگاه پرداخت خود را فعال کنید.</p>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:16px 0;background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;border-collapse:collapse;">
<tr><td style="padding:16px 20px;text-align:right;color:#1e3a8a;line-height:1.9;">
<strong>مزایای همکاری با {_BRAND}:</strong><br>
✅ دریافت درگاه به‌صورت کاملاً غیرحضوری<br>
✅ کارمزد رقابتی<br>
✅ فعال‌سازی سریع<br>
✅ تسویه منظم<br>
✅ مستندات API و افزونه‌های آماده<br>
✅ پشتیبانی تخصصی قبل و بعد از فعال‌سازی
</td></tr></table>

<p>اگر در حال راه‌اندازی یا توسعه فروش آنلاین هستید، کارشناسان ما آماده پاسخگویی و راهنمایی شما هستند.</p>

<p style="margin-top:20px;color:#475569;font-size:14px;">
وب‌سایت: <strong>{_SITE}</strong><br>
تلفن: <strong>{_PHONE}</strong>
</p>

<p>با احترام<br><strong>تیم {_BRAND}</strong></p>""",
    },
    {
        "id": "gateway-cta",
        "title": "درگاه پرداخت + دکمه اقدام",
        "description": "متن کوتاه‌تر با دکمه آبی «درخواست درگاه»",
        "subject": "فعال‌سازی درگاه پرداخت برای {{domain}}",
        "body": f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 18px;border-collapse:collapse;">
<tr><td style="border-right:4px solid #2563eb;padding:4px 16px 4px 0;text-align:right;">
<span style="font-size:20px;font-weight:bold;color:#1e40af;">درگاه پرداخت {_BRAND}</span>
</td></tr></table>

<p>{{{{owner_name}}}} عزیز،</p>

<p>کسب‌وکار <strong>{{{{business_name}}}}</strong> با دامنه <strong>{{{{domain}}}}</strong> اینماد دریافت کرده است. برای شروع فروش آنلاین، درگاه پرداخت اینترنتی را از {_BRAND} دریافت کنید.</p>

<table role="presentation" cellpadding="0" cellspacing="0" style="margin:24px 0;">
<tr><td style="border-radius:8px;background:#2563eb;text-align:center;">
<a href="https://{_SITE}" style="display:inline-block;padding:14px 28px;color:#ffffff;text-decoration:none;font-weight:bold;font-size:15px;">درخواست درگاه پرداخت</a>
</td></tr></table>

<p style="color:#64748b;font-size:14px;">سؤالی دارید؟ با ما تماس بگیرید: <strong>{_PHONE}</strong></p>""",
    },
    {
        "id": "enamad-welcome",
        "title": "تبریک دریافت اینماد",
        "description": "پیام خوش‌آمدگویی و معرفی خدمات بعد از صدور اینماد",
        "subject": "تبریک! {{domain}} اینماد دریافت کرد",
        "body": f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 20px;border-collapse:collapse;">
<tr><td style="background:#dbeafe;padding:18px 22px;border-radius:10px;text-align:right;">
<span style="font-size:22px;">🎉</span>
<span style="font-size:17px;font-weight:bold;color:#1e40af;margin-right:8px;">تبریک دریافت نماد اعتماد الکترونیکی</span>
</td></tr></table>

<p>سلام <strong>{{{{owner_name}}}}</strong>،</p>

<p>وب‌سایت <strong>{{{{domain}}}}</strong> با موفقیت اینماد دریافت کرده است
(تاریخ صدور: <strong>{{{{approve_date}}}}</strong>).</p>

<p>گام بعدی برای فروش آنلاین، دریافت <strong>درگاه پرداخت اینترنتی</strong> است.
{_BRAND} این مسیر را برای شما ساده و سریع کرده است.</p>

<ul style="padding-right:20px;color:#334155;line-height:1.9;">
<li>بدون مراجعه حضوری</li>
<li>راه‌اندازی سریع</li>
<li>پشتیبانی تخصصی</li>
</ul>

<p style="margin-top:16px;font-size:14px;color:#475569;">
{_SITE} · {_PHONE}
</p>""",
    },
    {
        "id": "follow-up",
        "title": "پیگیری تماس",
        "description": "یادآوری تماس یا پیگیری همکاری — ظاهر ساده و رسمی",
        "subject": "پیگیری همکاری — {{business_name}}",
        "body": f"""<p style="color:#1e40af;font-weight:bold;font-size:16px;margin:0 0 16px;">پیگیری همکاری با {_BRAND}</p>

<p>{{{{owner_name}}}} عزیز،</p>

<p>پیرو تماس/پیام قبلی درباره کسب‌وکار <strong>{{{{business_name}}}}</strong>
(دامنه: <strong>{{{{domain}}}}</strong>)، خواستیم بدانید آیا نیاز به راهنمایی
برای دریافت درگاه پرداخت اینترنتی دارید؟</p>

<p>تیم پشتیبانی ما آماده پاسخگویی است:</p>
<p style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:12px 16px;font-size:14px;">
📞 {_PHONE}<br>
🌐 {_SITE}
</p>

<p>با تشکر<br>تیم {_BRAND}</p>""",
    },
    {
        "id": "minimal-blue",
        "title": "معرفی کوتاه (مینیمال)",
        "description": "متن فشرده با خط تزئینی آبی — مناسب پیام اول",
        "subject": "درگاه پرداخت برای {{domain}}",
        "body": f"""<p style="border-bottom:3px solid #2563eb;padding-bottom:12px;margin:0 0 20px;font-size:17px;font-weight:bold;color:#1e40af;">{_BRAND}</p>

<p>{{{{owner_name}}}} عزیز،</p>

<p>با توجه به دریافت اینماد برای <strong>{{{{domain}}}}</strong>،
دریافت درگاه پرداخت اینترنتی را بدون مراجعه حضوری و با کارمزد رقابتی
از {_BRAND} انجام دهید.</p>

<p>فعال‌سازی سریع و پشتیبانی تخصصی در کنار شماست.</p>

<p style="font-size:14px;color:#64748b;margin-top:24px;">
{_SITE} · {_PHONE}
</p>""",
    },
]


def list_email_presets() -> list[dict[str, Any]]:
    """Public preset list for the admin UI (no raw HTML in list if needed later)."""
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
