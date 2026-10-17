FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
WORKDIR /app
ARG STORAGE_BACKEND=local
COPY requirements.lock requirements-s3.lock ./
RUN case "$STORAGE_BACKEND" in local) requirements=requirements.lock ;; s3) requirements=requirements-s3.lock ;; *) echo "STORAGE_BACKEND must be local or s3" >&2; exit 2 ;; esac && pip install --no-cache-dir --require-hashes -r "$requirements"
COPY pyproject.toml LICENSE THIRD_PARTY_NOTICES.txt ./
COPY src ./src
ENV PYTHONPATH=/app/src WR_SIM_DATA=/data PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --uid 10001 --create-home simulator && mkdir /data && chown simulator:simulator /data
USER simulator
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()"
CMD ["python", "-m", "uvicorn", "web_research_sim.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
