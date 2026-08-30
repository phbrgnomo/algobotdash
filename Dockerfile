FROM python:3.12-slim AS builder

WORKDIR /app
ENV POETRY_VERSION=2.1.3 \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1

RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}"
COPY pyproject.toml poetry.lock poetry.toml ./
RUN poetry install --only main --no-root
COPY algobotdash ./algobotdash
COPY generate_trade_report.py README.md ./
RUN poetry install --only main

FROM python:3.12-slim AS runtime

WORKDIR /app
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/algobotdash /app/algobotdash
COPY --from=builder /app/generate_trade_report.py /app/README.md /app/

EXPOSE 8765
CMD ["uvicorn", "algobotdash.web:app", "--host", "0.0.0.0", "--port", "8765"]
