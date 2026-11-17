"""The spoken lines of an uploaded prompt or script, for the Create wizard's *Words / Sound* step.

The person uploads (or pastes) a prompt or a scene they wrote elsewhere; the lines spoken or sung in it become rows
of the step's Voice / Line list, which they can still edit, remove or add to by hand. The words of each line are
kept exactly as written. Four ways of writing a line are read, the first that finds any wins:

1. MiniMax H3's own prompt (what step 4 shows): ``The speaker, in a calm voice (S1), says: <d>[English] Hello.</d>``.
   Its ``overall_soundscape:`` and ``non_diegetic_music:`` are returned too.
2. A screenplay: a character's name alone in capitals, an optional (parenthetical), then the line.
3. ``Name: the line`` or ``Name (a deep voice): "the line"``, one a line of text.
4. Prose: a quoted line after a speaking verb, ``she whispers, "Not yet."``; "in a ... voice" beside it is the voice.

Nothing here runs a tool or reads a file: it is text in, rows out.
"""

from __future__ import annotations

import re

LIMIT = 200_000  # characters read of one upload: a feature film's script is about this size

BARE_D = re.compile(r"<d>\s*(?:\[(?P<lang>[^\]]*)\])?\s*(?P<text>.*?)</d>", re.S | re.I)
# the sound and the music of a prompt: MiniMax H3's own headings first, else a plain heading that appears only once
# (a "Sound:" in every shot of a shot list is that shot's, not the clip's)
SOUND_HEADS = (r"overall[_ ]soundscape", r"soundscape|sound design|sound|ambience|ambient sound|audio")
MUSIC_HEADS = (r"non[_ -]diegetic[_ ]music", r"music|score|soundtrack|background music")
ANY_HEADING = re.compile(r"^\s*(?:#+\s*)?[A-Za-z_][\w \-/]{0,40}:(?:\s|$)|^\s*\[Shot\b|^\s*#", re.I)


def section(text: str, names: tuple[str, str]) -> str:
    """The body of the heading ``names`` in ``text``: its own line's rest and the lines under it, to the next heading
    or a blank line after some words."""

    for i, pattern in enumerate(names):
        head = re.compile(r"^\s*(?:#+\s*)?(?:" + pattern + r")\s*:\s*(?P<rest>.*)$", re.I)
        rows = text.splitlines()
        found = [k for k, line in enumerate(rows) if head.match(line)]
        if not found or (i and len(found) > 1):
            continue
        k = found[0]
        body = [head.match(rows[k]).group("rest").strip()]
        for line in rows[k + 1:]:
            if ANY_HEADING.match(line) or (not line.strip() and any(body)):
                break
            body.append(line.strip())
        value = " ".join(one for one in body if one).strip()
        if value:
            return value
    return ""

# words that open a line of a prompt and are not a person speaking
NOT_NAMES = {"scene", "shot", "action", "camera", "sound", "soundscape", "music", "setting", "location", "style", "note",
             "notes", "prompt", "lighting", "light", "mood", "time", "int", "ext", "cut to", "fade in", "fade out",
             "title", "subject", "subjects", "voice", "line", "lines", "dialogue", "audio", "video", "picture", "lens",
             "integrated_multimodal_description", "overall_soundscape", "non_diegetic_music", "negative", "seed",
             "duration", "length", "frames", "aspect", "transition", "description", "character", "characters", "intent",
             "image", "images", "visual", "visuals", "shot size", "camera movement", "frame", "composition", "color",
             "colour", "background", "foreground", "atmosphere", "costume", "wardrobe", "props", "effects", "vfx", "sfx",
             "output", "output_spec", "format", "resolution", "fps", "aspect ratio", "tone", "emotion", "expression",
             "movement", "performance", "beat", "beats", "character design", "end", "start"}
