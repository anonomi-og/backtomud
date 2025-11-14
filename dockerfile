FROM python:3.11-slim

WORKDIR /app

# System deps (optional but helps if you later add more libs)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV SECRET_KEY="change-me-in-prod"

EXPOSE 5000

CMD ["python", "app.py"]
