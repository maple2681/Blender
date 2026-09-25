# ============================================================================
#  5 AM PRE-DAWN  -  cinematic seascape builder for Blender 5.x + Cycles
# ============================================================================
"""
Builds the complete 5 AM shot, fully procedural apart from three terrain heightmaps
(grown by tools/make_terrain.py and packed into the .blend):

  * civil-twilight sky: physically based multiple-scattering atmosphere with the sun
    6 deg BELOW the horizon, so there is no sun in frame and no direct light at all -
    everything is lit by the soft, diffuse glow of the sky (a thin waning crescent moon
    rides low in the east, as it does before sunrise; P["moon"] turns it off)
  * three layers of eroded mountains fading into the distance (coastal hills 3-8 km,
    fjord range 9-18 km, snow-capped massif 17-44 km) with forest, rock, scree and snow
  * FFT ocean (Tessendorf spectra via two Ocean modifiers: wind sea + long swell) with
    whitecap foam, capillary ripples and a distance fade so the horizon never aliases
  * thick sea fog rolling just above the water (drifting, evolving volume with a
    ragged top), a denser fog bank at the foot of the mountains and aerial haze
  * slow cinematic camera: a 15 s glide low over the water with gimbal-soft drift
  * Cycles settings, AgX colour management, compositor grade, 60 fps / 900 frames

Run: Blender > Scripting workspace > open this file > Run Script
     or from a terminal:  blender --python build_5am_predawn.py
Re-running is safe: everything it creates is prefixed "H5_" and rebuilt in a scene
called "5AM_Predawn" (other scenes - e.g. the 3AM_Harbor one - are left alone).
Written for Blender 5.x (tested on 5.0; the 3AM harbour file came from 5.2 LTS).
"""

import bpy
import bmesh
import json
import os
import time
from math import radians, sin, cos, atan2
from mathutils import Vector, Euler

# ----------------------------------------------------------------------------
#  PARAMETERS  (everything worth tweaking lives here)
# ----------------------------------------------------------------------------
P = dict(
    seed=5,
    # timing: 15 seconds at 60 fps
    fps=60, frame_start=1, frame_end=900,
    # output: DCI 2K scope (2.39:1). For 16:9 use 2048 x 1152 (or 2560 x 1440 for "2K/QHD").
    res_x=2048, res_y=858,
    # "FINAL" = full quality; "PREVIEW" = 50 % size, 48 samples (quick look on a slow machine)
    quality="FINAL",
    samples=128, adaptive_threshold=0.015, min_samples=32,    # raise samples to 256 if you see flicker
    volume_step_rate=2.0,                  # ray-march step (x the automatic size); 1 = finest, 4 = draft
    preview_samples=32,
    # output: "PNG" = frame sequence (safe to stop/resume) -> then render scene "5AM_Edit" for the MP4
    #         "VIDEO" = MP4 (H.264 + AAC) straight away,  "EXR" = multilayer EXR frames for grading
    output="PNG",
    audio_dir="",                          # "" = the 'audio' folder next to the .blend / this script
    terrain_dir="",                        # "" = the 'textures' folder next to the .blend / this script
    # colour management
    exposure=2.0,                          # stops, AgX view transform
    look="AgX - Medium High Contrast",
    # sky: civil twilight. Azimuths are compass degrees, 0 = +Y (straight ahead), 90 = +X (right)
    sun_elevation=-6.0,                    # below the horizon -> no sun, no direct light, no shadows
    sun_azimuth=62.0,                      # the brightening sky is ahead-right of the camera
    sky_strength=1.0,
    air_density=1.0, aerosol_density=1.6, ozone_density=1.0,
    clouds=0.55,                           # thin high veil (0 = clear)
    moon=True, moon_azimuth=21.0, moon_elevation=9.5,
    # ocean (metres, seconds)
    wind_speed=4.8, wind_dir=28.0,         # m/s (light breeze: fog weather), heading the wind blows TOWARD
    swell_dir=15.0,
    ocean_size=220.0, ocean_res=20, swell_size=900.0,
    swell_scale=0.7, windsea_scale=0.22,   # wave height multipliers (Hs ~0.6 m swell, ~0.25 m wind sea)
    choppiness=0.95, foam_coverage=0.18,
    wave_fade=(450.0, 1700.0),             # waves ease to a flat (shaded) sea between these ranges
    # atmosphere
    fog_density=0.20,                      # 1/m inside the layer: the thick rolling sea fog
    fog_top=2.2, fog_top_var=1.0,          # mean height of the fog's top and its slow swell (m)
    fog_billow=3.4,                        # height of the rolling billows on top of the layer (m)
    fog_speed=1.4,                         # m/s drift
    fog_fill=1.05,                         # in-scattered light (fog brightness vs the horizon sky behind it)
    fog_scatter=0.0,                       # > 0 adds path-traced single scattering on top (slow, noisy)
    bank_density=0.0035, bank_height=32.0, # fog bank at the foot of the mountains
    haze_density=0.00007, haze_height=800.0,
    haze_gain=0.92,                        # haze/fog-bank brightness relative to the horizon sky behind it
    # camera: 15 s glide over the water toward the mountains
    cam_start=(0.0, 0.0, 5.0), cam_travel=11.0, cam_rise=0.35,
    cam_yaw=(3.5, 5.0), cam_pitch=3.3,     # degrees (yaw + = right), pitch + = up
    lens=40.0, sensor=36.0, fstop=4.0, focus_dist=160.0,
    cam_drift=True,                        # very slow gimbal-soft float
    motion_blur=False,                     # sub-pixel at 60 fps for this slow move; costs render time
    # geometry detail
    ocean_rings_ratio=1.0,                 # < 1 thins the ocean mesh (faster, softer near water)
    viewport_terrain_decimate=4,           # viewport shows every Nth terrain vertex (render uses all)
    use_volumes=True,
)

LOG = []
SC = None
MATS = {}
OBJS = {}


def log(*a):
    s = " ".join(str(x) for x in a)
    LOG.append(s)
    print("[H5]", s)


def setp(obj, name, value):
    """Set an RNA property only if it exists (the API drifts between versions)."""
    if obj is not None and hasattr(obj, name):
        try:
            setattr(obj, name, value)
            return True
        except Exception as e:
            log("setp", name, e)
    return False


def heading_vec(deg):
    """Compass heading (0 = +Y, 90 = +X) -> unit XY vector."""
    a = radians(deg)
    return Vector((sin(a), cos(a), 0.0))


# ----------------------------------------------------------------------------
#  SCENE / COLLECTIONS / OBJECT HELPERS
# ----------------------------------------------------------------------------
def prepare_scene():
    """Remove everything a previous run created, then (re)use scene '5AM_Predawn'."""
    global SC
    for ob in list(bpy.data.objects):
        if ob.name.startswith("H5_"):
            bpy.data.objects.remove(ob, do_unlink=True)
    for c in list(bpy.data.collections):
        if c.name.startswith("H5_"):
            bpy.data.collections.remove(c)
    for _ in range(3):
        for coll in (bpy.data.meshes, bpy.data.curves, bpy.data.lights, bpy.data.cameras,
                     bpy.data.materials, bpy.data.node_groups, bpy.data.worlds, bpy.data.images):
            for d in list(coll):
                if d.name.startswith("H5_") and d.users == 0:
                    coll.remove(d)
    sc = bpy.data.scenes.get("5AM_Predawn")
    if sc is None:
        sc = bpy.data.scenes.new("5AM_Predawn")
    SC = sc
    try:
        if bpy.context.window is not None:
            bpy.context.window.scene = sc
    except Exception as e:
        log("window scene", e)
    MATS.clear()
    OBJS.clear()
    return sc


def coll(name):
    full = "H5_" + name
    c = bpy.data.collections.get(full)
    if c is None:
        c = bpy.data.collections.new(full)
        SC.collection.children.link(c)
    return c


def link_obj(name, data, c, parent=None):
    ob = bpy.data.objects.new("H5_" + name, data)
    c.objects.link(ob)
    if parent is not None:
        ob.parent = parent
    return ob


def new_empty(name, c, loc=(0, 0, 0), size=0.5, kind='PLAIN_AXES', parent=None):
    ob = bpy.data.objects.new("H5_" + name, None)
    c.objects.link(ob)
    ob.location = loc
    ob.empty_display_size = size
    ob.empty_display_type = kind
    if parent is not None:
        ob.parent = parent
    return ob


def bm_to_obj(name, bm, c, mats=None, smooth=False, parent=None):
    if smooth:
        for f in bm.faces:
            f.smooth = True
    me = bpy.data.meshes.new("H5_" + name)
    bm.to_mesh(me)
    bm.free()
    if mats:
        for m in (mats if isinstance(mats, (list, tuple)) else [mats]):
            me.materials.append(m)
    return link_obj(name, me, c, parent)


def add_driver(owner, path, expr, index=-1):
    try:
        fc = owner.driver_add(path, index) if index >= 0 else owner.driver_add(path)
        fc.driver.type = 'SCRIPTED'
        fc.driver.expression = expr
        if hasattr(fc.driver, "is_simple_expression") and not fc.driver.is_simple_expression:
            log("driver needs Python auto-run:", expr)
        return fc
    except Exception as e:
        log("driver", path, e)
        return None


def time_expr(scale=1.0, offset=0.0):
    """Driver expression for scene time in seconds (x scale), valid for any frame rate."""
    return "(frame-%d)/%.4f*%.6f+%.4f" % (P["frame_start"], float(P["fps"]), scale, offset)


# ----------------------------------------------------------------------------
#  NODE BUILDER
# ----------------------------------------------------------------------------
def _avail(s):
    if hasattr(s, "is_unavailable"):
        return not s.is_unavailable
    return getattr(s, "enabled", True)


PB = {  # Principled BSDF friendly names -> socket names (4.x / 5.x)
    'base': ["Base Color"], 'metal': ["Metallic"], 'rough': ["Roughness"], 'ior': ["IOR"],
    'alpha': ["Alpha"], 'normal': ["Normal"],
    'sss': ["Subsurface Weight", "Subsurface"], 'sss_radius': ["Subsurface Radius"],
    'sss_scale': ["Subsurface Scale"],
    'spec': ["Specular IOR Level", "Specular"], 'spec_tint': ["Specular Tint"],
    'trans': ["Transmission Weight", "Transmission"],
    'coat': ["Coat Weight", "Clearcoat"], 'coat_rough': ["Coat Roughness", "Clearcoat Roughness"],
    'coat_ior': ["Coat IOR"], 'coat_normal': ["Coat Normal", "Clearcoat Normal"],
    'sheen': ["Sheen Weight", "Sheen"], 'sheen_rough': ["Sheen Roughness"], 'sheen_tint': ["Sheen Tint"],
    'emit': ["Emission Color", "Emission"], 'emit_str': ["Emission Strength"],
    'aniso': ["Anisotropic"], 'aniso_rot': ["Anisotropic Rotation"],
}


