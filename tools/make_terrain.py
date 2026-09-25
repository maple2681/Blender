"""
5 AM PRE-DAWN - mountain heightmaps from a landscape-evolution simulation
=========================================================================

The mountains in build_5am_predawn.py are not raw noise: they are grown here with a
stream-power erosion model (Braun & Willett 2013 implicit scheme) plus threshold
hillslopes, so they get real drainage networks, V-shaped valleys, sharp ridges and
spurs that run down to the sea.

Three ranges are simulated, each on its own grid (finer where the camera is closer):

    near   a dark coastal headland on the left of frame, 3-8 km from the camera, 5 m cells
    mid    fjord range rising out of the bay, 7-18 km, 12 m cells
    far    snow-capped alpine massif, 16-44 km, 26 m cells

Output (next to this script, in ../textures):
    5AM_terrain_near.png / _mid.png / _far.png   16-bit greyscale heightmaps
    5AM_terrain.json                              extents + height range per map
    5AM_terrain_*_preview.png                     hillshade previews (not used by Blender)

This is an OFFLINE tool: it needs numpy, scipy, numba and pillow (a normal Python, not
Blender). The .blend and the builder only need the PNG + JSON it writes.

Usage:  python3 make_terrain.py [out_dir]
"""
import heapq
import json
import os
import sys
import time

import numba as nb
import numpy as np
from PIL import Image
from scipy import ndimage

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "textures")
SQRT2 = 2.0 ** 0.5

# ----------------------------------------------------------------------------
#  RANGES  (metres; camera near the origin looking along +Y)
# ----------------------------------------------------------------------------
RANGES = {
    "near": dict(x=(-4500.0, 1500.0), y=(2400.0, 8400.0), cell=5.0, seed=51, peak=520.0,
                 U=1.0e-3, K=1.1e-5, m=0.5, D=0.004, talus=52.0, steps=(500, 160, 150), crag=0.018),
    "mid": dict(x=(-12000.0, 12000.0), y=(7000.0, 18000.0), cell=12.0, seed=77, peak=1450.0,
                U=1.0e-3, K=0.8e-5, m=0.5, D=0.006, talus=55.0, steps=(500, 160, 130), crag=0.022),
    "far": dict(x=(-26000.0, 26000.0), y=(16000.0, 44000.0), cell=26.0, seed=93, peak=3300.0,
                U=1.0e-3, K=0.6e-5, m=0.42, D=0.010, talus=58.0, steps=(500, 160, 130), crag=0.028),
}


# ----------------------------------------------------------------------------
#  NOISE  (spectral synthesis, world-space consistent across grid levels)
# ----------------------------------------------------------------------------
def fractal(shape, beta, seed, lo_cut=0.0):
    """Gaussian random field with power spectrum 1/f^beta, normalised to [-1, 1]-ish."""
    rng = np.random.default_rng(seed)
    ny, nx = shape
    fy = np.fft.fftfreq(ny)[:, None]
    fx = np.fft.rfftfreq(nx)[None, :]
    f = np.sqrt(fx * fx + fy * fy)
    f[0, 0] = 1.0
    amp = f ** (-beta / 2.0)
    if lo_cut > 0.0:
        amp *= 1.0 - np.exp(-(f / lo_cut) ** 2)
    amp[0, 0] = 0.0
    spec = amp * (rng.normal(size=amp.shape) + 1j * rng.normal(size=amp.shape))
    a = np.fft.irfft2(spec, s=shape)
    return a / (3.0 * a.std())


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def noise1d(x, seed, scale, octaves=4):
    """Smooth 1-D fBm (sum of random-phase sines) for coastlines and ridge lines."""
    rng = np.random.default_rng(seed)
    v = np.zeros_like(x)
    a, f = 1.0, 1.0 / scale
    for _ in range(octaves):
        for _k in range(3):
            v += a / 3.0 * np.sin(2 * np.pi * f * rng.uniform(0.7, 1.3) * x + rng.uniform(0, 2 * np.pi))
        a *= 0.5
        f *= 2.1
    return v


# ----------------------------------------------------------------------------
#  EROSION KERNELS
# ----------------------------------------------------------------------------
@nb.njit(cache=True)
def fill_depressions(h, fixed, nx, ny, eps):
    """Priority-flood + epsilon (Barnes 2014): every cell drains to a fixed (base-level) cell."""
    n = nx * ny
    out = h.copy()
    closed = np.zeros(n, np.bool_)
    heap = [(0.0, 0)]
    heap.pop()
    for i in range(n):
        if fixed[i]:
            heapq.heappush(heap, (out[i], i))
            closed[i] = True
    while len(heap) > 0:
        z, i = heapq.heappop(heap)
        ix = i % nx
        iy = i // nx
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                if dx == 0 and dy == 0:
                    continue
                jx = ix + dx
                jy = iy + dy
                if jx < 0 or jy < 0 or jx >= nx or jy >= ny:
                    continue
                j = jy * nx + jx
                if closed[j]:
                    continue
                closed[j] = True
                if out[j] <= z + eps:
                    out[j] = z + eps
                heapq.heappush(heap, (out[j], j))
    return out


