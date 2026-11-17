# Traditional Chinese (Taiwan), optional local UI pack

`languages/zh-TW.js` is a self-contained dictionary and display translator for the local language hook.
It translates visible labels, help text, placeholders and browser dialogs. English remains the default;
the app does not install this file, download a dictionary or change its screens unless you choose the language.

## Install

Copy the generated file into the **same Studio data folder** the app uses, under `i18n/zh-TW.js`.
For a default macOS or Linux install, from the repository:

```sh
mkdir -p "${STUDIO_HOME:-$HOME/ImaginationStudio}/i18n"
cp languages/zh-TW.js "${STUDIO_HOME:-$HOME/ImaginationStudio}/i18n/zh-TW.js"
```

On Windows, create `i18n` inside the Studio data folder shown by the app and copy `languages/zh-TW.js` there.
If you already installed a language file, keep your earlier copy before replacing it.

Open Settings → Appearance → Language and choose `zh-TW`. The choice belongs to this browser and survives a reload.
Choose English there to return to the original screens. No restart is needed. If a file is missing or fails to load,
the existing language hook clears the choice and returns to English. The language selector is hidden when no local
language is installed. See [the language hook contract](../../docs/local-languages.md).

The file is trusted local JavaScript, not sandboxed data. It only changes display text: input and textarea values,
editable content, code, prompts, dialogue, model parameters and request payloads keep their original values.
An option without an explicit value keeps its English value even when its label is translated. `data-no-tr`
preserves text and attributes in a subtree. Model names, commands and required credit names remain in the original.

## Maintain

- `translations.json`: English → Traditional Chinese. `null` marks text intentionally kept in English, including
  model prompts and code. Historical screen wording is retained for compatibility.
- `overlay.js`: text-node and attribute translation, including asynchronous UI updates and parameterized messages.
- `GLOSSARY.md`: Taiwan terminology.
- `build_dict.py`: standard-library-only, deterministic generator; no network access.

```sh
python3 -I tools/i18n-zh/build_dict.py
python3 -I tools/i18n-zh/build_dict.py --check
python3 -m pytest -q tests/test_i18n_zh.py tests/test_language_pack.py tests/test_languages.py
```

For coverage after an upstream update, install `acorn`, `acorn-walk` and `parse5@6` in a scratch folder, then run:

```sh
NODE_PATH=/path/to/scratch/node_modules python3 -I tools/i18n-zh/pending.py . > pending.json
```

Review candidates before translating: the extractor also sees commands, model prompts, CSS and other code.
It is a maintenance aid, not a percentage of the interface translated. Merge reviewed strings into
`translations.json`, rebuild, test and replace the installed pack. Optional DOM tests also use `jsdom` on `NODE_PATH`.

Only dictionary matches can be translated. Unrecognized wording remains English. The overlay does not translate
canvas or CSS-generated text, and never translates the motion-graphics render templates into the output video.
