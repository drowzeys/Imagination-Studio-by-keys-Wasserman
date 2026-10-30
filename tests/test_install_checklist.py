"""The checklist at the start of the one-click install (scripts/install-checklist.sh): python3 -m pytest
tests/test_install_checklist.py. It is driven through STUDIO_CHECKLIST_INPUT and a pseudo-terminal; nothing installs."""
import os
import pty
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "scripts" / "install-checklist.sh"
BASH = "/bin/bash" if Path("/bin/bash").exists() else "bash"  # macOS's is bash 3.2: the list must run on it
SPARK_OPTIONAL = "--motion --voice --music --sound --bolt --train --ref2va --h3-base --image-edit --flux3 --moss --yue2 --swap"


def env(**extra):
    out = {k: v for k, v in os.environ.items() if k not in ("CI", "STUDIO_NO_CHECKLIST", "STUDIO_CHECKLIST_INPUT")}
    out.update(extra)
    return out


def run_list(answers, rows="$(checklist_rows_cuda aarch64)", free=800):
    """The flags the list prints for these answers (one per line), and what it drew."""
    script = f'. "{LIB}"; checklist_run "{rows}" {free} "Test machine" "bash oneshot-setup-spark.sh"'
    done = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=20,
                          env=env(STUDIO_CHECKLIST_INPUT="\\n".join(answers) + "\\n"))
    return done.returncode, done.stdout.strip(), done.stderr


def wanted(*args, **extra):
    script = f'. "{LIB}"; checklist_wanted "$@"'
    return subprocess.run([BASH, "-c", script, "x", *args], capture_output=True, timeout=20, env=env(**extra)).returncode == 0


def test_enter_installs_everything_ticked():
    code, flags, drawn = run_list([""])
    assert code == 0 and flags == SPARK_OPTIONAL
    assert "Included" in drawn and "Optional, larger" in drawn and "Free on this disk: 800 GB" in drawn
    assert "To install the same way without the list: bash oneshot-setup-spark.sh " + SPARK_OPTIONAL in drawn
    assert re.search(r"\b10 +\[x\] Training", drawn)


def test_unticking_two_rows_and_a_range():
    assert run_list(["7 8", ""])[1] == SPARK_OPTIONAL.replace("--music --sound ", "")
    assert run_list(["13-15", ""])[1] == "--motion --voice --music --sound --bolt --train --ref2va --h3-base --yue2 --swap"
    assert run_list(["2 3", ""])[1] == "--no-graphics --no-finish " + SPARK_OPTIONAL


def test_n_leaves_the_plain_install_and_a_and_d_tick_everything():
    code, flags, drawn = run_list(["n", ""])
    assert code == 0 and flags == ""
    assert "To install the same way without the list: bash oneshot-setup-spark.sh --yes" in drawn
    assert run_list(["n", "7", ""])[1] == "--music"
    assert run_list(["n", "4", "a", ""])[1] == SPARK_OPTIONAL
    assert run_list(["n", "2", "d", ""])[1] == SPARK_OPTIONAL


def test_a_bad_answer_says_why_and_changes_nothing():
    code, flags, drawn = run_list(["99", "7 x", "9-5", "a 3", ""])
    assert code == 0 and flags == SPARK_OPTIONAL
    assert "there is no row 99" in drawn and '"x" is not a number' in drawn
    assert "range low to high" in drawn and "on their own" in drawn


def test_the_core_stays_and_a_needed_row_follows():
    code, flags, drawn = run_list(["1", ""])
    assert flags == SPARK_OPTIONAL and "is always installed" in drawn
    assert run_list(["12", ""])[1] == "--motion --voice --music --sound --bolt --ref2va --image-edit --flux3 --moss --yue2 --swap"  # training goes with H3 base
    assert run_list(["n", "10", ""])[1] == "--train --h3-base"


def test_no_answer_installs_nothing():
    script = f'. "{LIB}"; checklist_run "$(checklist_rows_cuda aarch64)" 800 T C'
    done = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=20,
                          env=env(STUDIO_CHECKLIST_INPUT="7"))  # the input ends before an Enter
    assert done.returncode != 0 and done.stdout == "" and "nothing was installed" in done.stderr


def test_too_little_disk_is_said():
    assert "more than the free space" in run_list([""], free=100)[2]


