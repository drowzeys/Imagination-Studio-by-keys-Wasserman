# shellcheck shell=bash
# The checklist at the start of the one-click install. oneshot-setup.sh (Mac) and oneshot-setup-cuda.sh (DGX Spark,
# RTX Pro 6000) source this file. In a terminal, with no feature flag, the installer shows every part this machine
# runs, all ticked, and the person unticks what they do not want before anything is downloaded. The
# choice becomes the installer's own flags (--music, --no-finish, ...), so the install that follows is the same one
# those flags give, and the equivalent command line is printed so it can be run again without the list.
#
# Plain bash 3.2 (macOS's own): no associative arrays, no whiptail or dialog.
#
#   checklist_wanted "$@"                         0 when the list is shown: a terminal, no feature flag, no --yes,
#                                                 --verify or --upgrade, no CI or STUDIO_NO_CHECKLIST=1
#   checklist_rows_mac RAM_GB CHIP                the Mac's rows
#   checklist_note_mac RAM_GB                     what this Mac leaves out, said under the list
#   checklist_rows_cuda ARCH                      a CUDA machine's rows (aarch64: a DGX Spark; x86_64: an RTX Pro 6000)
#   checklist_run ROWS FREE_GB HEADING COMMAND [NOTE]
#                                                 the list on stderr, the chosen flags on stdout
#
# A row: key|group|in the plain install|locked|size in MB|flag when ticked|flag when unticked|key it needs|name|description
#   group "in" is Included (what the installer does with no flag, and with --yes), "opt" is Optional, larger. Every row
#   starts ticked. A row's flag is printed only where its tick differs from the plain install, so the flags are the
#   installer's own. Ticking a row ticks the row it needs; unticking that one unticks the row again.
# Sizes are the installers' own (their headers and their disk estimate).
#
# Testing: STUDIO_CHECKLIST_INPUT is read instead of the terminal when it is set (printf %b: "7 8\n\n"), and the list
# is then shown without a terminal. STUDIO_CHECKLIST_DRY=1 makes an installer print the flags it would run with, then
# stop before anything is installed.

CHECKLIST_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECKLIST_LATER=""  # the gated parts left out of this run, said again at its end (checklist_gate_part, checklist_said_later)

# checklist_gate URL: granted | open | sign_in | accept | pending | unknown for one file on Hugging Face, as the studio's
# Install reads it (app/hf_gate.py's gate_of: the person's own token, a one-byte read). No answer (no network, no
# Python) is "unknown". STUDIO_GATE_FAKE="part-of-url=answer;..." answers for tests.
checklist_gate() {
  local url="$1" pair
  if [ -n "${STUDIO_GATE_FAKE:-}" ]; then
    local IFS=';'
    for pair in $STUDIO_GATE_FAKE; do
      case "$url" in *"${pair%%=*}"*) echo "${pair#*=}"; return 0;; esac
    done
    echo unknown; return 0
  fi
  local py="${PREFIX:-}/venv/bin/python"; [ -x "$py" ] || py=python3
  "$py" - "$CHECKLIST_ROOT/app" "$url" 2>/dev/null <<'PY' || echo unknown
import sys
import types
try:
    import fastapi  # noqa: F401
except ImportError:  # before the studio's environment exists: hf_gate's web routes are not used here
    stub = types.ModuleType("fastapi")
    stub.APIRouter = stub.HTTPException = stub.Request = object
    sys.modules["fastapi"] = stub
sys.path.insert(0, sys.argv[1])
import hf_gate
print(hf_gate.gate_of(sys.argv[2]).get("access") or "unknown")
PY
}

