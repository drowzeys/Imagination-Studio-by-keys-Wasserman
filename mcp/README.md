# Slate Studio MCP

`studio-mcp.py` lets an agent you already use (Claude Code, Codex, Hermes, or any MCP client) work with a running
Wasserman Slate AI Studio: look at the Library, queue stills and clips, follow the queue, and bring files in. It
does what you would do on the Studio page, through the Studio's own API.

It is one Python file with no dependencies (Python 3.9 or newer). The Studio must be running.

```
 MCP client  ──stdio──▶  studio-mcp.py  ──HTTP──▶  Studio  (http://127.0.0.1:7870)
```

Set `STUDIO_URL` when the Studio is somewhere else (default `http://127.0.0.1:7870`). Paths in and out are
relative to the Studio's folder (`~/ImaginationStudio` by default), as the Library shows them; a finished file is
at `$STUDIO_URL/files/<path>`.

Inside the Studio, **Brains & agents** already connects this server for you. The sections below are for using it
from an agent on its own.

---

## Connect

Use the **absolute path** to `mcp/studio-mcp.py` in this folder in every config below.

### Claude Code

```bash
claude mcp add studio -e STUDIO_URL=http://127.0.0.1:7870 -- python3 /absolute/path/to/mcp/studio-mcp.py
```

`/mcp` in a session lists **studio** as connected. Remove it with `claude mcp remove studio`.

### Codex CLI

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.studio]
command = "python3"
args = ["/absolute/path/to/mcp/studio-mcp.py"]
env = { STUDIO_URL = "http://127.0.0.1:7870" }
```

### Hermes

In `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  studio:
    command: "python3"
    args: ["/absolute/path/to/mcp/studio-mcp.py"]
    env:
      STUDIO_URL: "http://127.0.0.1:7870"
```

Start a new Hermes session to load the tools.

### Any stdio MCP client

```json
{
  "mcpServers": {
    "studio": {
      "command": "python3",
      "args": ["/absolute/path/to/mcp/studio-mcp.py"],
      "env": { "STUDIO_URL": "http://127.0.0.1:7870" }
    }
  }
}
```

### From another machine

The Studio listens on its own `127.0.0.1` unless it was started with `HOST=0.0.0.0`. To use a Studio on a DGX Spark
from an agent on your Mac, run the server there over ssh, so nothing is opened to the network:

```json
{ "command": "ssh", "args": ["-o", "BatchMode=yes", "sam@spark", "--", "python3", "/path/on/the/spark/mcp/studio-mcp.py"] }
```

---

## Tools

| Tool | Params | Does |
|---|---|---|
| `studio_state` | — | **Call first.** Presets (sizes), qualities, clip lengths, picture models and the machine. |
| `list_library` | `kind?` (`video` / `image`), `limit?` | Clips and stills, newest first, with their paths. |
| `queue_still` | `prompt`, `count?`, `preset?`, `width?`, `height?`, `seed?`, `image_model?` | Queues stills: `count` candidates (1 to 12, default 4) and a contact sheet. |
| `queue_clip` | `prompt`, `first_frame?`, `image_prompt?`, `preset?`, `quality?`, `seconds?`, `seed?`, `title?` | Queues a clip with sound: from a Library picture (`first_frame`), from a picture it makes first (`image_prompt`), or from text alone. |
| `job_status` | `id` | Status (`queued`, `running`, `done`, `failed`, `cancelled`), progress, outputs, error. |
| `list_jobs` | `limit?` | The queue and finished jobs, newest first. |
| `upload_file` | `path` | Copies a file from the agent's machine into the Library (pictures, video, sound) and returns its path. |

Jobs run one at a time, in the order queued. A queued job returns at once with its `id`; poll `job_status` until it is
`done`, then use its `outputs`. Clip lengths are brought to the nearest the model takes (up to 15 seconds); sizes the
model cannot take are refused with the reason, as on the page.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `the Studio does not answer at …` | Start the Studio (`bash scripts/app.sh`, or the app), or point `STUDIO_URL` at it. |
| `the Studio said: … (400)` | The settings were refused, for the reason given. `studio_state` lists what this machine takes. |
| `python3: command not found` | Use the absolute path of Python 3 as the `command`. |
