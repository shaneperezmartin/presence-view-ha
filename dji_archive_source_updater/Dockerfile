FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN pip install --no-cache-dir PyYAML==6.0.3
COPY updater.py ./
COPY tests ./tests
RUN python -m unittest discover -s tests -v
COPY bundle ./bundle
RUN python -c "from updater import Updater; Updater('/local_apps', '/app/bundle')"
CMD ["python", "/app/updater.py"]
