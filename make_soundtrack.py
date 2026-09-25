"""
Soundtracks from REAL field recordings (no synthesis) for the two shots.

  --shot 5am  (default)  5 AM Pre-dawn - 15 s shot, stems are 20 s (the extra is handles)
      01_open_sea_waves.wav   swell rolling in under the fog, wide and a little distant
      02_sea_breeze.wav       the light onshore breeze that moves the fog

  --shot 3am             3 AM Harbor - 10 s shot, stems are 20 s
      01_shore_waves.wav       small night waves washing the stones at the shore end of the pier
      02_pier_wood_creaks.wav  moored boat + rope/timber creaks and water slapping under the boards
      03_night_crickets.wav    two crickets in the grass behind the camera (one near-left, one far-right)

All stems 48 kHz / 24-bit stereo, plus a *_soundtrack_mix.wav.

Sources (see audio/sources/CREDITS.txt):
  Luftrum      "oceanwavescrushing.wav"               freesound #48412    CC BY 4.0  (credit required)
  felix.blume  wind                                   freesound #217506   CC0
  Falcet       "Ambience_Sea_Boat_Night_Ropes_4.wav"  freesound #439365   CC0
  Lisa Redfern "Crickets Chirping At Night"           soundbible #2083    Public Domain
  (as edited for the Blanket app, github.com/rafaelmardojai/blanket, data/resources/sounds)

Processing is deliberately light: clean-up only (hum/engine notches, high-pass), distance EQ,
placement in the stereo field and level. Needs numpy, scipy and ffmpeg on PATH.
Usage:  python3 make_soundtrack.py  [--shot 5am|3am]  [sources_dir]  [out_dir]
"""
import os, sys, json, subprocess
import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, sosfiltfilt, iirnotch, filtfilt

SR = 48000
DUR = 20.0
HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = [a for a in sys.argv[1:]]
SHOT = "5am"
if "--shot" in ARGS:
    i = ARGS.index("--shot")
    SHOT = ARGS[i + 1].lower()
    del ARGS[i:i + 2]
SRC = ARGS[0] if len(ARGS) > 0 else os.path.join(HERE, "audio", "sources")
OUT = ARGS[1] if len(ARGS) > 1 else os.path.join(HERE, "audio")

FILES = dict(waves="waves.ogg", boat="boat.ogg", crickets="summer-night.ogg", wind="wind.ogg")


def load(name):
    """Decode any format to float32 48 kHz stereo via ffmpeg."""
    p = os.path.join(SRC, FILES[name])
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", p, "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).astype(np.float64)


def seg(x, t0, dur=DUR):
    a = int(t0 * SR)
    return x[a:a + int(dur * SR)].copy()


def loop_to(x, dur=DUR):
    """Tile a seamless loop (all Blanket sounds are edited to loop) out to 'dur' seconds."""
    n = int(dur * SR)
    reps = int(np.ceil(n / len(x)))
    return np.concatenate([x] * reps, axis=0)[:n].copy()


def hp(x, fc, order=4):
    return sosfiltfilt(butter(order, fc, 'highpass', fs=SR, output='sos'), x, axis=0)


def lp(x, fc, order=2):
    return sosfiltfilt(butter(order, fc, 'lowpass', fs=SR, output='sos'), x, axis=0)


def notch(x, f0, q=18.0):
    b, a = iirnotch(f0, q, fs=SR)
    return filtfilt(b, a, x, axis=0)


def pan_mono(m, pan):
    """Constant-power pan, pan in [-1 (left), +1 (right)]."""
    th = (pan + 1.0) * np.pi / 4.0
    return np.stack([m * np.cos(th), m * np.sin(th)], axis=1)


def width(x, w):
    """Mid/side stereo width: 0 = mono, 1 = as recorded, >1 = wider."""
    mid = 0.5 * (x[:, 0] + x[:, 1])
    side = 0.5 * (x[:, 0] - x[:, 1]) * w
    return np.stack([mid + side, mid - side], axis=1)


def fades(x, fin=0.03, fout=1.0):
    n = len(x)
    a, b = int(fin * SR), int(fout * SR)
    g = np.ones(n)
    g[:a] = np.sin(np.linspace(0, np.pi / 2, a)) ** 2
    g[n - b:] = np.cos(np.linspace(0, np.pi / 2, b)) ** 2
    return x * g[:, None]


