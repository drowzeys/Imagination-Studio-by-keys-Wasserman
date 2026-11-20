"""The optional pack installs using the public language hook and is reproducible from its source."""
import shutil
import subprocess
import sys
from pathlib import Path

from test_languages import client

ROOT = Path(__file__).resolve().parents[1]


def test_generated_language_pack_is_current():
    result = subprocess.run([sys.executable, "-I", str(ROOT / "tools/i18n-zh/build_dict.py"), str(ROOT), "--check"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_operator_can_install_the_self_contained_pack_using_the_existing_hook(tmp_path):
    c = client(tmp_path)
    assert c.get("/api/languages").json() == [{"id": "en", "label": "English"}]
    (tmp_path / "i18n").mkdir()
    pack = ROOT / "languages/zh-TW.js"
    shutil.copyfile(pack, tmp_path / "i18n/zh-TW.js")
    assert c.get("/api/languages").json() == [{"id": "en", "label": "English"}, {"id": "zh-TW", "label": "zh-TW"}]
    response = c.get("/api/languages/zh-TW.js")
    assert response.status_code == 200
    assert response.content == pack.read_bytes()
    assert response.headers["cache-control"] == "no-store"