# "Dialogue (Zixia, coldly): “…”", "Line (Tom): …", "VO (narrator, low voice): …": the name is the first thing in the
# brackets and the rest is how it is said
TAGGED = re.compile(r"^\s*(?:[-*•]\s*)?(?:dialogue|line|lines|speech|says|vo|v\.o\.|voice-over|voiceover|voice over|narration|"
                    r"lyrics?|sung|song)\s*\((?P<inside>[^)]*)\)\s*:\s*(?P<text>\S.*)$", re.I)
CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
JAPANESE = re.compile(r"[\u3040-\u30ff]")
HANGUL = re.compile(r"[\uac00-\ud7af]")
# "[Shot 4] 00:17-00:21": the end of the last shot is the clip's length
STAMP = re.compile(r"\b(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:\.\d+)?\s*[-–—]+\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})")
CUE = re.compile(r"^(?P<name>[A-Z][A-Z0-9 .'\-]{0,38}?)(?:\s*\((?:V\.?O\.?|O\.?S\.?|O\.?C\.?|CONT'?D)\))*\s*$")
PAREN = re.compile(r"^\((?P<note>[^)]*)\)$")
COLON = re.compile(r"^\s*(?:[-*•]\s*)?(?P<name>[A-Za-z][\w .'\-]{0,38}?)\s*(?:\((?P<voice>[^)]*)\))?\s*:\s*(?P<text>\S.*)$")
QUOTED = re.compile(r"[\"“](?P<text>[^\"“”]+)[\"”]")
VERBS = ("say", "says", "said", "saying", "ask", "asks", "asked", "asking", "shout", "shouts", "shouted", "shouting",
         "whisper", "whispers", "whispered", "whispering", "yell", "yells", "yelled", "call", "calls", "called",
         "reply", "replies", "replied", "mutter", "mutters", "muttered", "murmur", "murmurs", "murmured",
         "exclaim", "exclaims", "exclaimed", "announce", "announces", "announced", "add", "adds", "added",
         "answer", "answers", "answered", "tell", "tells", "told", "speak", "speaks", "spoke", "cry", "cries", "cried",
         "scream", "screams", "screamed", "laugh", "laughs", "laughed", "sigh", "sighs", "sighed",
         "sing", "sings", "sang", "singing", "hum", "hums", "hummed", "chant", "chants", "chanted")
SUNG = {"sing", "sings", "sang", "singing", "hum", "hums", "hummed", "chant", "chants", "chanted"}
VERB = re.compile(r"\b(" + "|".join(VERBS) + r")\b", re.I)
IN_VOICE = re.compile(r"\bin\s+(?P<voice>(?:an?|the|his|her|their)\s+[^.;:\"“”]{0,80}?\bvoice)\b", re.I)


def described(note: str) -> str:
    """A parenthetical or a note beside a name, as a voice: "whispering" is a "whispering voice"."""

    note = re.sub(r"^in\s+(?=(?:an?|the|his|her|their)\b)", "", (note or "").strip(), flags=re.I)  # the prompt says "in" itself
    if not note or re.search(r"\bvoice\b", note, re.I) and not re.search(r"voice[- ]?over", note, re.I):
        return note
    if re.fullmatch(r"(?:v\.?o\.?|o\.?s\.?|o\.?c\.?|voice[- ]?over|off[- ]?screen)", note, re.I):
        return "an off-screen voice"
    if " " not in note and "," not in note and not note.lower().endswith("ly"):
        return f"a {note} voice"  # "whispering", "hoarse"
    return f"a voice speaking {note}"  # "coldly, with a hint of a smile"


def language_of(text: str, said: str = "") -> str:
    """The language a line is in, from its own letters where they are not Latin; else what the prompt said, else English."""

    if JAPANESE.search(text):
        return "Japanese"
    if HANGUL.search(text):
        return "Korean"
    if CJK.search(text):
        return "Chinese"
    return (said or "English").strip()


def row(text: str, voice: str = "", speaker: str = "", language: str = "", sung: bool = False) -> dict:
    return {"voice": voice.strip(), "language": language_of(text, language), "text": text.strip(), "sung": sung,
            "speaker": speaker.strip()}


