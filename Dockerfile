FROM python:3.11-slim

# تثبيت hashcat والأدوات المطلوبة
RUN apt-get update && apt-get install -y \
    hashcat \
    nvidia-smi \
    pciutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# نسخ ملف المتطلبات وتثبيتها
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ التطبيق
COPY app.py .
COPY templates/ templates/
COPY static/ static/

# إنشاء مجلد للرفع
RUN mkdir -p /app/uploads

# فتح المنفذ
EXPOSE 5000

# تشغيل التطبيق
CMD ["python", "app.py"]
