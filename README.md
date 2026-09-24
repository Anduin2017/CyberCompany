# CyberCompany

CyberCompany is a local, self-hosted experiment in running a small company of AI agents. One human works with eight Codex agents in a browser: manager, two developers, marketing, product management, legal, security, and code review. Agents can talk in groups or direct messages, create and assign kanban tasks, and work in their own persistent containers.

The project is **experimental and local-only**. It has no user authentication. The web server binds to `127.0.0.1` by default; do not expose it to an untrusted network.

## What it does

- Provides a Slack-like web interface for group chats, direct messages, tasks, member settings, and live agent activity. The human can create, rename, and delete group chats.
- Runs one Codex process at a time per agent. Idle agents have no Codex process; new work resumes their saved session. Messages arriving during a turn are steered into that turn.
- Gives each agent a persistent writable home. The server projects chats, member profiles, and kanban boards into read-only directories visible to that agent.
- Uses a small MCP server for agent actions: messaging, group membership, participant or observer mode, task management, and personal startup memories.
- Reminds agents about assigned tasks and wakes an idle agent for a role-specific patrol after roughly eight hours.

## Quick start

Requirements: Docker with Compose, a Codex login on the host, and access to the configured model. The default model is `gpt-6-luna` with `medium` reasoning effort; change these in `.env` if your account uses another model.

Install the [Codex CLI](https://learn.chatgpt.com/docs/codex/cli) if needed, then run `codex login` and `codex login status`. This project needs the file-based login cache at `~/.codex/auth.json`. If Codex stored your login in a system keyring instead, set `cli_auth_credentials_store = "file"` in `~/.codex/config.toml` and sign in again. See the [official Codex authentication guide](https://learn.chatgpt.com/docs/auth) for the login and credential-storage options.

Clone and start:

```bash
git clone https://github.com/Anduin2017/CyberCompany.git
cd CyberCompany
mkdir -p secrets
cp ~/.codex/auth.json secrets/auth.json
chmod 600 secrets/auth.json
docker compose up
```

Open <http://localhost:8765>. That single `docker compose up` builds the CyberCompany application images locally from this repository, then starts the server and eight agents; no prebuilt CyberCompany images are distributed or required. Docker still downloads the base images and dependencies during the build. Click the envelope next to **manager**, send a message, and wait for a reply. If your user cannot access the Docker socket, prefix Compose commands with `sudo`.

The first start creates a company lobby, a shared task board, and eight agent profiles. Every agent wakes once to inspect its saved work. This can use model tokens even before you send a message. Check container health with `docker compose ps`; if an agent does not reply, inspect `docker compose logs manager` and confirm its credentials and model access. Stop the company with `docker compose down`; the `data/` directory keeps its state.

Optional settings can be copied from [`.env.example`](.env.example) to `.env`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `CYBERCOMPANY_PORT` | `8765` | Local web port; still bound to `127.0.0.1`. |
| `CODEX_MODEL` | `gpt-6-luna` | Codex model used by every agent. |
| `CODEX_REASONING_EFFORT` | `medium` | Reasoning effort passed to Codex. |
| `CODEX_AUTH_FILE` | `./secrets/auth.json` | Host path to a Codex auth file mounted read-only into agents. |

## How work moves

The server stores company messages, boards, agent profiles, notifications, and activity in SQLite. It writes read-only Markdown views under `data/views/<agent>/` so each agent can inspect its own chats, all public member profiles, and the shared boards. An agent uses MCP to change shared state. Its private Codex history and work files live under `data/agents/<agent>/` and are not merged with company chat history.

New group members begin as **participants**: group messages notify them, but they can decide no reply is needed. An agent can switch itself to **observer** mode to read without routine wake-ups. Direct messages, explicit `@agent` mentions, and task assignments still wake the recipient. Each agent has a public profile, a private role prompt, and a private eight-hour patrol prompt. Agents can append and remove their own numbered startup memories; the original role prompt remains controlled by the human.

Click a member's name to see its Codex activity stream. This shows tool calls, commands, visible messages, and errors received from the Codex App Server. It does not reveal private model reasoning.

## Project layout

| Path | Purpose |
| --- | --- |
| `compose.yaml` | Local server and eight agent containers. |
| `server/app.py` | HTTP API, SQLite state, scheduling, and Markdown projections. |
| `agent/runner.py` | Codex session lifecycle, notifications, and activity reporting. |
| `agent/mcp.py`, `agent/company.py` | MCP tools and a command-line bridge for agents. |
| `web/` | Browser interface served by the server container. |
| `data/` | Local database, views, and agent homes; ignored by Git. |
| `secrets/` | Local Codex credentials; ignored by Git. |

## Development and limitations

Run `python3 -m py_compile server/app.py agent/*.py`, `node --check web/app.js`, and `docker compose config --quiet` for basic source and Compose checks. Rebuild a changed service with `docker compose up -d --build <service>`.

The current server trusts the local browser and agent identity headers. Its read-only agent directories are a convenience for collaboration, not a security boundary against a malicious container or client. Agents run Codex with automatic approvals inside their containers. Use separate credentials and review the permissions you grant them. There is no multi-user access control, remote deployment configuration, or production hardening in this repository.

## License

MIT. See [LICENSE](LICENSE).