@nb.njit(cache=True)
def steepest_receivers(h, fixed, nx, ny, cell, seed, jitter):
    """Steepest descent with randomised slopes (in the spirit of Rho8): over many steps the
    D8 grid bias averages out and the drainage network grows organically, not along 45 deg lines."""
    np.random.seed(seed)
    n = nx * ny
    rec = np.arange(n)
    dist = np.full(n, cell)
    for i in range(n):
        if fixed[i]:
            continue
        ix = i % nx
        iy = i // nx
        best = 0.0
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                if dx == 0 and dy == 0:
                    continue
                jx = ix + dx
                jy = iy + dy
                if jx < 0 or jy < 0 or jx >= nx or jy >= ny:
                    continue
                j = jy * nx + jx
                d = cell * (1.4142135623730951 if dx != 0 and dy != 0 else 1.0)
                s = (h[i] - h[j]) / d * (1.0 + jitter * (np.random.random() - 0.5))
                if s > best:
                    best = s
                    rec[i] = j
                    dist[i] = d
    return rec, dist


@nb.njit(cache=True)
def accumulate(order, rec, a0):
    A = a0.copy()
    for k in range(order.size - 1, -1, -1):
        i = order[k]
        r = rec[i]
        if r != i:
            A[r] += A[i]
    return A


@nb.njit(cache=True)
def stream_power(h, order, rec, dist, A, K, U, dt, m, fixed, smax):
    """Implicit stream-power incision (n = 1) + uplift, then threshold hillslopes (talus angle)."""
    for k in range(order.size):
        i = order[k]
        if fixed[i]:
            continue
        r = rec[i]
        F = K[i] * dt * A[i] ** m / dist[i]
        v = (h[i] + dt * U[i] + F * h[r]) / (1.0 + F)
        lim = h[r] + smax * dist[i]
        h[i] = v if v < lim else lim
    return h


def diffuse(h, fixed, D, dt, cell):
    """Linear hillslope creep (explicit, sub-stepped for stability)."""
    if D <= 0.0:
        return h
    n = max(1, int(np.ceil(dt * D / (0.2 * cell * cell))))
    k = dt * D / n / (cell * cell)
    free = ~fixed
    for _ in range(n):
        lap = np.zeros_like(h)
        lap[1:-1, 1:-1] = (h[2:, 1:-1] + h[:-2, 1:-1] + h[1:-1, 2:] + h[1:-1, :-2] - 4.0 * h[1:-1, 1:-1])
        h = np.where(free, h + k * lap, h)
    return h


def evolve(h, fixed, U, K, cell, m, smax, steps, dt, D, seed):
    ny, nx = h.shape
    fx = fixed.ravel()
    Uf = U.ravel()
    Kf = K.ravel()
    a0 = np.full(h.size, cell * cell)
    for s in range(steps):
        hf = fill_depressions(h.ravel().copy(), fx, nx, ny, 1e-4)
        rec, dist = steepest_receivers(hf, fx, nx, ny, cell, seed + s, 0.9)
        order = np.argsort(hf, kind="stable")
        A = accumulate(order, rec, a0)
        hf = stream_power(hf, order, rec, dist, A, Kf, Uf, dt, m, fx, smax)
        h = diffuse(hf.reshape(ny, nx), fixed, D, dt, cell)
    return h, A.reshape(ny, nx)


