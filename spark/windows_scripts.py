"""The Studio's render scripts on native Windows (the CUDA tier, app/windows_cuda.py): scripts/video.sh, studio.sh,
scout.sh, image.sh, edit.sh and turnaround.sh as a DGX Spark runs them (their spark/ branches), in Python, with the same arguments
and environment, so the app's jobs build the same commands on both:

    python spark/windows_scripts.py video "a prompt" out.mp4          # scripts/video.sh -> spark/video.sh
    python spark/windows_scripts.py studio "the picture" "what happens" out.mp4
    python spark/windows_scripts.py scout "the picture" 4 scouts/x
    python spark/windows_scripts.py image "a prompt" out.png           # scripts/image.sh -> spark/image.sh
    python spark/windows_scripts.py edit photo.png "make it night" out.png
    python spark/windows_scripts.py turnaround subject.png folder/turn    # scripts/turnaround.sh (its Linux branch)

Each one checks what its script checks, then runs spark_generate.py (or spark_edit.py) with this same python (the GPU
venv's), which starts the studio's ComfyUI through spark/comfy_win.py. Keep the checks and the words in step with the
bash scripts: a refusal reads the same on both.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent / "app"
PREFIX = Path(os.environ.get("PREFIX") or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ImaginationStudio")
LEAD = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."


def env(name: str, default: str = "") -> str:
    return os.environ.get(name) or default


def fail(message: str, code: int = 2) -> None:
    print(message, file=sys.stderr, flush=True)
    raise SystemExit(code)


def generate(script: str, args: list[str], extra: dict | None = None) -> None:
    """spark_generate.py (or spark_edit.py) with this python, PREFIX set; its status is this script's."""

    child = {**os.environ, "PREFIX": str(PREFIX), **(extra or {})}
    code = subprocess.call([sys.executable, "-X", "utf8", str(HERE / script), *args], env=child)
    if code:
        raise SystemExit(code)


def ref_list(text: str) -> list[str]:
    """A list of files from the environment: separated by spaces, or one per line when a path has spaces."""

    if not text:
        return []
    if "\n" in text:
        return [line for line in text.splitlines() if line]
    return text.split()


def ambient(prompt: str, prompt_file: str, references: int) -> tuple[str, str]:
    """scripts/quiet-prompt.sh's ambient_prompt: (prompt, prompt_file) as they go to the renderer."""

    sys.path.insert(0, str(APP))
    import negation

    flags = dict(video_sound=env("REF_VIDEO_SOUND", "0") == "1", plain=env("REF_PLAIN", "0") == "1",
                 references=bool(references or env("REF_IMAGES") or env("REF_VIDEOS") or env("REF_AUDIOS")))
    if prompt_file:
        if not Path(prompt_file).is_file():
            fail(f"no prompt file at {prompt_file}")
        before = Path(prompt_file).read_text(encoding="utf-8")
        try:
            after = negation.ensure_ambient(before, **flags)
        except Exception:  # noqa: BLE001 - as the script: sent as written
            print("[tensorfold] prompt: could not be prepared, sent as written", file=sys.stderr)
            return prompt, prompt_file
        if after.rstrip("\n") == before.rstrip("\n"):
            return prompt, prompt_file
        prompt, prompt_file = after.rstrip("\n"), ""
    else:
        try:
            after = negation.ensure_ambient(prompt, **flags)
        except Exception:  # noqa: BLE001
            print("[tensorfold] prompt: could not be prepared, sent as written", file=sys.stderr)
            return prompt, prompt_file
        if after == prompt:
            return prompt, prompt_file
        prompt = after.rstrip("\n")  # a prepared prompt ends where its last field does, as compose() writes it
    print("[tensorfold] prompt: no spoken line and no overall_soundscape heading, so the clip is told: Only ambient sound is heard.", flush=True)
    return prompt, prompt_file


