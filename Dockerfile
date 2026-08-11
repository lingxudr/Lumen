FROM python:3.12-slim

WORKDIR /app

# App is pure stdlib — no pip install needed
COPY server/ ./server/
RUN mkdir -p /data /app/data
ENV DB_PATH=/data/lumen.db
ENV API_BASE=https://be.komikcast.cc
COPY public/ ./public/
COPY Procfile railway.toml requirements.txt ./

ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
# Railway injects PORT at runtime

EXPOSE 8080

# Ensure server package files present
COPY server/db.py ./server/db.py

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % (__import__('os').environ.get('PORT','8080')), timeout=3)" || exit 1

CMD ["python", "-u", "server/app.py"]