class NT:
    """Tiny helper to write node graphs as readable Python (shader, geometry and compositor trees)."""

    def __init__(self, tree):
        self.t = tree
        self.nodes = tree.nodes
        self.links = tree.links

    def new(self, typ, x=0, y=0, label=None, **props):
        n = self.nodes.new(typ)
        n.location = (x, y)
        if label:
            n.label = label
        for k, v in props.items():
            try:
                setattr(n, k, v)
            except Exception as e:
                log("node prop", typ, k, e)
        return n

    def link(self, a, b):
        if isinstance(a, bpy.types.Node):
            a = self.o(a)
        try:
            self.links.new(a, b)
        except Exception as e:
            log("link", e)

    def i(self, n, name, typ=None):
        cands = [s for s in n.inputs if (s.name == name or s.identifier == name)
                 and (typ is None or s.type == typ)]
        av = [s for s in cands if _avail(s)]
        if av:
            return av[0]
        if cands:
            return cands[0]
        raise KeyError("%s has no input '%s'" % (n.bl_idname, name))

    def o(self, n, name=None, typ=None):
        if name is None:
            for s in n.outputs:
                if _avail(s):
                    return s
            return n.outputs[0]
        cands = [s for s in n.outputs if (s.name == name or s.identifier == name)
                 and (typ is None or s.type == typ)]
        av = [s for s in cands if _avail(s)]
        if av:
            return av[0]
        if cands:
            return cands[0]
        raise KeyError("%s has no output '%s'" % (n.bl_idname, name))

    def fac(self, n):
        for nm in ("Factor", "Fac"):
            s = n.outputs.get(nm)
            if s is not None:
                return s
        return n.outputs[0]

    def feed(self, sock, v):
        if v is None:
            return
        if isinstance(v, bpy.types.NodeSocket):
            self.link(v, sock)
            return
        if isinstance(v, bpy.types.Node):
            self.link(self.o(v), sock)
            return
        t = sock.type
        try:
            if t == 'RGBA':
                if isinstance(v, (int, float)):
                    v = (v, v, v, 1.0)
                elif len(v) == 3:
                    v = (v[0], v[1], v[2], 1.0)
            elif t == 'VECTOR':
                if isinstance(v, (int, float)):
                    v = (v, v, v)
                n = len(sock.default_value)
                v = tuple(v[:n]) + tuple([0.0] * max(0, n - len(v)))
            elif t in ('VALUE', 'INT'):
                if not isinstance(v, (int, float, bool)):
                    v = v[0]
            sock.default_value = v
        except Exception as e:
            log("feed", sock.name, t, e)

    def set(self, n, d):
        for k, v in d.items():
            try:
                self.feed(self.i(n, k), v)
            except KeyError as e:
                log(str(e))
        return n

    # --- math -----------------------------------------------------------
    def math(self, op, a, b=None, c=None, clamp=False, x=0, y=0):
        n = self.new('ShaderNodeMath', x, y, operation=op, use_clamp=clamp)
        self.feed(n.inputs[0], a)
        if b is not None:
            self.feed(n.inputs[1], b)
        if c is not None:
            self.feed(n.inputs[2], c)
        return n.outputs[0]

    def vmath(self, op, a, b=None, c=None, s=None, x=0, y=0):
        n = self.new('ShaderNodeVectorMath', x, y, operation=op)
        self.feed(n.inputs[0], a)
        if b is not None:
            self.feed(n.inputs[1], b)
        if c is not None:
            self.feed(n.inputs[2], c)
        if s is not None:
            self.feed(self.i(n, "Scale"), s)
        return self.o(n, "Value") if op in ('DOT_PRODUCT', 'LENGTH', 'DISTANCE') else self.o(n, "Vector")

    def mix(self, fac, a, b, dtype='RGBA', blend='MIX', clamp=False, x=0, y=0):
        n = self.new('ShaderNodeMix', x, y, data_type=dtype, blend_type=blend, clamp_result=clamp)
        st = {'FLOAT': 'VALUE', 'VECTOR': 'VECTOR', 'RGBA': 'RGBA'}[dtype]
        self.feed(self.i(n, "Factor", 'VALUE'), fac)
        self.feed(self.i(n, "A", st), a)
        self.feed(self.i(n, "B", st), b)
        return self.o(n, "Result", st)

    def ramp(self, fac, stops, interp='LINEAR', x=0, y=0):
        n = self.new('ShaderNodeValToRGB', x, y)
        cr = n.color_ramp
        cr.interpolation = interp
        els = cr.elements
        while len(els) > 1:
            els.remove(els[-1])

        def col(cc):
            if isinstance(cc, (int, float)):
                return (cc, cc, cc, 1.0)
            return (cc[0], cc[1], cc[2], 1.0) if len(cc) == 3 else tuple(cc)
        els[0].position = stops[0][0]
        els[0].color = col(stops[0][1])
        for p, cc in stops[1:]:
            e = els.new(p)
            e.color = col(cc)
        self.feed(n.inputs[0], fac)
        return n

    def mrange(self, v, a, b, c=0.0, d=1.0, interp='LINEAR', clamp=True, x=0, y=0):
        n = self.new('ShaderNodeMapRange', x, y, data_type='FLOAT', interpolation_type=interp, clamp=clamp)
        self.feed(self.i(n, "Value", 'VALUE'), v)
        self.feed(self.i(n, "From Min", 'VALUE'), a)
        self.feed(self.i(n, "From Max", 'VALUE'), b)
        self.feed(self.i(n, "To Min", 'VALUE'), c)
        self.feed(self.i(n, "To Max", 'VALUE'), d)
        return self.o(n, "Result", 'VALUE')

    def combine(self, a, b, c, x=0, y=0):
        n = self.new('ShaderNodeCombineXYZ', x, y)
        self.feed(n.inputs[0], a)
        self.feed(n.inputs[1], b)
        self.feed(n.inputs[2], c)
        return n.outputs[0]

    def separate(self, v, x=0, y=0):
        n = self.new('ShaderNodeSeparateXYZ', x, y)
        self.feed(n.inputs[0], v)
        return n.outputs

    def value(self, v, x=0, y=0, label=None):
        n = self.new('ShaderNodeValue', x, y, label=label)
        n.outputs[0].default_value = v
        return n

    def mapping(self, vec, loc=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1), x=0, y=0):
        n = self.new('ShaderNodeMapping', x, y, vector_type='POINT')
        self.feed(self.i(n, "Vector"), vec)
        self.set(n, {"Location": loc, "Rotation": rot, "Scale": scale})
        return self.o(n, "Vector")

    # --- textures -------------------------------------------------------
    def noise(self, vec, scale=5.0, detail=2.0, rough=0.5, lac=2.0, dist=0.0, w=None, dims='3D',
              ntype=None, x=0, y=0):
        n = self.new('ShaderNodeTexNoise', x, y, noise_dimensions=dims)
        if ntype:
            setp(n, "noise_type", ntype)
        if vec is not None:
            self.feed(self.i(n, "Vector"), vec)
        self.set(n, {"Scale": scale, "Detail": detail, "Roughness": rough, "Distortion": dist})
        if n.inputs.get("Lacunarity") is not None:
            self.feed(n.inputs["Lacunarity"], lac)
        if w is not None:
            self.feed(self.i(n, "W"), w)
        return n

    def voronoi(self, vec, scale=5.0, feature='F1', rand=1.0, dims='3D', x=0, y=0):
        n = self.new('ShaderNodeTexVoronoi', x, y, voronoi_dimensions=dims, feature=feature)
        if vec is not None:
            self.feed(self.i(n, "Vector"), vec)
        self.set(n, {"Scale": scale, "Randomness": rand})
        return n

    def wave(self, vec, scale=5.0, dist=0.0, detail=2.0, dscale=1.0, droughness=0.5,
             wtype='BANDS', direction='X', profile='SIN', x=0, y=0):
        n = self.new('ShaderNodeTexWave', x, y, wave_type=wtype, wave_profile=profile)
        if wtype == 'BANDS':
            n.bands_direction = direction
        else:
            n.rings_direction = direction
        if vec is not None:
            self.feed(self.i(n, "Vector"), vec)
        self.set(n, {"Scale": scale, "Distortion": dist, "Detail": detail,
                     "Detail Scale": dscale, "Detail Roughness": droughness})
        return n

    def bump(self, height, strength=0.5, distance=0.01, normal=None, x=0, y=0):
        n = self.new('ShaderNodeBump', x, y)
        self.set(n, {"Strength": strength, "Distance": distance})
        self.feed(self.i(n, "Height"), height)
        if normal is not None:
            self.feed(self.i(n, "Normal"), normal)
        return self.o(n, "Normal")

    def principled(self, x=0, y=0, **kw):
        n = self.new('ShaderNodeBsdfPrincipled', x, y)
        for k, v in kw.items():
            for nm in PB.get(k, [k]):
                s = n.inputs.get(nm)
                if s is not None:
                    self.feed(s, v)
                    break
            else:
                log("principled: no input", k)
        return n


def new_mat(name):
    m = bpy.data.materials.new("H5_" + name)
    if bpy.app.version < (5, 0, 0):           # always on (and deprecated) in 5.x
        setp(m, "use_nodes", True)
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    g = NT(nt)
    out = g.new('ShaderNodeOutputMaterial', 1800, 0)
    return m, g, out


def finish(m, g, out, bsdf=None, disp=None, volume=None, method=None):
    if bsdf is not None:
        g.link(bsdf, out.inputs["Surface"])
    if disp is not None:
        g.link(disp, out.inputs["Displacement"])
    if volume is not None:
        g.link(volume, out.inputs["Volume"])
    if method:
        if not setp(m, "displacement_method", method):
            setp(getattr(m, "cycles", None), "displacement_method", method)
    return m


def new_tree(name, kind, inputs, outputs):
    """Node group with an interface. kind: 'ShaderNodeTree' | 'GeometryNodeTree'."""
    ng = bpy.data.node_groups.new("H5_" + name, kind)
    for nm, st, dv in inputs:
        s = ng.interface.new_socket(nm, in_out='INPUT', socket_type=st)
        if dv is not None:
            try:
                s.default_value = dv
            except Exception:
                pass
    for nm, st in outputs:
        ng.interface.new_socket(nm, in_out='OUTPUT', socket_type=st)
    g = NT(ng)
    gi = g.new('NodeGroupInput', -1400, 0)
    go = g.new('NodeGroupOutput', 1400, 0)
    return ng, g, gi, go


def set_volume_opts(m):
    for obj in (m, getattr(m, "cycles", None)):
        setp(obj, "volume_sampling", 'MULTIPLE_IMPORTANCE')
        setp(obj, "volume_interpolation", 'LINEAR')
        setp(obj, "homogeneous_volume", False)


def time_value(g, x, y, scale=1.0, label="Time (s)"):
    tv = g.value(0.0, x, y, label)
    add_driver(tv.outputs[0], "default_value", time_expr(scale))
    return tv.outputs[0]


# ============================================================================
#  WORLD: CIVIL-TWILIGHT SKY
# ============================================================================
def sky_node(g, x, y):
    sky = g.new('ShaderNodeTexSky', x, y)
    for t in ('MULTIPLE_SCATTERING', 'NISHITA', 'SINGLE_SCATTERING'):
        if setp(sky, "sky_type", t):
            break
    setp(sky, "sun_disc", False)
    setp(sky, "sun_elevation", radians(P["sun_elevation"]))
    # sky texture sun_rotation is a compass heading too: 0 = +Y, 90 deg = +X
    setp(sky, "sun_rotation", radians(P["sun_azimuth"]))
    setp(sky, "altitude", 0.0)
    setp(sky, "air_density", P["air_density"])
    setp(sky, "aerosol_density", P["aerosol_density"])
    setp(sky, "dust_density", P["aerosol_density"])
    setp(sky, "ozone_density", P["ozone_density"])
    return sky


