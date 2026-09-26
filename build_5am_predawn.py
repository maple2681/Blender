# ============================================================================
#  5 AM PRE-DAWN  -  cinematic seascape builder for Blender 5.x + Cycles
# ============================================================================
"""
Builds the complete 5 AM shot, fully procedural apart from three terrain heightmaps
(grown by tools/make_terrain.py and packed into the .blend):

  * nautical-twilight sky: physically based multiple-scattering atmosphere with the sun
    8.5 deg BELOW the horizon, so there is no sun in frame and no direct sunlight -
    the sky glow lights everything but the boat's lantern (a thin waning crescent moon
    rides low in the east, as it does before sunrise; P["moon"] turns it off)
  * three layers of eroded mountains fading into the distance (coastal hills 3-8 km,
    fjord range 9-18 km, snow-capped massif 17-44 km) with forest, rock, scree and snow
  * FFT ocean (Tessendorf spectra via two Ocean modifiers: wind sea + long swell) with
    whitecap foam, capillary ripples and a distance fade so the horizon never aliases
  * thick sea fog rolling just above the water (drifting, evolving volume with a
    ragged top), a denser fog bank at the foot of the mountains and aerial haze
  * a clinker-built wooden rowboat (no motor) rowed away through the fog by a detailed fisherman
    (MakeHuman CC0 body, assets/5AM_rower.blend) with a full sculling stroke: IK hands on the oar grips,
    body swing, feathering blades, oar ripples and puddles, the boat floating on the FFT waves
  * the rotten posts of an old jetty with weed skirts, barnacles and moss, a moored buoy, kelp,
    gulls, a lighthouse sweeping its beam on the headland, a kerosene lantern swinging at the stern
  * slow cinematic camera: a 15 s glide low over the water that pans with the boat
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
import random
import time
from math import radians, sin, cos, atan2, pi, exp
from mathutils import Vector, Euler, Matrix, noise

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
    exposure=1.8,                          # stops, AgX view transform (nautical twilight: moody, a little dark)
    look="AgX - Medium High Contrast",
    # sky: civil twilight. Azimuths are compass degrees, 0 = +Y (straight ahead), 90 = +X (right)
    sun_elevation=-8.5,                    # below the horizon -> no sun, no direct light, no shadows
    sun_azimuth=62.0,                      # the brightening sky is ahead-right of the camera
    sky_strength=1.0,
    air_density=1.0, aerosol_density=1.6, ozone_density=1.0,
    clouds=0.55,                           # thin high veil (0 = clear)
    moon=True, moon_azimuth=16.0, moon_elevation=7.2,
    # ocean (metres, seconds)
    wind_speed=4.8, wind_dir=28.0,         # m/s (light breeze: fog weather), heading the wind blows TOWARD
    swell_dir=15.0,
    ocean_size=220.0, ocean_res=20, swell_size=900.0,
    swell_scale=0.7, windsea_scale=0.22,   # wave height multipliers (Hs ~0.6 m swell, ~0.25 m wind sea)
    choppiness=0.95, foam_coverage=0.18,
    wave_fade=(450.0, 1700.0),             # waves ease to a flat (shaded) sea between these ranges
    # atmosphere
    fog_density=0.14,                      # 1/m inside the layer: the thick rolling sea fog
    fog_top=1.9, fog_top_var=0.8,          # mean height of the fog's top and its slow swell (m)
    fog_billow=2.6,                        # height of the rolling billows on top of the layer (m)
    fog_speed=1.4,                         # m/s drift
    fog_fill=1.05,                         # in-scattered light (fog brightness vs the horizon sky behind it)
    fog_scatter=0.0,                       # > 0 adds path-traced single scattering on top (slow, noisy)
    bank_density=0.0035, bank_height=32.0, # fog bank at the foot of the mountains
    haze_density=0.00007, haze_height=800.0,
    haze_gain=0.92,                        # haze/fog-bank brightness relative to the horizon sky behind it
    # the old pier with the red fish house (3 AM builder's code), placed in pier space -> world
    pier=True, pier_offset=(-1.5, 2.6), pier_rotation=-5.0,        # m, deg (negative = runs toward frame centre)
    pier_lamps=(5.0, 11.5, 18.0), pier_dead_lamp=2, pier_lamp_power=14.0,   # pier-space y of the lamp posts
    # the ruined jetty beyond the pier's collapsed end, the rowing boat and its fisherman
    jetty=True, jetty_from=(2.2, 30.0), jetty_to=(6.8, 84.0),
    boat=True, rower=True, lantern=True,
    boat_start=(6.2, 17.0), boat_heading=8.0, boat_speed=0.85,   # m, compass deg, m/s (rowing away)
    stroke_period=2.7, stroke_phase0=0.62,                         # s per stroke (~22 strokes/min)
    glance=(7.0, 9.4),                                             # s: he looks over his shoulder
    lantern_power=6.0, lantern_halo=1.3, lantern_halo_density=0.018,
    elbow_pole_angle=-90.0, knee_pole_angle=-90.0, finger_curl=65.0,
    gulls=True, lighthouse=True, kelp=True,
    lighthouse_azimuth=-12.0, beam_period=10.0, beam_strength=0.15, lighthouse_flash=2500.0,
    # camera: 15 s glide over the water toward the mountains
    cam_start=(0.0, 0.0, 2.5), cam_travel=5.5, cam_rise=0.25,
    cam_yaw=(5.0, 7.5), cam_pitch=-2.5,    # degrees (yaw + = right), pitch + = up
    lens=40.0, sensor=36.0, fstop=4.0, focus_dist=16.0,           # focus follows the boat when there is one
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
GROUPS = {}


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
    pre = ("H5_", "H5R_", "H5H_")               # H5R_ = the rower, H5H_ = the pier built by the 3 AM code
    for ob in list(bpy.data.objects):
        if ob.name.startswith(pre):
            bpy.data.objects.remove(ob, do_unlink=True)
    for c in list(bpy.data.collections):
        if c.name.startswith(pre):
            bpy.data.collections.remove(c)
    for _ in range(3):
        for coll in (bpy.data.meshes, bpy.data.curves, bpy.data.lights, bpy.data.cameras, bpy.data.armatures,
                     bpy.data.materials, bpy.data.node_groups, bpy.data.worlds, bpy.data.images,
                     bpy.data.particles, bpy.data.textures, bpy.data.actions):
            for d in list(coll):
                if d.name.startswith(pre) and d.users == 0:
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
    GROUPS.clear()
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
    # oar marks: expanding rings from every catch, glassy swirl puddles left at every finish
    rings, calm = ripple_slots(g, pos, t)
    rs = g.math('MULTIPLY', rs, g.math('SUBTRACT', 1.0, calm, x=-1300, y=-350), x=-1150, y=-300)
    # the hull: water cut away inside it, a lip of foam and small ripples running off its skin
    bc = g.new('ShaderNodeTexCoord', -2400, 1400)
    bc.name = "BoatCoords"
    bx, by, bz = g.separate(bc.outputs["Object"], x=-2200, y=1400)
    ax = g.math('ABSOLUTE', bx, x=-2000, y=1450)
    bwl = g.math('MULTIPLY', g.math('MAXIMUM', g.math('SUBTRACT', 1.0, g.math('POWER', g.math('DIVIDE', ax, 1.95,
                                                                                            x=-1850, y=1450),
                                                                         2.4, x=-1700, y=1450), x=-1550, y=1450),
                                    0.0, x=-1400, y=1450), 0.42, x=-1250, y=1450)
    dedge = g.math('SUBTRACT', g.math('ABSOLUTE', by, x=-2000, y=1300), bwl, x=-1100, y=1350)
    inside = g.math('MULTIPLY', g.math('LESS_THAN', dedge, 0.0, x=-950, y=1400),
                    g.math('LESS_THAN', ax, 1.95, x=-950, y=1300), x=-800, y=1350)
    near_hull = g.math('MULTIPLY', g.mrange(dedge, 0.0, 0.7, 1.0, 0.0, x=-950, y=1200),
                       g.math('LESS_THAN', ax, 2.3, x=-950, y=1100), x=-800, y=1150)
    hrip = g.math('SINE', g.math('MULTIPLY', g.math('MULTIPLY_ADD', t, -0.35, dedge, x=-950, y=1000), 2 * pi / 0.14,
                                 x=-800, y=1000), x=-650, y=1000)
    rings = g.math('MULTIPLY_ADD', g.math('MULTIPLY', hrip, near_hull, x=-500, y=1000), 0.5, rings, x=-350, y=900)
    nrm = g.bump(rip, strength=rs, distance=0.07, x=-1200, y=-150)
    nrm = g.bump(rings, strength=1.0, distance=0.01, normal=nrm, x=-1050, y=-150)
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
    lip = g.math('MULTIPLY', g.mrange(dedge, 0.0, 0.06, 1.0, 0.0, x=-950, y=1600),
                 g.mrange(g.fac(g.noise(pos, scale=9.0, detail=4.0, x=-1150, y=1650)), 0.35, 0.65, x=-950, y=1700),
                 x=-800, y=1650)
    lip = g.math('MULTIPLY', lip, g.math('LESS_THAN', ax, 2.1, x=-950, y=1800), x=-650, y=1650)
    foam = g.math('MAXIMUM', foam, g.math('MULTIPLY', lip, 0.7, x=-500, y=1650), x=-400, y=800)
    # water body: deep, cold, blue-green; distant water is optically rougher (unresolved waves)
    rough = g.math('MULTIPLY_ADD', far, 0.16, 0.035, x=-800, y=-300)
    rough = g.mix(foam, rough, 0.75, dtype='FLOAT', x=-600, y=-300)
    body = (0.0045, 0.0165, 0.0185)
    base = g.mix(foam, body, (0.62, 0.66, 0.68), x=-600, y=300)
    bsdf = g.principled(-200, 0, base=base, rough=rough, ior=1.333, spec=0.5, normal=nrm,
                        sss=g.math('MULTIPLY', foam, 0.3, x=-600, y=0), sss_radius=(0.4, 0.6, 0.7), sss_scale=0.02)
    tr = g.new('ShaderNodeBsdfTransparent', 100, 300)
    ms = g.new('ShaderNodeMixShader', 400, 100)
    g.link(inside, ms.inputs[0])
    g.link(bsdf.outputs[0], ms.inputs[1])
    g.link(tr.outputs[0], ms.inputs[2])
    return finish(m, g, out, ms.outputs[0])


RIPPLE_SLOTS = 4


def ripple_slots(g, pos, t):
    """Shader-side oar marks. Each slot is (x, y, birth time) keyed by build_boat()."""
    px, py, pz = g.separate(pos, x=-3200, y=2200)
    rings = 0.0
    calm = 0.0
    for i in range(RIPPLE_SLOTS):
        yy = 2200 - i * 700
        for kind in ("ring", "puddle"):
            yk = yy if kind == "ring" else yy - 350
            X = g.value(0.0, -3200, yk, "%s%d_X" % (kind, i))
            X.name = "%s%d_X" % (kind, i)
            Y = g.value(0.0, -3200, yk - 60, "%s%d_Y" % (kind, i))
            Y.name = "%s%d_Y" % (kind, i)
            T = g.value(-100.0, -3200, yk - 120, "%s%d_T" % (kind, i))
            T.name = "%s%d_T" % (kind, i)
            r = g.vmath('LENGTH', g.combine(g.math('SUBTRACT', px, X.outputs[0], x=-3000, y=yk),
                                            g.math('SUBTRACT', py, Y.outputs[0], x=-3000, y=yk - 60), 0.0,
                                            x=-2850, y=yk), x=-2700, y=yk)
            age = g.math('SUBTRACT', t, T.outputs[0], x=-3000, y=yk - 150)
            alive = g.math('GREATER_THAN', age, 0.0, x=-2850, y=yk - 150)
            if kind == "ring":
                R = g.math('MULTIPLY_ADD', age, 0.5, 0.12, x=-2700, y=yk - 150)
                w = g.math('MULTIPLY_ADD', age, 0.16, 0.10, x=-2700, y=yk - 250)
                q = g.math('DIVIDE', g.math('SUBTRACT', r, R, x=-2550, y=yk), w, x=-2400, y=yk)
                env = g.math('EXPONENT', g.math('MULTIPLY', g.math('MULTIPLY', q, q, x=-2250, y=yk), -1.0,
                                                x=-2100, y=yk), x=-1950, y=yk)
                env = g.math('MULTIPLY', env, g.math('EXPONENT', g.math('MULTIPLY', age, -1.0 / 1.6, x=-2550, y=yk - 250),
                                                     x=-2400, y=yk - 250), x=-1800, y=yk)
                env = g.math('MULTIPLY', env, g.math('MULTIPLY', alive, g.mrange(age, 0.0, 0.08, x=-2550, y=yk - 350),
                                                     x=-2250, y=yk - 350), x=-1650, y=yk)
                wave = g.math('SINE', g.math('MULTIPLY', g.math('SUBTRACT', r, R, x=-2250, y=yk + 80), 2 * pi / 0.2,
                                             x=-2100, y=yk + 80), x=-1950, y=yk + 80)
                rings = g.math('MULTIPLY_ADD', env, wave, rings, x=-1500, y=yk)
            else:
                sp = g.math('EXPONENT', g.math('MULTIPLY', g.math('MULTIPLY', g.math('DIVIDE', r, 0.32, x=-2550, y=yk),
                                                                  g.math('DIVIDE', r, 0.32, x=-2550, y=yk - 60),
                                                                  x=-2400, y=yk), -1.0, x=-2250, y=yk), x=-2100, y=yk)
                sp = g.math('MULTIPLY', sp, g.math('EXPONENT', g.math('MULTIPLY', age, -1.0 / 3.0, x=-2550, y=yk - 250),
                                                   x=-2400, y=yk - 250), x=-1950, y=yk)
                sp = g.math('MULTIPLY', sp, alive, x=-1800, y=yk)
                calm = g.math('MAXIMUM', calm, sp, x=-1650, y=yk)
    return rings, calm


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
    MATS["piling"] = mat_piling()
    MATS["algae_hair"] = mat_algae_hair()
    MATS["wood_dark"] = mat_wood("M_Wood_Structure", 0.6, 0.45, 0.35, 0.6, 0.5, wet=0.4)
    MATS["rope"] = mat_rope()
    MATS["buoy"] = mat_buoy()
    MATS["kelp"] = mat_simple("M_Kelp", (0.035, 0.030, 0.008), 0.25, coat=0.6, noise_scale=8.0, bump=0.3)
    MATS["gull"] = mat_gull()
    MATS["lighthouse"] = mat_lighthouse()
    MATS["beam"] = mat_beam()
    build_boat_materials()


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


def focus_on_boat():
    """Pull focus with the boat as it rows away (falls back to the fixed focus distance)."""
    cam = OBJS.get("camera")
    body = OBJS.get("boat_body")
    if cam is not None and body is not None:
        tgt = new_empty("FocusTarget", coll("00_Camera"), (-0.4, 0.0, 0.8), 0.1, 'SPHERE', body)
        cam.data.dof.focus_object = tgt


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
#  GEOMETRY HELPERS  (the proven ones from the 3 AM harbour builder)
# ============================================================================
def smoothstep(e0, e1, x):
    if e1 == e0:
        return 1.0 if x >= e1 else 0.0
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return t * t * (3.0 - 2.0 * t)


def xform_verts(bm, verts, loc=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1)):
    M = (Matrix.Translation(Vector(loc)) @ Euler(rot).to_matrix().to_4x4()
         @ Matrix.Diagonal(Vector((scale[0], scale[1], scale[2], 1.0))))
    bmesh.ops.transform(bm, matrix=M, verts=verts)


def bm_box(bm, loc, size, rot=(0, 0, 0)):
    r = bmesh.ops.create_cube(bm, size=1.0)
    xform_verts(bm, r['verts'], loc, rot, size)
    return r['verts']


def bm_cyl(bm, loc, radius, depth, rot=(0, 0, 0), segs=16, r2=None, caps=True):
    r = bmesh.ops.create_cone(bm, cap_ends=caps, cap_tris=False, segments=segs,
                              radius1=radius, radius2=(radius if r2 is None else r2), depth=depth)
    xform_verts(bm, r['verts'], loc, rot)
    return r['verts']


def bm_sphere(bm, loc, radius, scale=(1, 1, 1), useg=16, vseg=10):
    r = bmesh.ops.create_uvsphere(bm, u_segments=useg, v_segments=vseg, radius=radius)
    xform_verts(bm, r['verts'], loc, (0, 0, 0), scale)
    return r['verts']


def bm_torus(bm, loc, R, r, rot=(0, 0, 0), seg=32, rseg=8, arc=2 * pi):
    rings, verts = [], []
    closed = arc >= 2 * pi - 1e-6
    n = seg if closed else seg + 1
    for i in range(n):
        a = arc * i / seg
        ring = []
        for j in range(rseg):
            b = 2 * pi * j / rseg
            ring.append(bm.verts.new(((R + r * cos(b)) * cos(a), (R + r * cos(b)) * sin(a), r * sin(b))))
        rings.append(ring)
        verts += ring
    for i in range(seg if closed else seg):
        ra, rb = rings[i], rings[(i + 1) % n]
        for j in range(rseg):
            j2 = (j + 1) % rseg
            bm.faces.new((ra[j], rb[j], rb[j2], ra[j2]))
    xform_verts(bm, verts, loc, rot)
    return verts


def bm_lathe(bm, profile, loc=(0, 0, 0), rot=(0, 0, 0), seg=24, cap_bottom=False, cap_top=False):
    """Surface of revolution. profile = [(radius, z), ...] bottom to top."""
    rings, verts = [], []
    for (rr, z) in profile:
        ring = [bm.verts.new((rr * cos(2 * pi * k / seg), rr * sin(2 * pi * k / seg), z)) for k in range(seg)]
        rings.append(ring)
        verts += ring
    for ra, rb in zip(rings[:-1], rings[1:]):
        for k in range(seg):
            k2 = (k + 1) % seg
            bm.faces.new((ra[k], ra[k2], rb[k2], rb[k]))
    if cap_bottom:
        bm.faces.new(list(reversed(rings[0])))
    if cap_top:
        bm.faces.new(rings[-1])
    xform_verts(bm, verts, loc, rot)
    return verts


def bm_tube(bm, pts, r, seg=6, cap=True, r_end=None):
    """Sweep a circle along a polyline (parallel-transport frame); optional taper to r_end."""
    pts = [Vector(p) for p in pts]
    rings = []
    a_prev = None
    n = len(pts)
    for i, p in enumerate(pts):
        if i == 0:
            t = pts[1] - pts[0]
        elif i == n - 1:
            t = pts[-1] - pts[-2]
        else:
            t = pts[i + 1] - pts[i - 1]
        t.normalize()
        if a_prev is None:
            a = t.orthogonal().normalized()
        else:
            a = (a_prev - t * a_prev.dot(t))
            a = a.normalized() if a.length > 1e-6 else t.orthogonal().normalized()
        b = t.cross(a).normalized()
        a_prev = a
        rr = r if r_end is None else r + (r_end - r) * i / max(1, n - 1)
        rings.append([bm.verts.new(p + (a * cos(2 * pi * k / seg) + b * sin(2 * pi * k / seg)) * rr)
                      for k in range(seg)])
    for ra, rb in zip(rings[:-1], rings[1:]):
        for k in range(seg):
            k2 = (k + 1) % seg
            bm.faces.new((ra[k], ra[k2], rb[k2], rb[k]))
    if cap:
        bm.faces.new(list(reversed(rings[0])))
        bm.faces.new(rings[-1])
    return rings


def obox(bm, p0, p1, up, width, thick):
    """Box running from p0 to p1; 'width' measured along 'up', 'thick' across."""
    p0 = Vector(p0)
    p1 = Vector(p1)
    a = p1 - p0
    L = a.length
    if L < 1e-6:
        return []
    a /= L
    side = a.cross(Vector(up))
    if side.length < 1e-6:
        side = a.orthogonal()
    side.normalize()
    up2 = side.cross(a).normalized()
    hs = side * (thick * 0.5)
    hu = up2 * (width * 0.5)
    pts = [p0 - hs - hu, p1 - hs - hu, p1 + hs - hu, p0 + hs - hu,
           p0 - hs + hu, p1 - hs + hu, p1 + hs + hu, p0 + hs + hu]
    vs = [bm.verts.new(p) for p in pts]
    A, B, C, D, E, F, G, H = vs
    for face in ((A, D, C, B), (E, F, G, H), (A, B, F, E), (B, C, G, F), (C, D, H, G), (D, A, E, H)):
        bm.faces.new(face)
    return vs


def make_curve(name, pts, c, radius, mat=None, parent=None, res=2):
    cu = bpy.data.curves.new("H5_" + name, 'CURVE')
    cu.dimensions = '3D'
    cu.bevel_depth = radius
    cu.bevel_resolution = res
    setp(cu, "use_fill_caps", True)
    sp = cu.splines.new('POLY')
    sp.points.add(len(pts) - 1)
    for p, co in zip(sp.points, pts):
        p.co = (co[0], co[1], co[2], 1.0)
    sp.use_smooth = True
    if mat:
        cu.materials.append(mat)
    return link_obj(name, cu, c, parent)


def sag_wire(p0, p1, sag, n=14):
    p0 = Vector(p0)
    p1 = Vector(p1)
    out = []
    for i in range(n + 1):
        s = i / n
        p = p0.lerp(p1, s)
        p.z -= sag * 4.0 * s * (1.0 - s)
        out.append(tuple(p))
    return out


def add_subsurf(ob, levels=1, render=2, adaptive=False, pixel=1.0):
    m = ob.modifiers.new("Subdivision", 'SUBSURF')
    m.levels = levels
    m.render_levels = render
    if adaptive and setp(m, "use_adaptive_subdivision", True):
        setp(m, "adaptive_space", 'PIXEL')
        setp(m, "adaptive_pixel_size", pixel)
    return m


def add_hair(ob, name, count, length, children=10, radius=0.0003, mat_name=None, vg=None, seed=0,
             normal=1.0, align=(0.0, 0.0, 0.0), rand=0.1, clump=0.2, rough=0.002, steps=3, tip=0.2,
             display=15, n_hint=(0.0, 0.0, 1.0)):
    mod = ob.modifiers.new("H5_" + name, 'PARTICLE_SYSTEM')
    ps = mod.particle_system
    st = ps.settings
    st.name = "H5_%s_%s" % (name, ob.name[3:])
    st.type = 'HAIR'
    st.count = count
    st.emit_from = 'FACE'
    setp(st, "use_emit_random", True)
    setp(st, "use_even_distribution", True)
    setp(st, "distribution", 'RAND')
    st.use_advanced_hair = True
    # hair length follows the emission velocity (length = 4 x velocity), so scale the whole mix
    v = Vector(n_hint) * normal + Vector(align)
    s = length / (4.0 * max(1e-6, v.length))
    st.normal_factor = normal * s
    st.object_align_factor = (align[0] * s, align[1] * s, align[2] * s)
    st.factor_random = rand * s
    st.hair_step = steps
    st.render_step = 3
    st.display_step = 2
    st.child_type = 'INTERPOLATED' if children else 'NONE'
    st.rendered_child_count = children
    setp(st, "child_percent", min(children, 2))
    st.clump_factor = clump
    setp(st, "roughness_1", rough)
    setp(st, "roughness_1_size", 0.01)
    setp(st, "roughness_endpoint", rough * 0.5)
    st.radius_scale = radius
    st.root_radius = 1.0
    st.tip_radius = tip
    st.display_percentage = display
    if mat_name:
        setp(st, "material_slot", mat_name)
    if vg:
        ps.vertex_group_density = vg
    ps.seed = seed
    return ps


def set_emission_sampling(m, mode):
    if not setp(getattr(m, "cycles", None), "emission_sampling", mode):
        setp(m, "emission_sampling", mode)


# ============================================================================
#  WEATHERED WOOD, MARINE GROWTH, METAL, ROPE
# ============================================================================
def ng_weathered_wood():
    """Weathered timber: fibres, growth lines, stains, checks, rot.
    Outputs Color / Roughness / Height (for bump) / Rot mask / Crack mask."""
    if "wood" in GROUPS:
        return GROUPS["wood"]
    ng, g, gi, go = new_tree(
        "NG_WeatheredWood", 'ShaderNodeTree',
        [("Vector", 'NodeSocketVector', None), ("Seed", 'NodeSocketFloat', 0.0),
         ("Weathering", 'NodeSocketFloat', 0.85), ("Rot Amount", 'NodeSocketFloat', 0.5),
         ("Crack Amount", 'NodeSocketFloat', 0.5)],
        [("Color", 'NodeSocketColor'), ("Roughness", 'NodeSocketFloat'), ("Height", 'NodeSocketFloat'),
         ("Rot", 'NodeSocketFloat'), ("Crack", 'NodeSocketFloat')])
    V = gi.outputs["Vector"]
    S = gi.outputs["Seed"]
    off = g.vmath('SCALE', (13.17, 7.71, 3.37), s=S, x=-1200, y=300)
    co = g.vmath('ADD', V, off, x=-1050, y=300)
    grain = g.mapping(co, scale=(0.55, 9.0, 9.0), x=-900, y=500)
    fib = g.noise(grain, scale=6.0, detail=10.0, rough=0.62, dist=0.25, x=-700, y=550)
    rings = g.wave(g.mapping(co, scale=(0.22, 1.0, 1.0), x=-900, y=250), scale=9.0, dist=7.0, detail=3.0,
                   dscale=1.2, droughness=0.6, direction='Y', x=-700, y=250)
    gv = g.mix(0.45, g.fac(fib), g.fac(rings), dtype='FLOAT', x=-500, y=400)
    grey = g.ramp(gv, [(0.0, (0.040, 0.036, 0.031)), (0.42, (0.098, 0.090, 0.078)),
                       (0.72, (0.150, 0.142, 0.125)), (1.0, (0.205, 0.192, 0.170))], x=-300, y=650)
    brown = g.ramp(gv, [(0.0, (0.035, 0.020, 0.011)), (0.5, (0.090, 0.058, 0.032)),
                        (1.0, (0.160, 0.110, 0.062))], x=-300, y=400)
    jit = g.math('FRACT', g.math('MULTIPLY', S, 7.31, x=-700, y=0), x=-550, y=0)
    wv = g.math('MULTIPLY_ADD', g.math('SUBTRACT', jit, 0.5, x=-400, y=0), 0.35,
                gi.outputs["Weathering"], clamp=True, x=-250, y=0)
    col = g.mix(wv, brown.outputs[0], grey.outputs[0], x=0, y=500)
    st = g.noise(co, scale=1.4, detail=4.0, rough=0.55, x=-700, y=-150)
    stain = g.mrange(g.fac(st), 0.5, 0.72, x=-500, y=-150)
    col = g.mix(g.math('MULTIPLY', stain, 0.6, x=-300, y=-150), col, (0.012, 0.011, 0.009), x=200, y=450)
    crv = g.voronoi(g.mapping(co, scale=(0.09, 2.6, 2.6), x=-900, y=-350), scale=2.2,
                    feature='DISTANCE_TO_EDGE', x=-700, y=-350)
    crk = g.mrange(g.o(crv, "Distance"), 0.0, 0.035, 1.0, 0.0, x=-500, y=-350)
    cpres = g.mrange(g.fac(g.noise(co, scale=2.5, detail=2.0, x=-700, y=-550)), 0.42, 0.6, x=-500, y=-550)
    crack = g.math('MULTIPLY', g.math('MULTIPLY', crk, cpres, x=-300, y=-400),
                   g.math('MULTIPLY', gi.outputs["Crack Amount"], 2.0, x=-300, y=-550), clamp=True, x=-100, y=-450)
    col = g.mix(g.math('MULTIPLY', crack, 0.85, x=100, y=-450), col, (0.006, 0.005, 0.004), x=400, y=400)
    rn = g.noise(co, scale=0.9, detail=6.0, rough=0.6, x=-700, y=-750)
    t0 = g.math('MULTIPLY_ADD', gi.outputs["Rot Amount"], -0.2, 0.74, x=-500, y=-850)
    t1 = g.math('ADD', t0, 0.06, x=-350, y=-850)
    rot = g.mrange(g.fac(rn), t0, t1, 0.0, 1.0, interp='SMOOTHSTEP', x=-200, y=-750)
    rotcol = g.mix(g.fac(fib), (0.016, 0.012, 0.008), (0.045, 0.030, 0.018), x=0, y=-750)
    col = g.mix(rot, col, rotcol, x=600, y=350)
    h = g.math('MULTIPLY', g.fac(fib), 0.35, x=200, y=-100)
    h = g.math('MULTIPLY_ADD', g.fac(rings), 0.25, h, x=350, y=-100)
    h = g.math('MULTIPLY_ADD', crack, -1.0, h, x=500, y=-100)
    h = g.math('MULTIPLY_ADD', rot, -0.6, h, x=650, y=-100)
    rough = g.mrange(gv, 0.0, 1.0, 0.62, 0.82, x=200, y=150)
    rough = g.mix(rot, rough, 0.95, dtype='FLOAT', x=400, y=150)
    rough = g.mix(crack, rough, 0.9, dtype='FLOAT', x=600, y=150)
    g.link(col, go.inputs["Color"])
    g.link(rough, go.inputs["Roughness"])
    g.link(h, go.inputs["Height"])
    g.link(rot, go.inputs["Rot"])
    g.link(crack, go.inputs["Crack"])
    GROUPS["wood"] = ng
    return ng


def wood_node(g, vec, seed, weathering=0.85, rot=0.5, crack=0.5, x=-1200, y=200):
    w = g.new('ShaderNodeGroup', x, y)
    w.node_tree = ng_weathered_wood()
    g.feed(w.inputs["Vector"], vec)
    g.feed(w.inputs["Seed"], seed)
    w.inputs["Weathering"].default_value = weathering
    w.inputs["Rot Amount"].default_value = rot
    w.inputs["Crack Amount"].default_value = crack
    return w


def mat_wood(name, weathering=0.9, darken=1.0, coat=0.25, rot=0.4, crack=0.5, axis='X', wet=0.0):
    """Generic weathered timber on object coordinates (beams, thwarts, oars, masts)."""
    m, g, out = new_mat(name)
    tc = g.new('ShaderNodeTexCoord', -1700, 0)
    co = tc.outputs["Object"]
    if axis == 'Z':
        co = g.mapping(co, rot=(0.0, radians(90.0), 0.0), x=-1500, y=0)
    elif axis == 'Y':
        co = g.mapping(co, rot=(0.0, 0.0, radians(90.0)), x=-1500, y=0)
    oi = g.new('ShaderNodeObjectInfo', -1700, -300)
    seed = g.math('MULTIPLY', g.o(oi, "Random"), 100.0, x=-1500, y=-300)
    w = wood_node(g, co, seed, weathering, rot, crack, x=-1200, y=0)
    col = w.outputs["Color"]
    if darken != 1.0:
        col = g.mix(1.0, col, (darken, darken, darken), blend='MULTIPLY', x=-900, y=100)
    if wet > 0.0:
        col = g.mix(wet, col, (0.5, 0.5, 0.5), blend='MULTIPLY', x=-750, y=100)
    nrm = g.bump(w.outputs["Height"], strength=0.5, distance=0.003, x=-900, y=-250)
    bsdf = g.principled(-500, 0, base=col, rough=w.outputs["Roughness"], normal=nrm,
                        coat=coat, coat_rough=0.12, coat_ior=1.33)
    return finish(m, g, out, bsdf.outputs[0])


def mat_piling():
    """Rotten marine post (reference photo): grey cracked top with rust-orange rot, then moss, a thick
    skirt of bright green weed at the tide line, barnacle crust and black slime under water."""
    m, g, out = new_mat("M_Piling_Marine")
    tc = g.new('ShaderNodeTexCoord', -2800, 0)
    obj = tc.outputs["Object"]
    gen = tc.outputs["Generated"]
    oi = g.new('ShaderNodeObjectInfo', -2800, -300)
    seed = g.math('MULTIPLY', g.o(oi, "Random"), 100.0, x=-2600, y=-300)
    grain = g.mapping(obj, rot=(0.0, radians(90.0), 0.0), x=-2600, y=0)     # grain along Z
    wood = wood_node(g, grain, seed, 0.9, 0.55, 0.9, x=-2300, y=300)
    geo = g.new('ShaderNodeNewGeometry', -2800, -600)
    zw = g.separate(geo.outputs["Position"], x=-2600, y=-600)[2]
    zn = g.noise(obj, scale=3.0, detail=4.0, x=-2600, y=-800)
    zz = g.math('ADD', zw, g.math('MULTIPLY', g.math('SUBTRACT', g.fac(zn), 0.5, x=-2400, y=-800), 0.35,
                                  x=-2250, y=-800), x=-2100, y=-700)
    wetband = g.mrange(zz, 1.25, 0.7, x=-1900, y=-500)
    moss_z = g.math('MULTIPLY', g.mrange(zz, 1.8, 1.2, x=-1900, y=-420), g.mrange(zz, 0.4, 0.8, x=-1900, y=-460),
                    x=-1700, y=-440)
    mpatch = g.mrange(g.fac(g.noise(obj, scale=4.0, detail=6.0, x=-2100, y=-380)), 0.4, 0.62, x=-1900, y=-380)
    moss = g.math('MULTIPLY', moss_z, mpatch, clamp=True, x=-1500, y=-420)
    algae_z = g.math('MULTIPLY', g.mrange(zz, -0.55, -0.2, x=-1900, y=-900), g.mrange(zz, 1.05, 0.55, x=-1900, y=-1000),
                     x=-1700, y=-950)
    apatch = g.mrange(g.fac(g.noise(obj, scale=6.0, detail=5.0, x=-2100, y=-1100)), 0.22, 0.42, x=-1900, y=-1100)
    algae = g.math('MULTIPLY', algae_z, apatch, clamp=True, x=-1500, y=-1000)
    barn_z = g.math('MULTIPLY', g.mrange(zz, -0.8, -0.45, x=-1900, y=-1250), g.mrange(zz, 0.3, 0.02, x=-1900, y=-1350),
                    x=-1700, y=-1300)
    slime = g.mrange(zz, -0.15, -0.6, x=-1900, y=-1450)

    def barnacles(scale, x, y):
        v = g.voronoi(obj, scale=scale, feature='F1', x=x, y=y)
        rgb = g.separate(g.o(v, "Color"), x=x + 200, y=y - 150)
        present = g.mrange(rgb[0], 0.30, 0.36, x=x + 400, y=y - 150)
        rad = g.math('MULTIPLY_ADD', rgb[1], 0.35, 0.35, x=x + 400, y=y)
        dn = g.math('DIVIDE', g.o(v, "Distance"), rad, x=x + 600, y=y)
        cone = g.math('POWER', g.mrange(dn, 1.0, 0.0, x=x + 800, y=y + 100), 0.6, x=x + 1000, y=y)
        crater = g.mrange(dn, 0.32, 0.16, x=x + 800, y=y - 150)
        h = g.math('MULTIPLY', g.math('MULTIPLY_ADD', crater, -0.8, cone, x=x + 1200, y=y), present,
                   clamp=True, x=x + 1400, y=y)
        return h, crater, rgb[2]

    bh1, cr1, shade1 = barnacles(55.0, -2800, -1700)
    bh2, cr2, shade2 = barnacles(120.0, -2800, -2100)
    bcl = g.mrange(g.fac(g.noise(obj, scale=5.0, detail=3.0, x=-1600, y=-2300)), 0.38, 0.6, x=-1400, y=-2300)
    bmask = g.math('MULTIPLY', barn_z, bcl, clamp=True, x=-1200, y=-2200)
    bh = g.math('MULTIPLY', g.math('MAXIMUM', bh1, g.math('MULTIPLY', bh2, 0.6, x=-1200, y=-2000), x=-1050, y=-1900),
                bmask, x=-900, y=-1900)
    bvis = g.mrange(bh, 0.02, 0.1, x=-750, y=-1900)
    bshell = g.mix(shade1, (0.20, 0.19, 0.17), (0.36, 0.35, 0.31), x=-900, y=-2100)
    bcol = g.mix(g.math('MAXIMUM', cr1, cr2, x=-900, y=-2250), bshell, (0.02, 0.02, 0.018), x=-700, y=-2150)
    acn = g.noise(obj, scale=30.0, detail=4.0, x=-1500, y=-800)
    acol = g.ramp(g.fac(acn), [(0.2, (0.045, 0.110, 0.008)), (0.55, (0.110, 0.240, 0.015)),
                               (0.85, (0.200, 0.340, 0.025))], x=-1300, y=-800)            # Ulva: vivid green
    mcol = g.ramp(g.fac(acn), [(0.2, (0.045, 0.080, 0.012)), (0.7, (0.110, 0.180, 0.025))], x=-1300, y=-600)
    ah = g.fac(g.noise(g.mapping(obj, scale=(1.0, 1.0, 0.12), x=-1700, y=-1250), scale=45.0, detail=3.0,
                       x=-1500, y=-1250))
    gz = g.separate(gen, x=-2600, y=-2500)[2]
    top = g.mrange(gz, 0.85, 0.98, x=-2400, y=-2500)
    gs = g.fac(g.noise(g.mapping(obj, scale=(8.0, 8.0, 0.6), x=-2600, y=-2700), scale=3.0, detail=2.0,
                       x=-2400, y=-2700))
    guano = g.math('MULTIPLY', top, g.mrange(gs, 0.55, 0.62, x=-2200, y=-2700), clamp=True, x=-2000, y=-2600)
    # rust-orange rot streaks on the dry wood (as in the reference)
    rs = g.noise(g.mapping(obj, scale=(6.0, 6.0, 0.8), x=-2100, y=-2900), scale=2.0, detail=5.0, x=-1900, y=-2900)
    rust = g.math('MULTIPLY', g.mrange(g.fac(rs), 0.58, 0.7, x=-1700, y=-2900), g.mrange(zz, 0.7, 1.2, x=-1700, y=-3000),
                  x=-1500, y=-2950)
    col = wood.outputs["Color"]
    col = g.mix(g.math('MULTIPLY', rust, 0.8, x=-700, y=500), col, (0.20, 0.075, 0.025), x=-550, y=450)
    col = g.mix(g.math('MULTIPLY', wetband, 0.75, x=-700, y=300), col, (0.45, 0.45, 0.45), blend='MULTIPLY',
                x=-500, y=300)
    col = g.mix(slime, col, (0.012, 0.016, 0.008), x=-200, y=200)
    col = g.mix(moss, col, mcol.outputs[0], x=-120, y=180)
    col = g.mix(algae, col, acol.outputs[0], x=-50, y=150)
    col = g.mix(bvis, col, bcol, x=100, y=100)
    col = g.mix(guano, col, (0.50, 0.48, 0.44), x=250, y=50)
    r = wood.outputs["Roughness"]
    r = g.mix(wetband, r, 0.35, dtype='FLOAT', x=-500, y=-100)
    r = g.mix(slime, r, 0.15, dtype='FLOAT', x=-350, y=-100)
    r = g.mix(algae, r, 0.22, dtype='FLOAT', x=-200, y=-100)
    r = g.mix(bvis, r, 0.55, dtype='FLOAT', x=-50, y=-100)
    r = g.mix(guano, r, 0.7, dtype='FLOAT', x=100, y=-100)
    coat = g.math('MULTIPLY_ADD', wetband, 0.55, g.math('MULTIPLY', algae, 0.5, x=-500, y=-300), clamp=True,
                  x=-350, y=-300)
    h = g.math('MULTIPLY', wood.outputs["Height"], 0.3, x=-500, y=-500)
    h = g.math('MULTIPLY_ADD', g.math('MULTIPLY', g.math('MAXIMUM', algae, moss, x=-650, y=-650), ah, x=-500, y=-650),
               0.5, h, x=-350, y=-550)
    h = g.math('ADD', h, bh, x=-200, y=-550)
    disp = g.new('ShaderNodeDisplacement', 900, -500)
    g.feed(disp.inputs["Height"], h)
    disp.inputs["Midlevel"].default_value = 0.0
    disp.inputs["Scale"].default_value = 0.014
    nrm = g.bump(g.math('MULTIPLY', ah, g.math('MAXIMUM', algae, moss, x=-350, y=-800), x=-200, y=-750),
                 strength=0.35, distance=0.003, x=0, y=-750)
    bsdf = g.principled(500, 0, base=col, rough=r, coat=coat, coat_rough=0.06, coat_ior=1.33, normal=nrm,
                        sss=g.math('MULTIPLY', algae, 0.25, x=200, y=-900), sss_radius=(0.2, 0.5, 0.1),
                        sss_scale=0.004, sheen=g.math('MULTIPLY', moss, 0.5, x=200, y=-1050), sheen_rough=0.35)
    return finish(m, g, out, bsdf.outputs[0], disp.outputs[0], method='BOTH')


def _hair_mat(name, stops, trans=0.3, rough=0.5, root_dark=0.35):
    m, g, out = new_mat(name)
    hi = g.new('ShaderNodeHairInfo', -1100, 0)
    col = g.ramp(g.o(hi, "Random"), stops, x=-900, y=0)
    root = g.mrange(g.o(hi, "Intercept"), 0.0, 0.7, root_dark, 1.0, x=-900, y=-300)
    col2 = g.mix(1.0, col.outputs[0], root, blend='MULTIPLY', x=-650, y=0)
    pb = g.principled(-400, 150, base=col2, rough=rough, sheen=0.25, coat=0.4, coat_rough=0.1)
    tr = g.new('ShaderNodeBsdfTranslucent', -400, -250)
    g.feed(tr.inputs["Color"], col2)
    ms = g.new('ShaderNodeMixShader', -100, 0)
    ms.inputs[0].default_value = trans
    g.link(pb.outputs[0], ms.inputs[1])
    g.link(tr.outputs[0], ms.inputs[2])
    return finish(m, g, out, ms.outputs[0])


def mat_algae_hair():
    return _hair_mat("M_AlgaeHair", [(0.0, (0.030, 0.080, 0.008)), (0.5, (0.070, 0.170, 0.012)),
                                     (1.0, (0.140, 0.250, 0.020))], trans=0.4, rough=0.22, root_dark=0.5)


def mat_simple(name, col, rough=0.5, metal=0.0, coat=0.0, sheen=0.0, noise_scale=0.0, bump=0.0):
    m, g, out = new_mat(name)
    nrm = None
    base = col
    if noise_scale > 0.0:
        tc = g.new('ShaderNodeTexCoord', -1200, 0)
        n = g.noise(tc.outputs["Object"], scale=noise_scale, detail=6.0, rough=0.6, x=-1000, y=0)
        base = g.mix(g.mrange(g.fac(n), 0.3, 0.7, 0.0, 0.5, x=-800, y=100), col,
                     tuple(c * 0.55 for c in col), x=-600, y=100)
        if bump > 0.0:
            nrm = g.bump(g.fac(n), strength=bump, distance=0.004, x=-600, y=-200)
    bsdf = g.principled(-300, 0, base=base, rough=rough, metal=metal, coat=coat, coat_rough=0.15,
                        sheen=sheen, normal=nrm)
    return finish(m, g, out, bsdf.outputs[0])


def mat_rust():
    m, g, out = new_mat("M_RustMetal")
    tc = g.new('ShaderNodeTexCoord', -1200, 0)
    n = g.noise(tc.outputs["Object"], scale=9.0, detail=10.0, rough=0.7, x=-1000, y=0)
    col = g.ramp(g.fac(n), [(0.3, (0.030, 0.014, 0.008)), (0.6, (0.110, 0.045, 0.018)),
                            (0.85, (0.160, 0.075, 0.030))], x=-800, y=0)
    nrm = g.bump(g.fac(n), strength=0.6, distance=0.004, x=-800, y=-300)
    bsdf = g.principled(-400, 0, base=col.outputs[0], rough=0.82, metal=0.25, normal=nrm, coat=0.15, coat_rough=0.3)
    return finish(m, g, out, bsdf.outputs[0])


def mat_bronze():
    m, g, out = new_mat("M_Bronze_Patina")
    tc = g.new('ShaderNodeTexCoord', -1200, 0)
    n = g.noise(tc.outputs["Object"], scale=40.0, detail=8.0, rough=0.6, x=-1000, y=0)
    pat = g.mrange(g.fac(n), 0.45, 0.65, x=-800, y=0)
    col = g.mix(pat, (0.30, 0.20, 0.09), (0.10, 0.20, 0.15), x=-600, y=0)
    bsdf = g.principled(-300, 0, base=col, metal=g.math('SUBTRACT', 1.0, pat, x=-600, y=-200),
                        rough=g.mix(pat, 0.35, 0.8, dtype='FLOAT', x=-600, y=-350))
    return finish(m, g, out, bsdf.outputs[0])


def mat_rope():
    m, g, out = new_mat("M_Rope")
    tc = g.new('ShaderNodeTexCoord', -1200, 0)
    w = g.wave(tc.outputs["Object"], scale=40.0, dist=1.0, detail=2.0, direction='DIAGONAL', x=-1000, y=0)
    nrm = g.bump(g.fac(w), strength=0.8, distance=0.003, x=-800, y=-200)
    col = g.mix(g.fac(w), (0.10, 0.085, 0.06), (0.20, 0.17, 0.12), x=-800, y=100)
    bsdf = g.principled(-400, 0, base=col, rough=0.9, normal=nrm, sheen=0.4, coat=0.2, coat_rough=0.3)
    return finish(m, g, out, bsdf.outputs[0])


def mat_buoy():
    m, g, out = new_mat("M_Buoy")
    tc = g.new('ShaderNodeTexCoord', -1200, 0)
    z = g.separate(tc.outputs["Object"], x=-1000, y=0)[2]
    band = g.math('GREATER_THAN', g.math('ABSOLUTE', z, x=-850, y=0), 0.06, x=-700, y=0)
    col = g.mix(band, (0.30, 0.29, 0.26), (0.34, 0.07, 0.02), x=-500, y=0)
    dn = g.noise(tc.outputs["Object"], scale=8.0, detail=6.0, x=-800, y=-250)
    col = g.mix(g.mrange(g.fac(dn), 0.5, 0.75, 0.0, 0.7, x=-600, y=-250), col, (0.03, 0.03, 0.02), x=-350, y=-50)
    green = g.mrange(z, -0.02, -0.12, x=-600, y=-400)                # weed on its wet underside
    col = g.mix(green, col, (0.04, 0.09, 0.01), x=-200, y=-100)
    bsdf = g.principled(-150, 0, base=col, rough=0.5, coat=0.3, coat_rough=0.15)
    return finish(m, g, out, bsdf.outputs[0])


def mat_lamp_paint():
    m, g, out = new_mat("M_Lantern_Paint")
    tc = g.new('ShaderNodeTexCoord', -1400, 0)
    co = tc.outputs["Object"]
    chip = g.mrange(g.fac(g.noise(co, scale=30.0, detail=8.0, x=-1200, y=0)), 0.62, 0.66, x=-1000, y=0)
    rust = g.mrange(g.fac(g.noise(co, scale=12.0, detail=6.0, x=-1200, y=-250)), 0.55, 0.7, x=-1000, y=-250)
    col = g.mix(chip, (0.22, 0.020, 0.012), (0.50, 0.50, 0.48), x=-800, y=0)
    col = g.mix(g.math('MULTIPLY', rust, 0.8, x=-800, y=-250), col, (0.12, 0.045, 0.02), x=-600, y=0)
    metal = g.math('MULTIPLY', chip, g.math('SUBTRACT', 1.0, rust, x=-800, y=-400), x=-600, y=-350)
    rough = g.mix(rust, g.mix(chip, 0.4, 0.35, dtype='FLOAT', x=-800, y=-550), 0.8, dtype='FLOAT', x=-600, y=-500)
    bsdf = g.principled(-300, 0, base=col, metal=metal, rough=rough)
    return finish(m, g, out, bsdf.outputs[0])


def mat_lamp_glass():
    m, g, out = new_mat("M_Lantern_Glass")
    tc = g.new('ShaderNodeTexCoord', -1200, 0)
    z = g.separate(tc.outputs["Object"], x=-1000, y=0)[2]
    soot = g.mrange(z, 0.16, 0.215, 0.0, 0.85, x=-800, y=0)
    col = g.mix(soot, (0.95, 0.95, 0.93), (0.05, 0.04, 0.03), x=-600, y=0)
    tr = g.math('SUBTRACT', 1.0, g.math('MULTIPLY', soot, 0.8, x=-800, y=-200), x=-600, y=-200)
    rg = g.math('MULTIPLY_ADD', soot, 0.25, 0.02, x=-600, y=-350)
    bsdf = g.principled(-300, 0, base=col, trans=tr, rough=rg, ior=1.5)
    return finish(m, g, out, bsdf.outputs[0])


def mat_flame():
    m, g, out = new_mat("M_Lantern_Flame")
    tc = g.new('ShaderNodeTexCoord', -1200, 0)
    z = g.separate(tc.outputs["Generated"], x=-1000, y=0)[2]
    prof = g.math('MULTIPLY', g.mrange(z, 0.0, 0.25, 0.2, 1.0, x=-800, y=100),
                  g.mrange(z, 1.0, 0.55, 0.0, 1.0, x=-800, y=-50), x=-600, y=0)
    bb = g.new('ShaderNodeBlackbody', -800, -250)
    bb.inputs["Temperature"].default_value = 1750.0
    em = g.new('ShaderNodeEmission', -300, 0)
    g.link(bb.outputs[0], em.inputs["Color"])
    g.feed(em.inputs["Strength"], g.math('MULTIPLY', prof, 45.0, x=-450, y=-100))
    set_emission_sampling(m, 'NONE')
    return finish(m, g, out, em.outputs[0])


# ============================================================================
#  THE OLD JETTY: rotten posts with weed skirts, broken beams, rope, buoy
# ============================================================================
def make_piling(name, x, y, z0, z1, r, rng, lean, erosion, c, mats):
    """Eroded post: tidal-zone necking, pits, grain splits, a rotten ragged top."""
    bm = bmesh.new()
    segs = 22
    zs, z = [], z0
    while z < z1 - 0.2:
        zs.append(z)
        z += 0.06 if -0.8 < z < 1.2 else 0.2
    zs.append(z1)
    sd = rng.uniform(0, 100)
    rings = []
    for zi in zs:
        ring = []
        for k in range(segs):
            a = 2 * pi * k / segs
            ca, sa = cos(a), sin(a)
            neck = erosion * 0.24 * exp(-((zi - 0.05) / 0.45) ** 2)
            p = Vector((ca * 1.3 + sd, sa * 1.3, zi * 1.1))
            pit = noise.noise(p * 3.0) * 0.07 * (0.3 + erosion * exp(-((zi - 0.1) / 0.7) ** 2))
            crk = abs(noise.noise(Vector((ca * 4.0 + sd, sa * 4.0, zi * 0.35))))
            groove = -0.07 * (1.0 - smoothstep(0.0, 0.12, crk))
            rr = r * max(0.35, 1.0 - neck + pit + groove)
            ring.append(bm.verts.new((ca * rr, sa * rr, zi)))
        rings.append(ring)
    for k, v in enumerate(rings[-1]):
        v.co.z -= abs(noise.noise(Vector((k * 0.37 + sd, 0.0, 0.0)))) * 0.16 + rng.uniform(0.0, 0.04)
    for ra, rb in zip(rings[:-1], rings[1:]):
        for k in range(segs):
            k2 = (k + 1) % segs
            bm.faces.new((ra[k], ra[k2], rb[k2], rb[k]))
    bm.faces.new(list(reversed(rings[0])))
    tf = bm.faces.new(rings[-1])
    try:
        bmesh.ops.inset_region(bm, faces=[tf], thickness=r * 0.3, depth=-0.03)
    except Exception as e:
        log("inset", e)
    for f in bm.faces:
        f.smooth = True
    me = bpy.data.meshes.new("H5_" + name)
    bm.to_mesh(me)
    bm.free()
    for m in mats:
        me.materials.append(m)
    ob = link_obj(name, me, c)
    ob.location = (x, y, 0.0)
    ob.rotation_euler = (lean[0], lean[1], rng.uniform(0, 2 * pi))
    return ob


def add_weed(ob, rng, count, long=0.11):
    """Green weed skirt hanging from the tide line (Ulva / Enteromorpha strands)."""
    vg = ob.vertex_groups.new(name="weed")
    for v in ob.data.vertices:
        z = v.co.z
        w = smoothstep(-0.45, -0.1, z) * (1.0 - smoothstep(0.55, 0.95, z))
        w *= 0.45 + 0.55 * noise.noise(v.co * 4.0 + Vector((rng.random() * 9, 0, 0)))
        if w > 0.02:
            vg.add([v.index], min(1.0, w), 'REPLACE')
    add_hair(ob, "Weed", count=count, length=long, children=12, radius=0.0007,
             mat_name=MATS["algae_hair"].name, vg="weed", seed=rng.randint(0, 9999),
             normal=0.22, align=(0.0, 0.0, -0.09), rand=0.08, clump=0.55, rough=0.003, steps=5, tip=0.15,
             display=4, n_hint=(1.0, 0.0, 0.0))


def jetty_specs():
    """Two rows of posts running from the left foreground out into the fog."""
    rng = random.Random(P["seed"] + 31)
    (x0, y0), (x1, y1) = P["jetty_from"], P["jetty_to"]
    d = Vector((x1 - x0, y1 - y0, 0.0))
    L = d.length
    d.normalize()
    side = Vector((d.y, -d.x, 0.0))
    specs = []
    t = 0.0
    k = 0
    while t < L:
        for row in (-1.0, 1.0):
            if rng.random() < 0.22 and t > 6.0:
                continue                                                     # long gone
            p = Vector((x0, y0, 0.0)) + d * (t + rng.uniform(-0.25, 0.25)) + side * (row * 1.25
                                                                                   + rng.uniform(-0.12, 0.12))
            near = 1.0 - smoothstep(0.0, 30.0, t)
            top = rng.uniform(0.9, 2.3) if rng.random() > 0.25 else rng.uniform(0.25, 0.8)
            top += 0.5 * near
            lean = (rng.gauss(0, 0.05) + (rng.random() < 0.15) * rng.choice((-1, 1)) * rng.uniform(0.08, 0.2),
                    rng.gauss(0, 0.05))
            specs.append(("Post_%02d" % k, p.x, p.y, top, rng.uniform(0.13, 0.19), lean, 1.0 + 0.3 * rng.random(),
                          near, t))
            k += 1
        t += rng.uniform(2.6, 3.3)
    return specs, d, side


def build_jetty():
    if not P["jetty"]:
        return
    c = coll("30_Jetty")
    rng = random.Random(P["seed"] + 32)
    specs, d, side = jetty_specs()
    posts = []
    for (nm, x, y, top, r, lean, ero, near, t) in specs:
        ob = make_piling(nm, x, y, -2.6, top, r, rng, lean, ero, c, [MATS["piling"], MATS["algae_hair"]])
        add_weed(ob, rng, int(500 + 1600 * near), long=0.09 + 0.05 * near)   # (particles before subdivision)
        add_subsurf(ob, levels=0, render=2, adaptive=True, pixel=1.5)
        posts.append((ob, top, t))
    OBJS["posts"] = posts
    # remnants of the stringers: a few beams still bolted across, one fallen half into the sea
    bm = bmesh.new()
    pairs = {}
    for ob, top, t in posts:
        pairs.setdefault(round(t / 3.0), []).append((ob, top))
    for key, pr in sorted(pairs.items()):
        if len(pr) != 2 or rng.random() < 0.45 or key * 3.0 < 9.0:
            continue
        (a, ta), (b, tb) = pr
        z = min(ta, tb) - rng.uniform(0.15, 0.35)
        if z < 0.35:
            continue
        pa, pb = a.location.copy(), b.location.copy()
        pa.z = pb.z = z
        if rng.random() < 0.3:
            pb.z = rng.uniform(-0.2, 0.2)                                    # one end dropped into the water
        obox(bm, pa, pb, (0, 0, 1), 0.2, 0.1)
        for p in (pa, pb):
            bm_cyl(bm, p, 0.012, 0.3, rot=(0.0, radians(90.0), atan2(d.y, d.x) + pi / 2), segs=8)
    beams = bm_to_obj("Jetty_Beams", bm, c, MATS["wood_dark"])
    add_subsurf(beams, levels=0, render=1)
    # rope from a near post, sagging down to a buoy moored in the swell
    if len(posts) > 3:
        ob, top, t = posts[1]
        a = ob.location + Vector((0.0, 0.0, min(top, 1.0) - 0.15))
        buoy_p = ob.location + side * 2.2 + d * 1.2
        bmb = bmesh.new()
        bm_sphere(bmb, (0, 0, 0), 0.22, scale=(1.0, 1.0, 1.1), useg=24, vseg=14)
        bm_torus(bmb, (0, 0, 0.26), 0.035, 0.008, rot=(radians(90), 0, 0), seg=16, rseg=6)
        buoy = bm_to_obj("MooringBuoy", bmb, c, MATS["buoy"], smooth=True)
        buoy.location = (buoy_p.x, buoy_p.y, 0.05)
        float_on_ocean(buoy)
        rope = make_curve("MooringRope", sag_wire(a, buoy.location + Vector((0, 0, 0.25)), 0.6, 18), c, 0.012,
                          MATS["rope"])
        hk = rope.modifiers.new("Hook", 'HOOK')
        hk.object = buoy
        # hook the last few points to the buoy (it bobs, the rope follows)
        try:
            sp = rope.data.splines[0]
            n = len(sp.points)
            hk.vertex_indices_set([n - 1, n - 2])
            hk.falloff_type = 'NONE'
            hk.center = buoy.location
            hk.matrix_inverse = buoy.matrix_world.inverted()
        except Exception as e:
            log("hook", e)


def float_on_ocean(ob):
    """Keep an object riding the displaced sea surface (Shrinkwrap along world Z onto the ocean)."""
    oc = OBJS.get("ocean")
    if oc is None:
        return
    cn = ob.constraints.new('SHRINKWRAP')
    cn.target = oc
    cn.shrinkwrap_type = 'PROJECT'
    setp(cn, "project_axis", 'POS_Z')
    setp(cn, "project_axis_space", 'WORLD')
    setp(cn, "use_project_opposite", True)
    setp(cn, "cull_face", 'OFF')
    return cn


# ============================================================================
#  THE OLD PIER AND THE RED FISH HOUSE  (reused from the 3 AM harbour builder)
# ============================================================================
def harbour_script_path():
    cands = []
    if bpy.data.filepath:
        cands.append(os.path.join(os.path.dirname(bpy.data.filepath), "build_3am_harbor.py"))
    here = globals().get("__file__", "")
    if here and os.path.isabs(here):
        cands.append(os.path.join(os.path.dirname(here), "build_3am_harbor.py"))
    for p_ in cands:
        if os.path.isfile(p_):
            return p_
    return None


def harbour_to_world(local):
    """3 AM pier space (x across, +y out to sea, deck at z 0.8) -> this shot's world space."""
    (tx, ty), rot = P["pier_offset"], radians(P["pier_rotation"])
    x, y = local[0], local[1]
    return Vector((tx + x * cos(rot) - y * sin(rot), ty + x * sin(rot) + y * cos(rot),
                   local[2] if len(local) > 2 else 0.0))