def video(argv: list[str]) -> None:
    """spark/video.sh: one clip with FastH3 (or MiniMax H3, or Ref2VA from references) through ComfyUI."""

    if env("ENGINE", "fasth3") != "fasth3":
        fail("the Spark build renders with FastH3 only (ENGINE=fasth3)")
    if env("X2", "0") == "1":
        fail("the 2x decoder is not part of the Spark build: use WIDTH and HEIGHT up to 1344x768")
    prompt = argv[0] if argv else ""
    out = argv[1] if len(argv) > 1 else "outputs/h3.mp4"
    width, height = env("WIDTH", "864"), env("HEIGHT", "480")
    args = ["video", "-o", out, "--width", width, "--height", height, "--frames", env("FRAMES", "124"), "--seed", env("SEED", "0")]
    refs, clips, sounds = ref_list(env("REF_IMAGES")), ref_list(env("REF_VIDEOS")), ref_list(env("REF_AUDIOS"))
    count = len(refs) + len(clips) + len(sounds)
    if count:
        if env("FIRST_FRAME") or env("LAST_FRAME"):
            fail("references (REF_IMAGES, REF_VIDEOS, REF_AUDIOS) and FIRST_FRAME / LAST_FRAME do not combine: a reference is not a frame of the clip")
        if len(refs) > 9:
            fail(f"at most 9 reference pictures, got {len(refs)}")
        if len(clips) > 3:
            fail(f"at most 3 reference videos, got {len(clips)}")
        if len(sounds) > 3:
            fail(f"at most 3 reference audio clips, got {len(sounds)}")
        if count > 12:
            fail(f"at most 12 references in all, got {count}")
        if sounds and not (refs or clips):
            fail("REF_AUDIOS needs a reference picture or video beside it (REF_IMAGES or REF_VIDEOS): the model does not take sound alone")
        for flag, what, files in (("--ref", "picture", refs), ("--ref-video", "video", clips), ("--ref-audio", "audio clip", sounds)):
            for one in files:
                if not Path(one).is_file() or not Path(one).stat().st_size:
                    fail(f"no reference {what} at {one}")
                args += [flag, one]
        if env("REF_VIDEO_SIZE", "match") != "match":
            fail(f"REF_VIDEO_SIZE={env('REF_VIDEO_SIZE')} is not offered on a DGX Spark: a reference video is always brought down to the clip's pixel area there (match), for memory")
        if clips and env("REF_VIDEO_SOUND", "0") == "1":
            args.append("--ref-video-sound")
        steps = env("STEPS")
        if steps:
            if not steps.isdigit() or steps == "0":
                fail(f"STEPS must be a positive number of passes, got {steps}")
            args += ["--steps", steps]
        turbo = env("REF_TURBO", "0") == "1"
        args += ["--ref-mode", "turbo" if turbo else "full"]
        passes = steps or ("4" if turbo else "20")
        if env("REF_SIZE"):
            args += ["--ref-size", env("REF_SIZE")]
        print(f"[tensorfold] reference to video: {passes} passes" + (" with the Ref2VA Turbo adapter" if turbo else "")
              + f", {len(refs)} picture(s), {len(clips)} video(s), {len(sounds)} audio clip(s)", flush=True)
        kind = f"MiniMax H3 Ref2VA, {len(refs)} picture(s), {len(clips)} video(s), {len(sounds)} audio clip(s), {passes} passes"
    elif env("SPARK_BASE", "fasth3") == "h3":
        args += ["--base", "h3", "--steps", env("STEPS", "25")]
        kind = f"MiniMax H3 {env('STEPS', '25')} passes"
    else:
        args += ["--steps", env("STEPS", "8")]
        kind = f"FastH3 {env('STEPS', '8')} passes"
    if env("FIRST_FRAME"):
        args += ["--first-frame", env("FIRST_FRAME")]
    if env("LAST_FRAME"):
        if not Path(env("LAST_FRAME")).is_file() or not Path(env("LAST_FRAME")).stat().st_size:
            fail(f"no last frame at {env('LAST_FRAME')}")
        args += ["--last-frame", env("LAST_FRAME")]
    for name, flag in (("TEXT_ENCODER", "--text-encoder"), ("CROP", "--crop"), ("SPARK_SPARSITY", "--sparsity"), ("SPARK_ENGINE", "--engine")):
        if env(name):
            args += [flag, env(name)]
    for one in env("LORA").splitlines():
        if one:
            args += ["--lora", one]
    if count and env("REF_PLAIN", "0") == "1":
        args.append("--ref-plain")
    prompt, prompt_file = ambient(prompt, env("PROMPT_FILE"), count)
    args += ["--prompt-file", prompt_file] if prompt_file else ["--prompt", prompt]
    began = time.time()
    generate("spark_generate.py", args)
    rate = env("DELIVERY_AUDIO_RATE")
    if rate and shutil.which("ffprobe"):
        if rate not in ("44100", "48000", "96000"):
            fail("DELIVERY_AUDIO_RATE is 44100, 48000 or 96000")
        said = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate,channels",
                               "-of", "csv=p=0", out], capture_output=True, text=True, timeout=60).stdout.strip()
        if said and said != f"{rate},2":
            target = Path(out)
            normalized = target.with_name(f"{target.stem}.audio-{os.getpid()}.mp4")
            try:
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", out, "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy", "-c:a", "aac",
                                "-b:a", "256k", "-ar", rate, "-ac", "2", "-movflags", "+faststart", str(normalized)], check=True, timeout=600)
                os.replace(normalized, target)
            finally:
                normalized.unlink(missing_ok=True)
            print(f"[studio] delivery sound: stereo {int(rate) // 1000} kHz AAC (the picture copied unchanged)", flush=True)
    print(f"[studio] {kind}, {width}x{height}, total {int(time.time() - began)} s -> {out}", flush=True)


