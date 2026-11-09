# API image (also used for the worker + scheduler: only the command differs).
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app
COPY scripts ./scripts
RUN useradd -r -u 10001 appuser
USER appuser
EXPOSE 8000
# Migrate, then serve. Fine for a single instance; with several API replicas,
# run `alembic upgrade head` as a separate one-off job instead.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-2}"]
