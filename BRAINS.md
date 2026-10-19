# Brains: connecting an agent

A brain is a coding CLI the user is already paying for (Claude Code, Codex with a ChatGPT sign-in, Gemini CLI, Grok
CLI, Hermes) or a local OpenAI-compatible server. The studio never asks for an API key and keeps nothing of a
sign-in: each CLI keeps its own credentials where it always does. Code: `app/agents.py` (the `signin` and `connect`
sections), `app/static/machine.js` (`connectCard`, `connectPanel`, `linkPanel`, `connectNudge`, `qrMatrix`).

## Connect: one checklist the studio works through

Settings → Connections (and Machine → Brains & tools) shows each agent with one word and one button:

| Word | Means | The button |
| --- | --- | --- |
| Ready | signed in (or it answered) | *Make default*, or *Check* |
| Not installed | no CLI here (or on the linked computer) | *Install* |
| Not signed in | the CLI says so | *Connect* |
| Signed in elsewhere | a Mac reached over SSH whose sign-in is in its Keychain (below) | *Allow over SSH* |
| Cannot start | the CLI fails before it signs in, or SSH does not reach the computer | *Fix it* / *Check the link* |

Everything technical (path, version, the CLI's own words, Test, Sign out) is under *Details*.

*Connect* opens a checklist whose lines tick themselves:

1. **Find it.** On this computer, or on the linked one over SSH. When it is missing, *Install* runs the CLI's
   official installer and streams what it prints (`GET /api/brains/{id}/install?plan=1` says what will run first):

   | Agent | Installer, first one whose tool is there |
   | --- | --- |
   | Claude Code | `npm install -g @anthropic-ai/claude-code@latest`, else `curl -fsSL https://claude.ai/install.sh \| bash` |
   | Codex | `npm install -g @openai/codex@latest`, else `brew install --cask codex`. On a Mac with the ChatGPT app nothing is installed: the app's own Codex is used (it shares the app's ChatGPT sign-in) |
   | Gemini CLI | `npm install -g @google/gemini-cli@latest`, else `brew install gemini-cli` |
   | Grok CLI | `curl -fsSL https://x.ai/cli/install.sh \| bash` |
   | Hermes | `curl -fsSL https://hermes-agent.nousresearch.com/install.sh \| bash` |

   Never `sudo`: stdin is closed and nothing runs as root. When npm's global folder is not the user's to write, npm
   installs into `~/.npm-global` (which the studio looks in). Fifteen minutes at most.

   When both a `codex` on PATH and the ChatGPT app's are there, the newer one is used: an older Codex is refused a
   ChatGPT account's current models ("not supported when using Codex with a ChatGPT account"). Measured on a Mac:
   Homebrew's 0.130 failed after 68 s, the app's 0.162 answered in 6 s with the same sign-in.
2. **Sign in.** The CLI's own flow in a pseudo-terminal (`POST /api/brains/{id}/signin`). The *Connect* click opens a
   tab at once (a browser lets a click open one) and points it at the sign-in page when the CLI prints it. The panel
   always also shows a big *Open the sign-in page* button, the one-time code in large type with *Copy code* when the
   flow uses one, *Copy link*, and a QR code of the address for a phone (drawn as inline SVG, no library). For
   Claude Code, which shows a code to paste back, the code is typed into the studio and handed to the CLI. Codex uses
   its browser sign-in only when the page is open on the computer the CLI runs on (its callback is on localhost);
   otherwise a one-time code (`--device-auth`). Gemini CLI signs in inside its own first run: the panel shows the
   command and ticks when the CLI reports it is signed in.
3. **Check it answers.** One short real answer (`claude -p`, `codex exec`, `hermes -z`, …: "Reply with the single
   word: ok"), 60 seconds at most. A failure shows the CLI's exact words and the one action that fixes it: install or
   update, sign in again, check the link, or check again.
4. **Make it your default.** The default brain and, for Claude Code and Codex (the agents that drive the studio's
   tools), the Agent room's default (`agent` in `.studio-agents/settings.json`). This happens by itself only while the
   current default is not signed in; otherwise the line offers a button.

Home and the Agent room show a *Connect your agent* card while no agent is signed in (Home's can be dismissed).

## Linking another computer

A DGX Spark usually has no Claude or Codex sign-in; the Mac beside it does. *Link another computer*:

1. **This studio's key**: the machine's own SSH key, made with `ssh-keygen -t ed25519` only when it has none (an
   existing key is never replaced).
2. **Let the other computer in**: one line to run there, which adds the key to `~/.ssh/authorized_keys` once. On a
   Mac, Remote Login must be on (System Settings → General → Sharing).
3. **Test**: `ssh -o BatchMode=yes` (a key or nothing; no password is ever typed or kept). A computer met for the
   first time shows its host-key fingerprint and *Trust this computer and test* (`StrictHostKeyChecking=accept-new`,
   which never accepts a changed key).
4. **Use its agents**: the computer is saved as the remote host with the CLIs found there; each one then connects
   with the same four steps over SSH (install, sign in, answer, default).

## Claude Code on a Mac over SSH: the Keychain

On macOS, Claude Code keeps its sign-in in the login Keychain. An SSH session cannot read it: the item is there
(`security find-generic-password -s "Claude Code-credentials"` succeeds without the secret) but reading the secret
fails with "User interaction is not allowed". So over SSH `claude auth status` says `loggedIn: false` on a Mac whose
desktop session is signed in. Checked on a Mac reached from a DGX Spark, October 2026.

What the studio does:

- The status says **Signed in elsewhere** when, over SSH, Claude Code says it is signed out, the computer is a Mac
  and the Keychain holds the item.
- **Allow over SSH** runs `claude setup-token` there, Claude Code's own long-lived (one year) sign-in token for a
  subscription, in the same pseudo-terminal flow (the page, the paste-back code). The token it prints is caught in
  the studio's memory, written at once to that Mac over SSH on stdin into
  `~/.config/imagination-studio/claude-oauth-token` (file `0600`, folder `0700`), and cut out of everything the page
  and the log can see. It is never written on the studio's machine, never in its settings and never in a log.
- Every command the studio runs on another computer starts by exporting `CLAUDE_CODE_OAUTH_TOKEN` from that file
  when it is there and the variable is not already set (`REMOTE_PATH` in `app/agents.py`), so the status, the answer
  check and the Agent room all use it.
- *Sign out* removes the file there as well as running `claude auth logout`.

Rejected: `security unlock-keychain` in the SSH session needs the user's login password, which the studio must
never ask for or hold. Copying the Keychain item elsewhere would move a secret off the Mac.

## Routes

All behind the brains guard: this computer or its Tailscale name, same origin, JSON for writes.

| Route | |
| --- | --- |
| `GET /api/brains/{id}/connect` | the checklist's state: find, install, sign-in status, the running sign-in, the last answer, default |
| `GET /api/brains/{id}/install[?plan=1]` · `POST …/install` | the installer: what would run, start it, follow it |
| `POST /api/brains/{id}/verify` | one short answer, 60 s at most, with `fix` when it fails |
| `POST /api/brains/{id}/default` | the default brain (and the Agent room's, for Claude Code and Codex) |
| `GET /api/brains/link` · `POST …/link/key` · `…/link/test` · `…/link/save` | linking another computer |
| `…/signin`, `…/signin/code`, `…/signin/cancel`, `…/signout`, `…/status` | the CLI's own sign-in, as before |

Tests: `tests/test_agents_connect.py` (stand-in CLIs, npm and ssh; the Keychain case end to end with a stand-in Mac),
`tests/test_signin.py`, and the page tests in `tests/test_shell_page.py`.