def setup_world():
    w = bpy.data.worlds.get("H5_World") or bpy.data.worlds.new("H5_World")
    SC.world = w
    if bpy.app.version < (5, 0, 0):
        setp(w, "use_nodes", True)
    nt = w.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    g = NT(nt)
    out = g.new('ShaderNodeOutputWorld', 1600, 0)
    sky = sky_node(g, -600, 300)
    col = g.o(sky, "Color")
    tc = g.new('ShaderNodeTexCoord', -1800, 0)
    d = tc.outputs["Generated"]                      # world direction (unit vector)
    dx, dy, dz = g.separate(d, x=-1600, y=0)
    # --- thin high veil of cloud (projected onto a plane 8 km up); lighter than the sky toward
    #     the twilight glow, a touch darker away from it - exactly what a cirrostratus veil does
    zc = g.math('MAXIMUM', dz, 0.035, x=-1400, y=-250)
    proj = g.vmath('DIVIDE', d, g.combine(zc, zc, zc, x=-1250, y=-250), x=-1100, y=-250)
    drift = time_value(g, -1400, -450, 1.0, "Cloud time")
    pcl = g.vmath('ADD', g.vmath('SCALE', proj, s=0.9, x=-950, y=-250),
                  g.combine(g.math('MULTIPLY', drift, 0.0016, x=-1100, y=-450), 0.0, 0.0, x=-950, y=-450),
                  x=-800, y=-250)
    streak = g.mapping(pcl, scale=(0.55, 1.4, 1.0), rot=(0.0, 0.0, radians(18.0)), x=-650, y=-250)
    cn = g.noise(streak, scale=1.1, detail=9.0, rough=0.62, dist=0.35, x=-450, y=-250)
    cloud = g.mrange(g.fac(cn), 0.46, 0.78, 0.0, 1.0, interp='SMOOTHSTEP', x=-250, y=-250)
    horizon = g.mrange(dz, 0.0, 0.12, 0.25, 1.0, x=-250, y=-450)     # fades into the haze low down
    cloud = g.math('MULTIPLY', g.math('MULTIPLY', cloud, horizon, x=-50, y=-300), P["clouds"], x=100, y=-300)
    sv = heading_vec(P["sun_azimuth"])
    facing = g.math('MULTIPLY_ADD', g.vmath('DOT_PRODUCT', g.combine(dx, dy, 0.0, x=-1400, y=-650),
                                            (sv.x, sv.y, 0.0), x=-1250, y=-650), 0.5, 0.5, x=-1100, y=-650)
    facing = g.math('POWER', facing, 3.0, x=-950, y=-650)
    gain = g.math('MULTIPLY_ADD', facing, 0.75, 0.78, x=-800, y=-650)            # 0.78 .. 1.53
    cmul = g.mix(cloud, 1.0, g.combine(gain, gain, g.math('MULTIPLY', gain, 0.97, x=-650, y=-750), x=-500, y=-700),
                 x=250, y=-300)
    col = g.mix(1.0, col, cmul, blend='MULTIPLY', x=450, y=200)
    # --- waning crescent moon: lit limb faces the sun (below the horizon), faint earthshine
    if P["moon"]:
        ma, me = radians(P["moon_azimuth"]), radians(P["moon_elevation"])
        mv = Vector((sin(ma) * cos(me), cos(ma) * cos(me), sin(me)))
        sa, se = radians(P["sun_azimuth"]), radians(P["sun_elevation"])
        svv = Vector((sin(sa) * cos(se), cos(sa) * cos(se), sin(se)))
        toward_sun = (svv - mv * svv.dot(mv)).normalized()
        R = radians(0.26)                                    # true angular radius
        shadow = (mv * cos(0.32 * R) - toward_sun * sin(0.32 * R)).normalized()
        c_moon = g.vmath('DOT_PRODUCT', d, tuple(mv), x=-1400, y=900)
        c_shad = g.vmath('DOT_PRODUCT', d, tuple(shadow), x=-1400, y=750)
        disc = g.mrange(c_moon, cos(R * 1.04), cos(R * 0.96), x=-1200, y=900)
        dark = g.mrange(c_shad, cos(R * 1.00), cos(R * 0.93), x=-1200, y=750)
        lit = g.math('MULTIPLY', disc, g.math('SUBTRACT', 1.0, dark, x=-1050, y=750), x=-900, y=850)
        glow = g.math('POWER', g.mrange(c_moon, cos(radians(2.0)), 1.0, x=-1200, y=600), 8.0, x=-1050, y=600)
        mcol = g.new('ShaderNodeBlackbody', -900, 1050)
        mcol.inputs["Temperature"].default_value = 4300.0            # low moon through haze: warm white
        em = g.math('MULTIPLY_ADD', lit, 6.0, g.math('MULTIPLY', disc, 0.012, x=-900, y=650), x=-750, y=800)
        em = g.math('MULTIPLY_ADD', glow, 0.006, em, x=-600, y=800)
        # clouds pass in front of the moon
        em = g.math('MULTIPLY', em, g.math('SUBTRACT', 1.0, g.math('MULTIPLY', cloud, 0.9, x=-750, y=650),
                                            x=-600, y=650), x=-450, y=800)
        mo = g.mix(1.0, (0, 0, 0), g.o(mcol), blend='ADD', x=-600, y=1050)
        mo = g.mix(1.0, mo, g.combine(em, em, em, x=-450, y=950), blend='MULTIPLY', x=-300, y=1000)
        col = g.mix(1.0, col, mo, blend='ADD', x=650, y=300)
    bg = g.new('ShaderNodeBackground', 1100, 0)
    g.feed(bg.inputs["Color"], col)
    bg.inputs["Strength"].default_value = P["sky_strength"]
    g.link(bg.outputs[0], out.inputs["Surface"])
    setp(w.cycles, "sampling_method", 'MANUAL')
    setp(w.cycles, "sample_map_resolution", 2048)
    ms = w.mist_settings
    ms.start = 20.0
    ms.depth = 3000.0
    ms.falloff = 'QUADRATIC'
    return w


# ============================================================================
#  MATERIALS
# ============================================================================
def mat_ocean():
    """Sea water: Fresnel sky reflection, deep teal body, whitecap foam, capillary ripples."""
    m, g, out = new_mat("M_Ocean")
    geo = g.new('ShaderNodeNewGeometry', -2400, 0)
    pos = geo.outputs["Position"]
    cam = g.new('ShaderNodeCameraData', -2400, -300)
    dist = g.o(cam, "View Distance")
    t = time_value(g, -2400, -500)
    wv = heading_vec(P["wind_dir"])
    wx, wy = wv.x, wv.y
    # wind-aligned coordinates: u along the wind, v across it
    pu = g.vmath('DOT_PRODUCT', pos, (wx, wy, 0.0), x=-2200, y=0)
    pv = g.vmath('DOT_PRODUCT', pos, (-wy, wx, 0.0), x=-2200, y=-150)
    near = g.mrange(dist, 25.0, 420.0, 1.0, 0.0, interp='SMOOTHSTEP', x=-2000, y=-300)
    far = g.mrange(dist, 250.0, 2500.0, 0.0, 1.0, interp='SMOOTHSTEP', x=-2000, y=-450)
    # capillary / small gravity ripples: two drifting layers, stretched across the wind
    rp1 = g.combine(g.math('MULTIPLY_ADD', t, -0.55, pu, x=-2000, y=100), g.math('MULTIPLY', pv, 0.55, x=-2000, y=0),
                    0.0, x=-1800, y=50)
    r1 = g.noise(rp1, scale=1.3, detail=5.0, rough=0.55, dims='4D', w=g.math('MULTIPLY', t, 0.35, x=-1800, y=-80),
                 x=-1600, y=50)
    rp2 = g.combine(g.math('MULTIPLY_ADD', t, -0.3, pu, x=-2000, y=-600), pv, 0.0, x=-1800, y=-600)
    r2 = g.noise(rp2, scale=5.5, detail=3.0, rough=0.5, dims='4D', w=g.math('MULTIPLY', t, 0.9, x=-1800, y=-700),
                 x=-1600, y=-600)
    rip = g.math('MULTIPLY_ADD', g.fac(r2), 0.4, g.fac(r1), x=-1400, y=-100)
    # breeze patches ("cat's paws") and glassy slicks: the ripples come and go across the surface
    cp = g.noise(g.combine(g.math('MULTIPLY_ADD', t, -1.2, pu, x=-2000, y=-1500), pv, 0.0, x=-1800, y=-1500),
                 scale=1.0 / 38.0, detail=3.0, rough=0.55, dims='4D', w=g.math('MULTIPLY', t, 0.02, x=-1800, y=-1600),
                 x=-1600, y=-1500)
    paws = g.mrange(g.fac(cp), 0.38, 0.62, 0.3, 1.0, interp='SMOOTHSTEP', x=-1400, y=-1500)
    rs = g.math('MULTIPLY', g.math('MULTIPLY', near, paws, x=-1400, y=-250), 0.9, x=-1250, y=-250)
    nrm = g.bump(rip, strength=rs, distance=0.07, x=-1200, y=-150)
    # distant sea: long, low swells read as horizontal streaks in the reflection
    fsx, fsy, fsz = g.separate(pos, x=-2000, y=-1050)
    fpv = g.combine(g.math('MULTIPLY', fsx, 0.018, x=-1800, y=-1000), g.math('MULTIPLY', fsy, 0.07, x=-1800, y=-1100),
                    g.math('MULTIPLY', t, 0.05, x=-1800, y=-1200), x=-1600, y=-1050)
    fn = g.noise(fpv, scale=1.0, detail=6.0, rough=0.6, x=-1400, y=-1050)
    nrm = g.bump(g.fac(fn), strength=g.math('MULTIPLY', far, 0.12, x=-1400, y=-1250), distance=0.8, normal=nrm,
                 x=-1000, y=-700)
    # whitecaps: Jacobian foam from the Ocean modifier, broken into bubbly patches and streaks
    fa = g.new('ShaderNodeAttribute', -2400, 700)
    fa.attribute_name = "foam"
    setp(fa, "attribute_type", 'GEOMETRY')
    foam_raw = g.fac(fa)
    fb = g.voronoi(g.combine(g.math('MULTIPLY', pu, 0.6, x=-2200, y=900), pv, g.math('MULTIPLY', t, 0.2, x=-2200, y=800),
                             x=-2000, y=900), scale=6.0, feature='F1', x=-1800, y=900)
    bub = g.mrange(g.o(fb, "Distance"), 0.1, 0.55, 1.0, 0.25, x=-1600, y=900)
    fsn = g.noise(g.combine(g.math('MULTIPLY', pu, 0.18, x=-2200, y=600), pv, 0.0, x=-2000, y=600),
                  scale=0.35, detail=6.0, rough=0.7, x=-1800, y=600)
    streaks = g.mrange(g.fac(fsn), 0.45, 0.75, x=-1600, y=600)
    foam = g.mrange(foam_raw, 0.25, 0.85, 0.0, 1.0, interp='SMOOTHSTEP', x=-1600, y=750)
    foam = g.math('MULTIPLY', foam, g.math('MULTIPLY_ADD', streaks, 0.6, 0.55, x=-1400, y=650), x=-1250, y=750)
    foam = g.math('MULTIPLY', foam, bub, x=-1100, y=800)
    foam = g.math('MULTIPLY', foam, g.mrange(dist, 900.0, 2200.0, 1.0, 0.0, x=-1100, y=650), clamp=True,
                  x=-950, y=750)
    # water body: deep, cold, blue-green; distant water is optically rougher (unresolved waves)
    rough = g.math('MULTIPLY_ADD', far, 0.16, 0.035, x=-800, y=-300)
    rough = g.mix(foam, rough, 0.75, dtype='FLOAT', x=-600, y=-300)
    body = (0.0045, 0.0165, 0.0185)
    base = g.mix(foam, body, (0.62, 0.66, 0.68), x=-600, y=300)
    bsdf = g.principled(-200, 0, base=base, rough=rough, ior=1.333, spec=0.5, normal=nrm,
                        sss=g.math('MULTIPLY', foam, 0.3, x=-600, y=0), sss_radius=(0.4, 0.6, 0.7), sss_scale=0.02)
    return finish(m, g, out, bsdf.outputs[0])