# checklist_gate_part MODE FLAG NAME COMMAND URL...: 0 when every gated file of a part opens for this person's Hugging
# Face account. Otherwise one plain line says what to do, the part is remembered for the end of the run, and 1 is
# returned: the installer drops the part and installs the rest. MODE verify only says "waiting for the licence".
checklist_gate_part() {
  local mode="$1" flag="$2" what="$3" command="$4" url answer page why=""
  shift 4
  for url in "$@"; do
    answer="$(checklist_gate "$url" | tail -1)"
    case "$answer" in granted|open) continue;; esac
    page="${url%%/resolve/*}"
    case "$answer" in
      sign_in) why="sign in to Hugging Face first (hf auth login, or the studio's Install guide), then accept its licence at $page";;
      pending) why="your request at $page is waiting for the author's approval";;
      accept) why="accept its licence at $page with your Hugging Face account (signed in: hf auth login)";;
      *) why="Hugging Face did not answer, so access could not be checked: accept its licence at $page (signed in: hf auth login)";;
    esac
    break
  done
  [ -n "$why" ] || return 0
  if [ "$mode" = verify ]; then
    echo "  - $what: waiting for the licence ($why)"
    return 1
  fi
  echo "  ! $what is gated on Hugging Face and left out of this install: $why. Everything else installs."
  CHECKLIST_LATER="${CHECKLIST_LATER}Not installed yet: $what ($why, then run: $command $flag)
"
  return 1
}

# the gated parts left out, at the end of a run that did not fail (an installer's EXIT trap)
checklist_said_later() {
  [ "$?" = 0 ] && [ -n "$CHECKLIST_LATER" ] || return 0
  printf '\n%s' "$CHECKLIST_LATER"
}

# checklist_wanted ARGS...: is the list shown for this run?
checklist_wanted() {
  [ -z "${CI:-}" ] || return 1
  [ "${STUDIO_NO_CHECKLIST:-0}" != 1 ] || return 1
  local arg
  for arg in "$@"; do
    case "$arg" in
      --app|--no-render|--fasth3|--lora-bundle=*) ;;  # not a choice of parts: the list is still shown
      *) return 1;;  # a feature flag, --all, --image-only, --yes, --verify, --upgrade: today's install, no list
    esac
  done
  [ -z "${STUDIO_CHECKLIST_INPUT+x}" ] || return 0
  # the person sees it (stdout and stderr on a terminal) and can answer it: stdin, or the terminal itself when stdin is
  # the pipe of a `curl ... | bash` one-liner
  [ -t 1 ] && [ -t 2 ] || return 1
  [ -t 0 ] && return 0
  { : </dev/tty; } 2>/dev/null
}

checklist_rows_mac() {
  local ram="$1" chip="$2" core=175000
  # a chip without tensor units also writes FastH3's 8-bit weights (37 GB)
  case "$chip" in *M[5-9]*) ;; *) core=212000;; esac
  printf '%s\n' \
    "core|in|1|1|$core||||Pictures and video|Qwen-Image and FastH3 video with sound" \
    "graphics|in|1|0|400||--no-graphics||Motion graphics|Titles, captions, slideshows, music videos" \
    "finish|in|1|0|150||--no-finish||Finish|Final cut and mix to Resolve, Premiere, After Effects" \
    "loras|in|1|0|4000||--no-loras||Essential LoRA bundle|Every preset of the LoRA hub: camera, style, looks" \
    "motion|opt|0|0|500|--motion|||Motion previs|Pose, camera solve and depth for the Motion Previs tab" \
    "voice|opt|0|0|78000|--voice|||Voice|Fish Audio Drama 3 for the Voice tab" \
    "music|opt|0|0|22000|--music|||Music 3|MiniMax Music 3 for the Music tab" \
    "sound|opt|0|0|13000|--sound|||Sound and foley|Stable Audio Open and MMAudio (gated: accept first)" \
    "bolt|opt|0|0|25000|--bolt|||Bolt brain|NVIDIA Nemotron 3.5 Bolt as the studio's own brain"
  if [ "$ram" -ge 128 ]; then
    printf '%s\n' \
      "turbo|opt|0|0|64000|--turbo|||H3 base and Turbo|MiniMax H3's own transformer and its Turbo adapter" \
      "ref2va|opt|0|0|64000|--ref2va|||Ref2VA|Reference pictures to video: a face held across clips"
  fi
  if [ "$ram" -ge 192 ]; then
    printf '%s\n' \
      "upscale|opt|0|0|2300|--upscale||ref2va|Upscale|The Library's Upscale (ticks Ref2VA)" \
      "remove|opt|0|0|1700|--remove||ref2va|Remove a person|Find and Remove a person (ticks Ref2VA)"
  fi
  printf '%s\n' \
    "image-edit|opt|0|0|13000|--image-edit|||Image edit (40 steps)|Qwen-Image-2.1's base model (non-commercial)" \
    "flux3|opt|0|0|41000|--flux3|||FLUX3-K|Black Forest Labs' picture models (gated: accept first)" \
    "moss|opt|0|0|28000|--moss|||MOSS-Image|OpenMOSS's picture model (Apache 2.0)"
}

