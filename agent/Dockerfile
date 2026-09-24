FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates git curl && rm -rf /var/lib/apt/lists/* \
    && npm install -g @openai/codex@0.155.1
COPY agent/runner.py agent/mcp.py agent/company.py /app/
ENV HOME=/home/agent CODEX_HOME=/home/agent/.codex PYTHONUNBUFFERED=1
WORKDIR /home/agent/work
ENTRYPOINT []
CMD ["python3", "-u", "/app/runner.py"]