def lufs(x):
    """Integrated loudness (EBU R128) measured by ffmpeg."""
    tmp = os.path.join(OUT, "_lufs_tmp.wav")
    wavfile.write(tmp, SR, x.astype(np.float32))
    r = subprocess.run(["ffmpeg", "-v", "info", "-nostats", "-i", tmp, "-af", "loudnorm=print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True).stderr
    os.remove(tmp)
    j = json.loads(r[r.rindex("{"):r.rindex("}") + 1])
    return float(j["input_i"]), float(j["input_tp"])


def to_lufs(x, target):
    cur, _ = lufs(x)
    return x * 10 ** ((target - cur) / 20.0)


def write24(name, x):
    tmp = os.path.join(OUT, "_tmp.wav")
    wavfile.write(tmp, SR, x.astype(np.float32))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", tmp, "-c:a", "pcm_s24le", os.path.join(OUT, name)], check=True)
    os.remove(tmp)


def clean_waves(w):
    # The Luftrum recording has a distant ship's engine droning at 68/90/114/138/268 Hz
    # underneath - high-pass + narrow notches take it out.
    w = hp(w, 110.0)
    for f0 in (90.0, 114.0, 138.0, 268.0):
        w = notch(w, f0)
    return w


def stems_3am():
    # 01 - shore waves (stereo). Calm stretch 40-60 s.
    w = clean_waves(seg(load("waves"), 40.0))
    w = lp(w, 11000.0)
    w = to_lufs(fades(w), -27.0)

    # 02 - pier / boat creaks (mono source). 17-37 s puts creaks at ~1, 2, 5, 7 and 10-12 s into the shot.
    # 49-50 Hz generator hum + 73/102 Hz removed. Sits slightly right, where the fender piles
    # and the mooring dolphin are.
    b = seg(load("boat"), 17.0).mean(axis=1)
    b = hp(b, 95.0)
    for f0 in (50.0, 73.0, 102.0):
        b = notch(b, f0)
    b = pan_mono(b, 0.18)
    b = to_lufs(fades(b), -29.0)

    # 03 - night crickets (mono source). Rumble under 1 kHz removed, air absorption for distance.
    c = load("crickets").mean(axis=1)
    c = hp(c, 1500.0)
    near = lp(seg(c[:, None], 2.0)[:, 0], 9000.0)
    far = lp(seg(c[:, None], 24.5)[:, 0], 5500.0) * 10 ** (-8.0 / 20)
    cr = pan_mono(near, -0.45) + pan_mono(far, 0.6)
    cr = to_lufs(fades(cr, fin=0.03, fout=1.0), -35.0)
    return {"01_shore_waves.wav": w, "02_pier_wood_creaks.wav": b, "03_night_crickets.wav": cr}, "3AM_Harbor"


def stems_5am():
    # 01 - open sea. The steadiest stretch of the recording (84-104 s): swell after swell, no
    # single big crash. The camera is out over the water, so the surf is pushed back a little:
    # softer top end (air absorption) and a wider, more enveloping image.
    w = clean_waves(seg(load("waves"), 84.0))
    w = lp(w, 7500.0)
    w = width(w, 1.35)
    w = to_lufs(fades(w, fin=0.5, fout=1.5), -26.0)

    # 02 - sea breeze. The loop is 14.8 s, tiled seamlessly to 20 s. Low rumble (mic buffeting)
    # removed so it reads as air moving, not wind on a microphone; it sits under the waves.
    b = loop_to(load("wind"))
    b = hp(b, 160.0)
    b = lp(b, 6000.0)
    b = width(b, 1.2)
    b = to_lufs(fades(b, fin=0.5, fout=1.5), -34.0)
    return {"01_open_sea_waves.wav": w, "02_sea_breeze.wav": b}, "5AM_Predawn"


def main():
    os.makedirs(OUT, exist_ok=True)
    stems, title = stems_5am() if SHOT == "5am" else stems_3am()
    mix = sum(stems.values())
    peak = np.abs(mix).max()
    if peak > 0.708:                      # keep ~-3 dBFS sample-peak headroom for the AAC encoder
        g = 0.708 / peak
        stems = {k: v * g for k, v in stems.items()}
        mix = mix * g
    for k, v in stems.items():
        write24(k, v)
    write24("%s_soundtrack_mix.wav" % title, mix)
    report = {k: lufs(v) for k, v in stems.items()}
    report["mix"] = lufs(mix)
    for k, (i, tp) in report.items():
        print("%-28s %6.1f LUFS  %5.1f dBTP" % (k, i, tp))


if __name__ == "__main__":
    main()
