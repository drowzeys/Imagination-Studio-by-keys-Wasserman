"""What Slate's compile sends to the model (app/slate.py): python3 tests/test_slate_compile.py. No GPU, no network."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
sys.path.insert(0, str(ROOT / "app"))
import negation as N  # noqa: E402
import server  # noqa: E402  (attaches Slate to the Studio's prompt builders)
import slate  # noqa: E402


def film(**shot) -> dict:
    """One scene with one shot: Leo and Mia on the walkway, as a production run wrote them."""

    one = {"id": "shot-1", "name": "1.4 First Smiles", "intent": "0:12.1-0:16.1 (2 bars, 5 s). The audience sees the first spark.",
           "spec": {"durationSec": 5, "fps": 24, "aspectRatio": "16:9", "size": "MS", "angle": "eye level", "lens": "50mm", "movement": "locked"},
           "prompt": "# Subject\nLeo hands Mia the stack of books and they smile.\n# Composition\nMedium two-shot in profile.\n# Lighting\nLate-morning sun through leaves.",
           "beatSheet": None, "mutedLines": [], "takes": [],
           "tf": {"start": "text", "cast": ["char-leo", "char-mia"], "location": "loc-1", "lines": [], "soundscape": "Leaf rustle and distant birdsong.", "music": "N/A", "cue": "", "seed": 42}}
    one.update(shot)
    return {"id": "p-1", "name": "Hesitation", "defaults": {"durationSec": 8}, "tf": {"res": "480p", "quality": "fh8"},
            "characters": [{"id": "char-leo", "name": "Leo", "age": "21", "gender": "male", "hair": "short sandy-blond hair", "clothing": "a rust-orange work jacket"},
                           {"id": "char-mia", "name": "Mia", "age": "20", "gender": "female", "hair": "long pale-blonde hair", "clothing": "an emerald-green cardigan"}],
            "locations": [{"id": "loc-1", "name": "Campus Walkway", "description": "A paved walkway under old trees"}],
            "scenes": [{"id": "scene-1", "name": "Scene 1", "shots": [one]}]}


def test_a_shots_intent_stays_out_of_the_prompt():
    made = slate.compile_shot(film(), "shot-1")
    assert "0:12.1" not in made["prompt"] and "audience" not in made["prompt"] and "2 bars" not in made["prompt"]
    assert "Leo hands Mia the stack of books" in made["prompt"]  # what happens is the Subject section's
    # with a beat sheet the beats are the action, and the intent is still a note
    beats = slate.compile_shot(film(beatSheet=[{"from": 0, "to": 2, "text": "Leo holds out the books."}, {"from": 2, "to": 5, "text": "Mia takes them and smiles."}]), "shot-1")
    assert "First, leo holds out the books. Finally, mia takes them and smiles." in beats["prompt"] and "audience" not in beats["prompt"]
    # an empty shot still compiles, and says so
    empty = slate.compile_shot(film(prompt=""), "shot-1")
    assert "audience" not in empty["prompt"] and any("no prompt yet" in w for w in empty["warnings"])


def test_a_compiled_prompt_forbids_nothing_by_name():
    """Slate's own words (a locked camera, a shot without lines) say what is there, so the negation check has nothing
    to list in a prompt compiled from a clean shot, with none of the app's sentences passed over."""

    had, N.OWN = N.OWN, ()
    try:
        made = slate.compile_shot(film(), "shot-1")
        prompt = made["prompt"]
        assert "Static shot on a locked, still camera." in prompt and "does not move" not in prompt
        assert "Mouths stay closed." in prompt and "Only ambient sound is heard." in prompt
        assert "Nobody speaks" not in prompt and "No voices" not in prompt
        assert N.find(prompt.replace("non_diegetic_music: N/A", "")) == [] and N.check(prompt)["negations"] == []
        assert not any("no negative prompt" in w for w in made["warnings"])
        for move in slate.MOVES.values():
            assert N.find(move) == [], move
        # a shot with nobody cast and no person in its words: no mouth is named into it
        empty = film(prompt="# Subject\nWaves roll onto an empty beach at dusk.", tf={"start": "text", "cast": [], "location": "", "lines": [], "soundscape": "", "music": "N/A", "seed": 1})
        assert "Mouths" not in slate.compile_shot(empty, "shot-1")["prompt"]
        # the director's own negation is still said, never rewritten
        told = slate.compile_shot(film(prompt="# Subject\nLeo walks. No cars anywhere."), "shot-1")
        assert "No cars anywhere." in told["prompt"] and any("No cars" in w for w in told["warnings"])
    finally:
        N.OWN = had


