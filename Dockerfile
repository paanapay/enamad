# Enamad scraper + Telegram bot + scheduler
FROM python:3.11-slim

# System libs required by opencv-python / ddddocr (onnxruntime)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Tehran

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default command runs the Telegram bot; compose overrides per-service.
CMD ["python", "telegram_bot.py"]
