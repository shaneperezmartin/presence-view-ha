FROM python:3.13-alpine
RUN apk add --no-cache tzdata
WORKDIR /app
COPY server.py history_store.py observation_tracks.py index.html test_server.py test_http.py test_history_store.py test_observation_tracks.py test_service_history.py /app/
RUN python -m unittest -v test_server test_http test_history_store test_observation_tracks test_service_history
ENV PYTHONUNBUFFERED=1
CMD ["python", "/app/server.py"]
