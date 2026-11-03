FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --system --create-home --home-dir /app --shell /usr/sbin/nologin sonarx && mkdir -p /app/data /app/logs && chown -R sonarx:sonarx /app
USER sonarx
CMD ["sh","-c","alembic upgrade head && python -m sonarx.bot"]
