FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY vinted_scraper ./vinted_scraper
COPY config.example.yaml .
VOLUME ["/app/data"]
# Mount your config at /app/config.yaml
CMD ["python", "-m", "vinted_scraper", "-c", "config.yaml", "run"]