def test_each_platform_lists_only_its_parts():
    def rows(call):
        out = subprocess.run([BASH, "-c", f'. "{LIB}"; {call}'], capture_output=True, text=True, timeout=20).stdout
        return {line.split("|")[0]: line.split("|") for line in out.splitlines()}
    spark, rtx = rows("checklist_rows_cuda aarch64"), rows("checklist_rows_cuda x86_64")
    small, big = rows('checklist_rows_mac 64 "Apple M5 Max"'), rows('checklist_rows_mac 256 "Apple M5 Ultra"')
    assert "train" in spark and "train" not in big and "turbo" not in spark
    assert "Tesseract runs on your Mac" in spark["finish"][-1] and "Tesseract runs" not in rtx["finish"][-1]
    assert not {"turbo", "ref2va", "upscale", "remove"} & set(small) and {"turbo", "ref2va", "upscale", "remove"} <= set(big)
    assert big["upscale"][7] == "ref2va"
    assert rows('checklist_rows_mac 64 "Apple M4 Max"')["core"][4] == "212000"  # 8-bit FastH3 weights too
    # every flag a row gives is one its installer reads
    for table, script in ((spark, "oneshot-setup-cuda.sh"), (big, "oneshot-setup.sh")):
        text = (ROOT / script).read_text()
        for row in table.values():
            for flag in filter(None, row[5:7]):
                assert re.search(rf"(^|[\s|]){re.escape(flag)}(\)|\|)", text, re.M), (script, flag)
            assert row[1] in ("in", "opt") and row[2] in ("0", "1") and row[4].isdigit()


def test_flags_yes_verify_upgrade_ci_and_no_terminal_leave_the_list_out():
    hook = {"STUDIO_CHECKLIST_INPUT": "\\n"}
    assert wanted(**hook) and wanted("--app", "--no-render", **hook)
    for args in (["--yes"], ["-y"], ["--music"], ["--verify"], ["--upgrade"], ["--no-finish"], ["--all"], ["--app", "--ref2va"]):
        assert not wanted(*args, **hook), args
    assert not wanted(CI="true", **hook) and not wanted(STUDIO_NO_CHECKLIST="1", **hook)
    assert not wanted()  # pipes here: no terminal


def in_terminal(script, extra=None):
    """Run a bash line with stdin, stdout and stderr on a pseudo-terminal; what it wrote there."""
    leader, follower = pty.openpty()
    proc = subprocess.Popen([BASH, "-c", script], stdin=follower, stdout=follower, stderr=follower,
                            env=env(**(extra or {})), start_new_session=True)
    os.close(follower)
    seen = b""
    sent = False
    while True:
        try:
            chunk = os.read(leader, 4096)
        except OSError:
            break
        if not chunk:
            break
        seen += chunk
        if not sent and b"> " in seen:
            os.write(leader, b"7\r")
            os.write(leader, b"\r")
            sent = True
    proc.wait(timeout=20)
    os.close(leader)
    return proc.returncode, seen.decode(errors="replace")


@pytest.mark.skipif(not sys.platform.startswith(("darwin", "linux")), reason="needs a pseudo-terminal")
def test_a_terminal_shows_the_list_and_takes_the_answer(tmp_path):
    out = tmp_path / "flags"
    script = (f'. "{LIB}"; checklist_wanted || {{ echo NO-LIST; exit 3; }}; '
              f'f="$(checklist_run "$(checklist_rows_cuda aarch64)" 800 T "bash oneshot-setup-spark.sh")" && printf %s "$f" > "{out}"')
    code, seen = in_terminal(script)
    assert code == 0 and "NO-LIST" not in seen and "Optional, larger" in seen
    assert out.read_text() == SPARK_OPTIONAL.replace("--music ", "")
    code, seen = in_terminal(f'. "{LIB}"; checklist_wanted --yes && echo LIST || echo NO-LIST')
    assert "NO-LIST" in seen


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac installer")
def test_the_mac_installer_turns_the_list_into_its_own_flags():
    def dry(*args, answers="\\n"):
        done = subprocess.run([BASH, str(ROOT / "oneshot-setup.sh"), *args], capture_output=True, text=True, timeout=30,
                              env=env(STUDIO_CHECKLIST_DRY="1", STUDIO_CHECKLIST_INPUT=answers))
        assert done.returncode == 0, done.stderr
        return done.stdout.strip(), done.stderr
    flags, drawn = dry("--music")
    assert flags == "--music" and "Optional, larger" not in drawn
    flags, drawn = dry("--app")
    ram = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout) // 2**30
    if ram >= 96:
        assert "Optional, larger" in drawn and flags.startswith("--app --motion --voice --music")
    else:  # below 96 GB the install itself says what this Mac can run (--image-only)
        assert flags == "--app" and "Optional, larger" not in drawn


