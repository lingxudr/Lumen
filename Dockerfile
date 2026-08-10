FROM python:3.12-slim

WORKDIR /app

# App is pure stdlib — no pip install needed
COPY server/ ./server/
COPY public/ ./public/
COPY Procfile railway.toml requirements.txt ./

ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
# Railway injects PORT at runtime

EXPOSE 8080

CMD ["python", "-u", "server/app.py"]