def music_video() -> dict:
    """"Hesitation": 117.45 BPM, the first beat at 0.07 s, a bar of 2.043 s; three shots of 2, 4 and 2 bars."""

    made = film()
    made["tf"]["song"] = {"name": "Hesitation, kAi", "bpm": 117.45, "beatsPerBar": 4, "firstBeat": 0.07}
    first = made["scenes"][0]["shots"][0]
    first["tf"]["bars"] = 2
    for index, bars in ((2, 4), (3, 2)):
        more = {**first, "id": f"shot-{index}", "name": f"1.{index}", "tf": {**first["tf"], "bars": bars}}
        made["scenes"][0]["shots"].append(more)
    return made


def test_a_slot_in_bars_becomes_the_clip_length_that_covers_it():
    made = music_video()
    song = slate.song_of(made)
    assert round(song["bar"], 3) == 2.043 and song["beats"] == 4
    one = slate.compile_shot(made, "shot-1")["plan"]
    # 2 bars are 4.087 s, 98 frames: the shortest count that covers them is 107 (the run rendered 124 and trimmed)
    assert one["slot"] == {"bars": 2.0, "seconds": 4.087, "frames": 98, "start": 0.07, "bar": 1.0, "spare": 9} and one["frames"] == 107
    assert one["seconds"] == round(107 / 24, 3)
    two = slate.compile_shot(made, "shot-2")["plan"]
    # 4 bars are 8.17 s, 196 frames: 8 seconds (192 frames) is 4 short, the run took a 10 s clip; 209 frames cover it
    assert two["slot"]["frames"] == 196 and two["frames"] == 209 and two["slot"]["spare"] == 13
    assert two["slot"]["bar"] == 3.0 and two["slot"]["start"] == round(0.07 + 2 * song["bar"], 3)
    three = slate.compile_shot(made, "shot-3")["plan"]
    assert three["slot"]["bar"] == 7.0 and three["slot"]["start"] == round(0.07 + 6 * song["bar"], 3)
    # every length a slot gets is one the model takes, and the job is built from it
    for plan in (one, two, three):
        assert (plan["frames"] - 5) % 17 == 0 and server.clip_frames(plan) == plan["frames"]
    # a shot without a slot keeps its seconds, and the shots after it are placed after its clip
    made["scenes"][0]["shots"][0]["tf"]["bars"] = None
    assert "slot" not in slate.compile_shot(made, "shot-1")["plan"] and slate.compile_shot(made, "shot-1")["plan"]["frames"] == 124
    assert slate.compile_shot(made, "shot-2")["plan"]["slot"]["start"] == round(0.07 + 124 / 24, 3)
    # bars without a song, and a slot longer than a clip, are said
    alone = film()
    alone["scenes"][0]["shots"][0]["tf"]["bars"] = 2
    told = slate.compile_shot(alone, "shot-1")
    assert "slot" not in told["plan"] and any("no song" in w for w in told["warnings"])
    made["scenes"][0]["shots"][1]["tf"]["bars"] = 8
    long = slate.compile_shot(made, "shot-2")
    assert long["plan"]["frames"] == 362 and any("longer than a clip" in w for w in long["warnings"])
    # a tempo that is none is no song
    for bad in ({}, {"bpm": 0}, {"bpm": "fast"}, {"bpm": 5000}, {"bpm": 120, "beatsPerBar": 0}):
        assert slate.song_of({"tf": {"song": bad}}) is None or bad.get("beatsPerBar") == 0 and slate.song_of({"tf": {"song": bad}})["beats"] == 4