checklist_note_mac() {
  local ram="$1" note="Not on a Mac: training a LoRA (it runs on a DGX Spark)."
  [ "$ram" -ge 128 ] || note="$note Not on ${ram} GB: H3 Turbo and Ref2VA (128 GB)."
  [ "$ram" -ge 192 ] || note="$note Not on ${ram} GB: Upscale and Remove a person (192 GB)."
  printf '%s\n' "$note"
}

checklist_rows_cuda() {
  local arch="$1" finish="Final cut and mix to Resolve, Premiere, After Effects" bolt="NVIDIA Nemotron 3.5 Bolt as the studio's own brain"
  # the swap runs on Ref2VA's 8-bit copy where it is here; elsewhere it needs Ref2VA (the installer adds its bf16 file)
  local swap_needs="ref2va" swap_note="ticks Ref2VA"
  if [ -s "${SPARK_MODELS:-$HOME/spark-studio-models}/MiniMax-H3-Comfy/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors" ]; then
    swap_needs="" swap_note="on Ref2VA's 8-bit copy, here already"
  fi
  case "$arch" in
    aarch64|arm64) finish="Fonts only here; Tesseract runs on your Mac";;
    *) bolt="Nemotron 3.5 Bolt as the brain, through llama.cpp here";;
  esac
  printf '%s\n' \
    "core|in|1|1|101000||||Pictures and video|Qwen-Image and FastH3 video with sound" \
    "graphics|in|1|0|400||--no-graphics||Motion graphics|Titles, captions, slideshows, music videos" \
    "finish|in|1|0|150||--no-finish||Finish|$finish" \
    "loras|in|1|0|4000||--no-loras||Essential LoRA bundle|Every preset of the LoRA hub: camera, style, looks" \
    "motion|opt|0|0|500|--motion|||Motion previs|Pose, camera solve and depth for the Motion Previs tab" \
    "voice|opt|0|0|41000|--voice|||Voice|Fish Audio Drama 3 for the Voice tab" \
    "music|opt|0|0|22000|--music|||Music 3|MiniMax Music 3 for the Music tab" \
    "sound|opt|0|0|11000|--sound|||Sound and foley|Stable Audio Open and MMAudio (gated: accept first)" \
    "bolt|opt|0|0|25000|--bolt|||Bolt brain|$bolt" \
    "train|opt|0|0|14000|--train||h3-base|Training|Looks -> Train a LoRA (ticks H3 base, which it needs)" \
    "ref2va|opt|0|0|39000|--ref2va|||Ref2VA|Reference pictures to video, Upscale, Remove a person" \
    "h3-base|opt|0|0|40000|--h3-base|||H3 base|MiniMax H3's own transformer: keyframe LoRAs" \
    "image-edit|opt|0|0|14000|--image-edit|||Image edit (40 steps)|Qwen-Image-2.1's base model (non-commercial)" \
    "flux3|opt|0|0|41000|--flux3|||FLUX3-K|Black Forest Labs' picture models (gated: accept first)" \
    "moss|opt|0|0|30000|--moss|||MOSS-Image|OpenMOSS's picture model (Apache 2.0)" \
    "yue2|opt|0|0|13700|--yue2|||YuE2 songs|YuE2 for the Music tab (creators may sell songs; companies need a licence)" \
    "swap|opt|0|0|2100|--swap||$swap_needs|Character Swap|A picture's character in place of a person in a clip ($swap_note; H3 licence: not EU, UK, KR, US)"
}

