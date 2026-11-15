# Local UI language hook

English is the default. Studio ships no translation dictionary, downloads none and loads no language
script for an English browser. To make a language available, the operator installs a script such as
`<studio home>/i18n/zh-TW.js`. Language IDs are 2–3 letters followed by optional hyphen-separated
2–8 letter/digit subtags, up to 48 characters. Files and the `i18n` folder cannot be symlinks.

When a local file exists, Settings → Appearance shows **Language**. The choice is saved in that browser,
not the server's shared settings. Selecting a language reloads the page; its local script runs before
all built-in application modules. A missing, unreadable or failing script clears the choice and reloads
in English. Removing all local files hides the switch. The script is served from the same Studio,
behind the normal access controls; there is no URL setting, upload or remote installer.

These scripts are **trusted executable JavaScript installed by the operator**, not sandboxed data.
A translation may wrap UI helpers before the modules capture them; it must leave prompts, dialogue,
request payloads and model parameters alone. This hook does not translate model content or implement
an automatic translator. Keep translation dictionaries and their tests in your add-on or fork.
`StudioLanguage.current()` reports the selection; use a script's own localized display labels as needed.
