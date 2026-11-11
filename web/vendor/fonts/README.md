# Vendored fonts

Two variable fonts, latin subset, weight axis 100–900:

| File | Family | Licence |
| --- | --- | --- |
| `inter-latin-wght.woff2` | Inter (body text) | SIL OFL 1.1, `LICENSE-inter.txt` |
| `montserrat-latin-wght.woff2` | Montserrat (titles and numbers) | SIL OFL 1.1, `LICENSE-montserrat.txt` |

Both come from the Fontsource builds of the upstream projects and are the same
files the sister project *Mapa de Antenas Anatel* ships.

They are vendored for the same reason as Leaflet: the standalone map must open
from disk with no request other than the basemap tiles. The bundler
(`rendering/standalone.py`) checks them against `CHECKSUMS.txt` and inlines
them into the HTML as `data:` URIs; served from `web/`, `fonts.css` loads them
by relative path. If a font fails to load, the stack falls back to the system
UI font.