def build_harbour():
    """The rotting plank pier with moss in the cracks, its support piles and fender posts, the abandoned
    red clapboard fish house with peeling paint, barrel chimney, mast and wires, crates, barrel, rope and
    buoy - built by the 3 AM builder's own code, renamed H5H_ and set down on the left of this shot."""
    if not P["pier"]:
        return
    path = harbour_script_path()
    if not path:
        log("pier: build_3am_harbor.py not found next to the script - skipped")
        return
    src = open(path).read().replace('if __name__ == "__main__":\n    build_all()', '')
    ns = {"__name__": "harbour3am", "__file__": path}
    exec(compile(src, path, "exec"), ns)
    ns["SC"] = SC
    kinds = (bpy.data.objects, bpy.data.meshes, bpy.data.curves, bpy.data.materials, bpy.data.node_groups,
             bpy.data.particles, bpy.data.collections, bpy.data.textures)
    before = {id(k): set(k.keys()) for k in kinds}
    M = ns["MATS"]
    M["deck"] = ns["mat_deck"]()
    M["wood_trim"] = ns["mat_wood"]("M_Wood_Trim", 0.95, 1.0, 0.25, 0.35, 0.6)
    M["wood_dark"] = ns["mat_wood"]("M_Wood_Structure", 0.6, 0.45, 0.35, 0.6, 0.5)
    M["wood_mast"] = ns["mat_wood"]("M_Wood_Mast", 0.9, 0.8, 0.2, 0.3, 0.7, axis='Z')
    for k, fn in (("moss_bed", "mat_moss_bed"), ("moss_hair", "mat_moss_hair"), ("algae_hair", "mat_algae_hair"),
                  ("leaf", "mat_leaf"), ("piling", "mat_piling"), ("paint", "mat_paint"), ("roof", "mat_roof"),
                  ("glass", "mat_glass_grimy"), ("rust", "mat_rust"), ("rope", "mat_rope"), ("buoy", "mat_buoy")):
        M[k] = ns[fn]()
    M["dark"] = ns["mat_simple"]("M_Interior_Dark", (0.012, 0.010, 0.008), 0.9)
    # the ruins past the collapsed end and the mooring dolphin are this shot's own jetty instead
    specs = ns["piling_specs"]
    ns["piling_specs"] = lambda: [s for s in specs() if s[0] in ("Pile_Support", "Pile_Fender", "Pile_Walk")]
    for step in ("build_deck", "build_moss", "build_sprouts", "build_pilings", "build_house", "build_props"):
        try:
            ns[step]()
        except Exception as e:
            import traceback
            log("pier", step, "FAILED", e, traceback.format_exc()[-600:])
    rope = bpy.data.objects.get("H3_Dolphin_Rope")
    if rope is not None:
        bpy.data.objects.remove(rope, do_unlink=True)
    # rename everything it made (so re-running this builder never touches a real 3 AM scene)
    for k in kinds:
        for nm in set(k.keys()) - before[id(k)]:
            d = k.get(nm)
            if d is not None and d.name.startswith("H3_"):
                d.name = "H5H_" + d.name[3:]
    c = coll("20_Pier")
    root = new_empty("PierRoot", c, (P["pier_offset"][0], P["pier_offset"][1], 0.0), 1.0, 'ARROWS')
    root.rotation_euler = (0.0, 0.0, radians(P["pier_rotation"]))
    for ob in SC.objects:
        if ob.name.startswith("H5H_") and ob.parent is None:
            ob.parent = root
    for cl in list(SC.collection.children):
        if cl.name.startswith("H5H_"):
            SC.collection.children.unlink(cl)
            c.children.link(cl)
    OBJS["pier_root"] = root
    build_pier_lamps(c, root, M)