def test_a_shots_own_lora_goes_into_its_plan_and_through_the_memory_guard():
    made = music_video()
    made["scenes"][0]["shots"][0]["tf"]["loras"] = [{"id": "camera-motion", "strength": 0.9, "style": "Orbit"}, {"id": ""}, "junk"]
    plan = slate.compile_shot(made, "shot-1")["plan"]
    assert plan["loras"] == [{"id": "camera-motion", "strength": 0.9, "style": "Orbit"}] and "loras" not in slate.compile_shot(made, "shot-2")["plan"]
    # the guard's verdict is the queue's own: asked with the plan, and with whether a LoRA is on the shot
    asked, had = [], slate.room
    try:
        slate.room = lambda plan, lora: asked.append((plan["frames"], plan.get("preset"), lora)) or {"fits": not lora, "reason": "a clip with a LoRA ... is refused. What fits: 1280x736 at this length.", "need_mb": 76843, "nearest": {"size": [1280, 736]}}
        made["tf"]["res"] = "sharp"
        told = slate.compile_shot(made, "shot-1")
        assert asked[-1] == (107, "sharp", True) and told["memory"]["fits"] is False and told["memory"]["nearest"] == {"size": [1280, 736]}
        assert any(w.startswith("This shot will be refused when queued:") and "What fits" in w for w in told["warnings"])
        fine = slate.compile_shot(made, "shot-2")
        assert asked[-1] == (209, "sharp", False) and fine["memory"]["fits"] is True and not any("refused" in w for w in fine["warnings"])
        # the film's LoRAs count on a shot that has none of its own
        made["tf"]["loras"] = [{"id": "camera-motion"}]
        slate.compile_shot(made, "shot-2")
        assert asked[-1][2] is True
    finally:
        slate.room = had
    # with the app's own guard on a DGX Spark: a Sharp shot with a LoRA at 124 frames fits where the engine merges the
    # LoRA itself (2.7.1, measured to 1376x768 at 243 frames), and is refused in the preflight where it predates that
    if server.SPARK:
        import memory_guard

        was = memory_guard.room_mb
        memory_guard.room_mb = lambda: 118700
        try:
            made["tf"]["loras"] = []
            made["scenes"][0]["shots"][0]["tf"]["bars"] = None
            made["scenes"][0]["shots"][0]["spec"]["durationSec"] = 5
            real = slate.compile_shot(made, "shot-1")
            merges = server.SPARK_ENGINE_LIB.exists() and memory_guard.engine_takes_lora(server.SPARK_ENGINE_LIB)
            assert real["plan"]["frames"] == 124
            assert real["memory"]["fits"] is True if merges else (real["memory"]["fits"] is False and "1280x736 at this length" in real["memory"]["reason"])
            made["tf"]["res"] = "720p"
            assert slate.compile_shot(made, "shot-1")["memory"]["fits"] is True
        finally:
            memory_guard.room_mb = was


def test_a_speaker_not_pictured_is_an_offscreen_voice():
    """A shot on cast stills with a line by someone not pictured (issue #56): the line is an off-screen voice, never
    given to <Subject 1>, and the preflight says so. Lines go to the subject whose still is in the shot, also when a
    cast member before them has no still."""

    stills = Path(slate.HOME) / "casting"
    stills.mkdir(parents=True, exist_ok=True)
    (stills / "mia.png").write_bytes(b"png")
    made = film()
    made["characters"].append({"id": "char-hale", "name": "Hale", "age": "50", "gender": "male"})
    made["characters"][1]["images"] = ["casting/mia.png"]  # Mia has a still, Leo (cast first) has none
    tf = made["scenes"][0]["shots"][0]["tf"]
    tf.update(start="cast", lines=[{"characterId": "char-hale", "text": "Varga, do you copy?"}])
    had = slate.ready
    slate.ready = lambda: {"ref2va": True}
    try:
        told = slate.compile_shot(made, "shot-1")
        assert told["mode"] == "references" and told["plan"]["references"] == ["casting/mia.png"]
        assert "An off-screen voice, Hale, (S1)" in told["prompt"] and "<Subject 1> (S1)" not in told["prompt"]
        assert "listen with their mouths closed" in told["prompt"]
        assert any(w.startswith("Hale speaks but is not pictured") for w in told["warnings"])
        # Mia's own line goes to her picture, <Subject 1>, though Leo comes first in the cast
        tf["lines"].append({"characterId": "char-mia", "text": "Copy that."})
        both = slate.compile_shot(made, "shot-1")
        assert "<Subject 1> (S2)" in both["prompt"] and "During the off-screen lines the mouths in the frame stay closed." in both["prompt"]
        assert not N.find(N.picture_part(both["prompt"]))
    finally:
        slate.ready = had


