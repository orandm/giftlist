FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY giftlist ./giftlist
COPY wsgi.py .

RUN useradd --system --uid 1000 app && mkdir -p /data && chown app /data
USER app

EXPOSE 8000
CMD ["gunicorn", "--workers", "2", "--threads", "4", "--bind", "0.0.0.0:8000", "--access-logfile", "-", "wsgi:app"]