def build_pier_lamps(c, root, M):
    """Old pier lights (third reference): tarred poles, enamel shades, warm bulbs still burning at dawn,
    one of them dead, a sagging cable between them."""
    DZ = 0.8
    rng = random.Random(P["seed"] + 13)
    ys = P["pier_lamps"]
    bmp = bmesh.new()
    tops = []
    for i, y in enumerate(ys):
        x = 1.92
        h = 3.1 + rng.uniform(-0.1, 0.1)
        lean = rng.gauss(0, 0.03)
        base = Vector((x, y, DZ - 0.3))
        top = base + Vector((lean, 0.0, h))
        bm_tube(bmp, [base, base.lerp(top, 0.5) + Vector((0.01, 0, 0)), top], 0.06, seg=10, r_end=0.045)
        arm_end = top + Vector((-0.45, 0.0, -0.05))
        bm_tube(bmp, [top - Vector((0, 0, 0.12)), top + Vector((-0.25, 0, 0.02)), arm_end], 0.018, seg=6)
        tops.append((top, arm_end, i))
    bm_to_obj("Pier_LampPoles", bmp, c, M["wood_dark"], smooth=True, parent=root)
    shades = bmesh.new()
    bulbs = bmesh.new()
    for top, arm, i in tops:
        bm_lathe(shades, [(0.015, 0.0), (0.05, -0.03), (0.13, -0.10), (0.135, -0.11)],
                 loc=arm + Vector((0, 0, -0.02)), seg=24)
        bm_sphere(bulbs, arm + Vector((0, 0, -0.1)), 0.035, useg=12, vseg=8)
    sh = bm_to_obj("Pier_LampShades", shades, c, MATS["lamp_paint"], smooth=True, parent=root)
    so = sh.modifiers.new("Solidify", 'SOLIDIFY')
    so.thickness = 0.004
    bmat, gg, out = new_mat("M_PierBulb")
    em = gg.new('ShaderNodeEmission', 0, 0)
    bb = gg.new('ShaderNodeBlackbody', -200, 0)
    bb.inputs["Temperature"].default_value = 2400.0
    gg.link(bb.outputs[0], em.inputs["Color"])
    em.inputs["Strength"].default_value = 60.0
    finish(bmat, gg, out, em.outputs[0])
    bu = bm_to_obj("Pier_Bulbs", bulbs, c, bmat, smooth=True, parent=root)
    bu.visible_shadow = False
    for top, arm, i in tops:
        if i == P["pier_dead_lamp"]:
            continue
        ld = bpy.data.lights.new("H5_PierLamp_%d" % i, 'POINT')
        ld.energy = P["pier_lamp_power"]
        ld.shadow_soft_size = 0.04
        if not (setp(ld, "use_temperature", True) and setp(ld, "temperature", 2400.0)):
            ld.color = (1.0, 0.55, 0.25)
        lo = link_obj("PierLamp_%d" % i, ld, c, root)
        lo.location = arm + Vector((0, 0, -0.14))
        if i == 1:                                           # a tired bulb that flickers now and then
            add_driver(ld, "energy", "%.2f*(1-0.6*(sin(frame*0.9)>0.93)-0.3*(sin(frame*0.37+1)>0.97))"
                       % P["pier_lamp_power"])
    for (t0, _, _), (t1, _, _) in zip(tops[:-1], tops[1:]):
        make_curve("Pier_Cable", sag_wire(t0 - Vector((0, 0, 0.05)), t1 - Vector((0, 0, 0.05)), 0.35, 16), c,
                   0.006, MATS["rope"], parent=root)