# a size in MB as people read it: 150 MB, 2.3 GB, 41 GB
_checklist_size() {
  local mb="$1"
  if [ "$mb" -lt 1000 ]; then printf '%s MB' "$mb"
  elif [ "$mb" -lt 10000 ] && [ $((mb % 1000)) -ne 0 ]; then printf '%s.%s GB' $((mb / 1000)) $(((mb % 1000) / 100))
  else printf '%s GB' $(((mb + 500) / 1000)); fi
}

# checklist_run ROWS FREE_GB HEADING COMMAND [NOTE]: the list on stderr, the chosen flags on stdout (one line, empty
# when the choice is the plain install). Returns 130 on Ctrl-C and 1 when the input ends without an answer; nothing is printed on
# stdout then.
checklist_run() {
  local rows="$1" free="$2" heading="$3" command="$4" note="${5:-}"
  local key=() group=() def=() lock=() mb=() on=() off=() needs=() name=() desc=() tick=()
  local n=0 k g d l m o f nd nm ds
  while IFS='|' read -r k g d l m o f nd nm ds; do
    [ -n "$k" ] || continue
    key[n]="$k"; group[n]="$g"; def[n]="$d"; lock[n]="$l"; mb[n]="$m"; on[n]="$o"; off[n]="$f"; needs[n]="$nd"
    name[n]="$nm"; desc[n]="$ds"; tick[n]=1; n=$((n + 1))
  done <<EOF
$rows
EOF
  [ "$n" -gt 0 ] || return 1
  if [ -n "${STUDIO_CHECKLIST_INPUT+x}" ]; then exec 4< <(printf '%b' "$STUDIO_CHECKLIST_INPUT")
  elif [ -t 0 ]; then exec 4<&0
  else exec 4</dev/tty || return 1; fi
  trap 'printf "\n\nStopped: nothing was installed.\n" >&2; exit 130' INT
  local cols=100 size
  size="$(stty size <&4 2>/dev/null || true)"
  case "${size#* }" in ''|0|*[!0-9]*) ;; *) cols="${size#* }";; esac
  local said="" answer i j t a b total width words bad locked_said
  while :; do
    # draw
    printf '\n%s\n' "$heading" >&2
    for g in in opt; do
      if [ "$g" = in ]; then printf '\n  Included\n' >&2; else printf '\n  Optional, larger\n' >&2; fi
      i=0
      while [ "$i" -lt "$n" ]; do
        if [ "${group[i]}" = "$g" ]; then
          t=" "; [ "${tick[i]}" = 1 ] && t="x"
          ds="${desc[i]}"; [ "${lock[i]}" = 1 ] && ds="$ds (always)"
          width=$((cols - 44)); [ "$width" -ge 20 ] || width=20
          [ "${#ds}" -le "$width" ] || ds="${ds:0:$((width - 3))}..."
          printf '  %3d  [%s] %-22s %7s  %s\n' $((i + 1)) "$t" "${name[i]}" "$(_checklist_size "${mb[i]}")" "$ds" >&2
        fi
        i=$((i + 1))
      done
    done
    total=0; i=0
    while [ "$i" -lt "$n" ]; do [ "${tick[i]}" != 1 ] || total=$((total + mb[i])); i=$((i + 1)); done
    printf '\n  Download: about %s    Free on this disk: %s GB\n' "$(_checklist_size "$total")" "$free" >&2
    case "$free" in ''|*[!0-9]*) ;; *) [ "$total" -le $((free * 1000)) ] \
      || printf '  That is more than the free space: untick some parts, or free some space first.\n' >&2;; esac
    [ -z "$note" ] || printf '  %s\n' "$note" >&2
    printf '\n  Type numbers to tick or untick (3, or 3 5 7, or 4-6), a for all, n for none of the optional ones,\n' >&2
    printf '  d to tick everything again. Press Enter to install what is ticked. Ctrl-C stops without installing.\n' >&2
    [ -z "$said" ] || printf '\n  %s\n' "$said" >&2
    said=""
    printf '  > ' >&2
    if ! IFS= read -r -u 4 answer; then
      printf '\n\nNo answer: nothing was installed.\n' >&2
      exec 4<&-; trap - INT; return 1
    fi
    answer="$(printf '%s' "$answer" | tr 'A-Z,' 'a-z ')"
    set -f; set -- $answer; set +f
    [ $# -gt 0 ] || break
    if [ $# = 1 ] && { [ "$1" = a ] || [ "$1" = n ] || [ "$1" = d ]; }; then
      i=0
      while [ "$i" -lt "$n" ]; do
        case "$1" in
          a) tick[i]=1;;
          n) [ "${group[i]}" != opt ] || tick[i]=0;;
          d) tick[i]=1;;
        esac
        i=$((i + 1))
      done
      continue
    fi
    # every word is checked before anything changes: one bad word changes nothing
    bad=""
    for words in "$@"; do
      case "$words" in
        a|n|d) bad="a, n and d go on their own, without numbers"; break;;
        *-*) a="${words%%-*}"; b="${words#*-}";;
        *) a="$words"; b="$words";;
      esac
      case "$a$b" in ''|*[!0-9]*) bad="\"$words\" is not a number from the list (1 to $n), a range like 4-6, a, n or d"; break;; esac
      a=$((10#$a)); b=$((10#$b))
      if [ "$a" -lt 1 ] || [ "$b" -gt "$n" ]; then bad="there is no row $([ "$a" -lt 1 ] && echo "$a" || echo "$b"): the rows are 1 to $n"; break; fi
      if [ "$a" -gt "$b" ]; then bad="\"$words\": write a range low to high, like $b-$a"; break; fi
    done
    if [ -n "$bad" ]; then said="Not changed: $bad."; continue; fi
    locked_said=""
    for words in "$@"; do
      case "$words" in *-*) a="${words%%-*}"; b="${words#*-}";; *) a="$words"; b="$words";; esac
      a=$((10#$a)); b=$((10#$b))
      while [ "$a" -le "$b" ]; do
        i=$((a - 1))
        if [ "${lock[i]}" = 1 ]; then
          locked_said="$a (${name[i]}) is always installed: everything else runs on it."
        elif [ "${tick[i]}" = 1 ]; then
          tick[i]=0
          # what needs this row goes with it
          j=0; while [ "$j" -lt "$n" ]; do [ "${needs[j]}" != "${key[i]}" ] || tick[j]=0; j=$((j + 1)); done
        else
          tick[i]=1
          j=0; while [ "$j" -lt "$n" ]; do [ -z "${needs[i]}" ] || [ "${key[j]}" != "${needs[i]}" ] || tick[j]=1; j=$((j + 1)); done
        fi
        a=$((a + 1))
      done
    done
    said="$locked_said"
  done
  exec 4<&-; trap - INT
  local flags="" picked=""
  i=0
  while [ "$i" -lt "$n" ]; do
    if [ "${tick[i]}" = 1 ]; then
      picked="$picked, ${name[i]}"
      [ "${def[i]}" = 1 ] || [ -z "${on[i]}" ] || flags="$flags ${on[i]}"
    else
      [ "${def[i]}" = 0 ] || [ -z "${off[i]}" ] || flags="$flags ${off[i]}"
    fi
    i=$((i + 1))
  done
  flags="${flags# }"
  printf '\nInstalling: %s (about %s).\n' "${picked#, }" "$(_checklist_size "$total")" >&2
  printf 'To install the same way without the list: %s %s\n' "$command" "${flags:---yes}" >&2
  printf '%s\n' "$flags"
}
