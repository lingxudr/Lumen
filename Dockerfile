FROM python:3.12-slim

WORKDIR /app

COPY server/ ./server/
RUN mkdir -p /data /app/data /tmp
ENV DB_PATH=/data/lumen.db
ENV API_BASE=https://be.komikcast.cc
COPY public/ ./public/
COPY Procfile railway.toml requirements.txt ./

ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0

EXPOSE 8080

CMD ["python", "-u", "server/app.py"]
