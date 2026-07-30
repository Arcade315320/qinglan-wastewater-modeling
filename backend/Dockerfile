FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    NUMBA_CACHE_DIR=/tmp/numba

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-models.txt ./
RUN pip install --no-cache-dir -r requirements-models.txt
COPY app ./app
RUN curl -L --fail --silent --show-error \
    https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/Variable/TTF/NotoSansCJKsc-VF.ttf \
    -o /app/app/assets/NotoSansCJKsc-VF.ttf
RUN mkdir -p /app/generated_reports

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