def mat_terrain():
    """One material for all three ranges; per-object 'treeline' / 'snowline' / 'relief' properties."""
    m, g, out = new_mat("M_Mountains")
    geo = g.new('ShaderNodeNewGeometry', -2600, 0)
    pos = geo.outputs["Position"]
    x, y, z = g.separate(pos, x=-2400, y=0)
    nz = g.separate(geo.outputs["Normal"], x=-2400, y=-200)[2]

    def objprop(name, yy):
        a = g.new('ShaderNodeAttribute', -2600, yy)
        a.attribute_name = name
        setp(a, "attribute_type", 'OBJECT')
        return g.fac(a)
    treeline = objprop("treeline", -500)
    snowline = objprop("snowline", -650)
    scale = objprop("detail_scale", -800)                # metres per noise unit (bigger for far ranges)
    co = g.vmath('DIVIDE', pos, g.combine(scale, scale, scale, x=-2400, y=-800), x=-2200, y=-700)
    # large-scale variation (rock type, lichen, weathering)
    big = g.noise(g.vmath('SCALE', co, s=0.02, x=-2000, y=-700), scale=1.0, detail=6.0, rough=0.55, x=-1800, y=-700)
    mid = g.noise(g.vmath('SCALE', co, s=0.15, x=-2000, y=-900), scale=1.0, detail=8.0, rough=0.62, x=-1800, y=-900)
    fine = g.noise(co, scale=1.4, detail=6.0, rough=0.6, x=-1800, y=-1100)
    # strata: bands following height, warped
    strata = g.wave(g.combine(x, y, g.math('MULTIPLY_ADD', g.fac(big), 60.0, z, x=-2000, y=-1300), x=-1800, y=-1300),
                    scale=0.005, dist=6.0, detail=3.0, dscale=1.5, direction='Z', x=-1600, y=-1300)
    # rock colour: cool grey granite with warmer brown bands
    rv = g.math('MULTIPLY_ADD', g.fac(strata), 0.18, g.fac(mid), x=-1400, y=-1000)
    rock = g.ramp(rv, [(0.15, (0.050, 0.050, 0.052)), (0.45, (0.100, 0.097, 0.092)),
                       (0.7, (0.135, 0.125, 0.110)), (0.95, (0.170, 0.162, 0.150))], x=-1200, y=-1000)
    rockc = g.mix(g.mrange(g.fac(big), 0.4, 0.7, 0.0, 0.5, x=-1200, y=-1250), rock.outputs[0], (0.07, 0.062, 0.050),
                  blend='MIX', x=-1000, y=-1000)
    # slope classes
    flatish = g.mrange(nz, 0.62, 0.85, x=-2000, y=-350)
    # forest below the treeline (ragged edge), on anything but cliffs
    tl = g.math('MULTIPLY_ADD', g.math('SUBTRACT', g.fac(mid), 0.5, x=-1800, y=-450), 180.0, treeline, x=-1600, y=-450)
    forest = g.math('MULTIPLY', g.mrange(z, g.math('ADD', tl, 40.0, x=-1400, y=-500), tl, x=-1200, y=-450),
                    g.mrange(nz, 0.66, 0.8, x=-1200, y=-600), x=-1000, y=-500)
    forest = g.math('MULTIPLY', forest, g.mrange(g.fac(fine), 0.25, 0.4, x=-1200, y=-750), x=-850, y=-550)
    fcol = g.ramp(g.fac(fine), [(0.3, (0.010, 0.018, 0.012)), (0.65, (0.022, 0.035, 0.020)),
                                (0.9, (0.040, 0.050, 0.030))], x=-1000, y=-750)
    # alpine meadow / heath between forest and bare rock
    meadow = g.math('MULTIPLY', flatish, g.mrange(z, g.math('ADD', tl, 350.0, x=-1400, y=-150), tl, x=-1200, y=-150),
                    x=-1000, y=-150)
    # snow: above a noisy snowline, holding on everything but the steepest faces
    sl = g.math('MULTIPLY_ADD', g.math('SUBTRACT', g.fac(big), 0.5, x=-1800, y=-1500), 500.0, snowline,
                x=-1600, y=-1500)
    snow = g.mrange(z, g.math('SUBTRACT', sl, 60.0, x=-1400, y=-1550), g.math('ADD', sl, 60.0, x=-1400, y=-1650),
                    x=-1200, y=-1550)
    hold = g.mrange(nz, g.math('MULTIPLY_ADD', g.fac(mid), 0.2, 0.33, x=-1400, y=-1800),
                    g.math('MULTIPLY_ADD', g.fac(mid), 0.2, 0.52, x=-1400, y=-1900), x=-1200, y=-1800)
    snow = g.math('MULTIPLY', snow, hold, clamp=True, x=-1000, y=-1600)
    # wind-scoured fields near the snowline, continuous cover high up
    sp = g.noise(g.vmath('SCALE', co, s=0.35, x=-1400, y=-2050), scale=1.0, detail=5.0, rough=0.6, x=-1200, y=-2050)
    fields = g.mrange(g.fac(sp), 0.38, 0.56, 0.0, 1.0, interp='SMOOTHSTEP', x=-1000, y=-2050)
    high = g.mrange(z, g.math('ADD', sl, 250.0, x=-1200, y=-2200), g.math('ADD', sl, 700.0, x=-1200, y=-2300),
                    x=-1000, y=-2200)
    snow = g.math('MULTIPLY', snow, g.math('MAXIMUM', fields, high, x=-850, y=-2100), clamp=True, x=-700, y=-1700)
    snow = g.math('MULTIPLY', snow, g.mrange(g.fac(fine), 0.3, 0.45, 0.35, 1.0, x=-1000, y=-1750), clamp=True,
                  x=-850, y=-1650)
    # scree fans below cliffs, wet dark rock at the tideline
    scree = g.math('MULTIPLY', g.mrange(nz, 0.6, 0.72, x=-1200, y=-1400), g.mrange(g.fac(mid), 0.55, 0.7, x=-1200, y=-1450),
                   x=-1000, y=-1400)
    wet = g.mrange(z, 6.0, 1.0, x=-1000, y=-1950)
    col = rockc
    col = g.mix(g.math('MULTIPLY', scree, 0.6, x=-850, y=-1400), col, (0.16, 0.155, 0.148), x=-700, y=-1100)
    col = g.mix(g.math('MULTIPLY', meadow, 0.7, x=-850, y=-200), col, (0.045, 0.048, 0.032), x=-550, y=-900)
    col = g.mix(forest, col, fcol.outputs[0], x=-400, y=-800)
    col = g.mix(g.math('MULTIPLY', wet, 0.8, x=-850, y=-1950), col, (0.02, 0.02, 0.02), x=-250, y=-900)
    col = g.mix(snow, col, (0.80, 0.82, 0.85), x=-100, y=-1000)
    rough = g.mix(snow, g.mix(forest, 0.82, 0.95, dtype='FLOAT', x=-400, y=-400), 0.62, dtype='FLOAT', x=-100, y=-400)
    # relief: rock detail, crumbly canopy, wind-packed snow
    h = g.math('MULTIPLY_ADD', g.fac(fine), 0.7, g.math('MULTIPLY', g.fac(mid), 0.3, x=-800, y=-2200), x=-600, y=-2200)
    h = g.mix(forest, h, g.math('MULTIPLY', g.fac(g.noise(co, scale=9.0, detail=3.0, x=-800, y=-2400)), 1.0,
                                 x=-600, y=-2400), dtype='FLOAT', x=-400, y=-2200)
    h = g.mix(snow, h, g.math('MULTIPLY', g.fac(fine), 0.3, x=-400, y=-2400), dtype='FLOAT', x=-250, y=-2250)
    nrm = g.bump(h, strength=0.7, distance=g.math('MULTIPLY', scale, 0.6, x=-400, y=-2550), x=-100, y=-2250)
    bsdf = g.principled(300, -600, base=col, rough=rough, normal=nrm,
                        sss=g.math('MULTIPLY', snow, 0.25, x=0, y=-1200), sss_radius=(0.6, 0.8, 1.0),
                        sss_scale=g.math('MULTIPLY', scale, 0.02, x=0, y=-1350))
    return finish(m, g, out, bsdf.outputs[0])


def horizon_light(g, geo, x, y, lift=0.05):
    """Radiance of the sky just above the horizon, in the azimuth this ray is looking along."""
    ix, iy, iz = g.separate(geo.outputs["Incoming"], x=x, y=y)
    hdir = g.vmath('NORMALIZE', g.combine(g.math('MULTIPLY', ix, -1.0, x=x + 200, y=y + 50),
                                          g.math('MULTIPLY', iy, -1.0, x=x + 200, y=y - 50), lift,
                                          x=x + 400, y=y), x=x + 600, y=y)
    sky = sky_node(g, x + 800, y + 100)
    g.link(hdir, g.i(sky, "Vector"))
    return g.o(sky, "Color")


