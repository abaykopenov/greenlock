# Greenlock — изолированный verifier-образ.
# Сборка:  docker build -t greenlock:latest .
# Запуск гейта внутри (repo монтируется ro, diff на stdin):
#   docker run --rm -i --network none --read-only --tmpfs /tmp:rw,exec,size=1g \
#     -e GREENLOCK_SANDBOX_DIR=/tmp/gl-sandbox -e HOME=/tmp \
#     -v /path/to/repo:/src:ro greenlock:latest \
#     python -m greenlock.gate /src - --json  < change.diff
# Удобнее — обёртка: python -m greenlock.isolate /path/to/repo change.diff
FROM python:3.12-slim

WORKDIR /app
# git нужен гейту для применения unified-diff (git apply)
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY greenlock ./greenlock
RUN pip install --no-cache-dir ".[treesitter]" pytest

# Если у проверяемого репо есть тест-зависимости — допишите их в свой образ:
#   FROM greenlock:latest
#   RUN pip install -r requirements.txt

RUN useradd -m -u 65532 -s /usr/sbin/nologin gl
USER gl
ENV GREENLOCK_SANDBOX_DIR=/tmp/gl-sandbox \
    HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1

ENTRYPOINT []
CMD ["python", "-m", "greenlock.gate", "--help"]