# ----------------------------------------------------------------------------
#  UPLIFT / BASE-LEVEL DESIGN FOR EACH RANGE
# ----------------------------------------------------------------------------
def design(name, X, Y, cfg):
    """Returns (sea mask, uplift, erodibility) on world-coordinate arrays X, Y (metres)."""
    s = cfg["seed"]
    xs = X[0]
    if name == "near":
        # a dark headland on the left of frame; past it the coast swings away and a wide bay
        # opens, so the mid and far ranges rise straight out of the fog over open water
        shore = 3050.0 + 220.0 * noise1d(xs, s, 1500.0, 3) + 45.0 * noise1d(xs, s + 1, 380.0, 3)
        shore = shore + 6000.0 * smoothstep(-1900.0, 900.0, xs) ** 1.3
        shore = shore[None, :]
        sea = Y < shore
        dn = Y - shore
        ridge = 5000.0 + 500.0 * noise1d(xs, s + 2, 2200.0)[None, :]
        env = smoothstep(0.0, 700.0, dn) * np.exp(-((Y - ridge) / 1800.0) ** 2)
        env *= 1.0 - smoothstep(-2400.0, -150.0, X)
        env += 0.7 * smoothstep(0.0, 500.0, dn) * np.exp(-((X + 1500.0) / 700.0) ** 2) \
            * np.exp(-((Y - 3800.0) / 800.0) ** 2)             # the cape's summit, close to the water
        env *= 1.0 - smoothstep(7400.0, 8300.0, Y)
        env *= smoothstep(-4450.0, -3700.0, X)
    elif name == "mid":
        shore = 9300.0 + 700.0 * noise1d(xs, s, 5200.0, 3) + 90.0 * noise1d(xs, s + 1, 900.0, 3)
        shore = shore[None, :]
        sea = Y < shore
        dn = Y - shore
        ridge = 13200.0 + 1100.0 * noise1d(xs, s + 2, 6000.0)[None, :]
        env = smoothstep(0.0, 1400.0, dn) * np.exp(-((Y - ridge) / 3000.0) ** 2)
        env *= 0.3 + 0.7 * smoothstep(-3500.0, 2500.0, X)      # low on the left: the massif shows through
        env *= 1.0 - smoothstep(16500.0, 17900.0, Y)
        env *= smoothstep(-11900.0, -10500.0, X) * (1.0 - smoothstep(10500.0, 11900.0, X))
    else:
        shore = np.full_like(xs, 17000.0)[None, :]
        sea = Y < shore
        dn = Y - shore
        ridge = 29000.0 + 2500.0 * noise1d(xs, s + 2, 14000.0)[None, :]
        env = smoothstep(0.0, 5000.0, dn) * np.exp(-((Y - ridge) / 7000.0) ** 2)
        env *= 0.35 + 0.65 * np.exp(-((X + 1000.0) / 7500.0) ** 2)  # the massif crowns just left of centre
        env *= 1.0 - smoothstep(41000.0, 43800.0, Y)
        env *= smoothstep(-25800.0, -22000.0, X) * (1.0 - smoothstep(22000.0, 25800.0, X))
    rough = fractal(X.shape, 3.2, s + 10)
    tect = fractal(X.shape, 3.6, s + 11)
    U = cfg["U"] * np.clip(env, 0.0, None) * np.clip(0.6 + 0.9 * tect, 0.12, 2.0)
    K = cfg["K"] * np.clip(1.0 + 0.45 * rough, 0.45, 1.6)          # hard and soft rock bands
    return sea, U, K


def grid(cfg, level):
    """World coordinates of the grid at resolution level (0 = coarsest, 2 = full)."""
    f = 2 ** (2 - level)
    cell = cfg["cell"] * f
    (x0, x1), (y0, y1) = cfg["x"], cfg["y"]
    nx = int(round((x1 - x0) / cell)) + 1
    ny = int(round((y1 - y0) / cell)) + 1
    xs = np.linspace(x0, x1, nx)
    ys = np.linspace(y0, y1, ny)
    X, Y = np.meshgrid(xs, ys)
    return X, Y, (x1 - x0) / (nx - 1)


def simulate(name, cfg):
    t0 = time.time()
    rng = np.random.default_rng(cfg["seed"])
    h = None
    for level in range(int(os.environ.get("TERRAIN_LEVELS", "3"))):
        X, Y, cell = grid(cfg, level)
        sea, U, K = design(name, X, Y, cfg)
        fixed = sea.copy()
        fixed[0, :] = fixed[-1, :] = True
        fixed[:, 0] = fixed[:, -1] = True
        if h is None:
            h = 2.0 * rng.random(X.shape) + 40.0 * np.clip(U / cfg["U"], 0, 1)
        else:
            h = ndimage.zoom(h, (X.shape[0] / h.shape[0], X.shape[1] / h.shape[1]), order=3)
            h += 0.5 * rng.random(X.shape)
        h[fixed] = 0.0
        smax = np.tan(np.radians(cfg["talus"]))
        steps = cfg["steps"][level]
        dt = 2.0e4 if level == 0 else 1.0e4
        D = cfg["D"] * (0.6 if level == 2 else 1.0)
        h, A = evolve(h, fixed, U, K, cell, cfg["m"], smax, steps, dt, D, cfg["seed"] * 1000 + level * 7919)
        print("  %-4s level %d  %4dx%-4d  %3d steps  max %.0f m  (%.0fs)"
              % (name, level, X.shape[1], X.shape[0], steps, h.max(), time.time() - t0))
    return X, Y, cell, h, sea, A


