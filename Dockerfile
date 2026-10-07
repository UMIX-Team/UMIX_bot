FROM grafana/k6:latest AS k6stage

FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    fonts-dejavu-core \
    libfreetype6 \
    libpng16-16 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=k6stage /usr/bin/k6 /usr/local/bin/k6

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data/reports /app/data/logs

CMD ["uvicorn", "bot.main:app", "--host", "0.0.0.0", "--port", "8000"]
