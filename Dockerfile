FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        libproj-dev \
        proj-bin \
        proj-data \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY wellnav/ wellnav/
COPY templates/ templates/
COPY static/ static/

RUN mkdir -p /app/data \
    && useradd --create-home --uid 1000 wellnav \
    && chown -R wellnav:wellnav /app

USER wellnav
EXPOSE 5050

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5050"]
