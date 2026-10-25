# Docker — Linux AI Assistant (headless CLI)

This directory contains a Docker image for running the **CLI front end** of
Linux AI Assistant inside a container. The GTK GUI is not included: it needs a
display server and GTK bindings that belong on the host. The container is
meant for scripting, automation, CI pipelines, and talking to local or remote
LLM servers over the network.

## Build

Build from the **repository root** (the build context is the whole repo):

```bash
docker build -f docker/Dockerfile -t linux-ai-assistant:cli .
```

With OCR support (installs `tesseract-ocr`):

```bash
docker build -f docker/Dockerfile --build-arg INSTALL_TESSERACT=1 -t linux-ai-assistant:cli-ocr .
```

## Run

Show the CLI help:

```bash
docker run --rm linux-ai-assistant:cli --help
```

Run a chat turn against the configured provider:

```bash
docker run --rm \
  -v "$HOME/.config/linux_ai_assistant:/home/assistant/.config/linux_ai_assistant" \
  -e LINUX_AI_API_PROVIDERS_OPENROUTER_API_KEY="$OPENROUTER_API_KEY" \
  linux-ai-assistant:cli chat "What is my kernel version?"
```

The volume mount persists `config.json`, `history.json` and `usage.json`
between runs. API keys can be passed as environment variables (the canonical
`LINUX_AI_API_PROVIDERS_<PROVIDER>_API_KEY` names) — they are never written to
the image.

## Talking to a local LLM server (Ollama / LM Studio)

Use `host.docker.internal` (Docker Desktop) or the host gateway IP to reach a
server running on the host:

```bash
# 1. Configure the local server URL once (persisted via the volume mount):
docker run --rm \
  -v "$HOME/.config/linux_ai_assistant:/home/assistant/.config/linux_ai_assistant" \
  linux-ai-assistant:cli config set api.providers.local_llm.base_url http://host.docker.internal:11434/v1

# 2. Select local mode and chat:
docker run --rm \
  -v "$HOME/.config/linux_ai_assistant:/home/assistant/.config/linux_ai_assistant" \
  linux-ai-assistant:cli mode local
docker run --rm \
  -v "$HOME/.config/linux_ai_assistant:/home/assistant/.config/linux_ai_assistant" \
  linux-ai-assistant:cli chat "Explain my disk usage"
```

List models installed on the local server:

```bash
docker run --rm \
  -v "$HOME/.config/linux_ai_assistant:/home/assistant/.config/linux_ai_assistant" \
  linux-ai-assistant:cli local-models
```

## Useful commands

```bash
# Export a sync bundle (settings + history, no credentials):
docker run --rm \
  -v "$HOME/.config/linux_ai_assistant:/home/assistant/.config/linux_ai_assistant" \
  -v "$PWD:/out" \
  linux-ai-assistant:cli sync export /out/bundle.json

# Import a sync bundle (review, then --yes):
docker run --rm \
  -v "$HOME/.config/linux_ai_assistant:/home/assistant/.config/linux_ai_assistant" \
  -v "$PWD:/out" \
  linux-ai-assistant:cli sync import /out/bundle.json --preview

# Run the micro-benchmarks:
docker run --rm linux-ai-assistant:cli benchmarks --scale 0.2

# Usage statistics:
docker run --rm \
  -v "$HOME/.config/linux_ai_assistant:/home/assistant/.config/linux_ai_assistant" \
  linux-ai-assistant:cli stats
```

## Docker Compose

A `docker-compose.yml` is provided for the common case of running the CLI
alongside a local Ollama server:

```bash
cd docker
docker compose up -d ollama
docker compose run --rm cli chat "Hello from the container"
```

## Security notes

- The container runs as a **non-root** user (`assistant`, uid 1000).
- `LINUX_AI_COMMAND_SANDBOX=0` is set because systemd user units are not
  available inside a typical container; privileged commands are therefore
  not sandboxed. Do not expose the container to untrusted networks.
- API keys should be passed via environment variables or the mounted
  `config.json`, never baked into the image.
- The image is built from `python:3.12-slim` with a multi-stage build; build
  tools (gcc, libc6-dev) are not present in the final image.