def image(argv: list[str], extra: str | None = None) -> None:
    """spark/image.sh: one picture (or several seeds) with Qwen-Image-2.1, FLUX3-K or MOSS-Image through ComfyUI."""

    prompt = argv[0] if argv else ""
    out = argv[1] if len(argv) > 1 else "outputs/image.png"
    child = {}
    turbo = env("TURBO", "1")
    if env("IMAGE_MODEL", "qwen") == "qwen-full":
        child["IMAGE_MODEL"], turbo = "qwen", "0"
    args = ["image", "-o", out, "--width", env("WIDTH", "1344"), "--height", env("HEIGHT", "768"), "--seed", env("SEED", "0")]
    if turbo != "1":
        args += ["--base", "--steps", env("STEPS", "40")]
    if env("UPSAMPLE", "1") != "1":
        args.append("--no-upsample")
    if env("NEGATIVE"):
        args += ["--negative", env("NEGATIVE")]
    args += ["--prompt-file", env("PROMPT_FILE")] if env("PROMPT_FILE") else ["--prompt", prompt]
    args += (extra if extra is not None else env("EXTRA")).split()  # word-split as bash splits ${EXTRA:-}
    generate("spark_generate.py", args, child)


def scout(argv: list[str]) -> None:
    """scripts/scout.sh: several pictures of one prompt from one model load, and a contact sheet to choose from."""

    prompt = argv[0] if argv else ""
    count = int(argv[1]) if len(argv) > 1 and argv[1] else 4
    folder = Path(argv[2] if len(argv) > 2 and argv[2] else "outputs/scout")
    first = int(env("SEED", "1"))
    seeds = [str(seed) for seed in range(first, first + count)]
    os.environ.setdefault("WIDTH", "1280")
    os.environ.setdefault("HEIGHT", "736")
    folder.mkdir(parents=True, exist_ok=True)
    image([prompt, str(folder / "scout.png")], f"{env('EXTRA')} --seeds {','.join(seeds)}")
    from PIL import Image, ImageDraw

    images = [(seed, Image.open(folder / f"scout_s{seed}.png").convert("RGB")) for seed in seeds]
    columns = 2 if len(images) <= 4 else 3
    width = 640
    height = images[0][1].height * width // images[0][1].width
    rows = -(-len(images) // columns)
    sheet = Image.new("RGB", (columns * width, rows * height), "black")
    for index, (seed, picture) in enumerate(images):
        tile = picture.resize((width, height))
        draw = ImageDraw.Draw(tile)
        draw.rectangle((0, 0, 110, 26), fill="black")
        draw.text((8, 6), f"seed {seed}", fill="white")
        sheet.paste(tile, ((index % columns) * width, (index // columns) * height))
    sheet.save(folder / "sheet.jpg", quality=90)
    print(f"[studio] {len(images)} scouts in {folder}; contact sheet {folder / 'sheet.jpg'}", flush=True)


def studio(argv: list[str]) -> None:
    """scripts/studio.sh: text to image to video in one go (or an image already made, IMAGE_FILE)."""

    image_prompt = argv[0] if argv else ""
    video_prompt = argv[1] if len(argv) > 1 else ""
    out = argv[2] if len(argv) > 2 else "outputs/studio.mp4"
    picture = str(Path(out).with_suffix(".png"))
    if env("QHD", "0") == "1":
        os.environ.update(WIDTH="2560", HEIGHT="1472", X2="1", CROP="2560x1440", IMAGE_WIDTH=env("IMAGE_WIDTH", "1280"), IMAGE_HEIGHT=env("IMAGE_HEIGHT", "736"))
    if env("TWOK", "0") == "1":
        os.environ.update(WIDTH="2048", HEIGHT="1152", X2="1", IMAGE_WIDTH=env("IMAGE_WIDTH", "1024"), IMAGE_HEIGHT=env("IMAGE_HEIGHT", "576"))
    os.environ["WIDTH"], os.environ["HEIGHT"] = env("WIDTH", "1344"), env("HEIGHT", "768")
    if env("VIDEO_PROMPT_FILE"):
        video_prompt = Path(env("VIDEO_PROMPT_FILE")).read_text(encoding="utf-8").rstrip("\n")
    if not video_prompt:
        fail("give a video prompt or VIDEO_PROMPT_FILE")
    if not video_prompt.startswith("For the target video") and env("RAW_PROMPT", "0") != "1":
        video_prompt = f"{LEAD}\n\n{video_prompt}"
    began = time.time()
    if env("IMAGE_FILE"):
        if not Path(env("IMAGE_FILE")).is_file() or not Path(env("IMAGE_FILE")).stat().st_size:
            fail(f"no image at {env('IMAGE_FILE')}")
        picture = env("IMAGE_FILE")
    else:
        saved = {k: os.environ.get(k) for k in ("SEED", "PROMPT_FILE", "WIDTH", "HEIGHT")}
        os.environ.update(SEED=env("IMAGE_SEED", env("SEED", "0")), PROMPT_FILE=env("IMAGE_PROMPT_FILE"),
                          WIDTH=env("IMAGE_WIDTH", env("WIDTH")), HEIGHT=env("IMAGE_HEIGHT", env("HEIGHT")))
        image([image_prompt, picture])
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    if not Path(picture).is_file() or not Path(picture).stat().st_size:
        fail("no image was written", 1)
    middle = time.time()
    os.environ.update(SEED=env("VIDEO_SEED", env("SEED", "0")), PROMPT_FILE="", FIRST_FRAME=picture)
    video([video_prompt, out])
    end = time.time()
    print(f"[studio] image {int(middle - began)} s, video {int(end - middle)} s, total {int(end - began)} s -> {picture}, {out}", flush=True)


def edit(argv: list[str]) -> None:
    """spark/edit.sh: a picture edited with Qwen-Image-2.1 or FLUX3-K through ComfyUI."""

    source = argv[0] if argv else ""
    prompt = argv[1] if len(argv) > 1 else ""
    out = argv[2] if len(argv) > 2 else ""
    if not Path(source).is_file():
        fail('give the picture to edit: scripts/edit.sh photo.png "what changes" out.png', 1)
    args = ["--image", source, "--seed", env("SEED", "0"), "--size", env("SIZE", "source"), "--resolution", env("RESOLUTION", "1024"),
            "--model", env("IMAGE_MODEL", "qwen")]
    if env("UPSAMPLE", "1") == "0":
        args.append("--no-upsample")
    for one in env("IMAGES").splitlines():
        if one:
            args += ["--image", one]
    if env("WIDTH") and env("HEIGHT"):
        args += ["--width", env("WIDTH"), "--height", env("HEIGHT")]
    if env("MOST_PIXELS"):
        args += ["--most", env("MOST_PIXELS")]
    if env("BASE", "0") == "1":
        args += ["--base", "--steps", env("STEPS", "40")]
    args += ["--set", "@" + env("SET_FILE")] if env("SET_FILE") else ["--prompt", prompt, "-o", out]
    for one in env("LORA").splitlines():
        if one:
            args += ["--lora", one]
    generate("spark_edit.py", args)


TURN_PROMPT = ("the camera orbits horizontally around the subject of <Picture 1> at eye level, ninety degrees clockwise, the "
               "subject stays upright on the ground")


def turnaround(argv: list[str]) -> None:
    """scripts/turnaround.sh's Linux branch: five views of one picture's subject with the Turnaround LoRA on MiniMax H3
    Ref2VA, through MATLOWAI's contact-sheet nodes (spark_workflows.py turnaround)."""

    ref = argv[0] if argv else ""
    out = argv[1] if len(argv) > 1 else "outputs/turn"
    if not ref or not Path(ref).is_file() or not Path(ref).stat().st_size:
        fail(f"usage: turnaround.sh PICTURE OUT_PREFIX (no picture at '{ref}')")
    size, strength, steps = env("SIZE", "1024"), env("STRENGTH", "1.0"), env("STEPS", "28")
    prompt = env("PROMPT", TURN_PROMPT)
    if size not in ("512", "1024", "2048"):
        fail(f"SIZE is 512, 1024 or 2048, got {size}")
    if "<Picture 1>" not in prompt:
        fail("the prompt names the picture as <Picture 1>")
    home = env("STUDIO_HOME") or subprocess.run([sys.executable, str(APP / "studio_home.py")], capture_output=True,
                                                text=True).stdout.strip()
    lora = env("TURNAROUND_LORA", str(Path(home) / "loras" / "turnaround.safetensors"))
    if not Path(lora).is_file() or not Path(lora).stat().st_size:
        fail(f"no Turnaround LoRA at {lora}: install it in the LoRA hub")
    generate("spark_workflows.py", ["turnaround", "--ref", ref, "--lora", f"{lora}:{strength}", "--size", size,
                                    "--seed", env("SEED", "0"), "--steps", steps, "--prompt", prompt, "-o", out])


COMMANDS = {"video": video, "studio": studio, "scout": scout, "image": image, "edit": edit, "turnaround": turnaround}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in COMMANDS:
        print("usage: windows_scripts.py video|studio|scout|image|edit|turnaround ARGS...", file=sys.stderr)
        return 2
    if not Path(sys.executable).is_file() or not (PREFIX / "ComfyUI" / "main.py").is_file():
        print(f"run oneshot-setup-rtx-spark-n1x.ps1 -Native first: no environment at {PREFIX}", file=sys.stderr)
        return 1
    COMMANDS[argv[0]](argv[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
