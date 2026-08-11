FROM python:3.12-slim

WORKDIR /app

COPY server/ ./server/
COPY public/ ./public/
COPY Procfile railway.toml requirements.txt ./

RUN mkdir -p /data /app/data /tmp

ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
ENV PORT=8080
ENV DB_PATH=/tmp/lumen.db
ENV API_BASE=https://be.komikcast.cc

EXPOSE 8080

CMD ["python", "-u", "server/boot.py"]
