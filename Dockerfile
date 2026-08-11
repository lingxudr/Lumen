FROM python:3.12-slim

WORKDIR /app

# system deps for Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo zlib1g libpng16-16 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ ./server/
COPY public/ ./public/
COPY Procfile railway.toml ./

RUN mkdir -p /data /app/data /tmp

ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
ENV PORT=8080
ENV DB_PATH=/tmp/lumen.db
ENV API_BASE=https://be.komikcast.cc
ENV WEBP_QUALITY=78

EXPOSE 8080

CMD ["python", "-u", "server/boot.py"]