def from_h3(text: str) -> list[dict]:
    lines = []
    for m in BARE_D.finditer(text):
        # the clause that leads up to the line: from the end of the sentence before (or the [Shot n] tag)
        clause = re.split(r"[.!?]\s+|\[Shot \d+\]|</d>|^[a-z_]+:", text[max(0, m.start() - 400):m.start()], flags=re.M)[-1]
        clause = clause.strip()
        sung = re.search(r"\bsings\s*:\s*$", clause, re.I) is not None
        # the speaker comes before the first comma, the voice after it: "The speaker, in a calm, raspy voice (S1), says:"
        after = clause.split(",", 1)[1] if "," in clause else clause
        voice = re.search(r"\bin\s+(?P<voice>.*?)\s*(?:\(S\d+\))?\s*,\s*(?:says|sings)\s*:\s*$", after, re.I)
        voice = voice.group("voice").strip() if voice else ""
        speaker = re.sub(r"\s*\(S\d+\)", "", clause.split(",")[0]).strip() if "," in clause else ""
        if speaker.lower() in ("the speaker", "an off-screen voice") or re.search(r"\b(says|sings)\s*:", speaker, re.I):
            speaker = ""
        lines.append(row(m.group("text"), "" if voice == "a natural voice" else voice, speaker, m.group("lang") or "English", sung))
    return [one for one in lines if one["text"]]


def named(name: str) -> bool:
    words = name.strip().split()
    return 0 < len(words) <= 4 and name.strip().lower() not in NOT_NAMES and words[0].lower() not in NOT_NAMES


def from_screenplay(text: str) -> list[dict]:
    lines, rows, i = [], text.splitlines(), 0
    while i < len(rows):
        cue = CUE.match(rows[i].strip())
        before_blank = i == 0 or not rows[i - 1].strip()
        if cue and before_blank and named(cue.group("name")) and i + 1 < len(rows) and rows[i + 1].strip():
            name, note, said, i = cue.group("name").strip(), "", [], i + 1
            while i < len(rows) and rows[i].strip():
                part = rows[i].strip()
                paren = PAREN.match(part)
                if paren and not said:
                    note = paren.group("note")
                elif not paren:
                    said.append(part)
                i += 1
            if said and not any(c.islower() for c in name) and any(c.islower() for c in " ".join(said)):
                lines.append(row(" ".join(said), described(note), name.title()))
            continue
        i += 1
    return lines


def unquote(text: str) -> str:
    text = text.strip()
    quoted = re.match(r"[\"“]([^\"“”]+)[\"”]", text)
    if quoted:
        return quoted.group(1).strip()
    if len(text) > 1 and text[0] in "\"“" and text[-1] in "\"”":
        return text[1:-1].strip()
    return text


CAST_HEADING = re.compile(r"^\s*(?:#+\s*)?(?:character design|characters?|cast|character notes?|wardrobe|costumes?)\s*:?\s*$", re.I)


def from_colons(text: str) -> list[dict]:
    tagged_lines, named_lines, cast = [], [], False
    for part in text.splitlines():
        # "Character design:" opens a list of "Name: who they are"; it runs to the next blank line or [Shot n]
        if CAST_HEADING.match(part):
            cast = True
            continue
        if cast and (not part.strip() or re.match(r"\s*\[Shot", part, re.I)):
            cast = False
        lines = tagged_lines
        tagged = TAGGED.match(part)
        if tagged:
            inside = [one.strip() for one in tagged.group("inside").split(",") if one.strip()]
            how = ", ".join(inside[1:])
            sung = re.match(r"\s*(?:[-*•]\s*)?(?:lyrics?|sung|song)\b", part, re.I) is not None
            lines.append(row(unquote(tagged.group("text")), described(how) if how else "", inside[0] if inside else "", sung=sung))
            continue
        if cast:
            continue
        lines = named_lines
        m = COLON.match(part)
        if not m or not named(m.group("name")) or "://" in part:
            continue
        said = m.group("text").strip()
        quoted = said[:1] in "\"“"
        # "Kitchen at dawn: warm light" is a description; a person's line is quoted, or the name is one or two words
        if not quoted and len(m.group("name").split()) > 2:
            continue
        lines.append(row(unquote(said), described(m.group("voice")), m.group("name")))
    # a text that marks its lines ("Dialogue (Name): …") uses "Name: …" for other things: descriptions, notes
    return tagged_lines or named_lines


