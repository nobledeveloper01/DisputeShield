# Multi-stage: no build tooling in the final image, non-root, slim (§11.8).

FROM python:3.12-slim AS builder
WORKDIR /build
RUN pip install --no-cache-dir hatchling
COPY pyproject.toml README.md LICENSE ./
COPY disputeshield ./disputeshield
RUN pip wheel --no-cache-dir --wheel-dir /wheels ".[server]"

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

RUN groupadd -r disputeshield && useradd -r -g disputeshield disputeshield

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels disputeshield[server] \
    && rm -rf /wheels

WORKDIR /app
COPY --chown=disputeshield:disputeshield server ./server
COPY --chown=disputeshield:disputeshield manage.py ./

USER disputeshield
EXPOSE 8000

# terminationGracePeriodSeconds must exceed this drain budget (§8.6 principle 5).
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"

CMD ["gunicorn", "server.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--worker-class", "gevent", \
     "--workers", "4", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-"]
