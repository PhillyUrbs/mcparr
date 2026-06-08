# mcparr container image.
# Editable install keeps template/static/locale package data resolvable via
# __file__, and lets us compile translation catalogs in place at build time.
FROM python:3.14-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    MCPARR_DATA_DIR=/data \
    MCPARR_HOST=0.0.0.0 \
    MCPARR_UI_PORT=7475 \
    MCPARR_MCP_PORT=7474

WORKDIR /app

# Version is supplied by CI (see .github/workflows). hatch-vcs reads this instead
# of probing git, so the image needs no .git directory to know its own version.
ARG MCPARR_VERSION=0.0.0

# Dependency/build metadata first for better layer caching.
COPY pyproject.toml README.md LICENSE babel.cfg ./
COPY mcparr ./mcparr

RUN SETUPTOOLS_SCM_PRETEND_VERSION="${MCPARR_VERSION}" pip install --no-cache-dir -e . \
    && pybabel compile -d mcparr/ui/locales -D messages \
    && useradd -u 10001 -r -s /usr/sbin/nologin mcparr \
    && mkdir -p /data \
    && chown -R mcparr:mcparr /data /app

USER mcparr

EXPOSE 7474 7475
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request as u,sys; sys.exit(0 if u.urlopen('http://127.0.0.1:7475/health').status==200 else 1)"

CMD ["python", "-m", "mcparr.main"]
