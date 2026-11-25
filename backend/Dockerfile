FROM docker.io/library/python:3.12-slim
WORKDIR /srv/tiso
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/srv/tiso/.venv/bin:$PATH"
COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN --mount=type=secret,id=build_ca,target=/run/secrets/build_ca \
    if [ -f /run/secrets/build_ca ]; then export SSL_CERT_FILE=/run/secrets/build_ca; fi; \
    uv sync --locked --no-dev --no-cache
COPY --chown=65534:65534 app ./app
RUN install -d -m 0700 -o 65534 -g 65534 /var/lib/tiso
USER 65534:65534
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
