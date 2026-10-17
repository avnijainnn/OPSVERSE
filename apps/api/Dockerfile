FROM python:3.13-slim
WORKDIR /code
COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
RUN pip install --no-cache-dir .
ENV OPSVERSE_DB=/data/opsverse.db
RUN mkdir -p /data
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
