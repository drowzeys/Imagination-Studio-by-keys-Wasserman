# Local add-ons

An operator can keep a room, a tab or a CLI agent beside Studio instead of editing its built-in lists.
Add-ons are **off by default**. Set `STUDIO_ADDONS=1` before starting Studio to load folders in
`<studio home>/addons/<name>/`. Restart after installing or changing a server module.
Without the opt-in, or without that folder, nothing is loaded. No add-on is fetched from the network.
Folder names are lowercase letters, digits, `_` and `-`, beginning with a letter (up to 48 characters).

**A server module is executable Python code installed and trusted by the operator. A client script is
executable JavaScript with the Studio page's access. Neither is sandboxed.** Do not install an untrusted
add-on. There is no remote installer, upload endpoint or automatic download.

## A room and its tab

Place `client.js` in the add-on folder. Studio loads it after its own modules, before the initial navigation.
For example, `addons/sample/client.js`:

```js
StudioAddons.register({
  rooms: [{ id: "sample", name: "Sample", group: "engines", tabs: ["Sample"] }],
  tabs: [{ id: "Sample", room: "sample", label: "Sample", view: () =>
    el("div", { class: "panel" }, el("h2", {}, "Sample")) }]
});
```

The groups are `apps`, `engines`, `agent` and `vaults`. A tab may also name an existing room.
Built-in room/tab ids cannot be replaced. A room uses the Studio's generic Agent icon.
An add-on can serve local `.js`, `.css`, `.svg` and `.png` assets under `/api/addons/<name>/assets/`;
Python files, hidden files and paths escaping the folder are not assets.

## A server route

An optional `server.py` exports `register(addon)`. Its router is prefixed with `/api/addons/<name>`.
Use the provided queue for all render/processing jobs: it is the same admission, memory checks and
job queue as the built-in app. Do not start a renderer, load models or run ComfyUI beside it.

```python
def register(addon):
    @addon.router.post("/still")
    def still(body: dict):
        return addon.queue({"kind": "image", "params": body})
```

`addon.home` is the Studio's data folder. All add-on routes and assets stay behind Studio's access-key,
trusted-network and write-origin checks. WebSockets and mounted child applications are not supported.
An invalid add-on is logged and skipped; other add-ons and the built-in app continue.

## A CLI agent

The module can call `addon.agent(id, label, command, ask=..., argv=..., read=..., session=True,
signin=..., hints=...)`. The id must be new. The executable is found on the same PATH as built-in CLIs,
or through the existing remote CLI setting. Installation and sign-in happen in its own terminal;
`signin` explains them. `hints` is a list of `(error regex, message)` pairs; a message may use `{where}`.
Studio's Agent, Connect and Brain menus include the backend automatically.

- `ask(tool, host, system, prompt, tier, model, timeout)` returns one text answer for writing assistance.
  Use `agents.run_cli` with its supplied timeout; do not wait indefinitely in a request handler.
- Supply both `argv(task, tool, config_path, servers, system, session)` and `read(line, task)` for a harness.
  `argv` returns an argument list, never a shell string. `read` turns each stdout line into displayed text
  and updates `task['reply']`; it may add working steps. `servers`/`config_path` contain only this turn's
  chosen MCP tools, including Studio, whose render calls use the normal job queue and memory admission.
- `session=True` lets a successful turn retain `task['_session']['id']`, assigned by `read`, for the next
  turn's `session` argument. Leave it off for stateless adapters. A cancelled/failed turn does not replace
  the conversation's session.

A harness runs through the existing agent worker and stream runner: the same task queue, working folder,
attachments, timeout, cancellation and output collection. It must honor the supplied working folder,
selected MCP tools and system instructions, just as the built-in adapters do. This interface is a trust
contract, not an OS sandbox; arbitrary operator-installed Python could bypass it.

The language hook and translation dictionaries are separate from this interface.
