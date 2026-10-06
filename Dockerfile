FROM python:3.12-slim AS app
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY pyproject.toml ./
COPY notes_bot ./notes_bot
COPY scripts ./scripts
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home notes
USER notes
CMD ["uvicorn", "notes_bot.web:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

FROM app AS worker
USER root
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates unzip \
    && rm -rf /var/lib/apt/lists/*
# CPU-only torch avoids downloading CUDA runtime packages.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir '.[worker]'
# yt-dlp's YouTube extraction uses a supported JavaScript runtime.
COPY --from=denoland/deno:bin-2.3.3 /deno /usr/local/bin/deno
RUN mkdir -p /data && chown notes:notes /data
ENV HF_HOME=/data/model-cache OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false
USER notes
CMD ["python", "-m", "notes_bot.worker"]