def bible_film() -> dict:
    """The film with two pictures pinned on Leo, three on Mia and two on the walkway, every one on disk."""

    folder = Path(slate.HOME) / "casting"
    folder.mkdir(parents=True, exist_ok=True)
    made = film()
    for sheet, names in ((made["characters"][0], ["leo1", "leo2"]), (made["characters"][1], ["mia1", "mia2", "mia3"]), (made["locations"][0], ["walk1", "walk2"])):
        sheet["images"] = []
        for name in names:
            (folder / f"{name}.png").write_bytes(b"png")
            sheet["images"].append(f"casting/{name}.png")
    made["tf"].update(bible="auto", dialogue="h3")  # as the page makes a new film
    return made


def with_ref2va(test):
    had = slate.ready
    slate.ready = lambda: {"ref2va": True}
    try:
        return test()
    finally:
        slate.ready = had


def test_the_bible_puts_the_casts_and_the_places_pictures_in_a_shot():
    def test():
        made = bible_film()
        told = slate.compile_shot(made, "shot-1")  # a shot from text: the bible makes it a shot on reference pictures
        refs = told["plan"]["references"]
        assert told["mode"] == "references"
        # each person's reference first (<Subject 1>, <Subject 2>), then their second look, then two of the place
        assert refs == ["casting/leo1.png", "casting/mia1.png", "casting/leo2.png", "casting/mia2.png", "casting/walk1.png", "casting/walk2.png"]
        prompt = told["prompt"]
        assert "<Subject 1> is Leo" in prompt and "<Subject 2> is Mia" in prompt and "<Subject 3>" not in prompt
        assert "<Picture 3> is another view of <Subject 1>: the look of Leo" in prompt and "<Picture 4> is another view of <Subject 2>: the look of Mia" in prompt
        assert "<Picture 5> is the place: Campus Walkway" in prompt and "<Picture 6> is the place: Campus Walkway" in prompt
        assert "Campus Walkway: A paved walkway under old trees" in prompt  # the place in words as well
        # nine pictures at most, the cast first
        for n in range(4):
            made["characters"].append({"id": f"char-{n}", "name": f"Extra {n}", "images": ["casting/leo1.png", "casting/mia3.png"]})
            made["scenes"][0]["shots"][0]["tf"]["cast"].append(f"char-{n}")
        full = slate.compile_shot(made, "shot-1")["plan"]["references"]
        assert len(full) == 9 and "casting/walk1.png" not in full
        # off: a shot from text is a shot from text
        made = bible_film()
        made["tf"]["bible"] = "off"
        assert slate.compile_shot(made, "shot-1")["mode"] == "text"
        made["scenes"][0]["shots"][0]["tf"]["start"] = "cast"  # and one on cast stills takes the references only
        assert slate.compile_shot(made, "shot-1")["plan"]["references"] == ["casting/leo1.png", "casting/mia1.png"]
        # a place alone is no reference: with nobody pictured the shot stays one from text
        made = bible_film()
        made["scenes"][0]["shots"][0]["tf"]["cast"] = []
        assert slate.compile_shot(made, "shot-1")["mode"] == "text"
    with_ref2va(test)
    # without the Ref2VA model a shot from text stays one, and says nothing of pictures (stood in for: a Spark that runs
    # the tests may have Ref2VA installed)
    had = slate.ready
    slate.ready = lambda: {"ref2va": False}
    try:
        told = slate.compile_shot(bible_film(), "shot-1")
    finally:
        slate.ready = had
    assert told["mode"] == "text" and not any("Ref2VA" in w for w in told["warnings"])


def test_a_film_made_before_the_bible_setting_renders_as_it_did():
    def test():
        made = bible_film()
        del made["tf"]["bible"], made["tf"]["dialogue"]
        assert slate.compile_shot(made, "shot-1")["mode"] == "text"  # no automatic pictures on a shot from text
        tf = made["scenes"][0]["shots"][0]["tf"]
        tf["start"] = "cast"  # cast stills: each person's reference still, as before
        assert slate.compile_shot(made, "shot-1")["plan"]["references"] == ["casting/leo1.png", "casting/mia1.png"]
        tf["lines"] = [{"characterId": "char-leo", "text": "Here, take these."}]
        assert slate.compile_shot(made, "shot-1")["dialogue"] == "h3"  # no cast voice: the video model speaks
        (Path(slate.HOME) / "voice").mkdir(exist_ok=True)
        (Path(slate.HOME) / "voice" / "leo.wav").write_bytes(b"wav")
        made["voices"] = [{"id": "v-leo", "name": "Leo", "characterId": "char-leo", "drama": {"path": "voice/leo.wav", "seconds": 5.0, "use": True}}]
        told = slate.compile_shot(made, "shot-1")  # a cast voice in use: the lines take it, as they did
        assert told["dialogue"] == "drama3" and told["plan"]["reference_audios"] == ["voice/leo.wav"]
        tf["dialogue"] = "h3"  # the shot's own choice still wins
        assert "reference_audios" not in slate.compile_shot(made, "shot-1")["plan"]
    with_ref2va(test)