# ============================================================================
#  THE ROWBOAT: a clinker-built wooden double-ender (Norwegian faering style), oars, no motor
# ============================================================================
# boat space: +X = bow, +Y = port, Z up, z = 0 is the waterline
BOAT = dict(L=4.4, B=1.44, keel=-0.19, sheer=0.40, rise=0.24, strakes=7, plank=0.016,
            seat_x=0.05, pin_dx=-0.30, oar_in=0.66, oar_out=1.64)


def hull_bs(u):
    """Half-breadth at the sheer, u = 0 (stern) .. 1 (bow)."""
    return 0.5 * BOAT["B"] * max(0.0, sin(pi * u)) ** 0.6


def hull_zs(u):
    return BOAT["sheer"] + BOAT["rise"] * (2 * u - 1) ** 4


def hull_zk(u):
    a = abs(2 * u - 1)
    return BOAT["keel"] + 0.06 * a * a + 0.52 * smoothstep(0.76, 1.0, a) ** 1.3


def hull_pt(u, s, side=1.0, out=0.0):
    """Point on the hull skin: s = 0 at the keel .. 1 at the sheer; 'out' pushes along the normal."""
    x = (u - 0.5) * BOAT["L"]
    zk, zs = hull_zk(u), hull_zs(u)
    z = zk + (zs - zk) * s
    w = hull_bs(u) * sin(0.5 * pi * s) ** 0.7
    p = Vector((x, side * w, z))
    if out != 0.0:
        e = 1e-3
        du = hull_pt(min(1.0, u + e), s, side) - hull_pt(max(0.0, u - e), s, side)
        ds = hull_pt(u, min(1.0, s + e), side) - hull_pt(u, max(0.0, s - e), side)
        n = du.cross(ds)
        if n.y * side < 0:
            n = -n
        if n.length > 1e-9:
            p += n.normalized() * out
    return p


