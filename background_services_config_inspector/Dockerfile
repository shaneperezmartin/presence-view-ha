FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY inspect_config.py public-baseline.blob ./
COPY tests ./tests
RUN python -m unittest discover -s tests -v
CMD ["python", "/app/inspect_config.py"]
