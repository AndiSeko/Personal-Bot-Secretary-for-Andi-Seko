FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# /data — монтируется как persistent volume на Fly.io / Northflank / Koyeb
# Если задан DATABASE_URL (Postgres), DATA_DIR игнорируется
ENV DATA_DIR=/data

EXPOSE 8000

# healthcheck для оркестраторов
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["python", "bot.py"]