def mat_sea_fog():
    """Rolling sea fog: a dense layer on the water whose ragged top heaves and drifts with the
    breeze, torn into wisps by evolving 4D noise."""
    m, g, out = new_mat("M_SeaFog")
    geo = g.new('ShaderNodeNewGeometry', -2400, 0)
    pos = geo.outputs["Position"]
    x, y, z = g.separate(pos, x=-2200, y=0)
    t = time_value(g, -2400, -300)
    wv = heading_vec(P["wind_dir"]) * P["fog_speed"]
    adv = g.vmath('SUBTRACT', pos, g.combine(g.math('MULTIPLY', t, wv.x, x=-2200, y=-300),
                                            g.math('MULTIPLY', t, wv.y, x=-2200, y=-400), 0.0, x=-2000, y=-350),
                  x=-1800, y=-200)
    # the top of the fog: slow swells of the whole layer (~170 m) + rolling billows (~35 m) that heave
    # up to several metres, so from just above the layer you see mounds of fog break the horizon
    tn = g.noise(g.vmath('SCALE', adv, s=1.0 / 170.0, x=-1600, y=250), scale=1.0, detail=3.0, rough=0.5, dims='4D',
                 w=g.math('MULTIPLY', t, 0.012, x=-1600, y=150), x=-1400, y=250)
    top = g.math('MULTIPLY_ADD', g.math('SUBTRACT', g.fac(tn), 0.5, x=-1200, y=250), 2.0 * P["fog_top_var"],
                 P["fog_top"], x=-1000, y=250)
    bt = g.noise(g.vmath('SCALE', adv, s=1.0 / 35.0, x=-1600, y=450), scale=1.0, detail=4.0, rough=0.55, dist=0.4,
                 dims='4D', w=g.math('MULTIPLY', t, 0.03, x=-1600, y=350), x=-1400, y=450)
    heave = g.mrange(g.fac(bt), 0.3, 0.72, -0.6, 1.0, interp='SMOOTHSTEP', x=-1200, y=450)
    top = g.math('MULTIPLY_ADD', heave, P["fog_billow"], top, x=-1000, y=400)
    layer = g.mrange(g.math('SUBTRACT', z, top, x=-800, y=150), 0.7, -0.9, 0.0, 1.0, interp='SMOOTHSTEP',
                     x=-600, y=150)
    low = g.math('EXPONENT', g.math('MULTIPLY', g.math('MAXIMUM', z, 0.0, x=-1000, y=0), -0.15, x=-850, y=0),
                 x=-700, y=0)
    layer = g.math('MULTIPLY', layer, g.math('MULTIPLY_ADD', low, 0.5, 0.5, x=-550, y=0), x=-400, y=100)
    # wisps: stretched along the wind, evolving so it rolls instead of sliding
    wd = heading_vec(P["wind_dir"])
    ang = atan2(wd.y, wd.x)
    wc = g.mapping(adv, rot=(0.0, 0.0, -ang), scale=(0.45, 1.0, 2.2), x=-1600, y=-300)
    wn = g.noise(g.vmath('SCALE', wc, s=1.0 / 22.0, x=-1400, y=-300), scale=1.0, detail=5.0, rough=0.58,
                 dist=0.6, dims='4D', w=g.math('MULTIPLY', t, 0.035, x=-1400, y=-450), x=-1200, y=-300)
    wisp = g.mrange(g.fac(wn), 0.36, 0.70, 0.03, 1.6, interp='SMOOTHSTEP', x=-1000, y=-300)
    # rolling billows (60-90 m) and whole banks with clear lanes between them (300 m+)
    bw = g.noise(g.vmath('SCALE', wc, s=1.0 / 75.0, x=-1400, y=-600), scale=1.0, detail=3.0, rough=0.5, dist=0.3,
                 dims='4D', w=g.math('MULTIPLY', t, 0.02, x=-1400, y=-700), x=-1200, y=-600)
    billow = g.mrange(g.fac(bw), 0.3, 0.68, 0.3, 1.3, interp='SMOOTHSTEP', x=-1000, y=-600)
    bk = g.noise(g.vmath('SCALE', adv, s=1.0 / 320.0, x=-1400, y=-850), scale=1.0, detail=2.0, rough=0.5,
                 x=-1200, y=-850)
    banks = g.mrange(g.fac(bk), 0.36, 0.6, 0.3, 1.2, interp='SMOOTHSTEP', x=-1000, y=-850)
    wisp = g.math('MULTIPLY', g.math('MULTIPLY', wisp, billow, x=-800, y=-450), banks, x=-650, y=-450)
    dens = g.value(P["fog_density"], -2400, -700, "Fog Density (1/m)")
    d = g.math('MULTIPLY', g.math('MULTIPLY', layer, wisp, x=-400, y=-150), dens.outputs[0], x=-200, y=-100)
    # keep the lens itself clear (the camera flies just above the layer)
    cs = Vector(P["cam_start"])
    ce = cs + heading_vec(P["cam_yaw"][1]) * P["cam_travel"]
    ry = g.math('ADD', g.math('MAXIMUM', g.math('SUBTRACT', y, max(cs.y, ce.y), x=-1000, y=-800), 0.0, x=-850, y=-800),
                g.math('MAXIMUM', g.math('SUBTRACT', min(cs.y, ce.y), y, x=-1000, y=-950), 0.0, x=-850, y=-950),
                x=-700, y=-850)
    rd = g.vmath('LENGTH', g.combine(g.math('SUBTRACT', x, 0.5 * (cs.x + ce.x), x=-700, y=-700), ry,
                                     g.math('SUBTRACT', z, cs.z, x=-700, y=-1000), x=-550, y=-850), x=-400, y=-850)
    clear = g.mrange(rd, 2.5, 14.0, 0.0, 1.0, interp='SMOOTHSTEP', x=-250, y=-850)
    d = g.math('MULTIPLY', d, clear, x=0, y=-300)
    # hand over to the fog bank far out (the bank carries on at the same height)
    d = g.math('MULTIPLY', d, g.mrange(y, 1250.0, 1650.0, 1.0, 0.0, x=-250, y=-500), x=150, y=-300)
    # Real fog is bright because light scatters in it dozens of times; the path tracer follows
    # two volume bounces, so the rest is added back as a soft fill (brighter at the fog's top,
    # where it faces the open sky, dimmer down on the dark water).
    hl = horizon_light(g, geo, -1000, 700)
    bw = g.new('ShaderNodeRGBToBW', -150, 800)
    g.link(hl, bw.inputs[0])
    fill = g.mix(0.45, hl, g.o(bw), x=-50, y=700)                 # droplets scatter white: less blue than the sky
    fill = g.mix(1.0, fill, (P["fog_fill"],) * 3, blend='MULTIPLY', x=100, y=600)
    fz = g.mrange(g.math('SUBTRACT', z, top, x=-400, y=650), -3.0, 0.6, 0.5, 1.0, x=-200, y=650)
    pv = g.new('ShaderNodeVolumePrincipled', 800, 0)
    sc_ = P["fog_scatter"]
    g.feed(pv.inputs["Color"], (0.95 * sc_, 0.965 * sc_, 0.98 * sc_))
    g.feed(pv.inputs["Density"], d)
    g.feed(pv.inputs["Absorption Color"], (0.0, 0.0, 0.0))
    pv.inputs["Anisotropy"].default_value = 0.45
    g.feed(pv.inputs["Emission Color"], fill)
    g.feed(pv.inputs["Emission Strength"], g.math('MULTIPLY', g.math('MULTIPLY', d, fz, x=200, y=400),
                                                   P["sky_strength"], x=400, y=400))
    set_volume_opts(m)
    return finish(m, g, out, volume=pv.outputs[0])


def mat_atmosphere():
    """Aerial haze (exponential with height) + the far fog bank that hides the mountains' feet.

    Under a sunless twilight sky the light reaching the haze is the same from every direction, so
    single scattering reduces to extinction + a constant in-scattered radiance: distant things fade
    toward the colour of the horizon sky behind them. That is rendered exactly as absorption plus
    emission, with the emitted colour read from the sky model itself (per viewing azimuth). It adds
    no light-sampling noise and does not grey out the sky the way a scattering box would."""
    m, g, out = new_mat("M_Atmosphere")
    geo = g.new('ShaderNodeNewGeometry', -2000, 0)
    pos = geo.outputs["Position"]
    x, y, z = g.separate(pos, x=-1800, y=0)
    zp = g.math('MAXIMUM', z, 0.0, x=-1600, y=0)
    haze = g.math('MULTIPLY', g.math('EXPONENT', g.math('MULTIPLY', zp, -1.0 / P["haze_height"], x=-1400, y=0),
                                     x=-1200, y=0), P["haze_density"], x=-1000, y=0)
    t = time_value(g, -2000, -300)
    wv = heading_vec(P["wind_dir"]) * P["fog_speed"]
    adv = g.vmath('SUBTRACT', pos, g.combine(g.math('MULTIPLY', t, wv.x, x=-1800, y=-300),
                                            g.math('MULTIPLY', t, wv.y, x=-1800, y=-400), 0.0, x=-1600, y=-350),
                  x=-1400, y=-300)
    bn = g.noise(g.mapping(adv, scale=(1.0 / 900.0, 1.0 / 450.0, 1.0 / 60.0), x=-1200, y=-300), scale=1.0,
                 detail=4.0, rough=0.55, dist=0.4, x=-1000, y=-300)
    bh = g.math('MULTIPLY', P["bank_height"], g.mrange(g.fac(bn), 0.3, 0.7, 0.45, 1.6, x=-800, y=-450), x=-600, y=-450)
    bank = g.math('EXPONENT', g.math('DIVIDE', g.math('MULTIPLY', zp, -1.0, x=-800, y=-150), bh, x=-600, y=-150),
                  x=-450, y=-150)
    bank = g.math('MULTIPLY', bank, g.mrange(g.fac(bn), 0.35, 0.65, 0.08, 1.0, interp='SMOOTHSTEP', x=-600, y=-300), x=-300, y=-200)
    bank = g.math('MULTIPLY', bank, g.mrange(y, 1150.0, 1600.0, x=-450, y=-600), x=-150, y=-250)
    bank = g.math('MULTIPLY', bank, P["bank_density"], x=0, y=-250)
    d = g.math('ADD', haze, bank, x=200, y=0)
    # in-scattered radiance = the horizon sky in the direction we are looking (a touch above it)
    glow = g.mix(1.0, horizon_light(g, geo, -1800, 500), (P["haze_gain"],) * 3, blend='MULTIPLY', x=-600, y=500)
    pv = g.new('ShaderNodeVolumePrincipled', 800, 0)
    g.feed(pv.inputs["Color"], (0.0, 0.0, 0.0))                 # no scattering: extinction ...
    g.feed(pv.inputs["Density"], d)
    g.feed(pv.inputs["Absorption Color"], (0.0, 0.0, 0.0))
    g.feed(pv.inputs["Emission Color"], glow)                   # ... + in-scattered sky light
    g.feed(pv.inputs["Emission Strength"], g.math('MULTIPLY', d, P["sky_strength"], x=400, y=-100))
    set_volume_opts(m)
    return finish(m, g, out, volume=pv.outputs[0])


def build_materials():
    MATS["ocean"] = mat_ocean()
    MATS["terrain"] = mat_terrain()
    MATS["fog"] = mat_sea_fog()
    MATS["atmo"] = mat_atmosphere()


# ============================================================================
#  OCEAN
# ============================================================================
def fan_mesh(apex, heading, half_deg, r0, r1, n_ang, r2, ratio2):
    """Camera-aligned polar fan: square cells whose size grows with distance (dense only where
    the lens resolves it). Beyond r1 the rings get coarse quickly out to r2 (flat far sea)."""
    bm = bmesh.new()
    half = radians(half_deg)
    step = 2.0 * half / n_ang
    radii = [r0]
    while radii[-1] < r1:
        radii.append(radii[-1] * (1.0 + step / P["ocean_rings_ratio"]))
    while radii[-1] < r2:
        radii.append(radii[-1] * ratio2)
    h0 = radians(heading)
    ax, ay = apex
    rows = []
    for r in radii:
        row = []
        for i in range(n_ang + 1):
            a = h0 - half + i * step
            row.append(bm.verts.new((ax + r * sin(a), ay + r * cos(a), 0.0)))
        rows.append(row)
    for a, b in zip(rows[:-1], rows[1:]):
        for i in range(n_ang):
            bm.faces.new((a[i], a[i + 1], b[i + 1], b[i]))
    return bm