def hull_halfwidth_at(u, z):
    zk, zs = hull_zk(u), hull_zs(u)
    s = min(1.0, max(0.0, (z - zk) / max(1e-6, zs - zk)))
    return hull_bs(u) * sin(0.5 * pi * s) ** 0.7


def u_of_x(x):
    return x / BOAT["L"] + 0.5


def build_hull(c, parent):
    N, t = BOAT["strakes"], BOAT["plank"]
    bm = bmesh.new()
    nu = 72
    us = [0.5 - 0.5 * cos(pi * i / nu) for i in range(nu + 1)]           # denser toward the stems
    for side in (1.0, -1.0):
        for k in range(N):
            s0 = k / N
            s1 = min(1.0, (k + 1) / N + 0.03)                              # each strake laps over the next
            ss = [s0 + (s1 - s0) * j / 3 for j in range(4)]
            grid = []
            for u in us:
                row = []
                for s in ss:
                    lap = t * 0.9 * (1.0 - (s - s0) / (s1 - s0))           # lower edge stands proud: the lands
                    row.append(bm.verts.new(hull_pt(u, s, side, out=lap + 0.001 * k)))
                grid.append(row)
            for i in range(nu):
                for j in range(3):
                    f = bm.faces.new((grid[i][j], grid[i + 1][j], grid[i + 1][j + 1], grid[i][j + 1]))
                    f.material_index = 2 if k == N - 1 else 0
                    f.normal_update()
                    ctr = f.calc_center_median()
                    if (f.normal.y * side) < 0 and abs(ctr.y) > 1e-4:
                        f.normal_flip()
    for f in bm.faces:
        f.smooth = True
    hull = bm_to_obj("Boat_Hull", bm, c, [MATS["hull_paint"], MATS["boat_inside"], MATS["hull_top"],
                                           MATS["boat_inside"]], parent=parent)
    so = hull.modifiers.new("Plank", 'SOLIDIFY')
    so.thickness = t
    so.offset = -1.0
    so.material_offset = 1
    so.use_even_offset = True
    add_subsurf(hull, levels=0, render=1)
    return hull


def build_boat_timber(c, parent):
    """Keel, stems, ribs, gunwales, thwarts, floorboards, stretcher."""
    L = BOAT["L"]
    bm = bmesh.new()
    # keel + stems: one curved timber from stern post round the forefoot to the bow post
    pts = []
    for i in range(41):
        u = 0.02 + 0.96 * i / 40
        p = hull_pt(u, 0.0)
        pts.append((p.x, 0.0, p.z - 0.025))
    for end, sgn in ((1.0, 1.0), (0.0, -1.0)):
        base = hull_pt(end, 0.0)
        top = hull_zs(end)
        stem = [(base.x + sgn * 0.02 * (j / 8) ** 2, 0.0, base.z + (top + 0.26 - base.z) * j / 8) for j in range(9)]
        stem.append((stem[-1][0] - sgn * 0.05, 0.0, stem[-1][2] + 0.03))          # a little curl at the head
        bm_tube(bm, stem, 0.035, seg=6, r_end=0.028)
    bm_tube(bm, pts, 0.04, seg=6)
    # ribs (bent frames) inside, every ~36 cm
    for u in [0.16 + 0.68 * i / 11 for i in range(12)]:
        for side in (1.0, -1.0):
            rib = [hull_pt(u, s, side, out=-(BOAT["plank"] + 0.012)) for s in [0.03 + 0.92 * j / 9 for j in range(10)]]
            bm_tube(bm, rib, 0.016, seg=4, cap=True)
    # gunwale (outside rubbing strake) and inwale
    for side in (1.0, -1.0):
        for off, rr in ((0.024, 0.026), (-0.03, 0.018)):
            rail = [hull_pt(u, 0.985, side, out=off) for u in [0.035 + 0.93 * i / 60 for i in range(61)]]
            bm_tube(bm, rail, rr, seg=6)
    # thwarts: bow seat, rowing thwart, stern seat
    for u, w in ((0.77, 0.20), (u_of_x(BOAT["seat_x"]), 0.24), (0.26, 0.22)):
        z = hull_zs(u) - 0.17
        hw = hull_halfwidth_at(u, z) - 0.03
        bm_box(bm, ((u - 0.5) * L, 0.0, z), (w, 2 * hw, 0.032))
        for side in (1.0, -1.0):                                                 # hanging knees
            bm_box(bm, ((u - 0.5) * L, side * (hw - 0.05), z - 0.08), (0.05, 0.08, 0.16), rot=(side * 0.4, 0, 0))
    # floorboards
    for yo in (-0.22, 0.0, 0.22):
        bm_box(bm, (0.0, yo, hull_zk(0.5) + 0.095), (2.3, 0.18, 0.018))
    # stretcher (foot brace) for the rower, toward the stern
    sx = BOAT["seat_x"] - 0.66
    bm_box(bm, (sx, 0.0, hull_zk(u_of_x(sx)) + 0.16), (0.035, 0.62, 0.11), rot=(0.0, radians(-28.0), 0.0))
    ob = bm_to_obj("Boat_Timber", bm, c, MATS["boat_inside"], parent=parent)
    return ob


def pin_pos(side):
    x = BOAT["seat_x"] + BOAT["pin_dx"]
    u = u_of_x(x)
    return Vector((x, side * (hull_bs(u) + 0.012), hull_zs(u) + 0.045))


def build_rowlocks(c, parent):
    bm = bmesh.new()
    for side in (1.0, -1.0):
        p = pin_pos(side)
        bm_cyl(bm, p - Vector((0, 0, 0.06)), 0.011, 0.11, segs=10)             # shank into the gunwale
        bm_torus(bm, p + Vector((0, 0, 0.035)), 0.034, 0.0075, rot=(radians(-90), 0, 0), seg=20, rseg=6,
                 arc=pi)                                                        # the U, open to the sky
    ob = bm_to_obj("Boat_Rowlocks", bm, c, MATS["bronze"], smooth=True, parent=parent)
    return ob


def build_oar(c, parent, name):
    """Oar in its own space: origin at the rowlock pin, +X out along the shaft to the blade.
    Blade width along local Z, so at rest (zero feather) the blade is square (vertical)."""
    bm = bmesh.new()
    i_, o_ = BOAT["oar_in"], BOAT["oar_out"]
    prof = [(0.017, -i_), (0.019, -i_ + 0.02), (0.018, -i_ + 0.11), (0.026, -i_ + 0.14), (0.029, -0.30),
            (0.029, 0.20), (0.025, 0.7), (0.022, o_ - 0.56), (0.018, o_ - 0.52)]
    bm_lathe(bm, [(r, x) for r, x in prof], rot=(0.0, radians(90.0), 0.0), seg=14, cap_bottom=True)
    # blade: tapered from the neck, rounded tip, slight spine
    bl = []
    n = 12
    for i in range(n + 1):
        s = i / n
        x = o_ - 0.54 + 0.54 * s
        w = 0.035 + (0.068 - 0.035) * smoothstep(0.0, 0.45, s)
        w *= 1.0 - 0.35 * smoothstep(0.88, 1.0, s) ** 2
        th = 0.012 + 0.006 * (1.0 - s)
        bl.append([bm.verts.new((x, -th * 0.5, -w)), bm.verts.new((x, th * 0.5, -w)),
                   bm.verts.new((x, th, 0.0)), bm.verts.new((x, th * 0.5, w)),
                   bm.verts.new((x, -th * 0.5, w)), bm.verts.new((x, -th, 0.0))])
    for a, b in zip(bl[:-1], bl[1:]):
        for k in range(6):
            bm.faces.new((a[k], a[(k + 1) % 6], b[(k + 1) % 6], b[k]))
    bm.faces.new(list(reversed(bl[0])))
    bm.faces.new(bl[-1])
    ob = bm_to_obj(name, bm, c, MATS["oar_wood"], smooth=True, parent=parent)
    add_subsurf(ob, levels=0, render=1)
    # leather sleeve where the loom rides in the rowlock
    bmc = bmesh.new()
    bm_lathe(bmc, [(0.031, -0.07), (0.033, -0.06), (0.033, 0.12), (0.031, 0.13)], rot=(0.0, radians(90.0), 0.0),
             seg=14)
    leather = bm_to_obj(name + "_Leather", bmc, c, MATS["leather"], smooth=True, parent=ob)
    return ob, leather