@pytest.mark.parametrize("script", ["oneshot-setup.sh", "oneshot-setup-cuda.sh", "oneshot-setup-spark.sh",
                                    "oneshot-setup-rtx-6000.sh", "scripts/make-app.sh", "scripts/install-checklist.sh"])
def test_the_scripts_parse(script):
    assert subprocess.run([BASH, "-n", str(ROOT / script)]).returncode == 0


SAO = "https://huggingface.co/stabilityai/stable-audio-open-1.0/resolve/main/model_index.json"
KLEIN = "https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy/resolve/main/diffusion_models/flux3-image-klein-bf16.safetensors"


def gate(tmp_path, *urls, mode="install", **extra):
    """checklist_gate_part for FLUX3-K: its status, what it said, and what the end of the run says."""
    script = (f'. "{LIB}"; checklist_gate_part {mode} --flux3 FLUX3-K "bash oneshot-setup-spark.sh" "$@"; s=$?; '
              'printf "STATUS=%s\\n%s" "$s" "$CHECKLIST_LATER"')
    done = subprocess.run([BASH, "-c", script, "x", *urls], capture_output=True, text=True, timeout=120,
                          env=env(HF_HOME=str(tmp_path), PREFIX=str(tmp_path), **extra))
    return int(re.search(r"STATUS=(\d+)", done.stdout).group(1)), done.stdout


def test_a_gated_part_whose_licence_is_accepted_is_kept(tmp_path):
    code, said = gate(tmp_path, KLEIN, STUDIO_GATE_FAKE="klein=granted")
    assert code == 0 and "Not installed yet" not in said and "!" not in said
    assert gate(tmp_path, KLEIN, STUDIO_GATE_FAKE="klein=open")[0] == 0  # a repo that is not gated


def test_a_refused_licence_drops_the_part_with_what_to_do(tmp_path):
    code, said = gate(tmp_path, KLEIN, STUDIO_GATE_FAKE="klein=accept")
    assert code == 1 and "left out of this install" in said and "Everything else installs" in said
    assert "https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy" in said and "hf auth login" in said
    assert re.search(r"Not installed yet: FLUX3-K \(accept its licence at https://huggingface.co/black-forest-labs/"
                     r"flux-3-image-klein-comfy .*then run: bash oneshot-setup-spark.sh --flux3\)", said)
    assert "sign in to Hugging Face first" in gate(tmp_path, KLEIN, STUDIO_GATE_FAKE="klein=sign_in")[1]
    assert "waiting for the author's approval" in gate(tmp_path, KLEIN, STUDIO_GATE_FAKE="klein=pending")[1]
    code, said = gate(tmp_path, KLEIN, mode="verify", STUDIO_GATE_FAKE="klein=accept")
    assert code == 1 and "FLUX3-K: waiting for the licence" in said and "Not installed yet" not in said