def test_a_save_older_than_the_film_on_disk_is_refused():
    from fastapi.testclient import TestClient

    client = TestClient(server.app)
    made = client.post("/api/slate/projects", json={"name": "Two writers"}).json()
    loaded = dict(made)
    import time as clock
    clock.sleep(0.01)
    later = slate.write({**slate.get_project(made["id"]), "logline": "the Director's"})  # a writer on the server
    assert later["updatedAt"] > loaded["updatedAt"]
    stale = client.put(f"/api/slate/projects/{made['id']}", json={**loaded, "logline": "the page's"})
    assert stale.status_code == 409 and "changed on the server" in stale.json()["error"]
    assert slate.get_project(made["id"])["logline"] == "the Director's"
    fresh = client.put(f"/api/slate/projects/{made['id']}", json={**later, "logline": "the page's"})
    assert fresh.status_code == 200 and fresh.json()["updatedAt"] >= later["updatedAt"]
    plain = client.put(f"/api/slate/projects/{made['id']}", json={k: v for k, v in later.items() if k != "updatedAt"})
    assert plain.status_code == 200  # a body without a stamp is saved as before


def test_a_shot_from_the_shot_before_or_an_image_takes_no_pictures_but_the_words():
    def test():
        made = bible_film()
        shot = made["scenes"][0]["shots"][0]
        before = {**shot, "id": "shot-0", "name": "1.3", "takes": [{"mediaPath": "renders/before.mp4", "rating": "circled"}]}
        made["scenes"][0]["shots"].insert(0, before)
        shot["tf"]["start"] = "previous"
        told = slate.compile_shot(made, "shot-1")
        assert told["mode"] == "previous" and "references" not in told["plan"] and told["plan"]["first_frame_from"] == "renders/before.mp4"
        assert "Leo is 21 years old, male" in told["prompt"] and "Campus Walkway" in told["prompt"]
        (Path(slate.HOME) / "casting" / "frame.png").write_bytes(b"png")
        shot["tf"].update(start="image", image="casting/frame.png")
        told = slate.compile_shot(made, "shot-1")
        assert told["mode"] == "image" and "references" not in told["plan"] and "Mia is 20 years old" in told["prompt"]
    with_ref2va(test)


def test_the_bibles_loras_join_the_shots_own_up_to_four():
    def test():
        made = film()
        made["characters"][0]["lora"] = {"id": "leo-face", "strength": 0.8}
        made["characters"][1]["lora"] = {"id": "camera-motion", "strength": 0.5}  # the shot has it already: once
        made["locations"][0]["lora"] = {"id": "walkway-look", "style": "Autumn"}
        tf = made["scenes"][0]["shots"][0]["tf"]
        tf["loras"] = [{"id": "camera-motion", "strength": 0.9, "style": "Orbit"}]
        plan = slate.compile_shot(made, "shot-1")["plan"]
        assert plan["loras"] == [{"id": "camera-motion", "strength": 0.9, "style": "Orbit"}, {"id": "leo-face", "strength": 0.8}, {"id": "walkway-look", "style": "Autumn"}]
        # in every mode: a shot on reference pictures keeps them too
        made2 = bible_film()
        made2["characters"][0]["lora"] = {"id": "leo-face"}
        with_refs = slate.compile_shot(made2, "shot-1")
        assert with_refs["mode"] == "references" and with_refs["plan"]["loras"] == [{"id": "leo-face"}]
        # no shot LoRA: the film's stand in, and the bible's join them
        tf["loras"] = []
        made["tf"]["loras"] = [{"id": "film-grain"}]
        assert [one["id"] for one in slate.compile_shot(made, "shot-1")["plan"]["loras"]] == ["film-grain", "leo-face", "camera-motion", "walkway-look"]
        # at most 4, said
        tf["loras"] = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        told = slate.compile_shot(made, "shot-1")
        assert [one["id"] for one in told["plan"]["loras"]] == ["a", "b", "c", "leo-face"]
        assert any("at most 4 LoRAs" in w and "camera-motion" in w and "walkway-look" in w for w in told["warnings"])
    with_ref2va(test)


