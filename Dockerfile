FROM python:3.11-slim

# تثبيت hashcat والأدوات المطلوبة
RUN apt-get update && apt-get install -y \
    hashcat \
    wget \
    curl \
    git \
    nano \
    pciutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# نسخ requirements وتثبيت dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ باقي الملفات
COPY . .

# إنشاء مجلد مؤقت للملفات المرفوعة
RUN mkdir -p /tmp/uploads

# استخدام port 8080 كما هو محدد في Railway
EXPOSE 8080

# تشغيل التطبيق
CMD ["gunicorn", "-k", "eventlet", "-w", "1", "app:app"]
