# Earth textures for the globe

| File | Content |
| --- | --- |
| `earth_atmos_2048.jpg` | Daylight surface colour |
| `earth_lights_2048.png` | City lights at night |
| `earth_clouds_1024.png` | Cloud cover, transparent background |

Copied from the three.js repository, release r170
(`examples/textures/planets/`, MIT licence), where they are derived from NASA
Blue Marble and Black Marble imagery. They are decoration for the sign-in and
home pages only; nothing is measured from them. If they cannot be loaded, the
globe falls back to a texture painted from `src/assets/land-outline.json`.