def test_dialogue_by_the_video_model_takes_no_voice_reference_and_by_drama_3_does():
    def test():
        made = bible_film()
        (Path(slate.HOME) / "voice").mkdir(exist_ok=True)
        (Path(slate.HOME) / "voice" / "leo.wav").write_bytes(b"wav")
        made["voices"] = [{"id": "v-leo", "name": "Leo", "characterId": "char-leo", "ageGender": "a young man", "drama": {"path": "voice/leo.wav", "seconds": 5.0, "use": True}}]
        tf = made["scenes"][0]["shots"][0]["tf"]
        tf["lines"] = [{"characterId": "char-leo", "text": "Here, take these."}]
        h3 = slate.compile_shot(made, "shot-1")  # the default: the video model speaks the line from the words
        assert h3["dialogue"] == "h3" and "reference_audios" not in h3["plan"] and "<d>[English] Here, take these.</d>" in h3["prompt"]
        assert "<Audio" not in h3["prompt"]
        made["tf"]["dialogue"] = "drama3"
        drama = slate.compile_shot(made, "shot-1")
        assert drama["dialogue"] == "drama3" and drama["plan"]["reference_audios"] == ["voice/leo.wav"] and "<Audio 1>" in drama["prompt"]
        tf["dialogue"] = "h3"  # the shot's own choice wins
        assert "reference_audios" not in slate.compile_shot(made, "shot-1")["plan"]
        made["tf"]["dialogue"], tf["dialogue"] = "h3", "drama3"
        assert slate.compile_shot(made, "shot-1")["plan"]["reference_audios"] == ["voice/leo.wav"]
        # Drama 3 on a shot that cannot take references: said, and spoken from the words
        made["tf"]["bible"] = "off"
        told = slate.compile_shot(made, "shot-1")
        assert "reference_audios" not in told["plan"] and any("Drama 3 needs a shot on reference pictures" in w for w in told["warnings"])
    with_ref2va(test)


