"""Character Swap, a Library action on a clip: the person in a 5 to 15 second part of one continuous shot is replaced by
the character in one picture. The scene, the performance and the clip's own sound stay.

It is what Comfy Org's Openjutsu app does (MIT), made inside the studio: no ComfyUI to open, no cloud and no key. One
job of the studio's queue (kind ``swap``) runs spark/spark_swap.py on the studio's own ComfyUI engine with ComfyUI's
own nodes: SAM 3.1 finds the largest person in the part's first frame (or the one a few words name, ``person``) and follows them, the picture model puts the
character into the first frame (the anchor, FLUX3-K Fast), then MiniMax H3 Ref2VA with Akatz Labs' Character Swap
LoRA and LightX2V's 8-step Turbo LoRA draws the person again in windows of at most 124 frames, each starting with
the last 18 of the one before (those included in its 124). The settings are the ones Akatz Labs published for the LoRA; the code is the studio's own.

Its result is renders/<job>.mp4 (the part, swapped, with the part's own sound), with the part's mask preview
(masks/<job>/person.mp4: the person who is swapped, tinted green) and the anchor (scouts/<job>/anchor.png). It runs on
a DGX Spark or another NVIDIA GPU with MiniMax H3 Ref2VA (oneshot-setup-spark.sh --swap; oneshot-setup-rtx-6000.sh
--swap on an RTX Pro 6000); not on a Mac.

Several people in one job (``swaps``, at most four, each with its own picture): the chain of Noncoder's MiniMax H3
Multi Swap workflow, each swap made on the result of the one before. A job with ``who`` only follows everyone SAM 3.1
finds through the part, whenever they come into view, and numbers them (scouts/<job>/people.png); ``which`` in a
swap is one of those numbers. A person picked by number is drawn in a crop around them and pasted back by their mask
(spark/spark_swap.py).
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException

import windows_cuda  # noqa: E402  (the native Windows CUDA tier, beside this file)

# the windows a part is made in: spark/swap_plan.py, shared with the script that renders it
_spec = importlib.util.spec_from_file_location("swap_plan", Path(__file__).resolve().parent.parent / "spark" / "swap_plan.py")
plan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plan)

KINDS = ("swap",)
TITLE = "Character Swap"
SECONDS = (5.0, 15.0)
QUALITIES = (480, 768)
DIRECTION_MOST = 500
PERSON_MOST = 200  # as Remove a person's (app/actions.py): a few words for SAM 3.1, not a sentence
MOST_SWAPS, MOST_PEOPLE = plan.MOST_SWAPS, plan.MOST_PEOPLE  # the people of one job, and the numbers a picture shows
SEED = 904239  # Akatz Labs' workflow's own
WINDOW, HISTORY = 124, 18
PICTURES = (".png", ".jpg", ".jpeg", ".webp")
CLIPS = (".mp4", ".mov")
# the files on a Spark, under $SPARK_MODELS: what the job reads, and the option that installs what is missing
SAM = "Workflows-Comfy/checkpoints/sam3.1_multiplex_fp16.safetensors"
REF2VA = ("MiniMax-H3-Comfy/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors",
          "MiniMax-H3-Comfy/diffusion_models/minimax_h3_ref2va_pruned_bf16.safetensors")
TURBO = "MiniMax-H3-Comfy/loras/minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors"
SWAP_LORA_FILE = "h3_character_swap_v1.5.safetensors"
SWAP_LORA = "MiniMax-H3-Comfy/loras/" + SWAP_LORA_FILE
INSTALL = "bash oneshot-setup-spark.sh --swap"
INSTALL_CARD = "bash oneshot-setup-rtx-6000.sh --swap"  # the same install, on a workstation's card (oneshot-setup-rtx-6000.sh)
QUALITY = 480  # the panel's default: "Fast"; 768 is "High"
WHO = "people.png"  # the picture of a job that numbers the people, in scouts/<job>
# Measured on a DGX Spark (GB10), 2026-10-27, a 5 s and an 8 s part of a 1376x768 clip with one person, int8 Ref2VA on
# ComfyUI's own blocks, <Video 1> at half the canvas, models loaded from the first window on:
#   per window of 124 frames: the prompt and references encoded, the frames encoded and decoded (OVERHEAD), and one
#   pass of eight (PASS); a shorter window takes (frames / 124) ** 1.2 of that
#   SAM: about 6 s to find the person, 0.35 s a frame to follow them; the anchor (Qwen-Image-2.1's edit) 40 to 54 s
# Checked 2026-10-27 on the same Spark: a 10 s part at 480 in three windows (124, 124 and 39 frames, the history in
# them), 660 s against the 670 estimated, the two joins holding
PASS = {480: 22.0, 768: 65.0}
OVERHEAD = {480: 62.0, 768: 113.0}
ANCHOR = {480: 41.0, 768: 54.0}
FIND, FOLLOW, OTHER = 6.0, 0.35, 4.0
LICENCE = ("MiniMax H3 and the Character Swap LoRA (Akatz Labs): MiniMax H3 Community License, not licensed for use in "
           "the EU, the UK, South Korea or the US. Turbo LoRA (LightX2V): Apache-2.0. SAM 3.1 (Meta): SAM License. "
           "The idea and its settings: Comfy Org's Openjutsu and Akatz Labs' workflow (MIT). Several people in one job: "
           "the chain of Noncoder's MiniMax H3 Multi Swap workflow.")


def estimate(seconds: float, quality: int, anchor: bool = True, swaps: int = 1) -> int:
    """The seconds a swap takes on a DGX Spark, from the measured stages (above). Each further person of ``swaps`` is
    followed, anchored and drawn again: one more whole swap less the finding. (A person picked by number is drawn in
    a crop of no more pixels than the frame, which was not timed: the whole frame's time stands for it.)"""

    frames = int(round(float(seconds) * plan.FPS))
    each = FOLLOW * frames + (ANCHOR[quality] if anchor else 0.0)
    for one in plan.windows(frames):
        share = (one["length"] / plan.WINDOW) ** 1.2
        each += (OVERHEAD[quality] + 8 * PASS[quality]) * share
    return int(round(FIND + OTHER + each * max(1, int(swaps))))


def work_folder(home: Path, job_id: str) -> Path:
    """Where a swap keeps its frames while it runs (spark_swap.py --work): gigabytes of pictures for a long part."""

    return home / "renders" / f".swap-{job_id}"


def tidy(home: Path, job_id: str) -> None:
    """After a swap job, however it ended (server.perform): its work folder, if the script could not remove it. A swap
    that made no clip (cancelled, or failed) leaves no mask preview and no anchor either: they belong to the clip."""

    shutil.rmtree(work_folder(home, job_id), ignore_errors=True)
    if not (home / "renders" / f"{job_id}.mp4").is_file():
        # a job that numbered the people made no clip: its picture is what it is for
        keep = (home / "scouts" / job_id / WHO).is_file()
        for folder in (home / "masks" / job_id,) + (() if keep else (home / "scouts" / job_id,)):
            shutil.rmtree(folder, ignore_errors=True)


def on_card() -> bool:
    """Whether this studio is the CUDA build on a workstation's card (an RTX Pro 6000), not a DGX Spark: sparks.card_face."""

    try:
        import sparks  # looks the running server up; importing server here would start a second app

        return sparks.card_face()
    except Exception:  # noqa: BLE001 - no server to ask (a script, a test): named as a DGX Spark
        return False


def attach(home: Path, pack: Path, spark: bool, inside, relative, media_seconds, room=None, transformers=None, tried=None):
    """``media_seconds(path)``: (seconds, has sound) of a media file, or (None, None) when it cannot be measured.
    ``room``: what this machine has for a render with none of the Studio's own engines counted (MB, or None), the
    server's held_room, as sound.attach takes it. ``transformers()``: the file names of other builds of MiniMax H3
    Ref2VA this machine offers (the server's transformers, where its guard warns), or None. ``tried()``: whether
    this machine's guard warns instead of refusing, where a part may be as long as plan.LONGEST_TRIED."""

    router = APIRouter()
    prefix = Path(os.environ.get("PREFIX", Path.home() / ".local/opt/tensorfold-studio"))

    def models() -> Path:
        return Path(os.environ.get("SPARK_MODELS", Path.home() / "spark-studio-models"))

    def swap_lora() -> Path | None:
        """Akatz Labs' Character Swap LoRA, version 1.5, beside the H3 models. (The LoRA hub's Character Swap card is
        version 1, for a clip from references in Create: the action uses the author's recommended 1.5.)"""

        beside = models() / SWAP_LORA
        return beside if beside.is_file() else None

    def span() -> tuple[float, float]:
        """The shortest and longest part: 5 to 15 s, to plan.LONGEST_TRIED where the guard warns (not measured)."""

        return (SECONDS[0], plan.LONGEST_TRIED if tried and tried() else SECONDS[1])

    def ref_sizes() -> list[str]:
        """How the character's picture may reach the model (spark_swap.py REF_IMAGE), the default first: at the
        canvas's pixel area ("match", the measured one), and, where the guard warns instead of refusing, at full
        size ("max", not measured on a DGX Spark), which is then the default: a reference sheet keeps its face."""

        return ["max", "match"] if tried and tried() else ["match"]

    def missing() -> str:
        """Why Character Swap cannot run here, or '' when it can."""

        if not spark:
            return "Character Swap runs on an NVIDIA GPU (a DGX Spark, an RTX Pro 6000) with MiniMax H3 Ref2VA, not on a Mac."
        native = windows_cuda.active()
        # the command this machine's installer takes
        install = windows_cuda.setup_hint("swap") if native else INSTALL_CARD if on_card() else INSTALL
        if not any((models() / one).is_file() for one in REF2VA):
            return f"MiniMax H3 Ref2VA is not installed: run `{install}`."
        if not (models() / SAM).is_file():
            return f"SAM 3.1 is not installed: run `{install}`."
        if not (models() / TURBO).is_file():
            return f"LightX2V's 8-step Turbo LoRA is not installed: run `{install}`."
        if swap_lora() is None:
            return f"Akatz Labs' Character Swap LoRA (version 1.5) is not installed: run `{install}`."
        engine = Path(windows_cuda.python()) if native else prefix / "venv/bin/python"
        if not engine.exists() or not (prefix / "ComfyUI/main.py").is_file():
            return f"The studio's engine is not installed here: run `{install.rsplit(' ', 1)[0]}`."
        return ""

    def weights() -> str:
        """The Ref2VA the job will load (spark_swap.weights): SWAP_WEIGHTS's, else int8, else bf16."""

        asked = os.environ.get("SWAP_WEIGHTS", "int8")
        for one in [asked] + [w for w in ("int8", "bf16") if w != asked]:
            if (models() / REF2VA[0 if one == "int8" else 1]).is_file():
                return one
        return "int8"

    def guard(quality: int) -> dict:
        """The memory guard's word on a swap at ``quality`` (memory_guard.swap_check), as sound.py asks it."""

        import memory_guard  # spark/, on the server's path

        if memory_guard.card_room_applies():
            # Drama 3, Music and Bolt are unloaded before this job (server.perform); their room is the card's raw free
            # before they loaded
            import bolt
            import music
            import voice
            have = memory_guard.card_room_for_job([voice.room_mb(), music.room_mb(), bolt.room_mb()])
            return memory_guard.swap_check(quality, available_mb=have, card=True, weights=weights())
        return memory_guard.swap_check(quality, available_mb=room() if room else None, weights=weights())

    def anchor_model() -> str:
        flux = models() / "FLUX3-K-Comfy/diffusion_models"
        return "flux3k-fast" if any((flux / f).exists() for f in ("flux3-image-klein-bf16.safetensors", "flux3_image_klein_sd_bf16.safetensors")) else "qwen"

    @router.get("/api/swap")
    def state() -> dict:
        why = missing()
        lengths = range(int(span()[0]), int(span()[1]) + 1)
        out = {"title": TITLE, "ready": not why, "why": why, "seconds": list(span()), "qualities": list(QUALITIES), "quality": QUALITY,
                "estimates": {str(q): {str(n): estimate(n, q) for n in lengths} for q in QUALITIES}, "anchor_seconds": {str(q): ANCHOR[q] for q in QUALITIES},
                "anchor": True, "anchor_model": anchor_model(), "seed": SEED, "window": WINDOW, "history": HISTORY,
                "direction_most": DIRECTION_MOST, "person_most": PERSON_MOST, "most_swaps": MOST_SWAPS, "most_people": MOST_PEOPLE,
                "once": FIND + OTHER,  # the seconds of a job that are not one person's: a further person adds the rest
                "ref_sizes": ref_sizes(), "transformers": list(transformers()) if transformers else [], "licence": LICENCE}
        if windows_cuda.native_os():
            import windows_library_checks as library
            found = library.usage_status("swap")
            out.update(found, ready=found["available"])
            if found["qualification_required"]:
                out.update(seconds=[5, 5], qualities=[480], quality=480, anchor=False)
        return out

    def media(params: dict, key: str, suffixes: tuple, what: str) -> Path:
        given = params.get(key)
        if not given or not isinstance(given, str):
            raise HTTPException(400, f"choose {what} from the Library first")
        path = inside(given)
        if not path.is_file() or path.suffix.lower() not in suffixes:
            raise HTTPException(400, f"{given} is not {what} in the studio folder")
        return path

    def number(params: dict, key: str, default, kind=float):
        value = params.get(key)
        if value in (None, ""):
            return default
        if isinstance(value, bool):
            raise HTTPException(400, f"{key} is a number")
        try:
            number = kind(value)
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(400, f"{key} is a number")
        if not math.isfinite(number):  # "nan" and "inf" read as numbers, and no part starts or lasts that long
            raise HTTPException(400, f"{key} is a number")
        return number

    def words(given, most: int, said: str, what: str) -> str:
        if not isinstance(given or "", str):
            raise HTTPException(400, f"{what} is text")
        given = " ".join((given or "").split())
        if len(given) > most:
            raise HTTPException(400, said)
        return given

    def person_of(given: dict, what: str = "") -> tuple[int, str]:
        """(which, person) of one swap: the person's number in the who's-who picture, or a few words, or neither."""

        which = number(given, "which", 0)
        if which != int(which) or (which and not 1 <= which <= MOST_PEOPLE):
            raise HTTPException(400, f"{what}a person's number is 1 to {MOST_PEOPLE}, as the numbered picture shows them")
        which = int(which)
        person = words(given.get("person"), PERSON_MOST, "describe the person in a few words", "the person")
        if which and person:
            raise HTTPException(400, f"{what}the person is picked by number or by a few words, not both")
        return which, person

    def settings(params: dict) -> dict:
        """The job's settings checked, with the defaults filled in; 400 with the reason for what cannot be done."""

        clip = media(params, "video", CLIPS, "a clip")
        who = params.get("who", False)
        if not isinstance(who, bool):
            raise HTTPException(400, "who is true or false")
        seed = number(params, "seed", SEED, int)
        if not 0 <= seed < 2 ** 32:
            raise HTTPException(400, "the seed is a whole number from 0")
        swaps = params.get("swaps")
        if who:
            swaps, picture, which, person, direction = None, None, 0, "", ""
        elif swaps is not None:
            if any(params.get(key) not in (None, "") for key in ("image", "which", "person", "direction")):
                raise HTTPException(400, "swaps takes the place of image, which, person and direction: each swap has its own")
            if not isinstance(swaps, list) or not all(isinstance(one, dict) for one in swaps) or not 1 <= len(swaps) <= MOST_SWAPS:
                raise HTTPException(400, f"swaps is a list of 1 to {MOST_SWAPS} people, each with a picture")
            made = []
            for index, one in enumerate(swaps, 1):
                what = f"swap {index}: "
                own = number(one, "seed", seed, int)
                if not 0 <= own < 2 ** 32:
                    raise HTTPException(400, what + "the seed is a whole number from 0")
                which, person = person_of(one, what)
                made.append({"image": media(one, "image", PICTURES, f"the character's picture of swap {index}"), "which": which, "person": person,
                             "direction": words(one.get("direction"), DIRECTION_MOST, f"{what}the direction is at most {DIRECTION_MOST} characters", "the direction"),
                             "seed": own})
            numbers = [one["which"] for one in made if one["which"]]
            if len(set(numbers)) < len(numbers):
                raise HTTPException(400, "two swaps name the same person: each person is swapped once")
            if len(made) > 1 and sum(1 for one in made if not one["which"] and not one["person"]) > 1:
                raise HTTPException(400, "say who each swap is for: a number from the numbered picture, or a few words")
            swaps, picture, which, person, direction = made, None, 0, "", ""
        else:
            picture = media(params, "image", PICTURES, "the character's picture")
            which, person = person_of(params)
            direction = words(params.get("direction"), DIRECTION_MOST, f"the direction is at most {DIRECTION_MOST} characters", "the direction")
        start = number(params, "start", 0.0)
        seconds = number(params, "seconds", 5.0)
        low, high = span()
        if start < 0:
            raise HTTPException(400, "the part starts at 0 s or later")
        if not low <= seconds <= high:
            raise HTTPException(400, f"the part is {low:g} to {high:g} seconds long")
        quality = number(params, "quality", QUALITY, int)
        if quality not in QUALITIES:
            raise HTTPException(400, "the quality is 480 or 768")
        anchor = params.get("anchor", True)
        if not isinstance(anchor, bool):
            raise HTTPException(400, "anchor is true or false")
        ref_size = params.get("ref_size") or ref_sizes()[0]
        if ref_size not in ("match", "max"):
            raise HTTPException(400, "ref_size is match or max")
        if ref_size not in ref_sizes():
            raise HTTPException(400, "the character's picture at full size (ref_size max) is not measured on this machine: it is "
                                     "scaled to the part's size here (match)")
        model = params.get("transformer") or ""
        if model in ("regular",):
            model = ""
        if model and (not isinstance(model, str) or Path(model).name not in (transformers() if transformers else [])):
            raise HTTPException(400, "no such video model file beside MiniMax H3 Ref2VA on this machine")
        model = Path(model).name if model else ""
        length, _ = media_seconds(str(clip))
        if length is not None:
            if length < low - 0.05:
                raise HTTPException(400, f"the clip is {length:.1f} s long: Character Swap needs a part of at least {low:g} s")
            if start + seconds > length + 0.05:
                raise HTTPException(400, f"the clip is {length:.1f} s long: a {seconds:g} s part from {start:g} s runs past its end")
        return {"clip": clip, "picture": picture, "start": round(start, 2), "seconds": round(seconds, 2), "quality": quality,
                "anchor": anchor, "direction": direction, "person": person, "which": which, "swaps": swaps, "who": who,
                "ref_size": ref_size, "transformer": model, "seed": seed}

    def build(kind: str, params: dict, job_id: str) -> tuple[list[str], dict, list[str]]:
        """(command, environment, output paths) for one swap; 400 with the reason when it cannot run."""

        if kind not in KINDS:
            raise HTTPException(400, f"unknown job kind {kind}")
        if not spark:  # a Mac is told it has no swap before anything about the clip it chose
            raise HTTPException(400, missing())
        chosen = settings(params)
        why = missing()
        if why:
            raise HTTPException(400, why)
        found = guard(chosen["quality"])  # before it is queued, and again when its turn comes
        if not found["fits"]:
            raise HTTPException(400, found["reason"])
        env = {"PREFIX": str(prefix), "SPARK_MODELS": str(models())}
        if chosen["seconds"] > SECONDS[1]:
            env["SWAP_LONGEST"] = f"{span()[1]:g}"
        if chosen["who"]:  # only the people of the part, followed and numbered: SAM 3.1 alone, no clip made
            people = home / "scouts" / job_id / WHO
            return [str(prefix / "venv/bin/python"), str(pack / "spark/spark_swap.py"), "--video", str(chosen["clip"]),
                    "--start", f"{chosen['start']:g}", "--seconds", f"{chosen['seconds']:g}", "--quality", str(chosen["quality"]),
                    "--who", str(people), "--work", str(work_folder(home, job_id))], env, [str(people)]
        target = home / "renders" / f"{job_id}.mp4"
        mask = home / "masks" / job_id / "person.mp4"
        still = home / "scouts" / job_id / "anchor.png"
        # the folders of the outputs are made by the job when it runs (spark_swap.py), not when it is queued: a job the
        # queue refuses leaves none behind
        command = [str(prefix / "venv/bin/python"), str(pack / "spark/spark_swap.py"), "--video", str(chosen["clip"]),
                   *(["--image", str(chosen["picture"])] if chosen["swaps"] is None else []),
                   "--start", f"{chosen['start']:g}", "--seconds", f"{chosen['seconds']:g}",
                   "--quality", str(chosen["quality"]), "--seed", str(chosen["seed"]), "--swap-lora", str(swap_lora()),
                   "--turbo-lora", str(models() / TURBO), "--mask-preview", str(mask), "-o", str(target),
                   "--work", str(work_folder(home, job_id))]
        if chosen["anchor"]:
            command += ["--anchor-out", str(still)]
        else:
            command.append("--no-anchor")
        if chosen["direction"]:
            command.append("--direction=" + chosen["direction"])  # one argument, even when the words start with "-"
        if chosen["person"]:
            command.append("--person=" + chosen["person"])
        if chosen["which"]:
            command += ["--which", str(chosen["which"])]
        if chosen["swaps"] is not None:  # several people, one after another: one argument, the pictures as full paths
            command.append("--swaps=" + json.dumps([{**one, "image": str(one["image"])} for one in chosen["swaps"]]))
        if chosen["ref_size"] == "max":
            command += ["--ref-size", "max"]
        if chosen["transformer"]:
            env["SPARK_TRANSFORMER"] = chosen["transformer"]  # another build of Ref2VA (spark_swap.py weights)
        return command, env, [str(target), str(mask)] + ([str(still)] if chosen["anchor"] else [])

    def title(params: dict) -> str:
        name = Path(str(params.get("video") or "")).name
        if params.get("who") is True:
            return f"{TITLE}: the people in {name}" if name else f"{TITLE}: the people"
        return f"{TITLE}: {name}" if name else TITLE

    def source_of(params: dict) -> str | None:
        path = params.get("video")
        try:
            return relative(inside(path)) if path else None
        except HTTPException:
            return None

    def guess(params: dict) -> int | None:
        """The job's expected seconds for the queue, or None for settings it would refuse."""

        try:
            quality = int(params.get("quality") or QUALITY)
            if params.get("who") is True:  # everyone followed through the part (FOLLOW was timed with one person)
                return int(round(OTHER + FOLLOW * float(params.get("seconds") or 5) * plan.FPS))
            swaps = len(params["swaps"]) if isinstance(params.get("swaps"), list) else 1
            return estimate(float(params.get("seconds") or 5), quality, params.get("anchor", True) is not False, swaps) if quality in QUALITIES else None
        except (TypeError, ValueError):
            return None

    build.weights = weights  # the server's Bolt check asks the memory guard with them (server.bolt_need)
    return router, build, title, source_of, guess
