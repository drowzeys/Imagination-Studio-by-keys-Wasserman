"""Optional local UI scripts: local boundary, bootstrap order and browser choice."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('STUDIO_HOME', str(Path(tempfile.mkdtemp()) / 'studio'))
sys.path.insert(0, str(ROOT / 'app'))
import languages
import server


def client(home, guarded=False):
    app = FastAPI(middleware=server.app.user_middleware if guarded else [])
    app.include_router(languages.attach(home))
    return TestClient(app)


def test_absent_folder_and_default_english(tmp_path):
    c = client(tmp_path)
    assert c.get('/api/languages').json() == [{'id': 'en', 'label': 'English'}]
    assert c.get('/api/languages/en.js').status_code == 404


def test_only_local_valid_files_are_served(tmp_path):
    at = tmp_path / 'i18n'
    at.mkdir()
    (at / 'zh-TW.js').write_text('/* local UI only */')
    (at / 'en.js').write_text('/* English must be built-in */')
    (at / '.hidden.js').write_text('secret')
    (tmp_path / 'outside.js').write_text('outside')
    (at / 'fr.js').symlink_to(tmp_path / 'outside.js')
    c = client(tmp_path)
    assert [x['id'] for x in c.get('/api/languages').json()] == ['en', 'zh-TW']
    r = c.get('/api/languages/zh-TW.js')
    assert r.text == '/* local UI only */' and r.headers['cache-control'] == 'no-store'
    for path in ['en', 'fr', '.hidden', '%2e%2e/outside', 'https:evil']:
        assert c.get('/api/languages/' + path + '.js').status_code == 404


def test_symlink_root_is_not_a_language_source(tmp_path):
    at = tmp_path / 'outside'
    at.mkdir()
    (at / 'zh-TW.js').write_text('/* outside */')
    (tmp_path / 'i18n').symlink_to(at, target_is_directory=True)
    assert len(client(tmp_path).get('/api/languages').json()) == 1
    assert client(tmp_path).get('/api/languages/zh-TW.js').status_code == 404


def test_language_assets_obey_access_key(tmp_path, monkeypatch):
    (tmp_path / 'i18n').mkdir()
    (tmp_path / 'i18n/zh-TW.js').write_text('/* local */')
    monkeypatch.setenv('STUDIO_ACCESS_KEY', 'on')
    monkeypatch.setattr(server, 'ACCESS_KEY', 'sample-test-access-key')
    c = client(tmp_path, guarded=True)
    for url in ['/api/languages', '/api/languages/zh-TW.js']:
        assert c.get(url).status_code == 401
        assert c.get(url, headers={'X-Studio-Key': server.ACCESS_KEY}).status_code == 200


def bootstrap(saved):
    if not shutil.which('node'):
        pytest.skip('node is not installed')
    code = '''const vm=require('vm'),fs=require('fs');
      const out={writes:[],reloads:0}, storage={value:JSON.parse(process.argv[1])};
      global.localStorage={getItem:()=>storage.value,setItem:(k,v)=>storage.value=v,removeItem:()=>storage.value=null};
      global.location={href:'http://studio.test/',reload:()=>out.reloads++};
      global.window={addEventListener:(name,fn)=>out.error=fn};
      global.document={write:s=>out.writes.push(s)};
      global.api=async()=>[{id:'en',label:'English'},{id:'zh-TW',label:'zh-TW'}];
      vm.runInThisContext(fs.readFileSync(process.argv[2],'utf8'));
      (async()=>{out.selected=StudioLanguage.current(); await StudioLanguage.load();
        StudioLanguage.choose('https://example.invalid/script'); out.noRemote=out.reloads===0;
        if(out.error) out.error({filename:'http://studio.test/api/languages/zh-TW.js'});
        out.afterError=StudioLanguage.current();out.saved=storage.value;
        console.log(JSON.stringify(out));})();'''
    result = subprocess.run(['node', '-e', code, json.dumps(saved), str(ROOT / 'app/static/language.js')], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize('saved', [None, 'en', '../../outside', 'https://example.invalid/evil'])
def test_default_or_invalid_selection_loads_no_file(saved):
    result = bootstrap(saved)
    assert result['selected'] == 'en' and result['writes'] == [] and result['reloads'] == 0


def test_selected_local_script_is_parser_blocking_and_failure_resets_to_english():
    result = bootstrap('zh-TW')
    assert result['selected'] == 'zh-TW'
    assert len(result['writes']) == 1 and 'src="/api/languages/zh-TW.js"' in result['writes'][0]
    assert 'async' not in result['writes'][0] and 'defer' not in result['writes'][0]
    assert result['noRemote'] and result['afterError'] == 'en' and result['saved'] is None and result['reloads'] == 1
    page = (ROOT / 'app/static/index.html').read_text()
    assert page.index('src="language.js"') < page.index('src="shell.js"')


def test_settings_switch_only_shows_with_local_languages():
    from test_shell_page import run
    out = run('''
      let choices=[{id:"en",label:"English"}];
      globalThis.StudioLanguage={choices:()=>choices,current:()=>"en",choose:id=>out.chosen=id};
      out.absent = !find(setAppearance(), n=>n.attrs && n.attrs["aria-label"] === "Studio language");
      choices.push({id:"zh-TW",label:"zh-TW"});
      const select=find(setAppearance(), n=>n.attrs && n.attrs["aria-label"] === "Studio language");
      out.options=select.kids.map(n=>n.attrs.value); select.attrs.onchange({target:{value:"zh-TW"}});
    ''')
    assert out == {'absent': True, 'options': ['en', 'zh-TW'], 'chosen': 'zh-TW'}
