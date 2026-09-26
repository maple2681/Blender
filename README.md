# 5 AM Pre-dawn — cinematic seascape (Blender 5.x + Cycles)

A hyper-realistic pre-dawn shot at 5:00 AM. The sun is 8.5° below the horizon, so there is no sun in the sky:
the scene is lit by the dim, diffuse glow of the twilight sky, a few old pier lamps and one warm kerosene
lantern. The camera glides slowly along a rotting plank pier past an abandoned red fish house. Beside it, an
old fisherman rows his wooden boat out past the posts of the pier's ruined end, into sea fog rolling just
above the water. Behind him, eroded mountain ranges fade into the distance.

| | |
|---|---|
| Duration | 15 s: 900 frames at **60 fps** |
| Resolution | **2048 × 858**: DCI 2K, 2.39:1 scope (change `res_x` / `res_y` for 16:9) |
| Engine | Cycles, AgX view transform, OpenImageDenoise |
| Camera | 40 mm at standing eye height on the pier, a slow 5.5 m walk along the planks; focus follows the boat |

![Frame 1 preview](preview/5AM_Predawn_preview.jpg)

*Frame 1: a 32-sample CPU preview, rendered in a cloud sandbox only to check the look. The real render is
yours to do on the PC.*

Built and tested with Blender 5.0. The file opens in 5.2 LTS.

## Rendering it on your PC

1. Open `5AM_Predawn.blend` in Blender 5.x.
2. In **Edit → Preferences → System → Cycles Render Devices**, pick **OptiX** (NVIDIA RTX), **CUDA** (older
   NVIDIA) or **HIP** (AMD), and tick your GPU. The scene is already set to render on the GPU.
3. Press **Render → Render Animation** (Ctrl+F12). Frames go to `renders/5AM_frames/5AM_0001.png` …
   `5AM_0900.png`.
   * The render is resumable. Frames that already exist are skipped, so you can stop and restart at any
     time. You can also run a second PC on the same folder and they'll share the work.
4. Switch to the scene **`5AM_Edit`** (scene selector, top right) and press **Render Animation** again. In about a minute it
   turns the frames and the soundtrack into `renders/5AM_Predawn_0001-0900.mp4` (H.264, AAC audio).

Keep the `audio` folder next to the `.blend`: the edit scene takes its sound from there.

### Render time

The frame is mostly smooth sky, fog and water. The fog and haze are ray-marched absorption + emission
volumes, so they add no noise, and 128 samples with adaptive sampling and OpenImageDenoise come out clean.

The only speed I could measure was on the 4-core cloud CPU used for the preview: a full-resolution frame at
64 samples took about 12½ minutes. A recent NVIDIA RTX GPU is usually 20–60× faster than that in Cycles.
Expect roughly **½–2 minutes per frame** at the default 128 samples, which is about 8–30 hours for all 900
frames, depending on the card.

To adjust it:

* If you see flicker in the fog or sky when you play the frames back, raise **Max Samples** to 256 in
  **Render Properties → Sampling**.
* For a faster draft, lower it to 64.
* For a quick look first, set `quality="PREVIEW"` in the script and rebuild. That renders at 50 % size with
  48 samples.
* `volume_step_rate` sets the ray-march step: 1 is the finest, 4 is a draft. The default of 2 is visually
  the same as 1.

## Files

| File | What it is |
|---|---|
| `5AM_Predawn.blend` | The finished scene, ready to render. The heightmaps are packed inside it. |
| `build_5am_predawn.py` | Builds the whole scene from scratch. All the controls are in the `P = dict(...)` block at the top. |
| `textures/5AM_terrain_*.png` | 16-bit heightmaps of the three ranges, plus `5AM_terrain.json` (their extents and heights). |
| `tools/make_terrain.py` | Grows those heightmaps with an erosion simulation. Needs numpy, scipy, numba and pillow; not Blender. |
| `assets/5AM_rower.blend` | The fisherman: rigged, dressed and groomed. The builder appends him into the boat. |
| `tools/make_rower.py` | Builds that character from MakeHuman's CC0 data (`blender -b --python tools/make_rower.py -- <mpfb2>/src/mpfb/data`). |
| `make_soundtrack.py` | Builds the soundtrack stems from real field recordings. `--shot 5am` is the default; `--shot 3am` still makes the old harbour soundtrack. |
| `audio/` | The 5 AM stems (20 s each, so the 15 s shot has handles) and the source recordings. |
| `build_3am_harbor.py` | The original 3 AM harbour builder. The 5 AM builder reuses its pier and fish house, so keep it next to the script. |

To rebuild after changing settings, open `build_5am_predawn.py` in Blender's Scripting workspace and click **Run
Script**, or run `blender --python build_5am_predawn.py`. It rebuilds only the `5AM_Predawn` scene, so the rest
of the file is left alone.

## What is in the shot, and why it looks the way it does

