# EvalBench 的 Python 執行環境。
# 目的:host 終端不裝任何 Python 套件,uv / ruff / pytest 全部活在容器裡。
FROM python:3.13-slim

# uv 從官方映像檔複製進來
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# UV_PROJECT_ENVIRONMENT 指到 /venv 而不是專案底下的 .venv——
# .venv 若落在 bind mount 上,容器裝的 Linux 套件會跟 host 的 macOS 混在一起。
ENV UV_CACHE_DIR=/cache/uv \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/venv \
    PATH=/venv/bin:$PATH

WORKDIR /work
