FROM python:3.14-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY configs ./configs
COPY pipelines ./pipelines
COPY schemas ./schemas

RUN python -m pip install --upgrade pip \
    && python -m pip install --no-cache-dir .

CMD ["python", "pipelines/clean/normalize_sft.py", "--config", "configs/cleaning/stage1_phase1_sft_cleaning.yaml"]