def build_boat_props(c, parent):
    """Net pile and fish box forward, a coil of rope, a tin bucket, the stern lantern on its crook."""
    bm = bmesh.new()
    rng = random.Random(P["seed"] + 51)
    # fish box
    bx, bz = 1.05, hull_zk(u_of_x(1.05)) + 0.2
    for (lx, ly, lz), (sx, sy, sz) in (((0, 0, 0.0), (0.5, 0.36, 0.02)), ((0, 0.17, 0.09), (0.5, 0.02, 0.17)),
                                       ((0, -0.17, 0.09), (0.5, 0.02, 0.17)), ((0.24, 0, 0.09), (0.02, 0.36, 0.17)),
                                       ((-0.24, 0, 0.09), (0.02, 0.36, 0.17))):
        bm_box(bm, (bx + lx, ly, bz + lz), (sx, sy, sz), rot=(0, 0, 0.08))
    box = bm_to_obj("Boat_FishBox", bm, c, MATS["wood_trim"], parent=parent)
    # net pile in the bows
    bmn = bmesh.new()
    vs = bm_sphere(bmn, (1.55, 0.0, hull_zk(u_of_x(1.55)) + 0.18), 0.3, scale=(1.2, 0.8, 0.45), useg=24, vseg=12)
    for v in vs:
        v.co += Vector((0, 0, 1)) * 0.06 * noise.noise(v.co * 6.0) + v.co.normalized() * 0.02 * noise.noise(v.co * 17.0)
    net = bm_to_obj("Boat_Net", bmn, c, MATS["net"], smooth=True, parent=parent)
    # rope coil on the floorboards
    pts = []
    zc = hull_zk(0.5) + 0.12
    for i in range(220):
        a = i * 0.16
        r = 0.06 + 0.02 * a / (2 * pi)
        if r > 0.2:
            break
        pts.append((0.55 + r * cos(a), -0.28 + r * sin(a), zc + 0.004 * sin(a * 3.1)))
    coil = make_curve("Boat_RopeCoil", pts, c, 0.009, MATS["rope"], parent=parent)
    # tin bucket aft
    bmb = bmesh.new()
    bm_lathe(bmb, [(0.10, 0.0), (0.105, 0.01), (0.13, 0.22), (0.132, 0.225)],
             loc=(-1.2, 0.18, hull_zk(u_of_x(-1.2)) + 0.12), seg=24, cap_bottom=True)
    bucket = bm_to_obj("Boat_Bucket", bmb, c, MATS["tin"], smooth=True, parent=parent)
    so = bucket.modifiers.new("Solidify", 'SOLIDIFY')
    so.thickness = 0.003
    # lantern crook: a bent wooden pole at the starboard quarter, lamp hanging off its arm
    lx, ly = -BOAT["L"] / 2 + 0.55, 0.30                 # port quarter: the lamp faces the jetty
    z0 = hull_zs(u_of_x(lx)) - 0.1
    bmp = bmesh.new()
    crook = [(lx, ly, z0), (lx, ly, z0 + 0.8), (lx - 0.03, ly + 0.02, z0 + 0.93), (lx - 0.14, ly + 0.06, z0 + 0.98),
             (lx - 0.26, ly + 0.1, z0 + 0.96)]
    bm_tube(bmp, crook, 0.02, seg=8, r_end=0.014)
    pole = bm_to_obj("Boat_LanternCrook", bmp, c, MATS["wood_trim"], smooth=True, parent=parent)
    pivot = new_empty("LanternPivot", c, (lx - 0.25, ly + 0.1, z0 + 0.93), 0.05, 'SPHERE', parent)
    add_driver(pivot, "rotation_euler", "0.06*sin(frame*0.041+0.3)+0.025*sin(frame*0.113)", 0)
    add_driver(pivot, "rotation_euler", "0.05*sin(frame*0.037+1.1)+0.02*sin(frame*0.09)", 1)
    return pivot, rng, (box, net, coil, bucket, pole)


def build_lantern(c, pivot):
    """Hurricane lantern hanging from 'pivot' (the bail top at the pivot); warm kerosene flame + light."""
    base = Vector((0.0, 0.0, -0.35))
    Bx, By, Bz = base
    bm = bmesh.new()
    bm_lathe(bm, [(0.07, 0.0), (0.084, 0.01), (0.088, 0.03), (0.08, 0.046), (0.045, 0.056), (0.03, 0.06)],
             loc=base, seg=28, cap_bottom=True)
    bm_cyl(bm, (Bx, By, Bz + 0.074), 0.03, 0.028, segs=16)
    bm_torus(bm, (Bx, By, Bz + 0.088), 0.045, 0.004, seg=28, rseg=6)
    for k in range(5):
        a = 2 * pi * k / 5 + 0.3
        prof = [(0.058, 0.09), (0.075, 0.12), (0.08, 0.15), (0.078, 0.18), (0.064, 0.21), (0.052, 0.222)]
        bm_tube(bm, [(Bx + r * cos(a), By + r * sin(a), Bz + z) for (r, z) in prof], 0.0022, seg=5)
    bm_torus(bm, (Bx, By, Bz + 0.15), 0.08, 0.0022, seg=32, rseg=5)
    bm_torus(bm, (Bx, By, Bz + 0.195), 0.072, 0.0022, seg=32, rseg=5)
    bm_lathe(bm, [(0.047, 0.222), (0.062, 0.232), (0.058, 0.25), (0.035, 0.27), (0.014, 0.282), (0.014, 0.30)],
             loc=base, seg=28, cap_top=True)
    bm_tube(bm, [(Bx + 0.11 * cos(a), By, Bz + 0.24 + 0.11 * sin(a)) for a in [i * pi / 16 for i in range(17)]],
            0.003, seg=5)
    bm_to_obj("Lantern_Metal", bm, c, MATS["lamp_paint"], smooth=True, parent=pivot)
    bm = bmesh.new()
    bm_lathe(bm, [(0.043, 0.088), (0.058, 0.10), (0.068, 0.135), (0.066, 0.175), (0.053, 0.205), (0.040, 0.222)],
             loc=base, seg=32)
    glass = bm_to_obj("Lantern_Globe", bm, c, MATS["lamp_glass"], smooth=True, parent=pivot)
    so = glass.modifiers.new("Solidify", 'SOLIDIFY')
    so.thickness = 0.0025
    glass.visible_shadow = False
    bm = bmesh.new()
    bm_sphere(bm, (0.0, 0.0, 0.0), 0.008, scale=(1.0, 1.0, 2.6), useg=12, vseg=8)
    flame = bm_to_obj("Lantern_Flame", bm, c, MATS["flame"], smooth=True, parent=pivot)
    flame.location = (Bx, By, Bz + 0.108)
    flame.visible_shadow = False
    add_driver(flame, "scale", "1+0.08*sin(frame*1.15)+0.05*sin(frame*3.05+0.7)", 2)
    ld = bpy.data.lights.new("H5_KeroseneLamp", 'POINT')
    ld.energy = P["lantern_power"]
    ld.shadow_soft_size = 0.012
    if not (setp(ld, "use_temperature", True) and setp(ld, "temperature", 1850.0)):
        ld.color = (1.0, 0.36, 0.08)
    lamp = link_obj("KeroseneLamp", ld, c, pivot)
    lamp.location = (Bx, By, Bz + 0.11)
    setp(lamp, "visible_camera", False)
    add_driver(ld, "energy", "%.3f*(1+0.05*sin(frame*0.97)+0.035*sin(frame*2.36+1.3)+0.02*sin(frame*5.6+0.4))"
               % P["lantern_power"])
    # the lamp's halo in the damp air: a small scattering volume around it
    bmh = bmesh.new()
    bm_sphere(bmh, (0, 0, 0), P["lantern_halo"], useg=24, vseg=12)
    halo = bm_to_obj("Lantern_Halo", bmh, c, MATS["halo"], parent=pivot)
    halo.location = (Bx, By, Bz + 0.11)
    halo.display_type = 'BOUNDS'
    OBJS["lamp"] = lamp
    return lamp


# ---------------------------------------------------------------------------- boat materials
def mat_hull_paint(name, paint, top=False):
    """Old paint over clinker planks: faded, chalky, peeling to grey wood, rust weeping from the rivets,
    a band of green weed and slime along the waterline (like the posts)."""
    m, g, out = new_mat(name)
    tc = g.new('ShaderNodeTexCoord', -2200, 0)
    co = tc.outputs["Object"]
    x, y, z = g.separate(co, x=-2000, y=0)
    oi = g.new('ShaderNodeObjectInfo', -2200, -300)
    wood = wood_node(g, co, g.math('MULTIPLY', g.o(oi, "Random"), 50.0, x=-2000, y=-300), 0.95, 0.35, 0.6,
                     x=-1700, y=300)
    pn = g.noise(co, scale=3.2, detail=12.0, rough=0.7, dist=0.2, x=-1700, y=-100)
    chips = g.voronoi(co, scale=26.0, feature='F1', x=-1700, y=-350)
    pv = g.math('MULTIPLY_ADD', g.math('SUBTRACT', g.o(chips, "Distance"), 0.5, x=-1500, y=-350), 0.12, g.fac(pn),
                x=-1350, y=-250)
    paint_amt = 0.72 if not top else 0.8
    thr = 1.0 - paint_amt
    has = g.mrange(pv, thr - 0.012, thr + 0.012, x=-1150, y=-250)
    fade = g.noise(co, scale=0.9, detail=3.0, x=-1500, y=-600)
    pcol = g.mix(g.mrange(g.fac(fade), 0.3, 0.7, x=-1300, y=-600), paint, tuple(min(1.0, c * 1.25 + 0.03) for c in paint),
                 x=-1100, y=-600)                                              # chalky, sun-faded patches
    col = g.mix(has, wood.outputs["Color"], pcol, x=-900, y=100)
    # rust weeping down from the clench rivets
    rv = g.voronoi(g.mapping(co, scale=(1.0, 1.0, 0.25), x=-1500, y=-850), scale=9.0, feature='F1', x=-1300, y=-850)
    weep = g.math('MULTIPLY', g.mrange(g.o(rv, "Distance"), 0.0, 0.25, 1.0, 0.0, x=-1100, y=-850),
                  g.mrange(g.fac(g.noise(co, scale=40.0, x=-1300, y=-1000)), 0.4, 0.7, x=-1100, y=-1000), x=-900, y=-900)
    col = g.mix(g.math('MULTIPLY', weep, 0.45, x=-750, y=-900), col, (0.11, 0.045, 0.02), x=-700, y=50)
    # tide line: dark wet band, green weed + slime below the waterline
    wl = g.math('ADD', z, g.math('MULTIPLY', g.math('SUBTRACT', g.fac(pn), 0.5, x=-1500, y=-1250), 0.06,
                                 x=-1350, y=-1250), x=-1200, y=-1200)
    wet = g.mrange(wl, 0.16, 0.02, x=-1000, y=-1200)
    weed = g.math('MULTIPLY', g.mrange(wl, 0.07, -0.02, x=-1000, y=-1350),
                  g.mrange(g.fac(g.noise(co, scale=14.0, detail=5.0, x=-1200, y=-1450)), 0.3, 0.5, x=-1000, y=-1450),
                  x=-800, y=-1400)
    col = g.mix(g.math('MULTIPLY', wet, 0.6, x=-800, y=-1200), col, (0.4, 0.42, 0.4), blend='MULTIPLY', x=-500, y=0)
    col = g.mix(weed, col, (0.05, 0.12, 0.012), x=-350, y=-50)
    rough = g.mix(has, wood.outputs["Roughness"], 0.55, dtype='FLOAT', x=-600, y=-300)
    rough = g.mix(wet, rough, 0.2, dtype='FLOAT', x=-450, y=-300)
    h = g.mix(has, g.math('MULTIPLY', wood.outputs["Height"], 0.4, x=-800, y=-500),
              g.math('MULTIPLY_ADD', g.fac(pn), 0.1, 0.5, x=-800, y=-600), dtype='FLOAT', x=-600, y=-550)
    nrm = g.bump(h, strength=0.45, distance=0.002, x=-400, y=-550)
    bsdf = g.principled(0, 0, base=col, rough=rough, normal=nrm, coat=g.math('MULTIPLY_ADD', wet, 0.4, 0.15, x=-400,
                                                                              y=-750), coat_rough=0.1, coat_ior=1.33)
    return finish(m, g, out, bsdf.outputs[0])


def mat_halo():
    m, g, out = new_mat("M_LanternHalo")
    tc = g.new('ShaderNodeTexCoord', -900, -300)
    r = g.vmath('LENGTH', tc.outputs["Object"], x=-700, y=-300)
    fall = g.mrange(r, P["lantern_halo"], 0.2, 0.0, 1.0, interp='SMOOTHSTEP', x=-500, y=-300)
    pv = g.new('ShaderNodeVolumePrincipled', -200, 0)
    g.feed(pv.inputs["Color"], (1.0, 1.0, 1.0))
    g.feed(pv.inputs["Density"], g.math('MULTIPLY', fall, P["lantern_halo_density"], x=-350, y=-300))
    pv.inputs["Anisotropy"].default_value = 0.55
    set_volume_opts(m)
    return finish(m, g, out, volume=pv.outputs[0])


def build_boat_materials():
    MATS["hull_paint"] = mat_hull_paint("M_Hull_Paint", (0.42, 0.43, 0.40))       # old off-white
    MATS["hull_top"] = mat_hull_paint("M_Hull_TopStrake", (0.030, 0.085, 0.110), top=True)   # faded teal-blue
    MATS["boat_inside"] = mat_wood("M_Boat_Inside", 0.75, 0.9, 0.2, 0.35, 0.5, wet=0.3)
    MATS["wood_trim"] = mat_wood("M_Wood_Trim", 0.95, 1.0, 0.25, 0.35, 0.6)
    MATS["oar_wood"] = mat_wood("M_Oar", 0.6, 1.2, 0.35, 0.2, 0.3)
    MATS["leather"] = mat_simple("M_Leather", (0.07, 0.035, 0.015), 0.55, coat=0.2, noise_scale=60.0, bump=0.3)
    MATS["bronze"] = mat_bronze()
    MATS["tin"] = mat_simple("M_Tin", (0.30, 0.30, 0.29), 0.45, metal=0.8, noise_scale=12.0, bump=0.2)
    MATS["net"] = mat_simple("M_Net", (0.05, 0.075, 0.06), 0.9, sheen=0.5, noise_scale=120.0, bump=0.8)
    MATS["lamp_paint"] = mat_lamp_paint()
    MATS["lamp_glass"] = mat_lamp_glass()
    MATS["flame"] = mat_flame()
    MATS["halo"] = mat_halo()


# ============================================================================
#  ROWING: the stroke, the floating rig, the rower, the oars, their ripples
# ============================================================================
def stroke(p):
    """One rowing stroke, p = 0..1 from the catch. Returns sweep, dip, feather (deg), torso lean (deg)
    and the boat-speed factor. Fixed-seat sculling: drive ~36 % of the cycle, blade square and buried
    through the drive, lifted and feathered on the recovery, body swings through the drive and
    comes forward again after the hands are away."""
    D = 0.36
    th_c, th_f = 48.0, -32.0
    ph_d, ph_r = 22.0, 12.0
    lam_c, lam_f = 22.0, -12.0

    def ease(x):
        x = min(1.0, max(0.0, x))
        return 0.5 - 0.5 * cos(pi * x)
    if p < D:
        q = p / D
        theta = th_c + (th_f - th_c) * ease(q)
        lean = lam_c + (lam_f - lam_c) * ease(q ** 0.85)
    else:
        q = (p - D) / (1.0 - D)
        theta = th_f + (th_c - th_f) * ease(q)
        lean = lam_f + (lam_c - lam_f) * ease((q - 0.12) / 0.8)       # hands away first, then the body
    # blade buried from just after the catch until the finish, clean entry and extraction
    bury = smoothstep(-0.035, 0.02, p if p < 0.5 else p - 1.0) * (1.0 - smoothstep(D - 0.02, D + 0.04, p))
    rec = 0.0 if p < D else sin(pi * min(1.0, (p - D) / (1.0 - D)))
    phi = ph_r + (ph_d - ph_r) * bury - 3.0 * rec * (1.0 - bury)
    feather = 70.0 * smoothstep(D + 0.03, D + 0.11, p) * (1.0 - smoothstep(0.86, 0.95, p))
    speed = 1.0 + 0.32 * cos(2.0 * pi * (p - D - 0.02))
    return theta, phi, feather, lean, bury, speed


