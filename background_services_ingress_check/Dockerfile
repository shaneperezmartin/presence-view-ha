FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY route_check.py test_route_check.py ./
RUN python -m unittest -v test_route_check
USER 65534:65534
CMD ["python","/app/route_check.py"]