* **Light.** A physically based *multiple-scattering* sky (Blender 5's twilight model) with the sun 8.5° below
  the horizon, ahead and to the right, so the sky on that side is slightly brighter. A thin waning crescent
  moon hangs low in the east, as it does before sunrise; its lit edge faces the hidden sun. Set
  `moon=False` to remove it.
* **Mountains.** Three ranges, each grown by a stream-power erosion simulation, so they have real branching
  drainage, sharp ridges and spurs that run down into the sea:
  * a dark coastal headland 3–8 km away on the left;
  * a fjord range 9–18 km away;
  * a snow-capped massif 17–44 km away.
  The material adds forest below the treeline, alpine meadow, rock, scree, snowfields and dark wet rock at
  the tideline.
* **Aerial perspective.** Each farther range fades toward the colour of the horizon sky behind it. That
  colour is read from the sky model itself, so the haze always matches the sky.
* **Ocean.** A real FFT ocean made from two Ocean modifiers:
  * a long, smooth swell (about 0.6 m);
  * a light wind sea (about 0.25 m) with Jacobian whitecap foam.

  Shader ripples come and go in breeze patches with glassy slicks between them. The waves ease out with
  distance so the horizon never shimmers.
* **The rower.** A detailed human built from MakeHuman's CC0 base mesh and shaped into a weathered man in
  his fifties. He has MakeHuman's full 163-bone skeleton and skin weights, real eyes, a grey-flecked beard,
  eyebrows, and skin with subsurface scattering and pores. He wears a dark oilskin jacket with a stand-up
  collar, canvas trousers, rubber sea boots and a knitted wool cap.
* **The rowing.** There is no motor. He sculls with two oars through a full, realistic stroke cycle every
  2.7 s:
  * the catch;
  * the drive, with the blades buried and square;
  * the finish, where the blades lift out and feather flat;
  * the recovery, hands away first and then the body swings forward.

  His hands are held to the oar grips by IK, his feet are braced on the stretcher, and he takes one look
  over his shoulder to check his course. The boat surges on every stroke and floats on the actual FFT
  waves: probes shrinkwrapped to the sea surface drive its heave, pitch and roll. Each catch leaves a ring
  of ripples and each finish leaves a glassy puddle in the water behind him.
* **The boat.** A clinker-built Norwegian-style double-ender with lapped planks, keel and curled stems,
  ribs, gunwales, thwarts and bronze rowlocks. The old paint is peeling to grey wood, with rust weeping
  from the rivets and green weed at the waterline. Aboard are a fish box, a net, a coil of rope, a tin
  bucket, and a hurricane lantern swinging on its crook, with a soft halo in the damp air.
* **The pier and the fish house.** These are built by your 3 AM builder's own code, which the 5 AM
  builder runs and renames `H5H_`, so `build_3am_harbor.py` must stay next to the script. The pier is
  rotting planks with moss and little plants in the cracks, missing and sagging boards, and a collapsed
  end. The house is red clapboard with peeling paint, broken windows, a hanging door, a barrel chimney,
  and a mast with wires. Crates, a barrel, rope and a buoy sit on the deck. Three old pier lamps line the
  walk, as in your third photo: one flickers, one is dead, and a cable sags between them.
* **The jetty.** Beyond the pier's collapsed end, two rows of rotten posts run out into the fog, modelled on your reference photo: grey
  cracked wood with rust-orange rot on top, moss, then a skirt of bright green weed strands at the tide
  line, barnacle crust and black slime below. Broken stringers, a sagging rope to a bobbing mooring buoy,
  and kelp and driftwood riding the swell complete it.
* **Life.** Gulls perch on the posts and fly slowly across the sky, flapping between glides. A lighthouse on
  the headland turns its beam through the haze, and the lamp flares each time the beam passes the camera.
* **Fog.** A dense layer 1–5 m deep lies on the water. Its top heaves in slow swells and rolling billows, it
  drifts with the breeze and keeps changing shape. Farther out, a fog bank hides the foot of the mountains.

The main controls in `P`:

| Control | What it does |
|---|---|
| `exposure` | overall brightness |
| `sun_elevation` / `sun_azimuth` | how light the sky is and which side glows |
| `fog_density`, `fog_top`, `fog_billow` | how thick the fog is and how it rolls |
| `haze_density` | how quickly the mountains fade |
| `wind_speed`, `swell_scale`, `windsea_scale` | how rough the sea is |
| `cam_travel` / `cam_yaw` | the camera move |
| `boat_start`, `boat_heading`, `boat_speed`, `stroke_period` | where he rows and how fast |
| `lantern_power` | the lantern's brightness |
| `pier`, `jetty`, `gulls`, `lighthouse`, `kelp`, `moon` | switch elements on or off |
| `pier_offset`, `pier_rotation`, `pier_lamp_power` | where the pier and house stand, and how bright their lamps are |

## Credits

* Waves: Luftrum, "oceanwavescrushing.wav", freesound #48412, **CC BY 4.0. Credit required.**
* Wind: felix.blume, freesound #217506, CC0.
* Both as edited for the Blanket app (github.com/rafaelmardojai/blanket).
* Human base mesh, body targets, skeleton and skin weights: MakeHuman / MPFB2
  (github.com/makehumancommunity/mpfb2), CC0.
