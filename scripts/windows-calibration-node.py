"""Calibration-only GPU-first loading and peak receipts; uses ComfyUI's load helpers and PyTorch counters."""
import torch
import comfy.model_management
import comfy.sd
import comfy.utils
import folder_paths
from aiohttp import web
from server import PromptServer
from windows_memory import process_memory
from windows_backend_handle import identity


def allocation_receipt():
    return {
        "peak_tensor_bytes": torch.cuda.max_memory_allocated(0),
        "peak_reserved_bytes": torch.cuda.max_memory_reserved(0),
        "reserved_bytes": torch.cuda.memory_reserved(0),
        "allocator": torch.cuda.memory.get_allocator_backend(),
        "gpu_fraction": torch.cuda.get_per_process_memory_fraction(0),
        "gpu_total_bytes": torch.cuda.get_device_properties(0).total_memory,
        "process_memory": process_memory(-1),
        "process_identity": identity(),
    }


@PromptServer.instance.routes.get("/windows_calibration/metrics")
async def metrics(_request):
    # Do not synchronize: cancellation may be observed while a GPU step is still running.
    return web.json_response(allocation_receipt())


class WindowsCalibrationCheckpointLoader:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"ckpt_name": (folder_paths.get_filename_list("checkpoints"),)}}

    RETURN_TYPES = ("MODEL", "CLIP", "VAE")
    FUNCTION = "load"
    CATEGORY = "studio/calibration"

    def load(self, ckpt_name):
        path = folder_paths.get_full_path_or_raise("checkpoints", ckpt_name)
        device = comfy.model_management.get_torch_device()
        state, metadata = comfy.utils.load_torch_file(path, device=device, return_metadata=True)
        result = comfy.sd.load_state_dict_guess_config(
            state, metadata=metadata, embedding_directory=folder_paths.get_folder_paths("embeddings"),
            model_options={"load_device": device, "offload_device": device},
        )
        if result is None:
            raise RuntimeError("the GPU-first calibration loader could not identify this checkpoint")
        return result[:3]



class WindowsCalibrationReceipt:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"audio": ("AUDIO",)}}

    RETURN_TYPES = ()
    FUNCTION = "report"
    OUTPUT_NODE = True
    CATEGORY = "studio/calibration"

    def report(self, audio):
        torch.cuda.synchronize()
        receipt = allocation_receipt()
        return {"ui": {"windows_calibration": [receipt]}, "result": ()}


class WindowsCalibrationImageReceipt:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"images": ("IMAGE",)}}

    RETURN_TYPES = ()
    FUNCTION = "report"
    OUTPUT_NODE = True
    CATEGORY = "studio/calibration"

    def report(self, images):
        torch.cuda.synchronize()
        return {"ui": {"windows_calibration": [allocation_receipt()]}, "result": ()}


NODE_CLASS_MAPPINGS = {
    "WindowsCalibrationReceipt": WindowsCalibrationReceipt,
    "WindowsCalibrationCheckpointLoader": WindowsCalibrationCheckpointLoader,
    "WindowsCalibrationImageReceipt": WindowsCalibrationImageReceipt,
}