def from_prose(text: str) -> list[dict]:
    lines = []
    for sentence in re.split(r"(?<=[.!?”\"])\s+(?=[A-Z“\"])|\n+", text):
        for m in QUOTED.finditer(sentence):
            near = sentence[max(0, m.start() - 120):m.start()] + " " + sentence[m.end():m.end() + 60]
            verb = VERB.search(near)
            if not verb:
                continue
            voice = list(IN_VOICE.finditer(sentence[:m.start()])) or list(IN_VOICE.finditer(sentence[m.end():]))
            lines.append(row(m.group("text"), voice[-1].group("voice") if voice else "", sung=verb.group(1).lower() in SUNG))
    return lines


STATED = re.compile(r"\b(?:duration|length|runtime|run time|total)\b\s*(?:of|is|:|=|-)?\s*(?:about\s+)?(?P<n>\d+(?:\.\d+)?)\s*"
                    r"(?P<unit>s|sec|secs|seconds?|m|min|mins|minutes?)\b", re.I)


def stated_seconds(text: str) -> float | None:
    """A length the text gives itself, in seconds: the end of its last timed shot ("00:21-00:25"), else
    "Duration: 20 s", "length 1 min", else "25 seconds" in its opening lines (a title or an output spec)."""

    ends = [int(m.group(4) or 0) * 3600 + int(m.group(5)) * 60 + int(m.group(6)) for m in STAMP.finditer(text)]
    if ends and max(ends) > 0:
        return float(max(ends))
    # a bare "25 seconds" counts only on a title line or an output spec's line (one with fps, aspect or resolution)
    spec = "\n".join(line for line in text[:1500].splitlines()
                     if line.lstrip().startswith("#") or re.search(r"\b(fps|aspect|resolution|output)\b", line, re.I))
    m = STATED.search(text) or re.search(r"(?<![\d.])(?P<n>\d+(?:\.\d+)?)\s*(?P<unit>s|sec|secs|seconds?|min|mins|minutes?)\b",
                                          spec, re.I)
    if not m:
        return None
    return float(m.group("n")) * (60 if m.group("unit").lower().startswith("m") else 1)


# a prompt pasted with its line breaks lost runs its parts together: "...her movements rest Dialogue (Mira, coldly):
# “…” overall_soundscape: Wind…". Each of these markers starts a line again before the text is read.
MARKERS = re.compile(r"(?<=\S)[ \t]+(?=(?:\[Shot\b|(?:dialogue|line|lines|speech|vo|v\.o\.|voice-over|voiceover|narration|lyrics?)"
                     r"\s*\([^)\n]*\)\s*:|(?:overall_soundscape|non_diegetic_music|integrated_multimodal_description|output_spec|"
                     r"shot size|camera movement|image|character design|characters)\s*:))", re.I)


def unfold(text: str) -> str:
    return MARKERS.sub("\n", text)


def extract(text: str) -> dict:
    """The spoken lines of ``text`` as Voice / Line rows, which way they were written, and an H3 prompt's sound."""

    text = (text or "")[:LIMIT].replace("\r\n", "\n")
    if not BARE_D.search(text):  # a composed prompt keeps its layout; a hand-written one is unfolded first
        text = unfold(text)
    for way, read in (("h3", from_h3), ("screenplay", from_screenplay), ("colons", from_colons), ("prose", from_prose)):
        lines = read(text)
        if lines:
            break
    else:
        way, lines = "", []
    found = {"dialogue": lines, "format": way, "seconds": stated_seconds(text)}
    for key, names in (("soundscape", SOUND_HEADS), ("music", MUSIC_HEADS)):
        value = section(text, names)
        if value:
            found[key] = value
    return found
