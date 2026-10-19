"""The Studio's MMAudio sampler for ComfyUI on a DGX Spark: kijai's ComfyUI-MMAudio, with the clip's frames taken at
the times MMAudio reads them.

MMAudio watches a clip twice: CLIP sees 8 frames a second (384x384) and Synchformer 25 a second (224x224), and the
sound is placed by the second. kijai's MMAudioSampler (ComfyUI-MMAudio, pinned in oneshot-setup-spark.sh --sound)
takes the first 8 x seconds and the first 25 x seconds of whatever frames it is given as those two streams, so a 24 fps
clip would put the footfalls a little early and show CLIP only the first third of the clip. This node takes the
frames and the clip's own rate (GetVideoComponents gives both), picks the frame on screen at each of those times, as
MMAudio's own load_video does, and runs kijai's loaders' models through kijai's own generate. spark/comfy.sh copies
it into ComfyUI/custom_nodes when ComfyUI-MMAudio is there.

Credit: Ho Kei Cheng et al. (MMAudio), kijai (ComfyUI-MMAudio).
"""
import sys

CLIP_SIZE, CLIP_FPS, SYNC_SIZE, SYNC_FPS = 384, 8.0, 224, 25.0


def times(count: int, fps: float, rate: float, seconds: float) -> list[int]:
    """The index of the frame on screen at each 1/rate step of ``seconds``, in a clip of ``count`` frames at ``fps``."""

    steps = int(rate * seconds)
    return [min(count - 1, int(i / rate * fps + 1e-6)) for i in range(steps)]


def kijai():
    """kijai's nodes module, as ComfyUI loaded it (generate and FlowMatching are names in it)."""

    import nodes
    sampler = nodes.NODE_CLASS_MAPPINGS.get("MMAudioSampler")
    if sampler is None:
        raise RuntimeError("ComfyUI-MMAudio is not loaded: bash oneshot-setup-spark.sh --sound, then bash spark/comfy.sh stop")
    return sys.modules[sampler.__module__]


class StudioMMAudioSampler:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "mmaudio_model": ("MMAUDIO_MODEL",),
            "feature_utils": ("MMAUDIO_FEATUREUTILS",),
            "images": ("IMAGE",),
            "fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 240.0, "step": 0.001}),
            "duration": ("FLOAT", {"default": 8.0, "min": 1.0, "max": 60.0, "step": 0.01}),
            "steps": ("INT", {"default": 25, "min": 1, "max": 200}),
            "cfg": ("FLOAT", {"default": 4.5, "min": 0.0, "max": 20.0, "step": 0.1}),
            "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
            "prompt": ("STRING", {"default": "", "multiline": True}),
            "negative_prompt": ("STRING", {"default": "", "multiline": True}),
        }}

    RETURN_TYPES = ("AUDIO",)
    FUNCTION = "sample"
    CATEGORY = "MMAudio"

    def sample(self, mmaudio_model, feature_utils, images, fps, duration, steps, cfg, seed, prompt, negative_prompt):
        import torch
        from torchvision.transforms import v2
        import comfy.model_management as mm

        module = kijai()
        device, offload = mm.get_torch_device(), mm.unet_offload_device()
        count = int(images.shape[0])
        duration = min(float(duration), count / float(fps))
        frames = images.permute(0, 3, 1, 2)  # N, C, H, W, floats 0..1
        clip_resize = v2.Resize((CLIP_SIZE, CLIP_SIZE), interpolation=v2.InterpolationMode.BICUBIC, antialias=True)
        sync_resize = v2.Compose([v2.Resize(SYNC_SIZE, interpolation=v2.InterpolationMode.BICUBIC, antialias=True),
                                  v2.CenterCrop(SYNC_SIZE), v2.Normalize(mean=[0.5] * 3, std=[0.5] * 3)])
        clip = torch.stack([clip_resize(frames[i]) for i in times(count, fps, CLIP_FPS, duration)]).clamp(0, 1)
        sync = torch.stack([sync_resize(frames[i].clamp(0, 1)) for i in times(count, fps, SYNC_FPS, duration)])
        # MMAudio's sequence lengths follow the seconds its streams cover
        duration = min(clip.shape[0] / CLIP_FPS, sync.shape[0] / SYNC_FPS)
        clip, sync = clip[:int(CLIP_FPS * duration)], sync[:int(SYNC_FPS * duration)]
        seq_cfg = mmaudio_model.seq_cfg
        seq_cfg.duration = duration
        mmaudio_model.update_seq_lengths(seq_cfg.latent_seq_len, seq_cfg.clip_seq_len, seq_cfg.sync_seq_len)
        rng = torch.Generator(device=device)
        rng.manual_seed(seed)
        feature_utils.to(device)
        mmaudio_model.to(device)
        try:
            with torch.inference_mode():
                audios = module.generate(clip.unsqueeze(0).to(device), sync.unsqueeze(0).to(device), [prompt],
                                         negative_text=[negative_prompt], feature_utils=feature_utils, net=mmaudio_model,
                                         fm=module.FlowMatching(min_sigma=0, inference_mode="euler", num_steps=steps),
                                         rng=rng, cfg_strength=cfg)
        finally:
            mmaudio_model.to(offload)
            feature_utils.to(offload)
            mm.soft_empty_cache()
        return ({"waveform": audios.float().cpu(), "sample_rate": seq_cfg.sampling_rate},)


NODE_CLASS_MAPPINGS = {"StudioMMAudioSampler": StudioMMAudioSampler}
NODE_DISPLAY_NAME_MAPPINGS = {"StudioMMAudioSampler": "MMAudio Sampler (clip's own frame rate)"}