def oar_matrix(side, theta, phi, feather):
    """Oar orientation in boat space: local +X along the shaft to the blade, blade width along local Z."""
    th, ph = radians(theta), radians(phi)
    d = Vector((cos(ph) * sin(th), side * cos(ph) * cos(th), -sin(ph)))      # sweep toward the bow is +theta
    y = Vector((0.0, 0.0, 1.0)).cross(d).normalized()
    z = d.cross(y).normalized()
    M = Matrix((d, y, z)).transposed()
    return M @ Matrix.Rotation(radians(feather) * side, 3, 'X')


def boat_schedule():
    """Integrate the boat's run through the shot: world XY, heading, stroke phase for every frame."""
    fs, fe, fps = P["frame_start"], P["frame_end"], P["fps"]
    T = P["stroke_period"]
    head0 = P["boat_heading"]
    pos = Vector((P["boat_start"][0], P["boat_start"][1], 0.0))
    out = {}
    for f in range(fs, fe + 1):
        t = (f - fs) / fps
        p = (t / T + P["stroke_phase0"]) % 1.0
        th, ph, fe_, lean, bury, spd = stroke(p)
        yaw = head0 + 0.45 * sin(2 * pi * t / T + 0.7) + 1.2 * sin(2 * pi * t / 13.0 + 0.4)
        out[f] = dict(t=t, p=p, pos=pos.copy(), yaw=yaw, theta=th, phi=ph, feather=fe_, lean=lean, bury=bury)
        fwd = heading_vec(yaw)
        pos = pos + fwd * (P["boat_speed"] * spd / fps)
    return out


def boat_world(sched, f, local):
    """Approximate world XY of a boat-space point at frame f (ignores the few cm of heave/tilt)."""
    s = sched[f]
    fwd = heading_vec(s["yaw"])
    port = Vector((-fwd.y, fwd.x, 0.0))
    return s["pos"] + fwd * local.x + port * local.y


def build_float_rig(c, sched):
    """The boat rides the FFT ocean: four probes shrinkwrapped onto the displaced surface drive position,
    pitch and roll, so it heaves and rolls with whatever sea the Ocean modifiers make."""
    path = new_empty("BoatPath", c, (0, 0, 0), 0.6, 'ARROWS')
    fs, fe = P["frame_start"], P["frame_end"]
    for f in range(fs, fe + 1, 2):
        s = sched[f]
        fwd = heading_vec(s["yaw"])
        path.location = (s["pos"].x, s["pos"].y, 0.0)
        path.rotation_euler = (0.0, 0.0, atan2(fwd.y, fwd.x))
        path.keyframe_insert("location", frame=f)
        path.keyframe_insert("rotation_euler", frame=f)
    probes = {}
    for nm, loc in (("Bow", (1.55, 0, 0)), ("Stern", (-1.55, 0, 0)), ("Port", (0, 0.55, 0)), ("Stbd", (0, -0.55, 0))):
        pr = new_empty("Probe_" + nm, c, loc, 0.08, 'SPHERE', path)
        float_on_ocean(pr)
        probes[nm] = pr
    flt = new_empty("BoatFloat", c, (0, 0, 0), 0.5, 'ARROWS')
    for nm, inf in (("Bow", 1.0), ("Stern", 0.5), ("Port", 1.0 / 3.0), ("Stbd", 0.25)):
        cn = flt.constraints.new('COPY_LOCATION')
        cn.target = probes[nm]
        cn.influence = inf
    cn = flt.constraints.new('DAMPED_TRACK')
    cn.target = probes["Bow"]
    cn.track_axis = 'TRACK_X'
    cn = flt.constraints.new('LOCKED_TRACK')
    cn.target = probes["Port"]
    cn.track_axis = 'TRACK_Y'
    cn.lock_axis = 'LOCK_X'
    body = new_empty("BoatBody", c, (0, 0, 0), 0.4, 'PLAIN_AXES', flt)
    # the rower's weight swinging fore and aft trims the boat a little each stroke
    for f in range(fs, fe + 1, 3):
        s = sched[f]
        body.rotation_euler = (0.0, radians(-0.7 * s["lean"] / 22.0), 0.0)
        body.keyframe_insert("rotation_euler", frame=f)
    OBJS["boat_path"], OBJS["boat_float"], OBJS["boat_body"] = path, flt, body
    return body


def rower_asset_path():
    cands = []
    if bpy.data.filepath:
        cands.append(os.path.join(os.path.dirname(bpy.data.filepath), "assets", "5AM_rower.blend"))
    here = globals().get("__file__", "")
    if here and os.path.isabs(here):
        cands.append(os.path.join(os.path.dirname(here), "assets", "5AM_rower.blend"))
    for p_ in cands:
        if os.path.isfile(p_):
            return p_
    return None


def append_rower(c):
    path = rower_asset_path()
    if not path:
        log("rower: assets/5AM_rower.blend not found (run tools/make_rower.py) - boat rows itself")
        return None
    with bpy.data.libraries.load(path, link=False) as (src, dst):
        dst.collections = [n for n in src.collections if n == "H5R_Rower"]
    rc = dst.collections[0]
    c.children.link(rc)
    rig = bpy.data.objects.get("H5R_Rig")
    return rig


def pose_rower(rig, body, sched, c):
    """Seat him on the rowing thwart facing the stern; IK hands to the oar grips, feet on the stretcher."""
    sx = BOAT["seat_x"]
    seat_top = hull_zs(u_of_x(sx)) - 0.17 + 0.016
    root = rig.data.bones["root"].head_local
    # skinned meshes must ride with their armature (in the asset they sit unparented at the origin)
    for ob in rig.users_collection[0].objects:
        if ob.type == 'MESH' and ob.parent is None:
            ob.parent = rig
            ob.matrix_parent_inverse = Matrix.Identity(4)
    rig.parent = body
    rig.rotation_euler = (0.0, 0.0, radians(-90.0))                  # MakeHuman faces -Y -> boat -X (stern)
    rig.location = (sx + 0.02 - root.y, 0.0, seat_top + 0.095 - root.z)
    pb = rig.pose.bones
    for b in pb:
        b.rotation_mode = 'XYZ'
    tg = {}
    for side, s_ in (("R", 1.0), ("L", -1.0)):                        # his right hand rows the port oar
        tg["hand" + side] = new_empty("HandTarget." + side, c, (0, 0, 0), 0.04, 'SPHERE', body)
        tg["elbow" + side] = new_empty("ElbowPole." + side, c, (sx + 0.55, s_ * 0.55, 0.35), 0.04, 'CUBE', body)
        tg["foot" + side] = new_empty("FootTarget." + side, c, (sx - 0.60, s_ * 0.13, hull_zk(u_of_x(sx - 0.6)) + 0.2),
                                      0.04, 'SPHERE', body)
        tg["knee" + side] = new_empty("KneePole." + side, c, (sx - 0.45, s_ * 0.18, 1.0), 0.04, 'CUBE', body)
        ik = pb["lowerarm02." + side].constraints.new('IK')
        ik.target = tg["hand" + side]
        ik.pole_target = tg["elbow" + side]
        ik.pole_angle = radians(P["elbow_pole_angle"])
        ik.chain_count = 4
        ik = pb["lowerleg02." + side].constraints.new('IK')
        ik.target = tg["foot" + side]
        ik.pole_target = tg["knee" + side]
        ik.pole_angle = radians(P["knee_pole_angle"])
        ik.chain_count = 4
        # fingers close round the loom
        for bn in pb:
            if bn.name.endswith("." + side) and bn.name.startswith("finger"):
                thumb = bn.name.startswith("finger1")
                bn.rotation_euler = (radians(P["finger_curl"] * (0.45 if thumb else 1.0)), 0.0, 0.0)
    fs, fe = P["frame_start"], P["frame_end"]
    spine = (("spine05", 0.26), ("spine04", 0.24), ("spine03", 0.2), ("spine02", 0.17), ("spine01", 0.13))
    for f in range(fs, fe + 1):
        s = sched[f]
        lam = radians(s["lean"])
        for bn, w in spine:
            pb[bn].rotation_euler = (lam * w, 0.0, 0.0)
            pb[bn].keyframe_insert("rotation_euler", frame=f)
        # keep the gaze roughly level; one long look over the right shoulder to check his course
        glance = smoothstep(P["glance"][0], P["glance"][0] + 0.8, s["t"]) * (1.0 - smoothstep(P["glance"][1] - 0.8,
                                                                                            P["glance"][1], s["t"]))
        pb["neck01"].rotation_euler = (-lam * 0.25, radians(22.0) * glance, 0.0)
        pb["head"].rotation_euler = (-lam * 0.35 - radians(4.0) * glance, radians(38.0) * glance, 0.0)
        pb["neck01"].keyframe_insert("rotation_euler", frame=f)
        pb["head"].keyframe_insert("rotation_euler", frame=f)
        # shoulders reach at the catch, draw back at the finish
        for side, s_ in (("R", 1.0), ("L", -1.0)):
            pb["clavicle." + side].rotation_euler = (0.0, 0.0, s_ * radians(6.0) * (s["lean"] / 22.0))
            pb["clavicle." + side].keyframe_insert("rotation_euler", frame=f)
    return tg


def animate_oars(oars, tg, sched):
    fs, fe = P["frame_start"], P["frame_end"]
    prev = {}
    for f in range(fs, fe + 1):
        s = sched[f]
        for side, key in ((1.0, "R"), (-1.0, "L")):
            ob = oars[side]
            M = oar_matrix(side, s["theta"], s["phi"], s["feather"])
            pin = pin_pos(side)
            e = M.to_euler('XYZ', prev.get(side, Euler((0, 0, 0))))
            prev[side] = e
            ob.location = pin
            ob.rotation_euler = e
            ob.keyframe_insert("location", frame=f)
            ob.keyframe_insert("rotation_euler", frame=f)
            if tg:
                d = M.col[0].to_3d()
                grip = pin - d * (BOAT["oar_in"] - 0.10)
                ht = tg["hand" + key]
                ht.location = grip + Vector((0.075, 0.0, 0.03))
                ht.keyframe_insert("location", frame=f)


def ripple_events(sched):
    """Where and when the blades leave marks: a ring at every catch, a glassy puddle at every finish."""
    fs, fe = P["frame_start"], P["frame_end"]
    ev = []
    for f in range(fs + 1, fe + 1):
        a, b = sched[f - 1], sched[f]
        for side in (1.0, -1.0):
            blade = pin_pos(side)
            if a["bury"] < 0.5 <= b["bury"]:
                M = oar_matrix(side, b["theta"], b["phi"], 0.0)
                ev.append(("ring", f, boat_world(sched, f, blade + M.col[0].to_3d() * 1.35)))
            if a["bury"] >= 0.5 > b["bury"]:
                M = oar_matrix(side, b["theta"], b["phi"], 0.0)
                ev.append(("puddle", f, boat_world(sched, f, blade + M.col[0].to_3d() * 1.35)))
    return ev


def key_ripples(sched):
    """Round-robin the events into the ocean shader's ripple slots (constant-interpolated keys)."""
    nt = MATS["ocean"].node_tree
    fps = P["fps"]
    slots = {"ring": 0, "puddle": 0}
    for kind, f, w in ripple_events(sched):
        i = slots[kind] % RIPPLE_SLOTS
        slots[kind] += 1
        for nm, v in (("X", w.x), ("Y", w.y), ("T", (f - P["frame_start"]) / fps)):
            sock = nt.nodes["%s%d_%s" % (kind, i, nm)].outputs[0]
            sock.default_value = v
            sock.keyframe_insert("default_value", frame=f)
    try:
        ad = nt.animation_data
        for fc in _fcurves(ad.action):
            if "ring" in fc.data_path or "puddle" in fc.data_path or "nodes[" in fc.data_path:
                for k in fc.keyframe_points:
                    k.interpolation = 'CONSTANT'
    except Exception as e:
        log("ripple interpolation", e)


def build_boat():
    if not P["boat"]:
        return
    c = coll("50_Boat")
    sched = boat_schedule()
    OBJS["boat_sched"] = sched
    body = build_float_rig(c, sched)
    build_hull(c, body)
    build_boat_timber(c, body)
    build_rowlocks(c, body)
    oars = {}
    for side, nm in ((1.0, "Oar_Port"), (-1.0, "Oar_Stbd")):
        oars[side], _ = build_oar(c, body, nm)
    pivot, rng, props = build_boat_props(c, body)
    if P["lantern"]:
        build_lantern(c, pivot)
    tg = None
    rig = append_rower(c) if P["rower"] else None
    if rig is not None:
        tg = pose_rower(rig, body, sched, c)
        OBJS["rower"] = rig
    animate_oars(oars, tg, sched)
    key_ripples(sched)
    try:
        MATS["ocean"].node_tree.nodes["BoatCoords"].object = body
    except Exception as e:
        log("boat mask", e)


# ============================================================================
#  LIFE AROUND THE SHOT: lighthouse on the headland, gulls, floating kelp, driftwood
# ============================================================================
def terrain_sampler(name="near"):
    """Height lookup (world x, y -> z) from a packed terrain heightmap, or None."""
    img = bpy.data.images.get("H5_5AM_terrain_%s.png" % name)
    d = terrain_folder()
    if img is None or d is None:
        return None
    meta = json.load(open(os.path.join(d, "5AM_terrain.json")))[name]
    W, H = img.size
    px = [0.0] * (W * H * 4)
    img.pixels.foreach_get(px)
    (x0, x1), (y0, y1) = meta["x"], meta["y"]
    lo, hi = meta["h_min"], meta["h_max"]

    def h(x, y):
        u = (x - x0) / (x1 - x0)
        v = (y - y0) / (y1 - y0)
        if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
            return None
        i = min(W - 1, int(u * (W - 1) + 0.5))
        j = min(H - 1, int(v * (H - 1) + 0.5))
        return lo + (hi - lo) * px[(j * W + i) * 4]
    return h


def mat_lighthouse():
    m, g, out = new_mat("M_Lighthouse")
    tc = g.new('ShaderNodeTexCoord', -1200, 0)
    z = g.separate(tc.outputs["Object"], x=-1000, y=0)[2]
    band = g.math('MULTIPLY', g.math('GREATER_THAN', z, 8.0, x=-800, y=100), g.math('LESS_THAN', z, 12.0, x=-800, y=0),
                  x=-600, y=50)
    grime = g.mrange(g.fac(g.noise(tc.outputs["Object"], scale=0.6, detail=6.0, x=-1000, y=-250)), 0.4, 0.8,
                     0.0, 0.5, x=-800, y=-250)
    col = g.mix(band, (0.62, 0.61, 0.57), (0.30, 0.035, 0.025), x=-400, y=50)
    col = g.mix(grime, col, (0.12, 0.12, 0.11), x=-200, y=0)
    bsdf = g.principled(100, 0, base=col, rough=0.8)
    return finish(m, g, out, bsdf.outputs[0])


def mat_lighthouse_lamp(lh, cam):
    """The lens: a steady glow, blazing when the rotating beam swings past the camera."""
    m, g, out = new_mat("M_Lighthouse_Lamp")
    t = time_value(g, -1200, 0)
    a = g.math('MULTIPLY', t, 2 * pi / P["beam_period"], x=-1000, y=0)
    dv = (cam - lh)
    dv.z = 0.0
    dv.normalize()
    ca = g.math('MULTIPLY', g.math('COSINE', a, x=-800, y=100), dv.x, x=-600, y=100)
    sa = g.math('MULTIPLY', g.math('SINE', a, x=-800, y=-100), dv.y, x=-600, y=-100)
    dot = g.math('ADD', ca, sa, x=-400, y=0)
    both = g.math('ABSOLUTE', dot, x=-250, y=0)                        # two opposed beams
    flash = g.math('POWER', g.math('MAXIMUM', both, 0.0, x=-100, y=0), 60.0, x=50, y=0)
    em = g.new('ShaderNodeEmission', 300, 0)
    bb = g.new('ShaderNodeBlackbody', 100, 200)
    bb.inputs["Temperature"].default_value = 3600.0
    g.link(bb.outputs[0], em.inputs["Color"])
    g.feed(em.inputs["Strength"], g.math('MULTIPLY_ADD', flash, P["lighthouse_flash"], 40.0, x=200, y=-100))
    return finish(m, g, out, em.outputs[0])