def ng_ocean_fade():
    """GN: blend the Ocean modifiers' displacement back to the rest surface with distance."""
    ng, g, gi, go = new_tree("GN_OceanDistanceFade", 'GeometryNodeTree',
                             [("Geometry", 'NodeSocketGeometry', None),
                              ("Fade Start", 'NodeSocketFloat', P["wave_fade"][0]),
                              ("Fade End", 'NodeSocketFloat', P["wave_fade"][1])],
                             [("Geometry", 'NodeSocketGeometry')])
    setp(ng, "is_modifier", True)
    rest = g.new('GeometryNodeInputNamedAttribute', -1100, 200, data_type='FLOAT_VECTOR')
    rest.inputs["Name"].default_value = "rest_position"
    rp = g.o(rest, "Attribute")
    pos = g.o(g.new('GeometryNodeInputPosition', -1100, 0))
    cs = P["cam_start"]
    d = g.vmath('LENGTH', g.vmath('MULTIPLY', g.vmath('SUBTRACT', rp, (cs[0], cs[1], 0.0), x=-900, y=200),
                                  (1.0, 1.0, 0.0), x=-750, y=200), x=-600, y=200)
    f = g.mrange(d, gi.outputs["Fade Start"], gi.outputs["Fade End"], 1.0, 0.0, interp='SMOOTHSTEP', x=-400, y=200)
    newp = g.vmath('ADD', rp, g.vmath('SCALE', g.vmath('SUBTRACT', pos, rp, x=-400, y=0), s=f, x=-200, y=0),
                   x=0, y=100)
    sp = g.new('GeometryNodeSetPosition', 300, 0)
    g.link(gi.outputs["Geometry"], sp.inputs["Geometry"])
    g.link(newp, sp.inputs["Position"])
    g.link(sp.outputs[0], go.inputs["Geometry"])
    return ng


