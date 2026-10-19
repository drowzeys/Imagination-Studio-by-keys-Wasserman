# Imagination Studio brand kit in the app

Source: Sam Wasserman's Imagination Studio Brand Kit, **Ribbon** direction (the poster on Home, `art/poster.webp`).
Only the files the app uses are copied here. The SVGs are outlined vectors (no embedded raster images, no fonts needed).

## Usage rules (from the kit)

- Clear space around a logo: at least 0.5 times the title's cap height.
- Minimum displayed width: title-only stacked 240px, creator lockup 320px, horizontal 280px. Below 320px of room use
  the horizontal logo; at icon size use the aperture mark.
- The authors stay in ALL CAPS: KEYS + WASSERMAN. The plus sign is part of the lockup.
- Never blur the letter shapes. Any glow is a faint outer drop shadow only (the creator credit uses
  `drop-shadow(0 0 8px rgba(99,255,54,.2))`).
- Use dark-ink versions on light surfaces. Avoid ivory on white and blue microtype on a dark background.
- The title-only logo goes in the application header; the creator credit on welcome, About and marketing surfaces.
- NVIDIA and Apple belong in the hardware line, as text. The kit's NVIDIA and Apple pictures are reproductions, not
  official masters, so they are not used. TensorFold sits beside POWERED BY.

## Colours (studio.css `:root`)

| Token | Value | Kit name | Use |
|---|---|---|---|
| `--bg` | `#080D16` | background | page |
| `--bg2` | `#0D1420` | (between background and surface) | sidebar, wells, fields |
| `--panel` | `#0E1724` | (one step below surface) | panels |
| `--raise` | `#111C2B` | surface | cards, buttons |
| `--raise2` | `#182539` | (one step above surface) | hover, selected |
| `--text` | `#F5EFDF` | ivory | text |
| `--dim` | `#A2ADBA` | secondary text | secondary text |
| `--faint` | `#86919F` | (derived) | tertiary text, at least 4.8:1 on every surface |
| `--blue` | `#0666FF` | cobalt | selection, focus ring, selected borders |
| `--blue-text` | `#5C9BFF` | (cobalt lightened for contrast) | links and blue text, at least 5.5:1 |
| `--green` | `#63FF36` | creator green | status LEDs, the creator credit |
| `--teal` | `#113D4C` | petrol | focus wash in fields, the render bay's edge |
| `--amber-muted` | `#B87942` | muted amber | hairlines on buttons, eyebrows |
| `--amber` | `#f1ad66` | (not in the kit) | the primary action and the glowing active pills |

Radii follow the kit: 6 / 12 / 20px (`--r1`, `--r2`, `--r3`). UI type stays Geist (system sans in the kit) and
Instrument Serif for one title a view (the kit's optional editorial serif).

**The one deliberate departure:** at Sam's request the vivid amber `#f1ad66` stays for the primary action fill and
the glowing active pills (the original Studio's look). Everything else that was amber is the kit's muted amber.
Text on the amber primary is `#140c03` (10:1).

## Files and where they are used

| File | Where |
|---|---|
| `ribbon-title-only-color.svg` | Sidebar header, 240px wide (its minimum) in a 260px sidebar |
| `film-ribbon.webp` | Sidebar header above the title, 230px wide, as a flourish read left to right (from the kit's `01-Ribbon/alpha/film-ribbon-cutout.png`, 720px wide WebP with its alpha); on the icon rail cropped to its aperture end at 44px |
| `aperture-mark-color.svg` | In the phone bar when the sub-tabs or Back and Home need the room; the Studio's own tile in Brains & tools |
| `ribbon-horizontal-color.svg` | Phone top bar, 280px, where there are no sub-tabs |
| `keys-wasserman-neon.svg` | Sidebar header under the title on every page: an italic "by" and the credit at 206px, 230px in all (see the exception below); Home under the tagline and Settings → About, 320px |
| `app-icon.svg` | SVG favicon; manifest icon |
| `favicon.ico` | Favicon |
| `app-icon-192.png`, `app-icon-512.png` | Web app manifest icons |
| `apple-touch-icon.png` | iOS home screen (180px, made from the kit's app-icon-256.png) |
| `module-story.svg`, `module-image.svg`, `module-video.svg`, `module-previs.svg`, `module-sound.svg`, `module-edit.svg` | Engine pages' headers; the same paths are drawn inline in `shell.js` for the sidebar and phone bar |
| `module-vault.svg` | Library (inline in `shell.js`) |
| `module-machine.svg` | Machine (inline in `shell.js`) |
| `grid-texture.svg` | Behind the main area only, at a line opacity under 5% |
| `tensorfold-symbol.webp`, `tensorfold-wordmark.webp` | Sidebar footer beside POWERED BY, then "for NVIDIA Sparks or Apple Silicon" as text |

**Exception, at Sam's request:** the creator credit sits in the 260px sidebar at its usable width, 230px, under the
kit's 320px minimum. It keeps its faint green glow and is shown at 320px on Home and About.

Looks, Suite and Learn keep the Studio's own glyphs, redrawn on the kit's 56-unit box with its 2.5 rounded stroke.

The kit's TensorFold SVGs wrap a 700 KB embedded PNG, so the app uses WebP files trimmed and resized from the kit's
transparent TensorFold PNGs instead (160px and 360px wide). Like the kit's originals they are reproductions of the
TensorFold marks, not official masters.
