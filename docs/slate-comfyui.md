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
