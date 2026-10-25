# syntax=docker/dockerfile:1
#
# Linux AI Assistant — headless CLI container.
#
# This image runs the CLI front end (src/cli.py), NOT the GTK GUI. The GUI
# requires a display server and GTK bindings that belong on the host; the
# container is meant for scripting, automation, CI, and talking to local or
# remote LLM servers over the network.
#
# Build from the repository root:
#   docker build -f docker/Dockerfile -t linux-ai-assistant:cli .
#
# Run:
#   docker run --rm linux-ai-assistant:cli --help
#   docker run --rm -v "$HOME/.config/linux_ai_assistant:/home/assistant/.config/linux_ai_assistant" \
#     linux-ai-assistant:cli chat "What is my kernel version?"
#
# The container runs as a non-root user (assistant, uid 1000).

# ---------------------------------------------------------------------------
# Stage 1: builder — install Python dependencies into a venv.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

# Build deps for any wheel that needs compilation (pillow, psutil, PyYAML).
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
        gcc \
        libc6-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 2: runtime — copy the venv and the source, drop build tools.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# tesseract is optional (OCR degrades to the binary when pytesseract is
# absent, and vice-versa); keep the image lean by default. Install it with
# a build arg if OCR inside the container is needed:
#   docker build -f docker/Dockerfile --build-arg INSTALL_TESSERACT=1 ...
ARG INSTALL_TESSERACT=0
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
        ca-certificates \
        $([ "$INSTALL_TESSERACT" = "1" ] && echo "tesseract-ocr" || true) \
    && rm -rf /var/lib/apt/lists/*

# Non-root user with a stable uid/gid (1000) so bind-mounted volumes work.
RUN groupadd --gid 1000 assistant \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash assistant

# The venv from the builder stage.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LINUX_AI_COMMAND_SANDBOX=0

WORKDIR /app

# Copy the application source. The .dockerignore keeps .git, venvs, caches
# and test artifacts out of the image.
COPY src/ ./src/
COPY plugins/ ./plugins/
COPY themes/ ./themes/
COPY pyproject.toml README.md LICENSE ./

# Install the package itself (console scripts: linux-ai-assistant-cli).
# --no-deps because the venv already has them from requirements.txt.
RUN pip install --no-cache-dir --no-deps .

USER assistant

# Persist config, history and usage under the user's home inside the
# container; bind-mount this path to share state with the host.
VOLUME ["/home/assistant/.config/linux_ai_assistant"]

# Default entry point is the CLI. Override with `chat`, `sessions`, `sync`,
# `benchmarks`, etc. `--help` shows the full command list.
ENTRYPOINT ["linux-ai-assistant-cli"]
CMD ["--help"]
