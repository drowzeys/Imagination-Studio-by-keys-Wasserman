# Render Slate shots on your ComfyUI PC

Run ComfyUI on your NVIDIA PC with the FastH3 nodes and weights installed. Open the workflow there and verify it writes an MP4. Keep ComfyUI on a private connection, such as an SSH tunnel from the Mac to the worker's loopback port. Do not expose ComfyUI to the internet.

Export each verified workflow as **API Format**. Keep the workflows and profile manifest on the Studio machine. The profile controls resolution, duration and model. Use the workflow's actual node IDs and input names in this example manifest.

```json
{
  "fasth3": {
    "label": "FastH3 V2 · 768p",
    "workflow": "/absolute/path/to/fasth3-api.json",
    "inputs": {"prompt": ["6", "text"], "seed": ["12", "noise_seed"]},
    "output": "25"
  },
  "omniref": {
    "label": "FastH3 OmniRef · 480p · two images",
    "workflow": "/absolute/path/to/omniref-api.json",
    "inputs": {
      "prompt": ["6", "text"],
      "seed": ["12", "noise_seed"],
      "references": [["21", "image"], ["22", "image"]]
    },
    "output": "25"
  }
}
```

Set these environment variables when starting the Studio server. The URL is the ComfyUI endpoint reachable from the Studio server. The workflow files are trusted operator configuration.

```sh
export STUDIO_COMFY_URL=http://127.0.0.1:8188
export STUDIO_COMFY_WORKFLOWS=/absolute/path/to/profiles.json
```

In Slate, open a shot and find **Render on my PC · ComfyUI**. For a reference workflow, enter the paths of images already in the Studio Library. For example, upload the character and boat stills through the existing Studio upload controls and reuse their paths for both shots.

The panel uses the manually edited H3 prompt if present, or the shot prompt, and uses the shot seed. It reports waiting status, then imports the finished MP4 under Takes. Keep the Studio tab open while waiting. If the connection fails, **Resume checking** uses the same ComfyUI job ID. Reopening the browser tab clears the panel's job state, and the output can still be imported manually from ComfyUI. The Studio's native queue and model installer are independent of this panel.


## Comfy Cloud instead of your own PC

The same panel can send a shot to [Comfy Cloud](https://cloud.comfy.org), Comfy Org's hosted ComfyUI, for a machine
or a place where MiniMax H3 cannot be run locally. It needs your own Comfy Cloud API key (made at
platform.comfy.org; API access needs a paid Comfy Cloud plan) and each render is paid for from that plan.

```sh
export STUDIO_COMFY_URL=https://cloud.comfy.org
export STUDIO_COMFY_API_KEY=your-key
export STUDIO_COMFY_WORKFLOWS=/absolute/path/to/profiles.json
```

- **Workflows:** the profile manifest is the same. Build the workflow on Comfy Cloud (its MiniMax H3 templates, or
  MiniMax's partner nodes), check that it writes an MP4, and export it as **API Format**. Comfy Cloud's models and
  nodes are the ones it has installed; you cannot add a node pack there.
- **The key:** it is read from the environment only. The Studio never writes it to a file, a log or a page, sends it
  only over https and only to the address above, and fetches the finished file from the cloud's signed address
  without it. It is also put in the workflow's `extra_data`, which is how Comfy Cloud bills partner nodes.
- **What leaves this machine:** the shot's prompt, its seed and the reference pictures you chose. Nothing else.
- **The panel** is titled **Render on Comfy Cloud** and says the render is paid for. A refused key, missing credits
  or an inactive plan is said in those words.
- **Limits are the cloud's:** reference pictures up to 20 MB here (50 MB there), a job's longest run by your plan.
- **Not checked against the live service.** This was written from Comfy Cloud's published API and tested against a
  stand-in, since it needs a paid key. If something does not match, please open an issue with the message shown.

## What the Studio does not do for these renders

A render sent from this panel runs on the other PC's ComfyUI, not in the Studio's queue: it is not in the job list,
Cancel does not reach it, and the Studio's memory guard and thermal gate do not see it. That is why the address may
not be the Studio's own ComfyUI (loopback port 8190, or 8191 on a Mac; the panel refuses it), and why a ComfyUI on
the same machine as the Studio is not supported: use the Studio's own renderers there. For a worker behind an SSH
tunnel, forward to any other local port, for example `ssh -L 8188:127.0.0.1:8188 you@worker`.

With `STUDIO_COMFY_URL` or `STUDIO_COMFY_WORKFLOWS` unset, the panel is not shown and nothing is sent anywhere.