def test_no_network_or_no_sign_in_is_not_yet(tmp_path):
    dead = {"HTTPS_PROXY": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9", "NO_PROXY": "", "no_proxy": ""}
    code, said = gate(tmp_path, SAO, HF_TOKEN="hf_not_a_real_token", **dead)  # app/hf_gate.py itself, with no way out
    assert code == 1 and "Hugging Face did not answer" in said and "Not installed yet: FLUX3-K" in said
    code, said = gate(tmp_path, SAO, **dead)  # no token saved and none given
    assert code == 1 and "sign in to Hugging Face first" in said


def test_the_cuda_installer_asks_before_downloading_and_the_rtx_entry_no_longer_stops():
    cuda = (ROOT / "oneshot-setup-cuda.sh").read_text()
    assert cuda.index("checklist_gate_part \"$GATE_MODE\" --flux3") < cuda.index("say \"Environment:")
    assert "|| FLUX3=0" in cuda and "|| SOUND=0" in cuda and "trap checklist_said_later EXIT" in cuda
    assert "exit 1" not in (ROOT / "oneshot-setup-rtx-6000.sh").read_text()


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac installer")
def test_the_mac_installer_drops_a_refused_part_and_installs_the_rest(tmp_path):
    done = subprocess.run([BASH, str(ROOT / "oneshot-setup.sh"), "--flux3", "--sound"], capture_output=True, text=True,
                          timeout=60, env=env(STUDIO_GATE_DRY="1", HF_HOME=str(tmp_path), PREFIX=str(tmp_path),
                                              FLUX3_MODEL_DIR=str(tmp_path / "flux3"),
                                              STUDIO_GATE_FAKE="flux-3-image-klein=granted;stable-audio=accept"))
    assert done.returncode == 0, done.stderr
    assert "flux3=1 sound=0" in done.stdout  # FLUX3-K stays, sound waits for its licence
    assert "Sound effects and foley is gated on Hugging Face and left out" in done.stdout
    assert "Not installed yet: Sound effects and foley" in done.stdout and "then run: bash oneshot-setup.sh --sound)" in done.stdout


FAKE_SMI = r"""#!/bin/bash
# nvidia-smi as one card answers it: FAKE_GPU_NAME, FAKE_VRAM_MB ("[N/A]" on a GB10, whose memory is the machine's)
for a in "$@"; do case "$a" in
  --query-gpu=name) echo "$FAKE_GPU_NAME"; exit 0;;
  --query-gpu=memory.total) echo "$FAKE_VRAM_MB"; exit 0;;
  --query-gpu=compute_cap) echo "$FAKE_CC"; exit 0;;
esac; done
echo "GPU 0: $FAKE_GPU_NAME"
"""
CARDS = {"gb10": ("NVIDIA GB10", "[N/A]", "12.1"), "rtx-pro-6000": ("NVIDIA RTX PRO 6000 Blackwell Workstation Edition", "97887", "12.0"),
         "rtx-5090": ("NVIDIA GeForce RTX 5090", "32607", "12.0")}


def disk_need(tmp_path, card, *flags, have=(), **extra):
    """oneshot-setup-cuda.sh up to its disk check on ``card`` (a stand-in nvidia-smi), with ``have`` already under the
    models folder: (exit code, its need line or error)."""

    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    (bin_ / "nvidia-smi").write_text(FAKE_SMI)
    for tool in ("git", "ffmpeg", "ffprobe", "uv"):
        (bin_ / tool).write_text("#!/bin/sh\nexit 0\n")
    for one in bin_.iterdir():
        one.chmod(0o755)
    models = tmp_path / "models"
    for name in have:
        (models / name).parent.mkdir(parents=True, exist_ok=True)
        (models / name).write_bytes(b"x")
    name, vram, cc = CARDS[card]
    done = subprocess.run([BASH, str(ROOT / "oneshot-setup-cuda.sh"), "--yes", *flags], capture_output=True, text=True, timeout=60,
                          env=env(PATH=f"{bin_}:/usr/bin:/bin", HOME=str(tmp_path), PREFIX=str(tmp_path / "prefix"), SPARK_MODELS=str(models),
                                  HF_HOME=str(tmp_path / "hf"), STUDIO_DISK_DRY="1", STUDIO_GATE_FAKE="akatz-ai=open",
                                  FAKE_GPU_NAME=name, FAKE_VRAM_MB=vram, FAKE_CC=cc, **extra))
    said = next((line for line in done.stdout.splitlines() if line.startswith("need=")), done.stderr.strip()[-300:])
    return done.returncode, said


def big_enough():
    try:
        total = int(next(l for l in Path("/proc/meminfo").read_text().splitlines() if l.startswith("MemTotal")).split()[1])
    except (OSError, StopIteration):
        return False
    return total >= 100 * 1024 * 1024 and sys.platform.startswith("linux")


INT8 = "MiniMax-H3-Comfy/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors"
BF16 = "MiniMax-H3-Comfy/diffusion_models/minimax_h3_ref2va_pruned_bf16.safetensors"


@pytest.mark.skipif(not big_enough(), reason="the installer's preflight wants a machine of 100 GB (a DGX Spark's) or more")
@pytest.mark.parametrize("card", ["gb10", "rtx-pro-6000", "rtx-5090"])
def test_character_swap_fetches_the_int8_ref2va_it_runs_on_not_the_40_gb_bf16_file(tmp_path, card):
    small = {"STUDIO_ALLOW_SMALL_VRAM": "1"} if card == "rtx-5090" else {}
    vram = {"gb10": "unified", "rtx-pro-6000": "95", "rtx-5090": "31"}[card]
    base = 5 + 26 + 75 + 1 + 1  # Qwen-Image, FastH3, motion graphics and Finish
    assert disk_need(tmp_path, card, **small) == (0, f"need={base} ref2va=0 swap=0 vram={vram}")
    assert disk_need(tmp_path, card, "--swap", **small) == (0, f"need={base + 4 + 22} ref2va=0 swap=1 vram={vram}")
    assert disk_need(tmp_path, card, "--swap", have=[INT8], **small) == (0, f"need={base + 4} ref2va=0 swap=1 vram={vram}")
    # with --ref2va the bf16 file comes too (reference clips with the Turbo adapter and LoRAs run on it), and swap's int8
    assert disk_need(tmp_path, card, "--ref2va", "--swap", have=[INT8], **small) == (0, f"need={base + 40 + 4} ref2va=1 swap=1 vram={vram}")
    assert disk_need(tmp_path, card, "--ref2va", have=[BF16], **small) == (0, f"need={base} ref2va=1 swap=0 vram={vram}")
    if card == "rtx-5090":  # a 32 GB card installs video only when asked to
        code, said = disk_need(tmp_path, card, "--swap")
        assert code == 1 and "STUDIO_ALLOW_SMALL_VRAM=1" in said and "docs/rtx-5090.md" in said


def test_swap_no_longer_ticks_the_bf16_ref2va():
    cuda = (ROOT / "oneshot-setup-cuda.sh").read_text()
    assert "--swap) SWAP=1;;" in cuda and 'fetch Comfy-Org/MiniMax-H3 MiniMax-H3-Comfy "$REF2VA_INT8"' in cuda
    rows = subprocess.run([BASH, "-c", f'. "{LIB}"; checklist_rows_cuda aarch64'], capture_output=True, text=True, timeout=20).stdout
    swap = next(line.split("|") for line in rows.splitlines() if line.startswith("swap|"))
    assert swap[4] == "23100" and swap[7] == ""


def fake_mac(tmp_path, gib, chip="Apple M5 Max"):
    """A PATH whose uname, sysctl, sw_vers and df answer as a Mac with ``gib`` GB of memory, on any machine."""

    bin_dir = tmp_path / "fake-mac-bin"
    bin_dir.mkdir(exist_ok=True)
    tools = {
        "uname": 'case "${1:-}" in -m) echo arm64;; *) echo Darwin;; esac',
        "sysctl": f'case "$*" in *hw.memsize*) echo {gib << 30};; *brand_string*) echo "{chip}";; *) exit 1;; esac',
        "sw_vers": "echo 26.0",
        "df": 'printf "Filesystem 1G-blocks Used Available\\n/dev/disk 2000 1000 900\\n"',
    }
    for name, body in tools.items():
        (bin_dir / name).write_text(f"#!/bin/sh\n{body}\n")
        (bin_dir / name).chmod(0o755)
    return f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


def mac_install(tmp_path, gib, *args, **extra):
    """oneshot-setup.sh on a fake Mac with ``gib`` GB, stopped right after its memory decision (STUDIO_MEMORY_DRY)."""

    done = subprocess.run([BASH, str(ROOT / "oneshot-setup.sh"), *args], capture_output=True, text=True, timeout=60,
                          env=env(PATH=fake_mac(tmp_path, gib), STUDIO_NO_CHECKLIST="1", STUDIO_MEMORY_DRY="1",
                                  PREFIX=str(tmp_path / "prefix"), H3_MODEL_DIR=str(tmp_path / "h3"), HOME=str(tmp_path), **extra))
    return done.returncode, done.stdout + done.stderr


@pytest.mark.parametrize("gib", [64, 95])
def test_the_mac_checklist_is_not_shown_below_96_gb(tmp_path, gib):
    done = subprocess.run([BASH, str(ROOT / "oneshot-setup.sh"), "--app"], capture_output=True, text=True, timeout=30,
                          env=env(PATH=fake_mac(tmp_path, gib), STUDIO_CHECKLIST_DRY="1", STUDIO_CHECKLIST_INPUT="\\n"))
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "--app" and "Optional, larger" not in done.stderr  # the install says what runs here


@pytest.mark.parametrize("gib", [96, 128])
def test_the_mac_checklist_is_shown_from_96_gb(tmp_path, gib):
    done = subprocess.run([BASH, str(ROOT / "oneshot-setup.sh"), "--app"], capture_output=True, text=True, timeout=30,
                          env=env(PATH=fake_mac(tmp_path, gib), STUDIO_CHECKLIST_DRY="1", STUDIO_CHECKLIST_INPUT="\\n"))
    assert done.returncode == 0, done.stderr
    assert "Optional, larger" in done.stderr and done.stdout.strip().startswith("--app --motion --voice --music")
    assert ("--turbo" in done.stdout) == (gib >= 128)


def test_a_mac_below_96_gb_is_offered_pictures_only(tmp_path):
    code, said = mac_install(tmp_path, 64, "--no-render")
    assert code == 1 and "this Mac has 64 GB; the default install (video, voice, music and Bolt) needs 96 GB" in said
    assert "bash oneshot-setup.sh --image-only" in said and "48 GB" in said
    code, said = mac_install(tmp_path, 95, "--no-render")
    assert code == 1 and "needs 96 GB" in said
    code, said = mac_install(tmp_path, 96, "--no-render")
    assert code == 0 and "need=96 video=1" in said
    for gib in (48, 64):  # pictures only stay at 48 GB
        code, said = mac_install(tmp_path, gib, "--image-only", "--no-render")
        assert code == 0 and "need=48 video=0" in said
    code, said = mac_install(tmp_path, 32, "--image-only", "--no-render")
    assert code == 1 and "needs 48 GB+" in said
    code, said = mac_install(tmp_path, 64, "--all", "--no-render")
    assert code == 0 and "pictures only (video, voice, music and Bolt need 96 GB)" in said and "need=48 video=0" in said


def test_an_upgrade_of_a_64_gb_mac_says_the_new_minimum_and_keeps_the_install(tmp_path):
    python = tmp_path / "prefix" / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    (tmp_path / "h3" / "FL2VA" / "text_encoder").mkdir(parents=True)
    (tmp_path / "h3" / "FL2VA" / "text_encoder" / "model.safetensors").write_bytes(b"")
    for args in (("--upgrade",), ("--verify",)):
        code, said = mac_install(tmp_path, 64, *args)
        assert code == 0 and "need=96 video=1" in said, said  # the video install stays one
        assert "The minimum for a Mac is now 96 GB (it was 64 GB up to 4.3.0); this one has 64 GB." in said
        assert "What is installed is kept" in said and "Voice and Music tabs now say they need 96 GB" in said
    assert "moved to this version" in mac_install(tmp_path, 64, "--upgrade")[1]
    code, said = mac_install(tmp_path, 96, "--upgrade")
    assert code == 0 and "minimum for a Mac is now" not in said
    code, said = mac_install(tmp_path, 48, "--upgrade")  # under the former floor too: not a video install it could have made
    assert code == 1 and "needs 96 GB" in said


@pytest.mark.parametrize("script,name", [("scripts/setup-voice.sh", "Drama 3 (the Voice tab)"), ("scripts/setup-music.sh", "MiniMax Music 3")])
def test_voice_and_music_setup_need_96_gb_on_a_mac(tmp_path, script, name):
    def run(gib, *args):
        done = subprocess.run([BASH, str(ROOT / script), *args], capture_output=True, text=True, timeout=30,
                              env=env(PATH=fake_mac(tmp_path, gib), HOME=str(tmp_path), PREFIX=str(tmp_path / "prefix"),
                                      DRAMA3_SRC=str(tmp_path / "none"), MUSIC3_SRC=str(tmp_path / "none")))
        return done.returncode, done.stdout + done.stderr
    code, said = run(64)
    assert code == 1 and f"FATAL: {name}" in said and "a Mac with 96 GB or more (this one has 64 GB)" in said
    code, said = run(64, "--verify")  # an install made under the 64 GB floor is checked, not refused
    assert f"this Mac has 64 GB: {name} needs 96 GB or more now" in said and "a Mac with 96 GB or more" not in said
    code, said = run(96, "--verify")
    assert "96 GB" not in said