def mat_beam():
    """A cone of light in hazy air: additive glow, soft at its edges, fading with distance."""
    m, g, out = new_mat("M_Lighthouse_Beam")
    tc = g.new('ShaderNodeTexCoord', -1200, 0)
    x = g.separate(tc.outputs["Object"], x=-1000, y=0)[0]
    lw = g.new('ShaderNodeLayerWeight', -1000, -250)
    lw.inputs["Blend"].default_value = 0.35
    edge = g.math('POWER', g.math('SUBTRACT', 1.0, g.o(lw, "Facing"), x=-800, y=-250), 4.0, x=-600, y=-250)
    along = g.math('EXPONENT', g.math('MULTIPLY', g.math('ABSOLUTE', x, x=-800, y=0), -1.0 / 380.0, x=-650, y=0),
                   x=-500, y=0)
    s = g.math('MULTIPLY', g.math('MULTIPLY', edge, along, x=-350, y=-100), P["beam_strength"], x=-200, y=-100)
    em = g.new('ShaderNodeEmission', 0, 100)
    bb = g.new('ShaderNodeBlackbody', -200, 250)
    bb.inputs["Temperature"].default_value = 4200.0
    g.link(bb.outputs[0], em.inputs["Color"])
    g.feed(em.inputs["Strength"], s)
    tr = g.new('ShaderNodeBsdfTransparent', 0, -150)
    add = g.new('ShaderNodeAddShader', 250, 0)
    g.link(em.outputs[0], add.inputs[0])
    g.link(tr.outputs[0], add.inputs[1])
    set_emission_sampling(m, 'NONE')
    setp(m, "blend_method", 'BLEND')
    return finish(m, g, out, add.outputs[0])


def build_lighthouse():
    if not P["lighthouse"]:
        return
    c = coll("65_Lighthouse")
    h = terrain_sampler("near")
    cam = Vector(P["cam_start"])
    site = None
    if h is not None:
        a = radians(P["lighthouse_azimuth"])
        dirv = Vector((sin(a), cos(a), 0.0))
        r = 2500.0
        while r < 7000.0:                                             # walk out to the shore, then a little inland
            q = cam + dirv * r
            z = h(q.x, q.y)
            if z is not None and z > 2.0:
                for k in range(1, 12):
                    q2 = cam + dirv * (r + 12.0 * k)
                    z2 = h(q2.x, q2.y)
                    if z2 is not None and 8.0 < z2 < 60.0:
                        site = Vector((q2.x, q2.y, z2))
                        break
                if site is None:
                    site = Vector((q.x, q.y, z))
                break
            r += 10.0
    if site is None:
        log("lighthouse: no shore found on that bearing - skipped")
        return
    bm = bmesh.new()
    bm_cyl(bm, (0, 0, 9.0), 2.2, 18.0, segs=32, r2=1.55)                   # tapering tower
    bm_cyl(bm, (0, 0, 18.1), 2.25, 0.25, segs=32)                          # gallery deck
    bm_torus(bm, (0, 0, 19.0), 2.15, 0.03, seg=48, rseg=6)                 # railing
    for k in range(24):
        a = 2 * pi * k / 24
        bm_cyl(bm, (2.15 * cos(a), 2.15 * sin(a), 18.6), 0.025, 0.9, segs=6)
    bm_lathe(bm, [(1.3, 20.4), (1.35, 20.5), (0.9, 21.3), (0.3, 21.8), (0.12, 22.3), (0.02, 22.5)], seg=24,
             cap_top=True)                                                 # dome roof
    bm_box(bm, (5.0, 1.5, 2.2), (6.0, 4.5, 4.4))                           # keeper's cottage
    obox(bm, (2.0, 1.5, 4.2), (8.0, 1.5, 4.2), (0, 0, 1), 0.3, 4.9)
    tower = bm_to_obj("Lighthouse", bm, c, MATS["lighthouse"])
    tower.location = site
    MATS["lh_lamp"] = mat_lighthouse_lamp(site + Vector((0, 0, 19.4)), cam)
    bml = bmesh.new()
    bm_cyl(bml, (0, 0, 19.3), 1.2, 2.2, segs=24)
    bm_sphere(bml, (0, 0, 19.4), 0.45)
    bm_to_obj("Lighthouse_Lamp", bml, c, MATS["lh_lamp"], parent=tower)
    # two opposed beams, turning once every beam_period seconds
    pivot = new_empty("Lighthouse_BeamPivot", c, (0, 0, 19.4), 1.0, 'PLAIN_AXES', tower)
    add_driver(pivot, "rotation_euler", "(frame-%d)/%.3f*%.6f" % (P["frame_start"], float(P["fps"]),
                                                                  2 * pi / P["beam_period"]), 2)
    bmb = bmesh.new()
    for sgn in (1.0, -1.0):
        bm_cyl(bmb, (sgn * 450.0, 0, 0), 0.4, 900.0, rot=(0.0, radians(90.0), 0.0), segs=24,
               r2=None, caps=False)
    for v in bmb.verts:                                               # flare to ~3 degrees half-angle
        k = abs(v.co.x) / 900.0
        v.co.y *= 1.0 + 110.0 * k
        v.co.z *= 1.0 + 110.0 * k
    beam = bm_to_obj("Lighthouse_Beams", bmb, c, MATS["beam"], parent=pivot)
    beam.visible_shadow = False
    setp(beam, "visible_diffuse", False)
    setp(beam, "visible_glossy", False)
    OBJS["lighthouse"] = tower


def gull_mesh(name, flying, c):
    """A herring gull: white body, grey mantle, black wingtips; wings as separate objects when flying."""
    bm = bmesh.new()
    bm_sphere(bm, (0, 0, 0), 0.11, scale=(2.1, 0.8, 0.8), useg=16, vseg=10)    # body
    bm_sphere(bm, (0.24, 0, 0.06), 0.052, useg=12, vseg=8)                    # head
    bm_cyl(bm, (0.31, 0, 0.055), 0.012, 0.06, rot=(0, radians(90), 0), segs=8, r2=0.002)
    obox(bm, (-0.2, 0, 0.0), (-0.34, 0, 0.01), (0, 0, 1), 0.02, 0.1)          # tail
    if not flying:
        for sd in (1, -1):
            obox(bm, (0.1, sd * 0.07, 0.03), (-0.34, sd * 0.05, 0.02), (0, 0, 1), 0.07, 0.03)   # folded wings
            bm_cyl(bm, (0.0, sd * 0.03, -0.14), 0.006, 0.12, segs=6)            # legs
    body = bm_to_obj(name, bm, c, MATS["gull"], smooth=True)
    wings = []
    if flying:
        for sd in (1, -1):
            bw = bmesh.new()
            # tapered, slightly cambered wing from the shoulder out to the tip
            pts = [(0.06, 0.0), (0.08, 0.2), (0.06, 0.45), (0.0, 0.66), (-0.08, 0.7)]
            back = [(-0.10, 0.0), (-0.12, 0.2), (-0.14, 0.42), (-0.12, 0.6), (-0.09, 0.69)]
            va = [bw.verts.new((x, sd * yy, 0.015 * sin(pi * yy / 0.7))) for x, yy in pts]
            vb = [bw.verts.new((x, sd * yy, 0.0)) for x, yy in back]
            for i in range(len(pts) - 1):
                f = bw.faces.new((va[i], va[i + 1], vb[i + 1], vb[i]))
                if sd < 0:
                    f.normal_flip()
            w = bm_to_obj(name + ("_WingL" if sd > 0 else "_WingR"), bw, c, MATS["gull"], smooth=True, parent=body)
            so = w.modifiers.new("Solidify", 'SOLIDIFY')
            so.thickness = 0.012
            wings.append((w, sd))
    return body, wings


def mat_gull():
    m, g, out = new_mat("M_Gull")
    tc = g.new('ShaderNodeTexCoord', -1200, 0)
    x, y, z = g.separate(tc.outputs["Object"], x=-1000, y=0)
    ay = g.math('ABSOLUTE', y, x=-800, y=0)
    wing = g.mrange(ay, 0.06, 0.1, x=-600, y=100)
    tip = g.mrange(ay, 0.5, 0.56, x=-600, y=-50)
    top = g.mrange(z, -0.01, 0.03, x=-600, y=-200)
    col = g.mix(g.math('MULTIPLY', wing, top, x=-450, y=0), (0.62, 0.62, 0.60), (0.30, 0.31, 0.32), x=-300, y=50)
    col = g.mix(tip, col, (0.02, 0.02, 0.02), x=-150, y=0)
    beak = g.math('MULTIPLY', g.math('GREATER_THAN', x, 0.285, x=-600, y=-350), g.mrange(ay, 0.02, 0.0, x=-600, y=-450),
                  x=-450, y=-400)
    col = g.mix(beak, col, (0.45, 0.30, 0.03), x=0, y=0)
    bsdf = g.principled(200, 0, base=col, rough=0.7, sheen=0.4)
    return finish(m, g, out, bsdf.outputs[0])


def build_gulls():
    if not P["gulls"]:
        return
    c = coll("75_Gulls")
    rng = random.Random(P["seed"] + 77)
    posts = OBJS.get("posts", [])
    perches = [p for p in posts if 0.9 < p[1] < 2.6 and 8.0 < p[2] < 40.0]
    rng.shuffle(perches)
    for i, (ob, top, t) in enumerate(perches[:2]):
        g_, _ = gull_mesh("Gull_Perched_%d" % i, False, c)
        g_.location = ob.location + Vector((0, 0, top - 0.05 + 0.14))
        g_.rotation_euler = (0.0, 0.0, rng.uniform(0, 2 * pi))
        add_driver(g_, "rotation_euler", "%.2f+0.35*sin(frame*0.013+%.1f)+0.25*(sin(frame*0.071+%.1f)>0.8)"
                   % (g_.rotation_euler.z, rng.uniform(0, 6), rng.uniform(0, 6)), 2)
    fs, fe = P["frame_start"], P["frame_end"]
    for i in range(3):
        g_, wings = gull_mesh("Gull_Flying_%d" % i, True, c)
        y = rng.uniform(40.0, 110.0)
        z = rng.uniform(6.0, 16.0)
        x0 = rng.uniform(-40.0, -10.0) if i % 2 == 0 else rng.uniform(10.0, 40.0)
        dx = rng.uniform(20.0, 35.0) * (1 if i % 2 == 0 else -1)
        g_.location = (x0, y, z)
        g_.rotation_euler = (0.0, 0.0, 0.0 if dx > 0 else pi)
        g_.keyframe_insert("location", frame=fs)
        g_.location = (x0 + dx, y + rng.uniform(-8, 8), z + rng.uniform(-2.5, 2.5))
        g_.keyframe_insert("location", frame=fe)
        ph = rng.uniform(0, 6.28)
        for w, sd in wings:
            add_driver(w, "rotation_euler", "%d*(0.08+0.5*sin(frame*0.22+%.2f)*(0.5+0.5*sin(frame*0.017+%.2f)))"
                       % (sd, ph, ph * 1.7), 0)
        add_driver(g_, "rotation_euler", "0.12*sin(frame*0.02+%.2f)" % ph, 0)


def build_flotsam():
    """Kelp fronds and a waterlogged branch riding the swell near the posts."""
    if not P["kelp"] or OBJS.get("ocean") is None:
        return
    c = coll("45_Flotsam")
    rng = random.Random(P["seed"] + 91)
    posts = OBJS.get("posts", [])
    anchors = [p[0].location for p in posts[:10]] or [Vector((-2.0, 12.0, 0.0))]
    bm = bmesh.new()
    for k in range(16):
        a = anchors[k % len(anchors)] + Vector((rng.uniform(-1.8, 2.2), rng.uniform(-1.5, 2.5), 0.0))
        ang = rng.uniform(0, 2 * pi)
        L = rng.uniform(0.6, 1.8)
        w = rng.uniform(0.03, 0.07)
        pts = []
        for i in range(13):
            s = i / 12
            ang += rng.gauss(0, 0.18)
            pts.append(a + Vector((cos(ang), sin(ang), 0.0)) * (L * s))
        rows = []
        for i, p in enumerate(pts):
            d = (pts[min(i + 1, 12)] - pts[max(i - 1, 0)]).normalized()
            n = Vector((-d.y, d.x, 0.0)) * w * (0.6 + 0.4 * sin(pi * i / 12))
            rows.append((bm.verts.new(p + n), bm.verts.new(p - n)))
        for (a0, a1), (b0, b1) in zip(rows[:-1], rows[1:]):
            bm.faces.new((a0, b0, b1, a1))
    kelp = bm_to_obj("Kelp", bm, c, MATS["kelp"], smooth=True)
    sw = kelp.modifiers.new("RideTheSwell", 'SHRINKWRAP')
    sw.target = OBJS["ocean"]
    sw.wrap_method = 'PROJECT'
    setp(sw, "use_project_z", True)
    setp(sw, "use_negative_direction", True)
    setp(sw, "use_positive_direction", True)
    sw.offset = 0.012
    bmd = bmesh.new()
    bm_tube(bmd, [(0, 0, 0), (0.5, 0.05, 0.01), (1.0, 0.02, -0.01), (1.4, 0.12, 0.0)], 0.045, seg=8, r_end=0.03)
    bm_tube(bmd, [(0.6, 0.05, 0.0), (0.85, 0.3, 0.02)], 0.018, seg=6)
    drift = bm_to_obj("Driftwood", bmd, c, MATS["wood_dark"], smooth=True)
    a = anchors[min(3, len(anchors) - 1)]
    drift.location = a + Vector((2.6, 3.0, 0.0))
    drift.rotation_euler = (0.0, 0.0, rng.uniform(0, 2 * pi))
    float_on_ocean(drift)


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
               "fog": (0.8, 0.8, 0.85, 1), "atmo": (0.8, 0.8, 0.85, 1), "piling": (0.25, 0.28, 0.18, 1),
               "algae_hair": (0.15, 0.35, 0.05, 1), "wood_dark": (0.15, 0.13, 0.11, 1), "rope": (0.4, 0.35, 0.25, 1),
               "buoy": (0.8, 0.3, 0.1, 1), "kelp": (0.12, 0.1, 0.03, 1), "gull": (0.7, 0.7, 0.7, 1),
               "lighthouse": (0.8, 0.8, 0.78, 1), "hull_paint": (0.7, 0.7, 0.66, 1), "hull_top": (0.1, 0.25, 0.3, 1),
               "boat_inside": (0.35, 0.3, 0.25, 1), "wood_trim": (0.35, 0.33, 0.3, 1), "oar_wood": (0.45, 0.38, 0.28, 1),
               "bronze": (0.4, 0.3, 0.15, 1), "lamp_paint": (0.6, 0.08, 0.05, 1), "flame": (1.0, 0.7, 0.2, 1)}


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
             ("pier", build_harbour), ("jetty", build_jetty), ("boat", build_boat), ("lighthouse", build_lighthouse),
             ("gulls", build_gulls), ("flotsam", build_flotsam), ("focus", focus_on_boat),
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