def ocean_mod(ob, name, spectrum, size, res, wind, direction, align, scale, chop, foam):
    m = ob.modifiers.new(name, 'OCEAN')
    m.geometry_mode = 'DISPLACE'
    setp(m, "spectrum", spectrum)
    m.spatial_size = int(size)
    m.resolution = res
    setp(m, "viewport_resolution", max(4, res // 3))
    m.wind_velocity = wind
    m.wave_alignment = align
    # Ocean modifier wave_direction is measured from +X, counter-clockwise
    m.wave_direction = radians(90.0 - direction)
    m.wave_scale = scale
    m.choppiness = chop
    m.depth = 200.0
    setp(m, "damping", 0.5)
    setp(m, "fetch_jonswap", 60.0)
    m.random_seed = P["seed"] + len(ob.modifiers)
    m.use_normals = False
    if foam:
        m.use_foam = True
        m.foam_layer_name = "foam"
        m.foam_coverage = P["foam_coverage"]
    add_driver(ob, 'modifiers["%s"].time' % name, time_expr())
    return m


def build_ocean():
    c = coll("40_Ocean")
    cs = P["cam_start"]
    yaw = 0.5 * (P["cam_yaw"][0] + P["cam_yaw"][1])
    back = heading_vec(yaw) * -30.0
    bm = fan_mesh((cs[0] + back.x, cs[1] + back.y), yaw, 42.0, 22.0, 2600.0, 440, 70000.0, 1.035)
    ob = bm_to_obj("Ocean", bm, c, MATS["ocean"], smooth=True)
    ob.add_rest_position_attribute = True
    # long swell from the open sea + the local wind sea (with whitecap foam) on top
    ocean_mod(ob, "Swell", 'PIERSON_MOSKOWITZ', P["swell_size"], 10, 10.0, P["swell_dir"], 0.9, P["swell_scale"], 0.5,
              False)
    ocean_mod(ob, "WindSea", 'JONSWAP', P["ocean_size"], P["ocean_res"], P["wind_speed"], P["wind_dir"], 0.35,
              P["windsea_scale"], P["choppiness"], True)
    gm = ob.modifiers.new("DistanceFade", 'NODES')
    gm.node_group = ng_ocean_fade()
    OBJS["ocean"] = ob
    return ob


# ============================================================================
#  MOUNTAINS
# ============================================================================
TERRAIN_LOOK = {   # per range: treeline / snowline (m), shading detail scale (m)
    "near": dict(treeline=330.0, snowline=9000.0, detail_scale=10.0),
    "mid": dict(treeline=520.0, snowline=950.0, detail_scale=28.0),
    "far": dict(treeline=900.0, snowline=1450.0, detail_scale=55.0),
}


def terrain_folder():
    if P.get("terrain_dir"):
        return bpy.path.abspath(P["terrain_dir"])
    cands = []
    if bpy.data.filepath:
        cands.append(os.path.join(os.path.dirname(bpy.data.filepath), "textures"))
    here = globals().get("__file__", "")
    if here and os.path.isabs(here):
        cands.append(os.path.join(os.path.dirname(here), "textures"))
    for d in cands:
        if os.path.isfile(os.path.join(d, "5AM_terrain.json")):
            return d
    return None


def ng_terrain(name, img, meta):
    """GN: a grid the size of the heightmap, lifted by it (cubic sampling), thinned in the viewport."""
    ng, g, gi, go = new_tree("GN_Terrain_" + name, 'GeometryNodeTree',
                             [("Geometry", 'NodeSocketGeometry', None)],
                             [("Geometry", 'NodeSocketGeometry')])
    setp(ng, "is_modifier", True)
    (x0, x1), (y0, y1) = meta["x"], meta["y"]
    W, H = meta["width"], meta["height"]
    dec = max(1, int(P["viewport_terrain_decimate"]))
    isv = g.new('GeometryNodeIsViewport', -1100, 400)

    def switch_int(a, b, yy):
        s = g.new('GeometryNodeSwitch', -900, yy, input_type='INT')
        g.link(g.o(isv), g.i(s, "Switch"))
        g.i(s, "False").default_value = a
        g.i(s, "True").default_value = b
        return g.o(s)
    grid = g.new('GeometryNodeMeshGrid', -700, 300)
    grid.inputs["Size X"].default_value = x1 - x0
    grid.inputs["Size Y"].default_value = y1 - y0
    g.link(switch_int(W, max(2, W // dec), 450), grid.inputs["Vertices X"])
    g.link(switch_int(H, max(2, H // dec), 250), grid.inputs["Vertices Y"])
    uv = g.o(grid, "UV Map")
    tex = g.new('GeometryNodeImageTexture', -500, 0, interpolation='Cubic', extension='EXTEND')
    tex.inputs["Image"].default_value = img
    g.link(uv, tex.inputs["Vector"])
    hgt = g.math('MULTIPLY_ADD', g.o(tex, "Color"), meta["h_max"] - meta["h_min"], meta["h_min"], x=-250, y=0)
    off = g.combine(0.5 * (x0 + x1), 0.5 * (y0 + y1), hgt, x=0, y=0)
    sp = g.new('GeometryNodeSetPosition', 250, 200)
    g.link(grid.outputs["Mesh"], sp.inputs["Geometry"])
    g.link(off, sp.inputs["Offset"])
    ss = g.new('GeometryNodeSetShadeSmooth', 500, 200)
    g.link(sp.outputs[0], ss.inputs[0])
    sm = g.new('GeometryNodeSetMaterial', 750, 200)
    g.link(ss.outputs[0], sm.inputs["Geometry"])
    sm.inputs["Material"].default_value = MATS["terrain"]
    g.link(sm.outputs[0], go.inputs["Geometry"])
    return ng


def ng_terrain_procedural(name, x0, x1, y0, y1, peak, cell):
    """Fallback when the heightmaps are missing: ridged multifractal ranges built in GN."""
    ng, g, gi, go = new_tree("GN_Terrain_" + name, 'GeometryNodeTree',
                             [("Geometry", 'NodeSocketGeometry', None)],
                             [("Geometry", 'NodeSocketGeometry')])
    setp(ng, "is_modifier", True)
    grid = g.new('GeometryNodeMeshGrid', -900, 300)
    grid.inputs["Size X"].default_value = x1 - x0
    grid.inputs["Size Y"].default_value = y1 - y0
    grid.inputs["Vertices X"].default_value = int((x1 - x0) / cell)
    grid.inputs["Vertices Y"].default_value = int((y1 - y0) / cell)
    pos = g.o(g.new('GeometryNodeInputPosition', -900, 0))
    wp = g.vmath('ADD', pos, (0.5 * (x0 + x1), 0.5 * (y0 + y1), 0.0), x=-700, y=0)
    px, py, pz = g.separate(wp, x=-500, y=-200)
    n = g.noise(g.vmath('SCALE', wp, s=1.0 / (0.35 * (y1 - y0)), x=-500, y=0), scale=1.0, detail=9.0, rough=0.55,
                ntype='RIDGED_MULTIFRACTAL', x=-300, y=0)
    env = g.math('MULTIPLY', g.mrange(py, y0, y0 + 0.25 * (y1 - y0), interp='SMOOTHSTEP', x=-300, y=-250),
                 g.mrange(py, y1, y1 - 0.2 * (y1 - y0), interp='SMOOTHSTEP', x=-300, y=-400), x=-100, y=-300)
    hgt = g.math('SUBTRACT', g.math('MULTIPLY', g.math('MULTIPLY', g.fac(n), peak * 0.6, x=-100, y=0), env, x=100, y=0),
                 20.0, x=250, y=0)
    sp = g.new('GeometryNodeSetPosition', 450, 200)
    g.link(grid.outputs["Mesh"], sp.inputs["Geometry"])
    g.link(g.combine(0.5 * (x0 + x1), 0.5 * (y0 + y1), hgt, x=300, y=-200), sp.inputs["Offset"])
    ss = g.new('GeometryNodeSetShadeSmooth', 650, 200)
    g.link(sp.outputs[0], ss.inputs[0])
    sm = g.new('GeometryNodeSetMaterial', 850, 200)
    g.link(ss.outputs[0], sm.inputs["Geometry"])
    sm.inputs["Material"].default_value = MATS["terrain"]
    g.link(sm.outputs[0], go.inputs["Geometry"])
    return ng


def build_mountains():
    c = coll("60_Mountains")
    d = terrain_folder()
    meta = None
    if d:
        with open(os.path.join(d, "5AM_terrain.json")) as f:
            meta = json.load(f)
    else:
        log("terrain: textures/5AM_terrain.json not found - using the procedural fallback ranges")
    fallback = {"near": (-6500, 3500, 2400, 8400, 470, 12), "mid": (-12000, 12000, 7000, 18000, 1450, 24),
                "far": (-26000, 26000, 16000, 44000, 3300, 50)}
    for name in ("near", "mid", "far"):
        if meta and name in meta:
            mm = meta[name]
            path = os.path.join(d, mm["file"])
            iname = "H5_" + mm["file"]
            img = bpy.data.images.get(iname)
            if img is None:
                img = bpy.data.images.load(path, check_existing=False)
                img.name = iname
            img.colorspace_settings.name = 'Non-Color'
            try:
                if not img.packed_file:
                    img.pack()
            except Exception as e:
                log("pack", iname, e)
            ng = ng_terrain(name, img, mm)
        else:
            ng = ng_terrain_procedural(name, *fallback[name])
        me = bpy.data.meshes.new("H5_Terrain_" + name)
        ob = link_obj("Mountains_" + name, me, c)
        gm = ob.modifiers.new("Terrain", 'NODES')
        gm.node_group = ng
        for k, v in TERRAIN_LOOK[name].items():
            ob[k] = v
        OBJS["terrain_" + name] = ob


# ============================================================================
#  ATMOSPHERE
# ============================================================================
def build_atmosphere():
    if not P["use_volumes"]:
        return
    c = coll("70_Atmosphere")

    def dom(name, x0, x1, y0, y1, z0, z1, mat):
        bm = bmesh.new()
        r = bmesh.ops.create_cube(bm, size=1.0)
        for v in r['verts']:
            v.co.x = x0 if v.co.x < 0 else x1
            v.co.y = y0 if v.co.y < 0 else y1
            v.co.z = z0 if v.co.z < 0 else z1
        ob = bm_to_obj(name, bm, c, mat)
        ob.display_type = 'BOUNDS'
        return ob
    top = P["fog_top"] + P["fog_top_var"] + P["fog_billow"] + 2.0
    dom("SeaFog", -520.0, 520.0, -60.0, 1700.0, -1.0, top, MATS["fog"])
    dom("Atmosphere", -48000.0, 48000.0, -300.0, 52000.0, -1.0, 5000.0, MATS["atmo"])


# ============================================================================
#  CAMERA
# ============================================================================
def build_camera():
    c = coll("00_Camera")
    cs = Vector(P["cam_start"])
    rig = new_empty("CamRig", c, cs, 0.4, 'ARROWS')
    cd = bpy.data.cameras.new("H5_Camera")
    cd.lens = P["lens"]
    cd.sensor_fit = 'HORIZONTAL'
    cd.sensor_width = P["sensor"]
    cd.clip_start = 0.1
    cd.clip_end = 90000.0
    cam = link_obj("Camera", cd, c, rig)
    cd.dof.use_dof = True
    cd.dof.focus_distance = P["focus_dist"]
    cd.dof.aperture_fstop = P["fstop"]
    cd.dof.aperture_blades = 9
    fs, fe = P["frame_start"], P["frame_end"]
    y0, y1 = P["cam_yaw"]
    pitch = radians(90.0 + P["cam_pitch"])
    # a steady glide: gentle ease at both ends (40 % of cruise speed), constant speed in between
    ce = cs + heading_vec(0.5 * (y0 + y1)) * P["cam_travel"] + Vector((0.0, 0.0, P["cam_rise"]))
    rig.location = cs
    rig.rotation_euler = Euler((pitch, 0.0, radians(-y0)), 'XYZ')
    rig.keyframe_insert("location", frame=fs)
    rig.keyframe_insert("rotation_euler", frame=fs)
    rig.location = ce
    rig.rotation_euler = Euler((pitch, 0.0, radians(-y1)), 'XYZ')
    rig.keyframe_insert("location", frame=fe)
    rig.keyframe_insert("rotation_euler", frame=fe)
    ad = rig.animation_data.action
    span = (fe - fs) / 3.0
    for fc in _fcurves(ad):
        kps = fc.keyframe_points
        if len(kps) != 2:
            continue
        a, b = kps[0], kps[1]
        dv = b.co[1] - a.co[1]
        slope = 0.4 * dv / (fe - fs)                  # 40 % of the mean speed at the ends
        for k in (a, b):
            k.interpolation = 'BEZIER'
            k.handle_left_type = 'FREE'
            k.handle_right_type = 'FREE'
        a.handle_right = (a.co[0] + span, a.co[1] + slope * span)
        a.handle_left = (a.co[0] - span, a.co[1] - slope * span)
        b.handle_left = (b.co[0] - span, b.co[1] - slope * span)
        b.handle_right = (b.co[0] + span, b.co[1] + slope * span)
    if P["cam_drift"]:
        # gimbal-soft float on the camera itself (not the rig): tiny, slow, never repeating
        for idx, (strength, scale, phase) in enumerate(((0.0022, 260.0, 3.0), (0.0012, 330.0, 17.0),
                                                        (0.0030, 300.0, 41.0))):
            cam.keyframe_insert("rotation_euler", index=idx, frame=fs)
            fcu = _find_fcurve(cam, "rotation_euler", idx)
            if fcu is not None:
                mod = fcu.modifiers.new('NOISE')
                mod.strength = strength
                mod.scale = scale
                mod.phase = phase
                setp(mod, "depth", 1)
        cam.keyframe_insert("location", index=2, frame=fs)
        fcu = _find_fcurve(cam, "location", 2)
        if fcu is not None:
            mod = fcu.modifiers.new('NOISE')
            mod.strength = 0.05
            mod.scale = 280.0
            mod.phase = 9.0
    SC.camera = cam
    OBJS["camera"] = cam
    return cam


def _fcurves(action):
    """All F-curves of an action (layered actions in 4.4+/5.x, flat list before)."""
    out = []
    try:
        for layer in action.layers:
            for strip in layer.strips:
                for cb in strip.channelbags:
                    out.extend(cb.fcurves)
    except Exception:
        pass
    if not out:
        try:
            out = list(action.fcurves)
        except Exception:
            pass
    return out


def _find_fcurve(ob, path, index):
    ad = ob.animation_data
    if ad is None or ad.action is None:
        return None
    for fc in _fcurves(ad.action):
        if fc.data_path == path and fc.array_index == index:
            return fc
    return None


# ============================================================================
#  RENDER SETTINGS, COMPOSITOR, OUTPUT
# ============================================================================
def setup_render():
    sc = SC
    r = sc.render
    r.engine = 'CYCLES'
    cy = sc.cycles
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        if prefs.compute_device_type in ('NONE', ''):
            for t in ('OPTIX', 'CUDA', 'HIP', 'METAL', 'ONEAPI'):
                try:
                    prefs.compute_device_type = t
                    break
                except Exception:
                    pass
        prefs.refresh_devices()
        for d in prefs.devices:
            if d.type != 'CPU':
                d.use = True
    except Exception as e:
        log("gpu setup", e)
    cy.device = 'GPU'                 # falls back to CPU by itself when no GPU is enabled in Preferences
    final = P["quality"] == "FINAL"
    cy.samples = P["samples"] if final else 48
    cy.preview_samples = P["preview_samples"]
    cy.use_adaptive_sampling = True
    cy.adaptive_threshold = P["adaptive_threshold"] if final else 0.05
    cy.adaptive_min_samples = P["min_samples"] if final else 16
    cy.use_denoising = True
    setp(cy, "denoiser", 'OPENIMAGEDENOISE')
    setp(cy, "denoising_input_passes", 'RGB_ALBEDO_NORMAL')
    setp(cy, "denoising_prefilter", 'ACCURATE')
    setp(cy, "denoising_quality", 'HIGH')
    setp(cy, "denoising_use_gpu", True)
    cy.use_preview_denoising = True
    # light paths: no lamps at all - sky light and volume scattering
    cy.max_bounces = 12
    cy.diffuse_bounces = 3
    cy.glossy_bounces = 4
    cy.transmission_bounces = 4
    cy.volume_bounces = 2
    cy.transparent_max_bounces = 8
    cy.sample_clamp_direct = 0.0
    cy.sample_clamp_indirect = 4.0
    cy.blur_glossy = 1.0
    cy.caustics_reflective = False
    cy.caustics_refractive = False
    setp(cy, "use_light_tree", True)
    # ray-marched volumes: the fog and haze are absorption + emission only, so marching is exact up to
    # the step size - about 4x faster than null scattering here and free of volume noise
    setp(cy, "volume_biased", True)
    setp(cy, "volume_step_rate", P["volume_step_rate"])
    setp(cy, "volume_preview_step_rate", 4.0)
    setp(cy, "volume_max_steps", 1024)
    cy.pixel_filter_type = 'BLACKMAN_HARRIS'
    cy.filter_width = 1.5
    cy.use_animated_seed = True
    setp(cy, "use_auto_tile", True)
    setp(cy, "tile_size", 2048)
    r.use_persistent_data = True       # the mountains and fog domains are only synced once
    r.resolution_x = P["res_x"]
    r.resolution_y = P["res_y"]
    r.resolution_percentage = 100 if final else 50
    r.fps = P["fps"]
    r.fps_base = 1.0
    sc.frame_start = P["frame_start"]
    sc.frame_end = P["frame_end"]
    r.use_motion_blur = P["motion_blur"]
    r.motion_blur_shutter = 0.5           # 180-degree shutter when enabled
    r.dither_intensity = 1.0              # no banding in the dark sky gradients
    vs = sc.view_settings
    try:
        vs.view_transform = 'AgX'
    except Exception as e:
        log("view transform", e)
    for look in (P["look"], "AgX - Base Contrast", "Base Contrast", "None"):
        try:
            vs.look = look
            break
        except Exception:
            pass
    vs.exposure = P["exposure"]
    vs.gamma = 1.0
    setup_output(sc)
    vl = sc.view_layers[0]
    vl.use_pass_z = True
    vl.use_pass_mist = True
    setp(vl, "use_pass_cryptomatte_object", True)
    setp(vl.cycles, "denoising_store_passes", False)


def setup_output(sc):
    r = sc.render
    ims = r.image_settings
    mode = P.get("output", "PNG")
    if mode == "VIDEO":
        setp(ims, "media_type", 'VIDEO')
        setp(ims, "file_format", 'FFMPEG')
        ff = r.ffmpeg
        setp(ff, "format", 'MPEG4')
        setp(ff, "codec", 'H264')
        setp(ff, "constant_rate_factor", 'PERC_LOSSLESS')
        setp(ff, "ffmpeg_preset", 'GOOD')
        setp(ff, "gopsize", P["fps"])
        setp(ff, "audio_codec", 'AAC')
        setp(ff, "audio_bitrate", 320)
        setp(ff, "audio_channels", 'STEREO')
        setp(ff, "audio_mixrate", 48000)
        r.filepath = "//renders/5AM_Predawn_"
    elif mode == "EXR":
        setp(ims, "media_type", 'MULTI_LAYER_IMAGE')
        for fmt in ('OPEN_EXR_MULTILAYER', 'OPEN_EXR'):
            if setp(ims, "file_format", fmt):
                break
        setp(ims, "color_depth", '16')
        setp(ims, "exr_codec", 'DWAA')
        r.filepath = "//renders/5AM_exr/5AM_####"
    else:
        setp(ims, "media_type", 'IMAGE')
        ims.file_format = 'PNG'
        ims.color_mode = 'RGB'
        ims.color_depth = '8'
        ims.compression = 15
        r.filepath = "//renders/5AM_frames/5AM_####"
        # resumable: frames that exist (or are being rendered by another machine) are skipped
        r.use_overwrite = False
        r.use_placeholder = True
    r.use_file_extension = True


def menu_set(node, name, options):
    s = node.inputs.get(name)
    if s is None:
        return False
    for o in options:
        try:
            s.default_value = o
            return True
        except Exception:
            continue
    log("compositor menu", name, "rejected", options)
    return False


def setup_compositor():
    """Render Layers -> soft glare (moon, sky glow) -> subtle lens -> teal grade -> vignette."""
    sc = SC
    setp(sc.render, "use_compositing", True)
    ng = getattr(sc, "compositing_node_group", None)
    if ng is None or not ng.name.startswith("H5_"):
        ng = bpy.data.node_groups.get("H5_Comp") or bpy.data.node_groups.new("H5_Comp", 'CompositorNodeTree')
        sc.compositing_node_group = ng
    for n in list(ng.nodes):
        ng.nodes.remove(n)
    if not [it for it in ng.interface.items_tree if getattr(it, "in_out", "") == 'OUTPUT']:
        ng.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')
    g = NT(ng)
    rl = g.new('CompositorNodeRLayers', -1100, 0)
    setp(rl, "scene", sc)
    out = g.new('NodeGroupOutput', 900, 0)
    img = rl.outputs["Image"]
    try:
        glare = g.new('CompositorNodeGlare', -800, 0)
        g.link(img, glare.inputs["Image"])
        menu_set(glare, "Type", ("Fog Glow", "FOG_GLOW", "Bloom", "BLOOM"))
        menu_set(glare, "Quality", ("High", "HIGH"))
        g.set(glare, {"Threshold": 0.6, "Smoothness": 0.5, "Strength": 0.35, "Size": 0.8})
        img = glare.outputs["Image"]
    except Exception as e:
        log("glare", e)
    try:
        lens = g.new('CompositorNodeLensdist', -550, 0)
        g.link(img, lens.inputs["Image"])
        g.set(lens, {"Distortion": -0.004, "Dispersion": 0.006})
        if lens.inputs.get("Fit") is not None:
            lens.inputs["Fit"].default_value = True
        img = lens.outputs["Image"]
    except Exception as e:
        log("lens", e)
    try:
        # grade in 'exposed' space so the colour-management exposure stays the one brightness knob
        ex1 = g.new('CompositorNodeExposure', -350, 150)
        g.link(img, ex1.inputs["Image"])
        g.set(ex1, {"Exposure": P["exposure"]})
        cb = g.new('CompositorNodeColorBalance', -150, 0)
        g.link(ex1.outputs["Image"], cb.inputs["Image"])
        lift, gamma, gain = g.i(cb, "Lift", 'RGBA'), g.i(cb, "Gamma", 'RGBA'), g.i(cb, "Gain", 'RGBA')
        dl = tuple(lift.default_value)
        if all(abs(v - 1.0) < 0.01 for v in dl[:3]):
            lift.default_value = (0.965, 1.005, 1.015, 1.0)    # deep teal shadows
            gamma.default_value = (0.97, 1.02, 1.005, 1.0)     # mid-tones lean cyan-green (the reference grade)
            gain.default_value = (1.01, 1.0, 0.985, 1.0)       # a whisper of warmth in the brights
        else:
            log("color balance defaults", dl)
        hs = g.new('CompositorNodeHueSat', -20, -150)
        g.link(cb.outputs["Image"], hs.inputs["Image"])
        g.set(hs, {"Hue": 0.488, "Saturation": 0.86, "Value": 1.0})
        ex2 = g.new('CompositorNodeExposure', 50, 150)
        g.link(hs.outputs["Image"], ex2.inputs["Image"])
        g.set(ex2, {"Exposure": -P["exposure"]})
        img = ex2.outputs["Image"]
    except Exception as e:
        log("color balance", e)
    try:
        em = g.new('CompositorNodeEllipseMask', -300, -400)
        g.set(em, {"Position": (0.5, 0.5), "Size": (1.15, 1.05)})
        bl = g.new('CompositorNodeBlur', -50, -400)
        g.link(g.o(em), bl.inputs["Image"])
        g.set(bl, {"Size": (300.0, 300.0)})
        vig = g.mrange(g.o(bl), 0.0, 1.0, 0.72, 1.0, x=150, y=-400)
        img = g.mix(1.0, img, vig, blend='MULTIPLY', x=400, y=0)
    except Exception as e:
        log("vignette", e)
    g.link(img, out.inputs[0])


AUDIO_STEMS = [  # (file, channel, volume) - real field recordings, prepared by make_soundtrack.py --shot 5am
    ("01_open_sea_waves.wav", 1, 1.0),     # swell under the fog (Luftrum, CC BY 4.0 - credit)
    ("02_sea_breeze.wav", 2, 1.0),         # light onshore breeze (felix.blume, CC0)
]
AUDIO_FALLBACK = [("01_shore_waves.wav", 1, 0.8)]      # the 3 AM harbour stem, if that's all there is


def audio_folder():
    if P.get("audio_dir"):
        return bpy.path.abspath(P["audio_dir"])
    cands = []
    if bpy.data.filepath:
        cands.append(os.path.join(os.path.dirname(bpy.data.filepath), "audio"))
    here = globals().get("__file__", "")
    if here and os.path.isabs(here):
        cands.append(os.path.join(os.path.dirname(here), "audio"))
    for d in cands:
        if os.path.isdir(d):
            return d
    return None


def add_sound_strips(sc, fades=False):
    """Sound strips from the audio folder. fades=True: 0.5 s in / 1.5 s out, so the finished clip
    doesn't start or stop on a hard cut (the stems themselves run 5 s past the shot as handles)."""
    d = audio_folder()
    if not d:
        log("audio: no 'audio' folder found - skipped (run make_soundtrack.py first)")
        return 0
    stems = AUDIO_STEMS if any(os.path.isfile(os.path.join(d, f)) for f, _, _ in AUDIO_STEMS) else AUDIO_FALLBACK
    se = sc.sequence_editor or sc.sequence_editor_create()
    n = 0
    for fname, ch, vol in stems:
        path = os.path.join(d, fname)
        if not os.path.isfile(path):
            log("audio: missing", fname)
            continue
        s = se.strips.new_sound("H5_" + os.path.splitext(fname)[0], path, ch, P["frame_start"])
        s.volume = vol
        if fades:
            fs, fe, fps = P["frame_start"], P["frame_end"], P["fps"]
            for f, v in ((fs, 0.0), (fs + int(0.5 * fps), vol), (fe - int(1.5 * fps), vol), (fe, 0.0)):
                s.volume = v
                try:
                    s.keyframe_insert("volume", frame=f)
                except Exception as e:
                    log("audio fade", e)
                    break
        n += 1
    return n


def setup_audio():
    """Sound-only strips in the 3D scene's Sequencer (used by the VIDEO output; the picture is untouched)."""
    sc = SC
    se = sc.sequence_editor or sc.sequence_editor_create()
    for s in list(se.strips_all):
        if s.name.startswith("H5_"):
            se.strips.remove(s)
    n = add_sound_strips(sc)
    setp(sc, "sync_mode", 'AUDIO_SYNC')
    setp(sc.render, "use_sequencer", n > 0)
    return n


def setup_edit_scene():
    """Scene '5AM_Edit': the rendered PNG frames + soundtrack -> one MP4 (renders in about a minute)."""
    ed = bpy.data.scenes.get("5AM_Edit") or bpy.data.scenes.new("5AM_Edit")
    se = ed.sequence_editor or ed.sequence_editor_create()
    for s in list(se.strips_all):
        se.strips.remove(s)
    r = ed.render
    r.resolution_x, r.resolution_y, r.resolution_percentage = P["res_x"], P["res_y"], 100
    r.fps, r.fps_base = P["fps"], 1.0
    ed.frame_start, ed.frame_end = P["frame_start"], P["frame_end"]
    folder = "//renders/5AM_frames/"
    files = ["5AM_%04d.png" % f for f in range(P["frame_start"], P["frame_end"] + 1)]
    try:
        st = se.strips.new_image("H5_Frames", folder + files[0], 1, P["frame_start"])
        for f in files[1:]:
            st.elements.append(f)
        st.colorspace_settings.name = 'sRGB'
    except Exception as e:
        log("edit frames", e)
    add_sound_strips(ed, fades=True)
    setp(ed.render, "use_sequencer", True)
    setp(ed.render, "use_compositing", False)
    ed.view_settings.view_transform = 'Standard'          # frames are already graded: pass straight through
    ed.view_settings.look = 'None'
    ed.view_settings.exposure = 0.0
    ims = r.image_settings
    setp(ims, "media_type", 'VIDEO')
    setp(ims, "file_format", 'FFMPEG')
    ff = r.ffmpeg
    setp(ff, "format", 'MPEG4')
    setp(ff, "codec", 'H264')
    setp(ff, "constant_rate_factor", 'PERC_LOSSLESS')
    setp(ff, "ffmpeg_preset", 'GOOD')
    setp(ff, "gopsize", P["fps"])
    setp(ff, "audio_codec", 'AAC')
    setp(ff, "audio_bitrate", 320)
    setp(ff, "audio_channels", 'STEREO')
    setp(ff, "audio_mixrate", 48000)
    r.filepath = "//renders/5AM_Predawn_"
    return ed


def setup_viewport():
    """Look through the camera in Solid mode (cheap on laptops); push the clip range out to the far range."""
    try:
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                if area.type != 'VIEW_3D':
                    continue
                sp = area.spaces.active
                sp.shading.type = 'SOLID'
                setp(sp.shading, "color_type", 'MATERIAL')
                sp.clip_start = 0.1
                sp.clip_end = 90000.0
                sp.region_3d.view_perspective = 'CAMERA'
                region = next((rg for rg in area.regions if rg.type == 'WINDOW'), None)
                if region is not None:
                    with bpy.context.temp_override(window=win, area=area, region=region):
                        bpy.ops.view3d.view_center_camera()
    except Exception as e:
        log("viewport", e)


VIEW_COLORS = {"ocean": (0.05, 0.10, 0.13, 1), "terrain": (0.30, 0.31, 0.33, 1),
               "fog": (0.8, 0.8, 0.85, 1), "atmo": (0.8, 0.8, 0.85, 1)}


def apply_viewport_colors():
    for k, m in MATS.items():
        if k in VIEW_COLORS:
            try:
                m.diffuse_color = VIEW_COLORS[k]
            except Exception:
                pass


def remove_default_scene():
    """In a fresh, unsaved file, drop the default cube scene so only the new shot remains."""
    if bpy.data.filepath:
        return
    sc = bpy.data.scenes.get("Scene")
    if sc is None or sc == SC:
        return
    if set(o.name for o in sc.objects) <= {"Cube", "Light", "Camera"}:
        for nm in ("Cube", "Light", "Camera"):
            ob = bpy.data.objects.get(nm)
            if ob is not None and ob.users <= 1:
                bpy.data.objects.remove(ob, do_unlink=True)
        bpy.data.scenes.remove(sc)


# ============================================================================
#  BUILD
# ============================================================================
def build_all(save_path=None):
    t0 = time.time()
    LOG.clear()
    prepare_scene()
    steps = [("render", setup_render), ("materials", build_materials), ("world", setup_world),
             ("camera", build_camera), ("ocean", build_ocean), ("mountains", build_mountains),
             ("atmosphere", build_atmosphere), ("compositor", setup_compositor), ("audio", setup_audio),
             ("edit scene", setup_edit_scene), ("viewport", setup_viewport)]
    for name, fn in steps:
        ts = time.time()
        try:
            fn()
            log("%-11s ok  %.1fs" % (name, time.time() - ts))
        except Exception as e:
            import traceback
            log("%-11s FAILED: %s" % (name, e))
            log(traceback.format_exc()[-1500:])
    apply_viewport_colors()
    try:
        if bpy.context.window is not None:
            bpy.context.window.scene = SC
    except Exception:
        pass
    SC.frame_set(P["frame_start"])
    try:
        remove_default_scene()
    except Exception as e:
        log("default scene", e)
    try:
        for wm in bpy.data.window_managers:           # open on the shot, not on the edit scene
            for win in wm.windows:
                win.scene = SC
    except Exception as e:
        log("window scene", e)
    if save_path:
        try:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            bpy.ops.wm.save_as_mainfile(filepath=save_path, check_existing=False, compress=True)
            try:                                       # sounds / frame folders relative to the .blend
                bpy.ops.file.make_paths_relative()
                bpy.ops.wm.save_mainfile(compress=True)
            except Exception as e:
                log("relative paths", e)
            log("saved", save_path)
        except Exception as e:
            log("save failed", e)
    log("total %.1fs, %d objects" % (time.time() - t0, len([o for o in SC.objects if o.name.startswith("H5_")])))
    return LOG


if __name__ == "__main__":
    build_all()
