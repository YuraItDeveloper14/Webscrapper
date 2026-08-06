FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# data/ holds the SQLite DB; mount a volume here to persist leads across deploys.
VOLUME ["/app/data"]

ENV HOST=0.0.0.0 PORT=8080
EXPOSE 8080

CMD ["python", "serve.py"]
