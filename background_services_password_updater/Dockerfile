FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN pip install --no-cache-dir PyYAML==6.0.3
COPY updater.py bounded.py compatibility.py release_pin.py ./
COPY tests ./tests
COPY bundle ./bundle
RUN python -m unittest discover -s tests -v
RUN python -c "from bounded import BoundedUpdater; BoundedUpdater('/local_apps', '/app/bundle')"
CMD ["python", "/app/bounded.py"]