def test_a_takes_sound_becomes_a_characters_voice():
    import shutil as sh
    import subprocess as sp
    import wave

    from fastapi.testclient import TestClient

    client = TestClient(server.app)
    home = Path(slate.HOME)
    made = film()
    made["id"] = "p-voicetake"
    tf = made["scenes"][0]["shots"][0]["tf"]
    tf["lines"] = [{"characterId": "char-mia", "text": "Thank you."}, {"characterId": "char-leo", "text": "Any time."}]
    (home / "slate" / "p-voicetake" / "takes").mkdir(parents=True, exist_ok=True)
    made["scenes"][0]["shots"][0]["takes"] = [{"id": "take-a", "mediaPath": "slate/p-voicetake/takes/a.mp4", "seed": 7},
                                              {"id": "take-b", "mediaPath": "slate/p-voicetake/takes/b.mp4", "seed": 8}]
    made["voices"] = [{"id": "v-mia", "name": "Mia's voice", "characterId": "char-mia"}]
    slate.write(made)
    seconds = {"a.mp4": 20.0, "b.mp4": 1.2}
    had = slate.take_sound

    def stand_in(source, target):  # the first 15 s of the take, as ffmpeg would cut it
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"wav")
        return min(seconds[source.name], 15.0)

    for name in seconds:
        (home / "slate" / "p-voicetake" / "takes" / name).write_bytes(b"mp4")
    slate.take_sound = stand_in
    try:
        ask = {"project": "p-voicetake", "shot": "shot-1", "take": "take-a"}
        back = client.post("/api/slate/voice-from-take", json={**ask, "character": "char-leo"}).json()
        leo = next(v for v in back["voices"] if v["characterId"] == "char-leo")  # a new sheet named after him
        assert leo["name"] == "Leo" and leo["drama"]["seconds"] == 15.0 and leo["drama"]["use"] is True and leo["drama"]["origin"] == "take"
        record = __import__("json").loads((home / leo["drama"]["path"]).with_suffix(".take.json").read_text())
        assert record["origin"] == "take" and record["slate"] == {"project": "p-voicetake", "voice": leo["id"]} and record["text"] == "Any time."
        assert record["path"] == leo["drama"]["path"] and record["path"].startswith("voice/")
        mia = client.post("/api/slate/voice-from-take", json={**ask, "character": "char-mia"}).json()
        assert [v["id"] for v in mia["voices"]].count("v-mia") == 1 and mia["voices"][0]["drama"]["text"] == "Thank you."  # her own sheet
        assert slate.get_project("p-voicetake")["voices"][0]["drama"]["path"]  # written to the film
        short = client.post("/api/slate/voice-from-take", json={**ask, "take": "take-b", "character": "char-leo"})
        assert short.status_code == 400 and "at least 2 s" in short.json()["error"]
        assert client.post("/api/slate/voice-from-take", json={**ask, "character": "nobody"}).status_code == 404
        assert client.post("/api/slate/voice-from-take", json={**ask, "take": "take-z", "character": "char-leo"}).status_code == 404
    finally:
        slate.take_sound = had
    # with ffmpeg here: a real clip's sound, cut to 15 s, mono 44.1 kHz
    ffmpeg = sh.which("ffmpeg")
    if ffmpeg:
        clip = home / "slate" / "p-voicetake" / "takes" / "a.mp4"
        sp.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=17", "-f", "lavfi", "-i", "sine=frequency=220:duration=17",
                "-shortest", "-c:v", "libx264", "-c:a", "aac", str(clip)], check=True)
        back = client.post("/api/slate/voice-from-take", json={**ask, "character": "char-leo"}).json()
        leo = next(v for v in back["voices"] if v["characterId"] == "char-leo")
        with wave.open(str(home / leo["drama"]["path"])) as sound:
            assert sound.getnchannels() == 1 and sound.getframerate() == 44100 and abs(sound.getnframes() / 44100 - 15.0) < 0.1
        silent = home / "slate" / "p-voicetake" / "takes" / "b.mp4"
        sp.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=3", "-c:v", "libx264", str(silent)], check=True)
        none = client.post("/api/slate/voice-from-take", json={**ask, "take": "take-b", "character": "char-leo"})
        assert none.status_code == 400 and "no sound" in none.json()["error"]


def test_a_reply_with_a_think_tag_spliced_in_gives_the_copy_after_it():
    """A brain with thinking off can still splice a think tag into its answer and start the answer again after it: the
    first copy breaks off inside a prompt, the whole one follows the tag."""

    whole = {"shots": [{"scene": "The Keeper Waves", "name": "Keeper Waves Lamp", "prompt": "# Subject\nThe keeper waves a lamp.\n# Mood\nDread."}]}
    reply = '{"shots":[{"scene":"The Keeper Waves","name":"Keeper Waves Lamp","prompt":"# Subject\\nThe keeper waves a lamp.\\n# Mood</think>' + __import__("json").dumps(whole)
    assert slate.extract_json(reply) == whole
    # a reply cut off inside its JSON says so: a value nested in the broken one is not taken for the answer
    for cut in ('{"issues":[{"severity":"warn","text":"a"},{"severity":', '{"shots":[{"name":"cut off" ... then {"shots": [{"name": "A"}]}'):
        try:
            slate.extract_json(cut)
            raise AssertionError("a fragment was taken for the answer: " + cut)
        except ValueError:
            pass
    # the usual fences and preamble still work
    assert slate.extract_json('Here you go:\n```json\n{"a": [1, {"b": 2}]}\n```') == {"a": [1, {"b": 2}]}
    assert slate.extract_json("[1, 2] and nothing else") == [1, 2]
    try:
        slate.extract_json("no json at all </think> still none")
        raise AssertionError("no JSON was found and none was made up")
    except ValueError:
        pass


def test_a_character_named_only_in_the_prompt_is_said():
    made = slate.compile_shot(film(tf={"start": "text", "cast": ["char-leo"], "location": "loc-1", "lines": [], "soundscape": "", "music": "N/A", "seed": 1}), "shot-1")
    assert any(w.startswith("Mia is named in the prompt but not cast") for w in made["warnings"])
    cast = slate.compile_shot(film(), "shot-1")
    assert not any("named in the prompt" in w for w in cast["warnings"])


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)