def finish(name, cfg, X, Y, cell, h, sea, A):
    s = cfg["seed"]
    # the flow router works on the 8-neighbour grid; a sub-cell blur removes its straight-line
    # facets before the continuous (grid-free) detail below goes on
    h = ndimage.gaussian_filter(h, 0.8)
    # rescale relief to the design peak height, gently (slopes stay near the talus angle)
    h = h * (cfg["peak"] / max(1.0, np.percentile(h, 99.97)))
    # rock texture: fine fractal roughness, strongest on steep faces, none in the channels
    gy, gx = np.gradient(h, cell)
    slope = np.hypot(gx, gy)
    chan = np.clip(np.log10(A / (cell * cell)) / 3.0, 0.0, 1.0)
    det = fractal(h.shape, 2.1, s + 20, lo_cut=0.02)
    h = h + det * (1.5 + 5.5 * smoothstep(0.35, 1.0, slope)) * (1.0 - 0.8 * chan) * (cell / 6.0) ** 0.5
    # crags: ridged, band-limited relief (rock ribs, buttresses, broken arêtes) on the steep upper
    # slopes, scaled to the range's relief - this is the detail that still reads from kilometres away
    rn = fractal(h.shape, 2.5, s + 21, lo_cut=0.012)
    crag = np.clip(1.0 - 2.2 * np.abs(rn), 0.0, 1.0) ** 2
    crag -= crag.mean()
    hi = smoothstep(0.15 * cfg["peak"], 0.7 * cfg["peak"], h)
    h = h + crag * cfg["crag"] * cfg["peak"] * smoothstep(0.25, 0.8, slope) * (0.35 + 0.65 * hi) * (1.0 - 0.7 * chan)
    # sea floor: shelves away under the water so the ocean plane meets a real shoreline
    dist = ndimage.distance_transform_edt(sea) * cell
    h = np.where(sea, -2.0 - 60.0 * smoothstep(0.0, 900.0, dist), h)
    land = ~sea
    h[land] = np.maximum(h[land], 0.3)
    # grid edges sink below the water so no side wall is ever visible
    e = np.minimum.reduce([X - X.min(), X.max() - X, Y - Y.min(), Y.max() - Y])
    h = h - 80.0 * (1.0 - smoothstep(0.0, 12.0 * cell, e))
    return h


def save(name, cfg, X, cell, h, meta):
    os.makedirs(OUT, exist_ok=True)
    lo, hi = float(h.min()), float(h.max())
    q = np.round((h - lo) / (hi - lo) * 65535.0).astype(np.uint16)
    # row 0 of the image = the far edge (max Y), like a map seen from above with +Y up
    Image.fromarray(q[::-1].copy()).save(os.path.join(OUT, "5AM_terrain_%s.png" % name))
    meta[name] = dict(file="5AM_terrain_%s.png" % name, x=list(cfg["x"]), y=list(cfg["y"]),
                      h_min=lo, h_max=hi, width=int(h.shape[1]), height=int(h.shape[0]), cell=cell)
    # hillshade preview (NW light) for checking the look
    gy, gx = np.gradient(h, cell)
    n = np.dstack([-gx, -gy, np.ones_like(h)])
    n /= np.linalg.norm(n, axis=2, keepdims=True)
    L = np.array([-0.5, 0.5, 0.7])
    L /= np.linalg.norm(L)
    sh = np.clip((n * L).sum(axis=2), 0, 1)
    img = np.where(h > 0, 0.25 + 0.75 * sh, 0.12)
    img = (img[::-1] * 255).astype(np.uint8)
    im = Image.fromarray(img)
    im.thumbnail((1600, 1600))
    im.save(os.path.join(OUT, "5AM_terrain_%s_preview.png" % name))


def main():
    meta = {}
    for name, cfg in RANGES.items():
        X, Y, cell, h, sea, A = simulate(name, cfg)
        h = finish(name, cfg, X, Y, cell, h, sea, A)
        save(name, cfg, X, cell, h, meta)
        print("  %-4s done: %.0f .. %.0f m" % (name, h.min(), h.max()))
    with open(os.path.join(OUT, "5AM_terrain.json"), "w") as f:
        json.dump(meta, f, indent=2)


if __name__ == "__main__":
    only = [a for a in sys.argv[2:]]
    if only:
        RANGES = {k: v for k, v in RANGES.items() if k in only}
    main()
