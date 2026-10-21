# syntax=docker/dockerfile:1.6
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv/flags

COPY pyproject.toml requirements.txt README.md ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY feature_flags ./feature_flags
RUN pip install --no-deps -e .

RUN addgroup --system flags && adduser --system --ingroup flags flags && chown -R flags:flags /srv/flags
USER flags

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "feature_flags.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
