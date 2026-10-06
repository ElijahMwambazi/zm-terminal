FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN pip install --upgrade pip \
    && pip install \
      openbb-platform-api \
      openbb-cboe \
      openbb-oecd \
      openbb-fred \
    && openbb-build

EXPOSE 6900

CMD ["openbb-api", "--host", "0.0.0.0", "--port", "6900"]
