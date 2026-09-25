# ============================================================================
#  3 AM HARBOR  -  cinematic night scene builder for Blender 5.x + Cycles
# ============================================================================
"""
Builds the complete 3 AM harbour shot, fully procedural (no external assets):

  * rotting pier deck with moss in the cracks, small plants, eroded pilings
    covered in wet algae and barnacles
  * abandoned red clapboard fish house with peeling paint and broken windows
  * deep ocean with Gerstner-swell true displacement (adaptive subdivision)
  * small skiff with an outboard, a seated fisherman and a kerosene hurricane
    lantern - the ONLY light source in the scene
  * ridged-multifractal mountains, sea fog, atmospheric haze, cloud banks
  * camera rig, Cycles settings, colour management, compositor, animation

Run: Blender > Scripting workspace > open this file > Run Script
     or from a terminal:  blender --python build_3am_harbor.py
Re-running is safe: everything it creates is prefixed "H3_" and rebuilt in a
scene called "3AM_Harbor" (your other scenes are left alone).
Written for Blender 5.x (built and tested on 5.2 LTS, Apple M5 / Metal).
"""

import bpy
import bmesh
import math
import random
import time
import os
from math import radians, sin, cos, tan, atan, atan2, pi, sqrt, exp
from mathutils import Vector, Matrix, Euler, noise

# ----------------------------------------------------------------------------
#  PARAMETERS  (everything worth tweaking lives here)
# ----------------------------------------------------------------------------
P = dict(
    seed=1987,
    # timing
    fps=24, frame_start=1, frame_end=240,
    # output
    res_x=1920, res_y=804,                 # 2.39:1 scope
    samples=1024, preview_samples=48, adaptive_threshold=0.012,
    # colour management / exposure
    exposure=6.0,                          # stops, AgX view transform
    look="AgX - Base Contrast",
    sky_glow=1.0,                          # residual overcast skyglow (0 = pure black sky)
    # the kerosene lamp (sole light source)
    lamp_power=15.0,                       # watts (Blender units)
    lamp_kelvin=1850.0,                    # kerosene flame colour temperature
    lamp_radius=0.012,                     # m, flame-sized soft source
    # ocean
    swell_dir_deg=255.0,                   # propagation direction (0 = +X, CCW)
    swell_scale=1.0,                       # amplitude multiplier for all swells
    choppiness=0.8,                        # Gerstner horizontal displacement
    # pier (metres, Z up, mean water level z = 0, +Y out to sea)
    deck_z=0.80,                           # top of the deck boards
    deck_x0=-3.8, deck_x1=2.0, walk_x0=0.1,
    deck_y0=-4.0, deck_y1=15.0, deck_y2=24.0, collapse_y=20.0,
    house=(-2.6, 0.8, 8.0, 14.0),          # x0, x1, y0, y1
    fender_x=2.42,
    # camera: LOCKED-OFF tripod shot from the artist's chosen spot (captured from Yan's viewport).
    # Nothing on the camera is animated: no keys, no drivers, no shake.
    cam_motion="STATIC",                   # "STATIC" | "GLIDE" (move from cam_loc down to deck_cam) | "PUSH"
    cam_loc=(-1.003, -7.439, 2.494),       # eye height on the shore end, 3.4 m behind the first boards
    cam_rot_deg=(87.53, -0.06, -10.56),    # XYZ Euler in degrees (2.5 deg tilt down, 10.6 deg pan right)
    lens=25.0, sensor=36.0, fstop=2.8,
    focus_loc=(-0.9, 8.0, 2.0),            # fish-house front wall, ~15.5 m: sharp from ~5 m to infinity at f/2.8
    # low deck framing, only used by the GLIDE / PUSH options (and to keep sprouts out of that lens)
    deck_cam=dict(loc=(1.87, -1.5, 0.965), yaw=3.0, pitch=3.5, lens=24.0),
    cam_arrive=210, push_in=0.35,
    # boat path (x, y) at first / last frame
    boat_start=(19.0, 30.8), boat_end=(12.0, 30.3),
    # output: "VIDEO" = MP4 (H.264 + AAC soundtrack), "EXR" = multilayer EXR frames for grading
    output="VIDEO",
    audio_dir="",                          # "" = the 'audio' folder next to the .blend / this script
    # atmosphere
    fog_density=0.010, fog_height=7.0,     # sea fog (1/m) and height falloff (m)
    haze_density=0.00012,
    cloud_density=0.006,
    # toggles
    use_hair=True, use_volumes=True,
)

LOG = []
SC = None
MATS = {}
GROUPS = {}
DECK = {}


def log(*a):
    s = " ".join(str(x) for x in a)
    LOG.append(s)
    print("[H3]", s)


def smoothstep(e0, e1, x):
    if e1 == e0:
        return 1.0 if x >= e1 else 0.0
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return t * t * (3.0 - 2.0 * t)


def setp(obj, name, value):
    """Set an RNA property only if it exists (the API drifts between versions)."""
    if obj is not None and hasattr(obj, name):
        try:
            setattr(obj, name, value)
            return True
        except Exception as e:
            log("setp", name, e)
    return False


# ----------------------------------------------------------------------------
#  SCENE / COLLECTIONS / OBJECT HELPERS
# ----------------------------------------------------------------------------
def prepare_scene():
    """Remove everything a previous run created, then (re)use scene '3AM_Harbor'."""
    global SC
    for ob in list(bpy.data.objects):
        if ob.name.startswith("H3_"):
            bpy.data.objects.remove(ob, do_unlink=True)
    for c in list(bpy.data.collections):
        if c.name.startswith("H3_"):
            bpy.data.collections.remove(c)
    for _ in range(3):
        for coll in (bpy.data.meshes, bpy.data.curves, bpy.data.lights, bpy.data.cameras,
                     bpy.data.particles, bpy.data.materials, bpy.data.node_groups,
                     bpy.data.worlds, bpy.data.images):
            for d in list(coll):
                if d.name.startswith("H3_") and d.users == 0:
                    coll.remove(d)
    sc = bpy.data.scenes.get("3AM_Harbor")
    if sc is None:
        sc = bpy.data.scenes.new("3AM_Harbor")
    SC = sc
    try:
        if bpy.context.window is not None:
            bpy.context.window.scene = sc
    except Exception as e:
        log("window scene", e)
    MATS.clear()
    GROUPS.clear()
    DECK.clear()
    return sc


def coll(name):
    full = "H3_" + name
    c = bpy.data.collections.get(full)
    if c is None:
        c = bpy.data.collections.new(full)
        SC.collection.children.link(c)
    return c


def link_obj(name, data, c, parent=None):
    ob = bpy.data.objects.new("H3_" + name, data)
    c.objects.link(ob)
    if parent is not None:
        ob.parent = parent
    return ob


def new_empty(name, c, loc=(0, 0, 0), size=0.5, kind='PLAIN_AXES', parent=None):
    ob = bpy.data.objects.new("H3_" + name, None)
    c.objects.link(ob)
    ob.location = loc
    ob.empty_display_size = size
    ob.empty_display_type = kind
    if parent is not None:
        ob.parent = parent
    return ob


def mark_sharp(bm, angle=35.0):
    lim = radians(angle)
    for e in bm.edges:
        if len(e.link_faces) == 2:
            try:
                if e.calc_face_angle(0.0) > lim:
                    e.smooth = False
            except Exception:
                pass


def bm_to_obj(name, bm, c, mats=None, smooth=False, sharp=None, parent=None, recalc=False):
    if recalc:
        try:
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        except Exception as e:
            log("recalc", name, e)
    if sharp is not None:
        mark_sharp(bm, sharp)
    if smooth:
        for f in bm.faces:
            f.smooth = True
    me = bpy.data.meshes.new("H3_" + name)
    bm.to_mesh(me)
    bm.free()
    if mats:
        for m in (mats if isinstance(mats, (list, tuple)) else [mats]):
            me.materials.append(m)
    return link_obj(name, me, c, parent)


def xform_verts(bm, verts, loc=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1)):
    M = (Matrix.Translation(Vector(loc)) @ Euler(rot).to_matrix().to_4x4()
         @ Matrix.Diagonal(Vector((scale[0], scale[1], scale[2], 1.0))))
    bmesh.ops.transform(bm, matrix=M, verts=verts)


def bm_box(bm, loc, size, rot=(0, 0, 0), layer=None, val=0.0):
    r = bmesh.ops.create_cube(bm, size=1.0)
    xform_verts(bm, r['verts'], loc, rot, size)
    if layer is not None:
        for v in r['verts']:
            v[layer] = val
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


def bm_torus(bm, loc, R, r, rot=(0, 0, 0), seg=32, rseg=8):
    rings, verts = [], []
    for i in range(seg):
        a = 2 * pi * i / seg
        ring = []
        for j in range(rseg):
            b = 2 * pi * j / rseg
            ring.append(bm.verts.new(((R + r * cos(b)) * cos(a), (R + r * cos(b)) * sin(a), r * sin(b))))
        rings.append(ring)
        verts += ring
    for i in range(seg):
        ra, rb = rings[i], rings[(i + 1) % seg]
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


def bm_tube(bm, pts, r, seg=6, cap=True):
    """Sweep a circle along a polyline (parallel transport frame)."""
    pts = [Vector(p) for p in pts]
    rings = []
    a_prev = None
    for i, p in enumerate(pts):
        if i == 0:
            t = pts[1] - pts[0]
        elif i == len(pts) - 1:
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
        rings.append([bm.verts.new(p + (a * cos(2 * pi * k / seg) + b * sin(2 * pi * k / seg)) * r)
                      for k in range(seg)])
    for ra, rb in zip(rings[:-1], rings[1:]):
        for k in range(seg):
            k2 = (k + 1) % seg
            bm.faces.new((ra[k], ra[k2], rb[k2], rb[k]))
    if cap:
        bm.faces.new(list(reversed(rings[0])))
        bm.faces.new(rings[-1])
    return rings


def obox(bm, p0, p1, up, width, thick, lay=None, val=0.0):
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
    if lay is not None:
        for v in vs:
            v[lay] = val
    A, B, C, D, E, F, G, H = vs
    for face in ((A, D, C, B), (E, F, G, H), (A, B, F, E), (B, C, G, F), (C, D, H, G), (D, A, E, H)):
        bm.faces.new(face)
    return vs


def wbox(bm, O, U, N, u0, u1, z0, z1, d0, d1, lay=None, val=0.0):
    """Box aligned to a wall frame: u along the wall, d along the outward normal N."""
    Z = Vector((0, 0, 1))
    co = [(u0, d0, z0), (u1, d0, z0), (u1, d1, z0), (u0, d1, z0),
          (u0, d0, z1), (u1, d0, z1), (u1, d1, z1), (u0, d1, z1)]
    vs = []
    for (u, d, z) in co:
        v = bm.verts.new(O + U * u + N * d + Z * z)
        if lay is not None:
            v[lay] = val
        vs.append(v)
    A, B, C, D, E, F, G, H = vs
    for face in ((A, D, C, B), (E, F, G, H), (A, B, F, E), (B, C, G, F), (C, D, H, G), (D, A, E, H)):
        bm.faces.new(face)
    return vs


def make_curve(name, pts, c, radius, mat=None, closed=False, parent=None, res=2):
    cu = bpy.data.curves.new("H3_" + name, 'CURVE')
    cu.dimensions = '3D'
    cu.bevel_depth = radius
    cu.bevel_resolution = res
    setp(cu, "use_fill_caps", True)
    sp = cu.splines.new('POLY')
    sp.points.add(len(pts) - 1)
    for p, co in zip(sp.points, pts):
        p.co = (co[0], co[1], co[2], 1.0)
    sp.use_cyclic_u = closed
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


def add_subsurf(ob, levels=1, render=2, adaptive=False, simple=False, pixel=1.0):
    m = ob.modifiers.new("Subdivision", 'SUBSURF')
    m.subdivision_type = 'SIMPLE' if simple else 'CATMULL_CLARK'
    m.levels = levels
    m.render_levels = render
    if adaptive:
        if setp(m, "use_adaptive_subdivision", True):
            setp(m, "adaptive_space", 'PIXEL')
            setp(m, "adaptive_pixel_size", pixel)
        else:
            log("adaptive subdivision not available for", ob.name)
    return m


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


def add_hair(ob, name, count, length, children=10, radius=0.0003, mat_name=None, vg=None, seed=0,
             normal=1.0, align=(0.0, 0.0, 0.0), rand=0.1, clump=0.2, rough=0.002, steps=3, tip=0.2,
             display=15, n_hint=(0.0, 0.0, 1.0)):
    mod = ob.modifiers.new("H3_" + name, 'PARTICLE_SYSTEM')
    ps = mod.particle_system
    st = ps.settings
    st.name = "H3_%s_%s" % (name, ob.name[3:])
    st.type = 'HAIR'
    st.count = count
    st.emit_from = 'FACE'
    setp(st, "use_emit_random", True)
    setp(st, "use_even_distribution", True)
    setp(st, "distribution", 'RAND')
    st.use_advanced_hair = True
    # Blender derives hair length from the emission velocity (hair_length == 4 x normal_factor),
    # so scale the whole velocity mix (normal + align + random) to hit the requested length.
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
    'coat_ior': ["Coat IOR"], 'coat_normal': ["Coat Normal", "Clearcoat Normal"], 'coat_tint': ["Coat Tint"],
    'sheen': ["Sheen Weight", "Sheen"], 'sheen_rough': ["Sheen Roughness"], 'sheen_tint': ["Sheen Tint"],
    'emit': ["Emission Color", "Emission"], 'emit_str': ["Emission Strength"],
    'diffuse_rough': ["Diffuse Roughness"],
}


class NT:
    """Tiny helper to write node graphs as readable Python."""

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

    def group(self, ng, x=0, y=0):
        n = self.new('ShaderNodeGroup', x, y)
        n.node_tree = ng
        return n

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
    m = bpy.data.materials.new("H3_" + name)
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


def new_group(name, inputs, outputs):
    ng = bpy.data.node_groups.new("H3_" + name, 'ShaderNodeTree')
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


def set_emission_sampling(m, mode):
    if not setp(getattr(m, "cycles", None), "emission_sampling", mode):
        setp(m, "emission_sampling", mode)


def set_volume_opts(m):
    for obj in (m, getattr(m, "cycles", None)):
        setp(obj, "volume_sampling", 'MULTIPLE_IMPORTANCE')
        setp(obj, "volume_interpolation", 'LINEAR')


# ============================================================================
#  NODE GROUPS
# ============================================================================
def ng_weathered_wood():
    """Weathered silver-grey timber: fibres, growth lines, stains, checks, rot.
    Outputs Color / Roughness / Height (for bump) / Rot mask / Crack mask."""
    if "wood" in GROUPS:
        return GROUPS["wood"]
    ng, g, gi, go = new_group(
        "NG_WeatheredWood",
        [("Vector", 'NodeSocketVector', None), ("Seed", 'NodeSocketFloat', 0.0),
         ("Weathering", 'NodeSocketFloat', 0.85), ("Rot Amount", 'NodeSocketFloat', 0.5),
         ("Crack Amount", 'NodeSocketFloat', 0.5)],
        [("Color", 'NodeSocketColor'), ("Roughness", 'NodeSocketFloat'), ("Height", 'NodeSocketFloat'),
         ("Rot", 'NodeSocketFloat'), ("Crack", 'NodeSocketFloat')])
    V = gi.outputs["Vector"]
    S = gi.outputs["Seed"]
    # per-board offset so no two boards share a pattern
    off = g.vmath('SCALE', (13.17, 7.71, 3.37), s=S, x=-1200, y=300)
    co = g.vmath('ADD', V, off, x=-1050, y=300)
    # fibres: noise stretched along the grain (X)
    grain = g.mapping(co, scale=(0.55, 9.0, 9.0), x=-900, y=500)
    fib = g.noise(grain, scale=6.0, detail=10.0, rough=0.62, dist=0.25, x=-700, y=550)
    # growth lines: distorted bands across the board
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
    # dark water stains
    st = g.noise(co, scale=1.4, detail=4.0, rough=0.55, x=-700, y=-150)
    stain = g.mrange(g.fac(st), 0.5, 0.72, x=-500, y=-150)
    col = g.mix(g.math('MULTIPLY', stain, 0.6, x=-300, y=-150), col, (0.012, 0.011, 0.009), x=200, y=450)
    # checks and cracks running with the grain
    crv = g.voronoi(g.mapping(co, scale=(0.09, 2.6, 2.6), x=-900, y=-350), scale=2.2,
                    feature='DISTANCE_TO_EDGE', x=-700, y=-350)
    crk = g.mrange(crv.outputs["Distance"], 0.0, 0.035, 1.0, 0.0, x=-500, y=-350)
    cpres = g.mrange(g.fac(g.noise(co, scale=2.5, detail=2.0, x=-700, y=-550)), 0.42, 0.6, x=-500, y=-550)
    crack = g.math('MULTIPLY', g.math('MULTIPLY', crk, cpres, x=-300, y=-400),
                   g.math('MULTIPLY', gi.outputs["Crack Amount"], 2.0, x=-300, y=-550), clamp=True, x=-100, y=-450)
    col = g.mix(g.math('MULTIPLY', crack, 0.85, x=100, y=-450), col, (0.006, 0.005, 0.004), x=400, y=400)
    # rot: soft dark blotches, sunken and fibrous
    rn = g.noise(co, scale=0.9, detail=6.0, rough=0.6, x=-700, y=-750)
    t0 = g.math('MULTIPLY_ADD', gi.outputs["Rot Amount"], -0.2, 0.74, x=-500, y=-850)
    t1 = g.math('ADD', t0, 0.06, x=-350, y=-850)
    rot = g.mrange(g.fac(rn), t0, t1, 0.0, 1.0, interp='SMOOTHSTEP', x=-200, y=-750)
    rotcol = g.mix(g.fac(fib), (0.016, 0.012, 0.008), (0.045, 0.030, 0.018), x=0, y=-750)
    col = g.mix(rot, col, rotcol, x=600, y=350)
    # height (for bump) and roughness
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


# Gerstner swell components: wavelength (m), amplitude (m), direction offset (deg), phase
WAVES = [
    (48.0, 0.170, 0.0, 0.0),
    (31.0, 0.105, 14.0, 1.7),
    (19.5, 0.062, -19.0, 4.1),
    (11.8, 0.034, 27.0, 2.6),
    (7.3, 0.019, -34.0, 5.3),
    (4.5, 0.010, 41.0, 0.9),
]


def gerstner_components():
    """(dir_x, dir_y, wavenumber k, angular speed w, amplitude A, phase) with deep-water dispersion."""
    base = radians(P["swell_dir_deg"])
    out = []
    for lam, amp, dang, ph in WAVES:
        k = 2.0 * pi / lam
        w = sqrt(9.81 * k)
        a = base + radians(dang)
        out.append((cos(a), sin(a), k, w, amp, ph))
    return out


def ocean_height(x, y, t):
    """Python mirror of NG_GerstnerSwell (vertical part) - used to make the boat ride the swell."""
    h = 0.0
    for dx, dy, k, w, A, ph in gerstner_components():
        h += A * P["swell_scale"] * cos(k * (dx * x + dy * y) - w * t + ph)
    return h


def ng_gerstner():
    """Sum of 6 Gerstner waves -> vector displacement (object space)."""
    if "gerstner" in GROUPS:
        return GROUPS["gerstner"]
    ng, g, gi, go = new_group(
        "NG_GerstnerSwell",
        [("Position", 'NodeSocketVector', None), ("Time", 'NodeSocketFloat', 0.0),
         ("Amplitude", 'NodeSocketFloat', 1.0), ("Choppiness", 'NodeSocketFloat', 0.8)],
        [("Displacement", 'NodeSocketVector'), ("Height", 'NodeSocketFloat')])
    pos = gi.outputs["Position"]
    t = gi.outputs["Time"]
    hz, hx, hy = 0.0, 0.0, 0.0
    for i, (dx, dy, k, w, A, ph) in enumerate(gerstner_components()):
        yy = 700 - i * 280
        d = g.vmath('DOT_PRODUCT', pos, (dx, dy, 0.0), x=-1100, y=yy)
        th = g.math('MULTIPLY_ADD', d, k, ph, x=-900, y=yy)            # k (d.p) + phase
        th = g.math('MULTIPLY_ADD', t, -w, th, x=-700, y=yy)           # - w t
        c = g.math('COSINE', th, x=-500, y=yy)
        s = g.math('SINE', th, x=-500, y=yy - 90)
        hz = g.math('MULTIPLY_ADD', c, A, hz, x=-300, y=yy)            # A cos
        hx = g.math('MULTIPLY_ADD', s, -A * dx, hx, x=-100, y=yy)      # -A dx sin
        hy = g.math('MULTIPLY_ADD', s, -A * dy, hy, x=100, y=yy)       # -A dy sin
    amp = gi.outputs["Amplitude"]
    ch = g.math('MULTIPLY', gi.outputs["Choppiness"], amp, x=300, y=-900)
    vx = g.math('MULTIPLY', hx, ch, x=500, y=100)
    vy = g.math('MULTIPLY', hy, ch, x=500, y=-50)
    vz = g.math('MULTIPLY', hz, amp, x=500, y=-200)
    v = g.combine(vx, vy, vz, x=800, y=0)
    g.link(v, go.inputs["Displacement"])
    g.link(vz, go.inputs["Height"])
    GROUPS["gerstner"] = ng
    return ng


# ============================================================================
#  MATERIALS
# ============================================================================
def wood_node(g, vec, seed, weathering=0.85, rot=0.5, crack=0.5, x=-1200, y=200):
    w = g.group(ng_weathered_wood(), x, y)
    g.feed(w.inputs["Vector"], vec)
    g.feed(w.inputs["Seed"], seed)
    w.inputs["Weathering"].default_value = weathering
    w.inputs["Rot Amount"].default_value = rot
    w.inputs["Crack Amount"].default_value = crack
    return w


def mat_deck():
    """Mossy, rotting, wet deck boards (joined mesh with a per-board 'board_rand' attribute)."""
    m, g, out = new_mat("M_DeckWood_MossyRot")
    tc = g.new('ShaderNodeTexCoord', -2400, 0)
    obj = tc.outputs["Object"]
    at = g.new('ShaderNodeAttribute', -2400, -350)
    at.attribute_name = "board_rand"
    seed = g.math('MULTIPLY', g.fac(at), 100.0, x=-2200, y=-350)
    wood = wood_node(g, obj, seed, 0.85, 0.55, 0.6, x=-1900, y=250)
    wet = g.value(0.55, -2400, -700, "Wetness")
    mossamt = g.value(0.5, -2400, -850, "Moss Amount")
    # --- where moss grows: crevices (AO), upward faces, patches, rot
    ao = g.new('ShaderNodeAmbientOcclusion', -1900, -300, samples=8, inside=False, only_local=True)
    g.set(ao, {"Distance": 0.06})
    crev = g.mrange(ao.outputs["AO"], 0.95, 0.4, x=-1700, y=-300)
    geo = g.new('ShaderNodeNewGeometry', -2400, -1050)
    nz = g.separate(geo.outputs["Normal"], x=-2200, y=-1050)[2]
    up = g.mrange(nz, 0.25, 0.8, x=-1700, y=-1050)
    patch = g.mrange(g.fac(g.noise(obj, scale=1.1, detail=6.0, rough=0.6, x=-1900, y=-550)), 0.42, 0.62,
                     x=-1700, y=-550)
    fine = g.mrange(g.fac(g.noise(obj, scale=38.0, detail=8.0, rough=0.7, x=-1900, y=-800)), 0.3, 0.7, 0.55, 1.0,
                    x=-1700, y=-800)
    mm = g.math('MULTIPLY', crev, 1.25, x=-1500, y=-300)
    mm = g.math('MULTIPLY_ADD', patch, 0.6, mm, x=-1350, y=-300)
    mm = g.math('MULTIPLY_ADD', wood.outputs["Rot"], 0.5, mm, x=-1200, y=-300)
    lo = g.math('MULTIPLY_ADD', mossamt.outputs[0], -0.5, 0.9, x=-1350, y=-500)
    hi = g.math('ADD', lo, 0.3, x=-1200, y=-500)
    moss = g.mrange(mm, lo, hi, x=-1050, y=-300)
    moss = g.math('MULTIPLY', g.math('MULTIPLY', moss, up, x=-900, y=-300), fine, clamp=True, x=-750, y=-300)
    # --- moss look
    mn = g.noise(obj, scale=26.0, detail=6.0, rough=0.6, x=-1500, y=-1300)
    mcol = g.ramp(g.fac(mn), [(0.25, (0.010, 0.018, 0.004)), (0.5, (0.030, 0.058, 0.009)),
                              (0.7, (0.066, 0.105, 0.016)), (0.85, (0.100, 0.120, 0.020))], x=-1300, y=-1300)
    mv = g.voronoi(obj, scale=240.0, feature='F1', x=-1500, y=-1600)
    mh = g.mrange(mv.outputs["Distance"], 0.0, 0.8, 1.0, 0.0, interp='SMOOTHSTEP', x=-1300, y=-1600)
    mf = g.noise(obj, scale=900.0, detail=2.0, x=-1500, y=-1850)
    mossh = g.math('MULTIPLY_ADD', g.fac(mf), 0.3, g.math('MULTIPLY', mh, 0.7, x=-1100, y=-1650),
                   x=-950, y=-1700)
    # --- wetness and puddles on flat, moss-free boards
    pn = g.noise(obj, scale=0.7, detail=3.0, x=-1900, y=-2100)
    puddle = g.mrange(g.fac(pn), 0.52, 0.58, x=-1700, y=-2100)
    flat = g.mrange(nz, 0.96, 0.995, x=-1700, y=-2300)
    puddle = g.math('MULTIPLY', g.math('MULTIPLY', puddle, flat, x=-1500, y=-2150),
                    g.math('SUBTRACT', 1.0, moss, x=-1500, y=-2300), clamp=True, x=-1300, y=-2200)
    wetf = g.math('ADD', wet.outputs[0], puddle, clamp=True, x=-1100, y=-2100)
    wcol = g.mix(wetf, wood.outputs["Color"], (0.45, 0.45, 0.45), blend='MULTIPLY', x=-700, y=300)
    mcolw = g.mix(wetf, mcol.outputs[0], (0.8, 0.8, 0.8), blend='MULTIPLY', x=-700, y=-1300)
    base = g.mix(moss, wcol, mcolw, x=-400, y=0)
    rw = g.mix(wetf, wood.outputs["Roughness"], 0.32, dtype='FLOAT', x=-700, y=100)
    rw = g.mix(puddle, rw, 0.03, dtype='FLOAT', x=-550, y=100)
    rm = g.mix(wetf, 0.75, 0.55, dtype='FLOAT', x=-700, y=-100)
    rough = g.mix(moss, rw, rm, dtype='FLOAT', x=-400, y=-100)
    hwood = g.math('MULTIPLY', wood.outputs["Height"], 0.5, x=-700, y=-500)
    hmoss = g.math('MULTIPLY_ADD', mossh, 0.6, 0.8, x=-700, y=-650)
    height = g.mix(moss, hwood, hmoss, dtype='FLOAT', x=-400, y=-550)
    nrm = g.bump(height, strength=0.65, distance=0.004, x=-200, y=-550)
    cnrm = g.bump(height, strength=0.12, distance=0.004, x=-200, y=-750)
    coat = g.math('MULTIPLY', g.math('MULTIPLY_ADD', wetf, 0.5, g.math('MULTIPLY', puddle, 0.5, x=-700, y=-900),
                                     x=-550, y=-900),
                  g.math('MULTIPLY_ADD', moss, -0.7, 1.0, x=-550, y=-1050), clamp=True, x=-400, y=-950)
    coat_r = g.mix(puddle, 0.08, 0.02, dtype='FLOAT', x=-400, y=-1100)
    bsdf = g.principled(100, 0, base=base, rough=rough, ior=1.45, spec=0.5, normal=nrm,
                        coat=coat, coat_rough=coat_r, coat_ior=1.33, coat_normal=cnrm,
                        sheen=g.math('MULTIPLY', moss, 0.6, x=-200, y=-1200), sheen_rough=0.35,
                        sss=g.math('MULTIPLY', moss, 0.12, x=-200, y=-1350),
                        sss_radius=(0.25, 0.6, 0.12), sss_scale=0.005)
    return finish(m, g, out, bsdf.outputs[0])


def mat_wood(name, weathering=0.9, darken=1.0, coat=0.25, rot=0.4, crack=0.5, axis='X'):
    """Generic weathered timber on object coordinates (trims, beams, crates, mast)."""
    m, g, out = new_mat(name)
    tc = g.new('ShaderNodeTexCoord', -1700, 0)
    co = tc.outputs["Object"]
    if axis == 'Z':
        co = g.mapping(co, rot=(0.0, radians(90.0), 0.0), x=-1500, y=0)
    elif axis == 'Y':
        co = g.mapping(co, rot=(0.0, 0.0, radians(90.0)), x=-1500, y=0)
    oi = g.new('ShaderNodeObjectInfo', -1700, -300)
    seed = g.math('MULTIPLY', oi.outputs["Random"], 100.0, x=-1500, y=-300)
    w = wood_node(g, co, seed, weathering, rot, crack, x=-1200, y=0)
    col = w.outputs["Color"]
    if darken != 1.0:
        col = g.mix(1.0, col, (darken, darken, darken), blend='MULTIPLY', x=-900, y=100)
    nrm = g.bump(w.outputs["Height"], strength=0.5, distance=0.003, x=-900, y=-250)
    bsdf = g.principled(-500, 0, base=col, rough=w.outputs["Roughness"], normal=nrm,
                        coat=coat, coat_rough=0.12, coat_ior=1.33)
    return finish(m, g, out, bsdf.outputs[0])


def mat_moss_bed():
    m, g, out = new_mat("M_MossBed")
    tc = g.new('ShaderNodeTexCoord', -900, 0)
    n1 = g.noise(tc.outputs["Object"], scale=60.0, detail=6.0, x=-700, y=0)
    col = g.ramp(g.fac(n1), [(0.3, (0.006, 0.010, 0.003)), (0.7, (0.028, 0.050, 0.008))], x=-500, y=0)
    nrm = g.bump(g.fac(n1), strength=0.8, distance=0.003, x=-500, y=-300)
    b = g.principled(-200, 0, base=col.outputs[0], rough=0.8, normal=nrm, sheen=0.5, sheen_rough=0.4)
    return finish(m, g, out, b.outputs[0])


def _hair_mat(name, stops, trans=0.3, rough=0.5, root_dark=0.35):
    m, g, out = new_mat(name)
    hi = g.new('ShaderNodeHairInfo', -1100, 0)
    col = g.ramp(hi.outputs["Random"], stops, x=-900, y=0)
    root = g.mrange(hi.outputs["Intercept"], 0.0, 0.7, root_dark, 1.0, x=-900, y=-300)
    col2 = g.mix(1.0, col.outputs[0], root, blend='MULTIPLY', x=-650, y=0)
    pb = g.principled(-400, 150, base=col2, rough=rough, sheen=0.25)
    tr = g.new('ShaderNodeBsdfTranslucent', -400, -250)
    g.feed(tr.inputs["Color"], col2)
    ms = g.new('ShaderNodeMixShader', -100, 0)
    ms.inputs[0].default_value = trans
    g.link(pb.outputs[0], ms.inputs[1])
    g.link(tr.outputs[0], ms.inputs[2])
    return finish(m, g, out, ms.outputs[0])


def mat_moss_hair():
    return _hair_mat("M_MossHair", [(0.0, (0.016, 0.032, 0.005)), (0.55, (0.040, 0.080, 0.011)),
                                    (1.0, (0.095, 0.125, 0.020))], trans=0.3, rough=0.5)


def mat_algae_hair():
    return _hair_mat("M_AlgaeHair", [(0.0, (0.012, 0.030, 0.006)), (0.5, (0.030, 0.085, 0.010)),
                                     (1.0, (0.070, 0.150, 0.018))], trans=0.4, rough=0.22, root_dark=0.5)


def mat_leaf():
    m, g, out = new_mat("M_Sprout_Leaf")
    oi = g.new('ShaderNodeObjectInfo', -1000, 0)
    col = g.ramp(oi.outputs["Random"], [(0.0, (0.030, 0.090, 0.012)), (1.0, (0.075, 0.150, 0.020))], x=-800, y=0)
    pb = g.principled(-450, 150, base=col.outputs[0], rough=0.42, coat=0.3, coat_rough=0.1)
    tr = g.new('ShaderNodeBsdfTranslucent', -450, -250)
    g.feed(tr.inputs["Color"], col.outputs[0])
    ms = g.new('ShaderNodeMixShader', -150, 0)
    ms.inputs[0].default_value = 0.35
    g.link(pb.outputs[0], ms.inputs[1])
    g.link(tr.outputs[0], ms.inputs[2])
    return finish(m, g, out, ms.outputs[0])


def mat_piling():
    """Eroded marine piling: dry wood > wet splash zone > algae > barnacles > slime (world-Z zoning)."""
    m, g, out = new_mat("M_Piling_Marine")
    tc = g.new('ShaderNodeTexCoord', -2800, 0)
    obj = tc.outputs["Object"]
    gen = tc.outputs["Generated"]
    oi = g.new('ShaderNodeObjectInfo', -2800, -300)
    seed = g.math('MULTIPLY', oi.outputs["Random"], 100.0, x=-2600, y=-300)
    grain = g.mapping(obj, rot=(0.0, radians(90.0), 0.0), x=-2600, y=0)     # grain along Z
    wood = wood_node(g, grain, seed, 0.9, 0.45, 0.8, x=-2300, y=300)
    geo = g.new('ShaderNodeNewGeometry', -2800, -600)
    zw = g.separate(geo.outputs["Position"], x=-2600, y=-600)[2]
    zn = g.noise(obj, scale=3.0, detail=4.0, x=-2600, y=-800)
    zz = g.math('ADD', zw, g.math('MULTIPLY', g.math('SUBTRACT', g.fac(zn), 0.5, x=-2400, y=-800), 0.3,
                                  x=-2250, y=-800), x=-2100, y=-700)
    wetband = g.mrange(zz, 1.35, 0.85, x=-1900, y=-500)
    tide = g.math('MULTIPLY', g.mrange(zz, 1.05, 0.7, x=-1900, y=-650), g.mrange(zz, 0.05, 0.4, x=-1900, y=-750),
                  x=-1700, y=-700)
    algae_z = g.math('MULTIPLY', g.mrange(zz, -0.65, -0.3, x=-1900, y=-900), g.mrange(zz, 0.55, 0.25, x=-1900, y=-1000),
                     x=-1700, y=-950)
    apatch = g.mrange(g.fac(g.noise(obj, scale=7.0, detail=5.0, x=-2100, y=-1100)), 0.33, 0.52, x=-1900, y=-1100)
    algae = g.math('MULTIPLY', algae_z, apatch, clamp=True, x=-1500, y=-1000)
    barn_z = g.math('MULTIPLY', g.mrange(zz, -0.9, -0.55, x=-1900, y=-1250), g.mrange(zz, 0.22, -0.02, x=-1900, y=-1350),
                    x=-1700, y=-1300)
    slime = g.mrange(zz, -0.2, -0.7, x=-1900, y=-1450)

    def barnacles(scale, x, y):
        v = g.voronoi(obj, scale=scale, feature='F1', x=x, y=y)
        rgb = g.separate(v.outputs["Color"], x=x + 200, y=y - 150)
        present = g.mrange(rgb[0], 0.30, 0.36, x=x + 400, y=y - 150)
        rad = g.math('MULTIPLY_ADD', rgb[1], 0.35, 0.35, x=x + 400, y=y)
        dn = g.math('DIVIDE', v.outputs["Distance"], rad, x=x + 600, y=y)
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
    acol = g.ramp(g.fac(acn), [(0.2, (0.015, 0.040, 0.006)), (0.55, (0.040, 0.110, 0.010)),
                               (0.85, (0.090, 0.190, 0.020))], x=-1300, y=-800)
    ah = g.fac(g.noise(g.mapping(obj, scale=(1.0, 1.0, 0.12), x=-1700, y=-1250), scale=45.0, detail=3.0,
                       x=-1500, y=-1250))
    gz = g.separate(gen, x=-2600, y=-2500)[2]
    top = g.mrange(gz, 0.8, 0.97, x=-2400, y=-2500)
    gs = g.fac(g.noise(g.mapping(obj, scale=(8.0, 8.0, 0.6), x=-2600, y=-2700), scale=3.0, detail=2.0,
                       x=-2400, y=-2700))
    guano = g.math('MULTIPLY', top, g.mrange(gs, 0.55, 0.62, x=-2200, y=-2700), clamp=True, x=-2000, y=-2600)
    col = wood.outputs["Color"]
    col = g.mix(g.math('MULTIPLY', wetband, 0.75, x=-700, y=300), col, (0.45, 0.45, 0.45), blend='MULTIPLY',
                x=-500, y=300)
    col = g.mix(g.math('MULTIPLY', tide, 0.5, x=-500, y=150), col, (0.02, 0.018, 0.012), x=-350, y=250)
    col = g.mix(slime, col, (0.020, 0.024, 0.012), x=-200, y=200)
    col = g.mix(algae, col, acol.outputs[0], x=-50, y=150)
    col = g.mix(bvis, col, bcol, x=100, y=100)
    col = g.mix(guano, col, (0.50, 0.48, 0.44), x=250, y=50)
    r = wood.outputs["Roughness"]
    r = g.mix(wetband, r, 0.35, dtype='FLOAT', x=-500, y=-100)
    r = g.mix(slime, r, 0.15, dtype='FLOAT', x=-350, y=-100)
    r = g.mix(algae, r, 0.2, dtype='FLOAT', x=-200, y=-100)
    r = g.mix(bvis, r, 0.55, dtype='FLOAT', x=-50, y=-100)
    r = g.mix(guano, r, 0.7, dtype='FLOAT', x=100, y=-100)
    coat = g.math('MULTIPLY_ADD', wetband, 0.55, g.math('MULTIPLY', algae, 0.4, x=-500, y=-300), clamp=True,
                  x=-350, y=-300)
    h = g.math('MULTIPLY', wood.outputs["Height"], 0.3, x=-500, y=-500)
    h = g.math('MULTIPLY_ADD', g.math('MULTIPLY', algae, ah, x=-500, y=-650), 0.5, h, x=-350, y=-550)
    h = g.math('ADD', h, bh, x=-200, y=-550)
    disp = g.new('ShaderNodeDisplacement', 900, -500)
    g.feed(disp.inputs["Height"], h)
    disp.inputs["Midlevel"].default_value = 0.0
    disp.inputs["Scale"].default_value = 0.014
    nrm = g.bump(g.math('MULTIPLY', ah, algae, x=-200, y=-750), strength=0.35, distance=0.003, x=0, y=-750)
    bsdf = g.principled(500, 0, base=col, rough=r, coat=coat, coat_rough=0.06, coat_ior=1.33, normal=nrm,
                        sss=g.math('MULTIPLY', algae, 0.15, x=200, y=-900), sss_radius=(0.2, 0.5, 0.1),
                        sss_scale=0.004)
    return finish(m, g, out, bsdf.outputs[0], disp.outputs[0], method='BOTH')


def mat_ocean():
    """Deep, dark ocean: Gerstner swell true displacement + wind chop + shading-only ripples."""
    m, g, out = new_mat("M_Ocean_Deep")
    tc = g.new('ShaderNodeTexCoord', -2000, 0)
    pos = tc.outputs["Object"]
    tv = g.value(0.0, -2000, -300, "Time (s)")
    add_driver(tv.outputs[0], "default_value", "frame/%.1f" % P["fps"])
    t = tv.outputs[0]
    ger = g.group(ng_gerstner(), -1600, 100)
    g.feed(ger.inputs["Position"], pos)
    g.feed(ger.inputs["Time"], t)
    ger.inputs["Amplitude"].default_value = P["swell_scale"]
    ger.inputs["Choppiness"].default_value = P["choppiness"]
    cx, cy = P["cam_loc"][0], P["cam_loc"][1]
    dist = g.vmath('DISTANCE', pos, (cx, cy, 0.0), x=-1600, y=-500)
    near = g.mrange(dist, 60.0, 450.0, 1.0, 0.0, interp='SMOOTHSTEP', x=-1400, y=-500)
    chop = g.noise(g.vmath('SCALE', pos, s=0.33, x=-1600, y=-800), scale=1.0, detail=5.0, rough=0.55, dims='4D',
                   w=g.math('MULTIPLY', t, 0.3, x=-1600, y=-950), x=-1400, y=-800)
    ch = g.math('MULTIPLY', g.math('SUBTRACT', g.fac(chop), 0.5, x=-1200, y=-800), 0.07, x=-1050, y=-800)
    ch = g.math('MULTIPLY', ch, near, x=-900, y=-800)
    dvec = g.vmath('ADD', ger.outputs["Displacement"], g.combine(0.0, 0.0, ch, x=-750, y=-800), x=-600, y=-200)
    vd = g.new('ShaderNodeVectorDisplacement', 1400, -500, space='OBJECT')
    g.feed(vd.inputs["Vector"], dvec)
    vd.inputs["Midlevel"].default_value = 0.0
    vd.inputs["Scale"].default_value = 1.0
    r1 = g.noise(g.vmath('SCALE', pos, s=1.8, x=-1600, y=-1200), scale=1.0, detail=6.0, rough=0.6, dims='4D',
                 w=g.math('MULTIPLY', t, 0.9, x=-1600, y=-1350), x=-1400, y=-1200)
    r2 = g.noise(g.vmath('SCALE', pos, s=7.0, x=-1600, y=-1500), scale=1.0, detail=3.0, rough=0.5, dims='4D',
                 w=g.math('MULTIPLY', t, 1.6, x=-1600, y=-1650), x=-1400, y=-1500)
    rip = g.math('MULTIPLY_ADD', g.fac(r2), 0.35, g.fac(r1), x=-1150, y=-1300)
    nrm = g.bump(rip, strength=g.math('MULTIPLY', near, 0.22, x=-1150, y=-1500), distance=0.03, x=-900, y=-1300)
    rough = g.mrange(dist, 40.0, 700.0, 0.035, 0.11, x=-900, y=-1000)
    bsdf = g.principled(200, 0, base=(0.0035, 0.0075, 0.0085), rough=rough, ior=1.333, spec=0.5, normal=nrm)
    # the water inside the boat's hull is cut away (Texture Coordinate -> Object = boat rig, set later)
    tcb = g.new('ShaderNodeTexCoord', -600, 600)
    tcb.name = "BoatMaskCoords"
    bxyz = g.separate(tcb.outputs["Object"], x=-400, y=600)
    ex = g.math('ABSOLUTE', bxyz[0], x=-250, y=700)
    ey = g.math('ABSOLUTE', bxyz[1], x=-250, y=550)
    stern = g.math('ADD', g.math('POWER', g.math('DIVIDE', ex, 2.05, x=-100, y=750), 6.0, x=50, y=750),
                   g.math('POWER', g.math('DIVIDE', ey, 0.62, x=-100, y=600), 6.0, x=50, y=600), x=200, y=700)
    bow = g.math('ADD', g.math('POWER', g.math('DIVIDE', ex, 2.0, x=-100, y=450), 2.0, x=50, y=450),
                 g.math('POWER', g.math('DIVIDE', ey, 0.62, x=-100, y=350), 2.0, x=50, y=350), x=200, y=400)
    isbow = g.math('GREATER_THAN', bxyz[0], 0.0, x=200, y=250)
    e = g.mix(isbow, stern, bow, dtype='FLOAT', x=400, y=550)
    inside = g.math('LESS_THAN', e, 1.0, x=550, y=550)
    tr = g.new('ShaderNodeBsdfTransparent', 550, 300)
    ms = g.new('ShaderNodeMixShader', 800, 200)
    g.link(inside, ms.inputs[0])
    g.link(bsdf.outputs[0], ms.inputs[1])
    g.link(tr.outputs[0], ms.inputs[2])
    return finish(m, g, out, ms.outputs[0], vd.outputs[0], method='DISPLACEMENT')


def mat_paint():
    """Faded red clapboard paint peeling off grey timber, with alligatoring, streaks and grime."""
    m, g, out = new_mat("M_FishHouse_PeelingPaint")
    tc = g.new('ShaderNodeTexCoord', -2600, 0)
    xyz = g.separate(tc.outputs["Object"], x=-2400, y=0)
    u = g.math('ADD', xyz[0], xyz[1], x=-2200, y=100)          # along the board on either wall
    co = g.combine(u, xyz[2], 0.0, x=-2000, y=0)
    at = g.new('ShaderNodeAttribute', -2600, -300)
    at.attribute_name = "board_rand"
    seed = g.math('MULTIPLY', g.fac(at), 100.0, x=-2400, y=-300)
    wood = wood_node(g, co, seed, 0.95, 0.3, 0.7, x=-1700, y=300)
    amount = g.value(0.62, -2600, -600, "Paint Amount")
    pn = g.noise(co, scale=2.4, detail=12.0, rough=0.72, dist=0.2, x=-1700, y=-300)
    chips = g.voronoi(co, scale=22.0, feature='F1', x=-1700, y=-550)
    pv = g.math('MULTIPLY_ADD', g.math('SUBTRACT', chips.outputs["Distance"], 0.5, x=-1500, y=-550), 0.12,
                g.fac(pn), x=-1350, y=-400)
    thr = g.math('SUBTRACT', 1.0, amount.outputs[0], x=-1500, y=-700)
    paint = g.mrange(pv, g.math('SUBTRACT', thr, 0.012, x=-1350, y=-750), g.math('ADD', thr, 0.012, x=-1350, y=-850),
                     x=-1150, y=-450)
    edge = g.math('MULTIPLY', g.math('MULTIPLY', paint, g.math('SUBTRACT', 1.0, paint, x=-1000, y=-600),
                                     x=-850, y=-500), 4.0, x=-700, y=-500)
    pc = g.noise(co, scale=0.8, detail=3.0, x=-1700, y=-900)
    pcol = g.ramp(g.fac(pc), [(0.3, (0.190, 0.026, 0.018)), (0.6, (0.270, 0.045, 0.030)),
                              (1.0, (0.340, 0.085, 0.060))], x=-1500, y=-900)
    allig = g.voronoi(co, scale=55.0, feature='DISTANCE_TO_EDGE', x=-1700, y=-1150)
    acr = g.math('MULTIPLY', g.mrange(allig.outputs["Distance"], 0.0, 0.03, 1.0, 0.0, x=-1500, y=-1150), paint,
                 x=-1300, y=-1150)
    dn = g.noise(g.mapping(co, scale=(8.0, 0.3, 1.0), x=-1900, y=-1400), scale=3.0, detail=3.0, x=-1700, y=-1400)
    dirt = g.mrange(g.fac(dn), 0.5, 0.72, x=-1500, y=-1400)
    grime = g.mrange(xyz[2], P["deck_z"] + 0.9, P["deck_z"] + 0.05, x=-1500, y=-1600)
    col = g.mix(paint, wood.outputs["Color"], pcol.outputs[0], x=-900, y=200)
    col = g.mix(g.math('MULTIPLY', acr, 0.7, x=-900, y=-50), col, (0.03, 0.008, 0.006), x=-700, y=150)
    col = g.mix(g.math('MULTIPLY', dirt, 0.5, x=-700, y=-50), col, (0.03, 0.025, 0.02), x=-500, y=100)
    col = g.mix(g.math('MULTIPLY', grime, 0.6, x=-500, y=-50), col, (0.02, 0.018, 0.015), x=-300, y=50)
    rough = g.mix(paint, wood.outputs["Roughness"], 0.6, dtype='FLOAT', x=-500, y=-200)
    h = g.mix(paint, g.math('MULTIPLY', wood.outputs["Height"], 0.4, x=-700, y=-300),
              g.math('MULTIPLY_ADD', g.fac(pn), 0.1, 0.5, x=-700, y=-400), dtype='FLOAT', x=-500, y=-350)
    h = g.math('MULTIPLY_ADD', edge, 0.3, h, x=-350, y=-350)
    h = g.math('MULTIPLY_ADD', acr, -0.2, h, x=-200, y=-350)
    nrm = g.bump(h, strength=0.45, distance=0.002, x=-50, y=-350)
    bsdf = g.principled(200, 0, base=col, rough=rough, normal=nrm, coat=0.22, coat_rough=0.12, coat_ior=1.33)
    return finish(m, g, out, bsdf.outputs[0])


def mat_roof():
    m, g, out = new_mat("M_Roof_TarShingle")
    tc = g.new('ShaderNodeTexCoord', -1600, 0)
    co = tc.outputs["Object"]
    rows = g.wave(co, scale=4.4, dist=0.4, detail=1.0, direction='Z', profile='SAW', x=-1300, y=0)
    n = g.noise(co, scale=3.0, detail=8.0, rough=0.6, x=-1300, y=-300)
    base = g.ramp(g.fac(n), [(0.3, (0.020, 0.021, 0.020)), (0.7, (0.050, 0.050, 0.046))], x=-1100, y=-300)
    lichen = g.mrange(g.fac(g.noise(co, scale=2.2, detail=6.0, x=-1300, y=-600)), 0.55, 0.62, x=-1100, y=-600)
    moss = g.mrange(g.fac(g.noise(co, scale=0.9, detail=5.0, x=-1300, y=-800)), 0.6, 0.7, x=-1100, y=-800)
    col = g.mix(lichen, base.outputs[0], (0.12, 0.13, 0.10), x=-800, y=-300)
    col = g.mix(moss, col, (0.02, 0.045, 0.008), x=-600, y=-300)
    h = g.math('MULTIPLY_ADD', g.fac(rows), 1.0, g.math('MULTIPLY', g.fac(n), 0.3, x=-1000, y=-100), x=-800, y=0)
    nrm = g.bump(h, strength=0.5, distance=0.01, x=-600, y=0)
    bsdf = g.principled(-300, 0, base=col, rough=0.75, normal=nrm, coat=0.15, coat_rough=0.2)
    return finish(m, g, out, bsdf.outputs[0])


def mat_glass_grimy():
    m, g, out = new_mat("M_Glass_Grimy")
    tc = g.new('ShaderNodeTexCoord', -1200, 0)
    dn = g.noise(tc.outputs["Object"], scale=6.0, detail=8.0, rough=0.65, x=-1000, y=0)
    dirt = g.mrange(g.fac(dn), 0.35, 0.7, 0.15, 0.85, x=-800, y=0)
    col = g.mix(dirt, (0.55, 0.57, 0.55), (0.06, 0.055, 0.045), x=-600, y=0)
    tr = g.math('SUBTRACT', 1.0, g.math('MULTIPLY', dirt, 0.7, x=-800, y=-200), x=-600, y=-200)
    rg = g.math('MULTIPLY_ADD', dirt, 0.5, 0.06, x=-600, y=-350)
    bsdf = g.principled(-300, 0, base=col, trans=tr, rough=rg, ior=1.52)
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


def mat_rope():
    m, g, out = new_mat("M_Rope")
    tc = g.new('ShaderNodeTexCoord', -1200, 0)
    w = g.wave(tc.outputs["Object"], scale=40.0, dist=1.0, detail=2.0, direction='DIAGONAL', x=-1000, y=0)
    nrm = g.bump(g.fac(w), strength=0.8, distance=0.003, x=-800, y=-200)
    col = g.mix(g.fac(w), (0.12, 0.10, 0.07), (0.22, 0.19, 0.14), x=-800, y=100)
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
    bsdf = g.principled(-150, 0, base=col, rough=0.5, coat=0.3, coat_rough=0.15)
    return finish(m, g, out, bsdf.outputs[0])


def mat_hull_paint():
    m, g, out = new_mat("M_Boat_HullPaint")
    tc = g.new('ShaderNodeTexCoord', -1400, 0)
    co = tc.outputs["Object"]
    z = g.separate(co, x=-1200, y=0)[2]
    stripe = g.mrange(z, 0.24, 0.27, x=-1000, y=100)
    bottom = g.mrange(z, 0.04, 0.0, x=-1000, y=-50)
    wear = g.mrange(g.fac(g.noise(co, scale=6.0, detail=10.0, rough=0.7, x=-1200, y=-250)), 0.6, 0.66, x=-1000, y=-250)
    col = g.mix(stripe, (0.018, 0.060, 0.075), (0.20, 0.035, 0.022), x=-800, y=100)
    col = g.mix(bottom, col, (0.05, 0.016, 0.010), x=-650, y=50)
    col = g.mix(wear, col, (0.16, 0.15, 0.13), x=-500, y=0)
    rough = g.mix(wear, 0.42, 0.8, dtype='FLOAT', x=-500, y=-250)
    bsdf = g.principled(-200, 0, base=col, rough=rough, coat=0.35, coat_rough=0.1, coat_ior=1.33)
    return finish(m, g, out, bsdf.outputs[0])


def mat_simple(name, col, rough=0.5, metal=0.0, coat=0.0, sheen=0.0, sss=0.0, sss_radius=(1.0, 0.35, 0.2),
               sss_scale=0.01, noise_scale=0.0, bump=0.0):
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
                        sheen=sheen, sss=sss, sss_radius=sss_radius, sss_scale=sss_scale, normal=nrm)
    return finish(m, g, out, bsdf.outputs[0])


def mat_fisherman(z_split):
    """Oilskin jacket above z_split, dark trousers below (boat-local object coordinates)."""
    m, g, out = new_mat("M_Fisherman_Clothes")
    tc = g.new('ShaderNodeTexCoord', -1400, 0)
    co = tc.outputs["Object"]
    z = g.separate(co, x=-1200, y=0)[2]
    top = g.mrange(z, z_split - 0.02, z_split + 0.02, x=-1000, y=0)
    wr = g.noise(co, scale=12.0, detail=4.0, rough=0.5, x=-1200, y=-300)
    col = g.mix(top, (0.012, 0.014, 0.020), (0.050, 0.043, 0.017), x=-800, y=0)
    rough = g.mix(top, 0.7, 0.36, dtype='FLOAT', x=-800, y=-150)
    coat = g.mix(top, 0.1, 0.5, dtype='FLOAT', x=-800, y=-300)
    nrm = g.bump(g.fac(wr), strength=0.35, distance=0.01, x=-800, y=-450)
    bsdf = g.principled(-400, 0, base=col, rough=rough, coat=coat, coat_rough=0.15, normal=nrm)
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
    prof = g.math('MULTIPLY', g.mrange(z, 0.0, 0.25, 0.2, 1.0, x=-800, y=100), g.mrange(z, 1.0, 0.55, 0.0, 1.0,
                                                                                         x=-800, y=-50), x=-600, y=0)
    bb = g.new('ShaderNodeBlackbody', -800, -250)
    bb.inputs["Temperature"].default_value = 1750.0
    em = g.new('ShaderNodeEmission', -300, 0)
    g.link(bb.outputs[0], em.inputs["Color"])
    g.feed(em.inputs["Strength"], g.math('MULTIPLY', prof, 45.0, x=-450, y=-100))
    set_emission_sampling(m, 'NONE')
    return finish(m, g, out, em.outputs[0])


def mat_rock():
    m, g, out = new_mat("M_MountainRock")
    tc = g.new('ShaderNodeTexCoord', -1600, 0)
    co = tc.outputs["Object"]
    geo = g.new('ShaderNodeNewGeometry', -1600, -400)
    nz = g.separate(geo.outputs["Normal"], x=-1400, y=-400)[2]
    z = g.separate(geo.outputs["Position"], x=-1400, y=-550)[2]
    n1 = g.noise(g.vmath('SCALE', co, s=0.004, x=-1400, y=0), scale=1.0, detail=9.0, rough=0.62, x=-1200, y=0)
    rock = g.ramp(g.fac(n1), [(0.3, (0.028, 0.028, 0.030)), (0.7, (0.075, 0.074, 0.072))], x=-1000, y=0)
    steep = g.mrange(nz, 0.75, 0.45, x=-1200, y=-400)
    forest = g.math('MULTIPLY', g.mrange(z, 700.0, 350.0, x=-1200, y=-550), g.mrange(nz, 0.55, 0.8, x=-1200, y=-700),
                    clamp=True, x=-1000, y=-600)
    scree = g.mrange(g.fac(g.noise(g.vmath('SCALE', co, s=0.01, x=-1400, y=-900), scale=1.0, detail=4.0,
                                   x=-1200, y=-900)), 0.55, 0.7, x=-1000, y=-900)
    scree = g.math('MULTIPLY', scree, g.math('SUBTRACT', 1.0, g.math('MAXIMUM', forest, steep, x=-1000, y=-1050),
                                             x=-850, y=-1050), x=-700, y=-950)
    col = g.mix(forest, rock.outputs[0], (0.010, 0.014, 0.009), x=-700, y=0)
    col = g.mix(g.math('MULTIPLY', scree, 0.7, x=-700, y=-200), col, (0.11, 0.108, 0.10), x=-500, y=0)
    fine = g.noise(g.vmath('SCALE', co, s=0.05, x=-1400, y=-1250), scale=1.0, detail=6.0, x=-1200, y=-1250)
    h = g.math('MULTIPLY_ADD', g.fac(fine), 0.3, g.fac(n1), x=-900, y=-1250)
    nrm = g.bump(h, strength=0.6, distance=4.0, x=-700, y=-1250)
    bsdf = g.principled(-300, 0, base=col, rough=0.88, normal=nrm)
    return finish(m, g, out, bsdf.outputs[0])


def _volume(name, density_sock, g, out, m, color=(0.78, 0.81, 0.85), aniso=0.5):
    pv = g.new('ShaderNodeVolumePrincipled', 600, 0)
    g.feed(pv.inputs["Color"], color)
    g.feed(pv.inputs["Density"], density_sock)
    pv.inputs["Anisotropy"].default_value = aniso
    set_volume_opts(m)
    return finish(m, g, out, volume=pv.outputs[0])


def mat_fog():
    """Sea fog hugging the water: exp height falloff x drifting 4D noise."""
    m, g, out = new_mat("M_Fog_Sea")
    geo = g.new('ShaderNodeNewGeometry', -1600, 0)
    pos = geo.outputs["Position"]
    z = g.separate(pos, x=-1400, y=0)[2]
    dens = g.value(P["fog_density"], -1600, -300, "Fog Density (1/m)")
    fz = g.math('EXPONENT', g.math('MULTIPLY', g.math('MAXIMUM', z, 0.0, x=-1200, y=0), -1.0 / P["fog_height"],
                                   x=-1050, y=0), x=-900, y=0)
    tv = g.value(0.0, -1600, -500, "Drift")
    add_driver(tv.outputs[0], "default_value", "frame*0.004")
    drift = g.combine(g.math('MULTIPLY', tv.outputs[0], 3.0, x=-1400, y=-500),
                      g.math('MULTIPLY', tv.outputs[0], -1.0, x=-1400, y=-650), 0.0, x=-1250, y=-550)
    pp = g.vmath('ADD', pos, drift, x=-1100, y=-400)
    n = g.noise(g.vmath('SCALE', pp, s=0.045, x=-950, y=-400), scale=1.0, detail=3.0, rough=0.5, dims='4D',
                w=tv.outputs[0], x=-800, y=-400)
    nm = g.mrange(g.fac(n), 0.3, 0.72, 0.25, 1.45, x=-600, y=-400)
    d = g.math('MULTIPLY', g.math('MULTIPLY', fz, nm, x=-400, y=-100), dens.outputs[0], x=-200, y=-100)
    return _volume("fog", d, g, out, m, (0.78, 0.81, 0.85), 0.5)


def mat_haze():
    m, g, out = new_mat("M_Haze_Atmosphere")
    geo = g.new('ShaderNodeNewGeometry', -1200, 0)
    z = g.separate(geo.outputs["Position"], x=-1000, y=0)[2]
    fz = g.math('EXPONENT', g.math('MULTIPLY', g.math('MAXIMUM', z, 0.0, x=-800, y=0), -1.0 / 700.0, x=-650, y=0),
                x=-500, y=0)
    dens = g.value(P["haze_density"], -1200, -300, "Haze Density (1/m)")
    d = g.math('MULTIPLY', fz, dens.outputs[0], x=-300, y=0)
    return _volume("haze", d, g, out, m, (0.80, 0.82, 0.86), 0.3)


def mat_cloud(name, z0, z1, scale, thresh):
    """Low cloud / fog bank draped over the mountains (world-space noise, vertical profile z0..z1)."""
    m, g, out = new_mat(name)
    geo = g.new('ShaderNodeNewGeometry', -1600, 0)
    pos = geo.outputs["Position"]
    z = g.separate(pos, x=-1400, y=0)[2]
    lo = g.mrange(z, z0, z0 + 0.35 * (z1 - z0), interp='SMOOTHSTEP', x=-1200, y=100)
    hi = g.mrange(z, z1, z1 - 0.55 * (z1 - z0), interp='SMOOTHSTEP', x=-1200, y=-50)
    prof = g.math('MULTIPLY', lo, hi, x=-1000, y=0)
    tv = g.value(0.0, -1600, -400, "Drift")
    add_driver(tv.outputs[0], "default_value", "frame*0.002")
    n = g.noise(g.vmath('SCALE', pos, s=1.0 / scale, x=-1200, y=-400), scale=1.0, detail=6.0, rough=0.55,
                dist=0.3, dims='4D', w=tv.outputs[0], x=-1000, y=-400)
    shape = g.math('SUBTRACT', g.fac(n), g.math('MULTIPLY_ADD', g.math('SUBTRACT', 1.0, prof, x=-900, y=-200), 0.35,
                                                 thresh, x=-750, y=-200), x=-600, y=-300)
    dens = g.value(P["cloud_density"], -1600, -700, "Cloud Density (1/m)")
    d = g.math('MULTIPLY', g.mrange(shape, 0.0, 0.18, 0.0, 1.0, interp='SMOOTHSTEP', x=-450, y=-300),
               dens.outputs[0], x=-250, y=-300)
    return _volume(name, d, g, out, m, (0.80, 0.83, 0.87), 0.35)


def build_materials():
    MATS["deck"] = mat_deck()
    MATS["wood_trim"] = mat_wood("M_Wood_Trim", 0.95, 1.0, 0.25, 0.35, 0.6)
    MATS["wood_dark"] = mat_wood("M_Wood_Structure", 0.6, 0.45, 0.35, 0.6, 0.5)
    MATS["wood_mast"] = mat_wood("M_Wood_Mast", 0.9, 0.8, 0.2, 0.3, 0.7, axis='Z')
    MATS["moss_bed"] = mat_moss_bed()
    MATS["moss_hair"] = mat_moss_hair()
    MATS["algae_hair"] = mat_algae_hair()
    MATS["leaf"] = mat_leaf()
    MATS["piling"] = mat_piling()
    MATS["ocean"] = mat_ocean()
    MATS["paint"] = mat_paint()
    MATS["roof"] = mat_roof()
    MATS["glass"] = mat_glass_grimy()
    MATS["rust"] = mat_rust()
    MATS["rope"] = mat_rope()
    MATS["buoy"] = mat_buoy()
    MATS["hull_paint"] = mat_hull_paint()
    MATS["hull_inside"] = mat_simple("M_Boat_HullInside", (0.20, 0.19, 0.17), 0.75, noise_scale=5.0, bump=0.3)
    MATS["outboard"] = mat_simple("M_Outboard", (0.025, 0.026, 0.028), 0.35, coat=0.3, noise_scale=20.0)
    MATS["skin"] = mat_simple("M_Skin", (0.30, 0.17, 0.12), 0.5, sss=0.3, sss_radius=(1.0, 0.35, 0.2),
                              sss_scale=0.008)
    MATS["knit"] = mat_simple("M_KnitCap", (0.10, 0.022, 0.018), 1.0, sheen=0.6, noise_scale=90.0, bump=0.5)
    MATS["dark"] = mat_simple("M_Interior_Dark", (0.012, 0.010, 0.008), 0.9)
    MATS["lamp_paint"] = mat_lamp_paint()
    MATS["lamp_glass"] = mat_lamp_glass()
    MATS["flame"] = mat_flame()
    MATS["rock"] = mat_rock()
    MATS["fog"] = mat_fog()
    MATS["haze"] = mat_haze()
    MATS["cloud_low"] = mat_cloud("M_CloudBank_Low", -20.0, 380.0, 420.0, 0.46)
    MATS["cloud_high"] = mat_cloud("M_CloudBank_High", 450.0, 1250.0, 650.0, 0.50)


# ============================================================================
#  DECK
# ============================================================================
def add_board(bm, lay, x0, x1, yc, w, t, ztop, rnd, prm, rng):
    """One warped plank running along X. 6-point section (cupped top), jagged broken ends."""
    L = x1 - x0
    nseg = max(2, int(L / 0.3))
    hw = w * 0.5
    cup = prm["cup"]
    sec = [(-hw, -t), (-hw, 0.0), (0.0, cup), (hw, 0.0), (hw, -t), (0.0, -t + cup * 0.5)]
    end0 = [rng.uniform(0.0, 0.07) for _ in sec] if prm["broken0"] else [0.0] * 6
    end1 = [-rng.uniform(0.0, 0.07) for _ in sec] if prm["broken1"] else [0.0] * 6
    xm = 0.5 * (x0 + x1)
    ttilt = tan(prm["tilt"])
    rings = []
    for i in range(nseg + 1):
        s = i / nseg
        x = x0 + L * s
        ang = prm["roll"] + prm["twist"] * (s - 0.5)
        ca, sa = cos(ang), sin(ang)
        zc = ztop + prm["zoff"] - prm["sag"] * 4.0 * s * (1.0 - s) + (x - xm) * ttilt
        ring = []
        for j, (dy, dz) in enumerate(sec):
            ry = dy * ca - dz * sa
            rz = dy * sa + dz * ca
            xo = end0[j] if i == 0 else (end1[j] if i == nseg else 0.0)
            v = bm.verts.new((x + xo, yc + ry, zc + rz))
            v[lay] = rnd
            ring.append(v)
        rings.append(ring)
    n = len(sec)
    for a, b in zip(rings[:-1], rings[1:]):
        for j in range(n):
            k = (j + 1) % n
            bm.faces.new((a[j], b[j], b[k], a[k]))
    bm.faces.new(rings[0])
    bm.faces.new(list(reversed(rings[-1])))


def covered(intervals, x, margin=0.03):
    for a, b in intervals:
        if a + margin <= x <= b - margin:
            return True
    return False


def build_deck():
    c = coll("10_Deck")
    rng = random.Random(P["seed"] + 1)
    DZ = P["deck_z"]
    T = 0.042
    HX0, HX1, HY0, HY1 = P["house"]
    bm = bmesh.new()
    lay = bm.verts.layers.float.new("board_rand")
    rows = []
    y = P["deck_y0"]
    while y < P["deck_y2"]:
        w = rng.uniform(0.165, 0.255)
        r = rng.random()
        gap = (rng.uniform(0.007, 0.018) if r < 0.6 else
               (rng.uniform(0.018, 0.04) if r < 0.93 else rng.uniform(0.05, 0.09)))
        yc = y + 0.5 * w
        x0 = P["deck_x0"] if yc < P["deck_y1"] else P["walk_x0"]
        rows.append((yc, w, gap, x0, P["deck_x1"]))
        y += w + gap
    cover = []
    boards = []
    for (yc, w, gap, x0, x1) in rows:
        col = smoothstep(P["collapse_y"], P["deck_y2"], yc)
        segs = []
        x = x0 + rng.uniform(0.0, 0.02)
        while x < x1 - 0.2:
            L = rng.uniform(1.8, 3.8) if (x1 - x0) > 3.0 else (x1 - x0)
            xe = min(x + L, x1 - rng.uniform(0.0, 0.01))
            segs.append((x, xe))
            x = xe + rng.uniform(0.004, 0.018)
        if HY0 + 0.05 < yc < HY1 - 0.05:            # nothing under the house floor
            cut = []
            for a, b in segs:
                if b <= HX0 + 0.05 or a >= HX1 - 0.05:
                    cut.append((a, b))
                    continue
                if a < HX0 + 0.05:
                    cut.append((a, HX0 + 0.05))
                if b > HX1 - 0.05:
                    cut.append((HX1 - 0.05, b))
            segs = [(a, b) for a, b in cut if b - a > 0.2]
        row_cover = []
        for a, b in segs:
            if rng.random() < 0.025 + 0.5 * col:
                continue                              # missing plank
            prm = dict(roll=rng.gauss(0, radians(1.3)) + rng.gauss(0, radians(10.0)) * col,
                       twist=rng.gauss(0, radians(0.9)),
                       sag=rng.gauss(0.0, 0.006) + 0.12 * col * rng.random(),
                       zoff=rng.gauss(0, 0.004) - 0.5 * col - rng.uniform(0.0, 0.15) * col,
                       tilt=rng.gauss(0, radians(0.4)) + rng.gauss(0, radians(7.0)) * col,
                       cup=rng.gauss(0, 0.0025),
                       broken0=rng.random() < 0.08 + 0.4 * col,
                       broken1=rng.random() < 0.08 + 0.4 * col)
            add_board(bm, lay, a, b, yc, w, T, DZ, rng.random(), prm, rng)
            boards.append((a, b, yc, w, DZ + prm["zoff"], prm["roll"], col))
            if col < 0.05:
                row_cover.append((a, b))
        cover.append(row_cover)
    ob = bm_to_obj("Deck_Boards", bm, c, MATS["deck"], smooth=True, sharp=35.0, recalc=True)
    bev = ob.modifiers.new("Bevel", 'BEVEL')
    bev.width = 0.0035
    bev.segments = 2
    bev.limit_method = 'ANGLE'
    bev.angle_limit = radians(35.0)
    setp(bev, "harden_normals", True)
    DECK["rows"] = rows
    DECK["cover"] = cover
    DECK["boards"] = boards
    build_deck_structure(c, rng)
    return ob


def build_deck_structure(c, rng):
    DZ = P["deck_z"]
    top = DZ - 0.042
    bm = bmesh.new()
    y0, y1, yc_ = P["deck_y0"], P["deck_y1"], P["collapse_y"]
    for xj in (-3.62, -2.45, -1.25, -0.05, 1.15, 1.95):          # platform joists (along Y)
        bm_box(bm, (xj, 0.5 * (y0 + y1), top - 0.11), (0.075, y1 - y0, 0.22))
    for xj in (0.22, 1.95):                                      # walkway joists
        bm_box(bm, (xj, 0.5 * (y1 + yc_), top - 0.11), (0.075, yc_ - y1, 0.22))
        L = 3.2
        ang = radians(rng.uniform(7.0, 12.0))                   # broken, sagging end
        bm_box(bm, (xj, yc_ + 0.5 * L * cos(ang), top - 0.11 - 0.5 * L * sin(ang)), (0.075, L, 0.22),
               rot=(-ang, 0.0, 0.0))
    for yp in (-3.5, 0.5, 4.5, 8.5, 12.5):                       # pile caps (along X)
        bm_box(bm, (0.5 * (P["deck_x0"] + P["deck_x1"]), yp, top - 0.34),
               (P["deck_x1"] - P["deck_x0"] + 0.1, 0.2, 0.24))
    bm_box(bm, (0.5 * (P["walk_x0"] + P["deck_x1"]), 16.5, top - 0.34), (P["deck_x1"] - P["walk_x0"] + 0.4, 0.2, 0.24))
    ob = bm_to_obj("Deck_Structure", bm, c, MATS["wood_dark"], recalc=True)
    bev = ob.modifiers.new("Bevel", 'BEVEL')
    bev.width = 0.006
    bev.segments = 1
    return ob


def add_strip(bm, x0, x1, yc, width, z, step=0.1):
    nx = max(1, int((x1 - x0) / step))
    vs = []
    for i in range(nx + 1):
        x = x0 + (x1 - x0) * i / nx
        vs.append((bm.verts.new((x, yc - 0.5 * width, z)), bm.verts.new((x, yc + 0.5 * width, z))))
    for (a0, a1), (b0, b1) in zip(vs[:-1], vs[1:]):
        bm.faces.new((a0, b0, b1, a1))
    return (x1 - x0) * width


def build_moss():
    """Moss beds tucked into the board gaps + moss cushions on the boards, both with hair."""
    c = coll("10_Deck")
    rng = random.Random(P["seed"] + 7)
    DZ = P["deck_z"]
    HX0, HX1, HY0, HY1 = P["house"]
    rows, cover = DECK["rows"], DECK["cover"]
    bm = bmesh.new()
    area = 0.0
    for i in range(len(rows) - 1):
        yc0, w0, gap0, a0, b0 = rows[i]
        ygap = yc0 + 0.5 * w0 + 0.5 * gap0
        if ygap > P["collapse_y"] - 0.5:
            break
        width = gap0 + 0.026
        xs, x = [], max(a0, rows[i + 1][3])
        xend = min(b0, rows[i + 1][4])
        while x < xend - 1e-6:
            inside = (HX0 - 0.05 < x < HX1 + 0.05) and (HY0 - 0.05 < ygap < HY1 + 0.05)
            good = (not inside and covered(cover[i], x) and covered(cover[i + 1], x)
                    and noise.noise(Vector((x * 0.8, ygap * 3.1, 0.5))) > -0.25)
            xs.append((x, good))
            x += 0.1
        start = None
        for k, (xx, good) in enumerate(xs):
            last = k == len(xs) - 1
            if good and start is None:
                start = xx
            if start is not None and (not good or last):
                end = xx + 0.1 if (good and last) else xx
                if end - start >= 0.15:
                    area += add_strip(bm, start, end, ygap, width, DZ - 0.028)
                start = None
    beds = bm_to_obj("Deck_MossBeds", bm, c, [MATS["moss_bed"], MATS["moss_hair"]])
    vg = beds.vertex_groups.new(name="moss")
    for v in beds.data.vertices:
        w = 0.35 + 0.65 * smoothstep(-0.2, 0.35, noise.noise(v.co * 2.3))
        vg.add([v.index], w, 'REPLACE')
    # cushions on the boards near the camera
    bm = bmesh.new()
    carea = 0.0
    cand = [b for b in DECK["boards"] if b[2] < 12.0 and b[6] < 0.05 and abs(b[5]) < radians(2.5)]
    rng.shuffle(cand)
    for (a, b, yc, w, ztop, roll, col) in cand[:90]:
        r = rng.uniform(0.025, 0.09)
        cx = rng.uniform(a + 0.1, b - 0.1)
        if HX0 - 0.1 < cx < HX1 + 0.1 and HY0 - 0.1 < yc < HY1 + 0.1:
            continue
        side = rng.choice((-1.0, 1.0))
        cy = yc + side * (0.5 * w - r * 0.45)
        z = ztop + 0.0035
        vs = []
        for k in range(10):
            ang = 2 * pi * k / 10
            rr = r * (0.7 + 0.5 * (0.5 + 0.5 * noise.noise(Vector((cos(ang) * 2 + cx * 7, sin(ang) * 2, yc)))))
            py = cy + rr * sin(ang)
            py = max(yc - 0.5 * w + 0.004, min(yc + 0.5 * w - 0.004, py))   # stay on the plank
            vs.append(bm.verts.new((cx + rr * cos(ang), py, z)))
        bm.faces.new(vs)
        carea += pi * r * r
    cush = bm_to_obj("Deck_MossCushions", bm, c, [MATS["moss_bed"], MATS["moss_hair"]])
    if P["use_hair"]:
        add_hair(beds, "MossBeds", count=int(min(40000, max(2000, area * 3500))), length=0.034, children=12,
                 radius=0.00018, mat_name=MATS["moss_hair"].name, vg="moss", seed=11, normal=1.0,
                 align=(0.0, 0.0, 0.3), rand=0.35, clump=0.45, rough=0.003, steps=3, tip=0.3, display=4)
        add_hair(cush, "MossCushions", count=int(min(15000, max(1000, carea * 20000))), length=0.014, children=12,
                 radius=0.00016, mat_name=MATS["moss_hair"].name, seed=12, normal=1.0, align=(0.0, 0.0, 0.2),
                 rand=0.4, clump=0.5, rough=0.002, steps=3, tip=0.3, display=4)
    return beds


def make_sprout_mesh(name, rng, n_leaves, leaf_len, leaf_w, tilt):
    bm = bmesh.new()
    for i in range(n_leaves):
        a = 2 * pi * i / n_leaves + rng.uniform(-0.25, 0.25)
        L = leaf_len * rng.uniform(0.75, 1.2)
        W = leaf_w * rng.uniform(0.8, 1.2)
        tl = tilt + rng.uniform(-0.2, 0.2)
        ca, sa = cos(a), sin(a)
        rws = []
        for j in range(6):
            s = j / 5.0
            half = 0.5 * W * sin(pi * min(1.0, s * 1.1 + 0.02)) + 0.0004
            r = L * s * cos(tl)
            zl = L * s * sin(tl) - (s ** 2) * L * 0.3
            row = []
            for k, off in enumerate((-half, 0.0, half)):
                dz = -0.25 * half if k == 1 else 0.0
                row.append(bm.verts.new((r * ca - off * sa, r * sa + off * ca, zl + dz + 0.004)))
            rws.append(row)
        for r0, r1 in zip(rws[:-1], rws[1:]):
            bm.faces.new((r0[0], r1[0], r1[1], r0[1]))
            bm.faces.new((r0[1], r1[1], r1[2], r0[2]))
    bm_cyl(bm, (0.0, 0.0, 0.0), 0.0022, 0.012, segs=6)
    for f in bm.faces:
        f.smooth = True
    me = bpy.data.meshes.new("H3_" + name)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(MATS["leaf"])
    return me


def build_sprouts():
    c = coll("10_Deck")
    rng = random.Random(P["seed"] + 8)
    HX0, HX1, HY0, HY1 = P["house"]
    meshes = [make_sprout_mesh("SproutA", rng, 6, 0.028, 0.013, 0.55),
              make_sprout_mesh("SproutB", rng, 8, 0.020, 0.011, 0.70),
              make_sprout_mesh("GrassTuft", rng, 9, 0.075, 0.006, 1.15)]
    rows, cover = DECK["rows"], DECK["cover"]
    n, tries = 0, 0
    while n < 55 and tries < 3000:
        tries += 1
        i = rng.randrange(0, len(rows) - 1)
        yc0, w0, gap0, a0, b0 = rows[i]
        ygap = yc0 + 0.5 * w0 + 0.5 * gap0
        if not (0.6 < ygap < 11.0):
            continue
        lo, hi = max(a0, rows[i + 1][3]) + 0.1, min(b0, rows[i + 1][4]) - 0.1
        if hi <= lo:
            continue
        x = rng.uniform(lo, hi)
        if HX0 - 0.1 < x < HX1 + 0.1 and HY0 - 0.1 < ygap < HY1 + 0.1:
            continue
        if not (covered(cover[i], x) and covered(cover[i + 1], x)):
            continue
        if abs(x - P["deck_cam"]["loc"][0]) < 0.15 and ygap < 1.3:
            continue                                   # keep the lens clear
        r = rng.random()
        me = meshes[0] if r < 0.45 else (meshes[1] if r < 0.75 else meshes[2])
        ob = link_obj("Sprout_%02d" % n, me, c)
        ob.location = (x, ygap, P["deck_z"] - 0.012)
        ob.rotation_euler = (rng.gauss(0, 0.08), rng.gauss(0, 0.08), rng.uniform(0, 2 * pi))
        s = rng.uniform(0.7, 1.45)
        ob.scale = (s, s, s)
        n += 1
    return n


# ============================================================================
#  PILINGS
# ============================================================================
def piling_specs():
    rng = random.Random(P["seed"] + 3)
    DZ = P["deck_z"]
    fx = P["fender_x"]
    s = []   # (name, x, y, top_z, radius, (lean_x, lean_y), erosion, hair)
    for yp in (-3.5, 0.5, 4.5, 8.5, 12.5):                       # platform supports (hidden)
        for xp in (-3.6, -1.2, 1.2):
            s.append(("Pile_Support", xp + rng.uniform(-0.05, 0.05), yp, DZ - 0.30, rng.uniform(0.13, 0.16),
                      (0.0, 0.0), 0.8, False))
    for yp in (5.6, 9.6, 13.6, 17.6, 21.6):                      # fender row: the hero pilings
        s.append(("Pile_Fender", fx + rng.uniform(-0.03, 0.05), yp + rng.uniform(-0.15, 0.15),
                  DZ + rng.uniform(0.2, 0.75), rng.uniform(0.14, 0.17),
                  (rng.gauss(0, 0.02), rng.gauss(0, 0.02)), 1.0, True))
    for yp, tops in ((16.5, (0.55, 0.3)), (20.5, (0.25, 0.05))):   # walkway posts
        for xp, tp in zip((-0.08, 2.18), tops):
            colf = smoothstep(19.0, 24.0, yp)
            s.append(("Pile_Walk", xp, yp, DZ + tp, rng.uniform(0.13, 0.16),
                      (rng.gauss(0, 0.05 * colf), rng.gauss(0, 0.07 * colf)), 1.0, True))
    for yp in (24.5, 27.0, 29.5, 32.0, 34.5):                    # ruins past the collapsed end
        for xp in (0.1, 2.0):
            if rng.random() < 0.18:
                continue
            s.append(("Pile_Ruin", xp + rng.uniform(-0.12, 0.12), yp + rng.uniform(-0.25, 0.25),
                      rng.uniform(0.25, 1.25), rng.uniform(0.13, 0.17),
                      (rng.gauss(0, 0.07), rng.gauss(0, 0.07)), 1.2, True))
    for k in range(3):                                           # mooring dolphin
        a = 2 * pi * k / 3
        s.append(("Pile_Dolphin", 8.6 + 0.2 * cos(a), 8.2 + 0.2 * sin(a), 1.7 + rng.uniform(-0.2, 0.2), 0.15,
                  (0.06 * sin(a), -0.06 * cos(a)), 1.0, True))
    return s


def make_piling(name, x, y, z0, z1, r, rng, lean, erosion, c, mats):
    bm = bmesh.new()
    segs = 20
    zs, z = [], z0
    while z < z1 - 0.25:
        zs.append(z)
        z += 0.06 if -0.9 < z < 1.1 else 0.2
    zs.append(z1)
    sd = rng.uniform(0, 100)
    rings = []
    for zi in zs:
        ring = []
        for k in range(segs):
            a = 2 * pi * k / segs
            ca, sa = cos(a), sin(a)
            neck = erosion * 0.26 * exp(-((zi - 0.05) / 0.45) ** 2)          # tidal-zone necking
            p = Vector((ca * 1.3 + sd, sa * 1.3, zi * 1.1))
            pit = noise.noise(p * 3.0) * 0.07 * (0.3 + erosion * exp(-((zi - 0.1) / 0.7) ** 2))
            crk = abs(noise.noise(Vector((ca * 4.0 + sd, sa * 4.0, zi * 0.35))))
            groove = -0.06 * (1.0 - smoothstep(0.0, 0.12, crk))           # grain splits
            rr = r * max(0.35, 1.0 - neck + pit + groove)
            ring.append(bm.verts.new((ca * rr, sa * rr, zi)))
        rings.append(ring)
    for k, v in enumerate(rings[-1]):                                     # rotten, ragged top
        v.co.z -= abs(noise.noise(Vector((k * 0.37 + sd, 0.0, 0.0)))) * 0.14 + rng.uniform(0.0, 0.03)
    for ra, rb in zip(rings[:-1], rings[1:]):
        for k in range(segs):
            k2 = (k + 1) % segs
            bm.faces.new((ra[k], ra[k2], rb[k2], rb[k]))
    bm.faces.new(list(reversed(rings[0])))
    tf = bm.faces.new(rings[-1])
    try:
        bmesh.ops.inset_region(bm, faces=[tf], thickness=r * 0.3, depth=-0.025)
    except Exception as e:
        log("inset", e)
    ob = bm_to_obj(name, bm, c, mats, smooth=True, recalc=True)
    ob.location = (x, y, 0.0)
    ob.rotation_euler = (lean[0], lean[1], rng.uniform(0, 2 * pi))
    return ob


def add_algae_hair(ob, rng):
    vg = ob.vertex_groups.new(name="algae")
    for v in ob.data.vertices:
        z = v.co.z
        w = smoothstep(-0.55, -0.3, z) * (1.0 - smoothstep(0.2, 0.45, z))
        w *= 0.5 + 0.5 * noise.noise(v.co * 4.0)
        if w > 0.01:
            vg.add([v.index], min(1.0, w), 'REPLACE')
    add_hair(ob, "AlgaeHair", count=700, length=0.075, children=10, radius=0.0005,
             mat_name=MATS["algae_hair"].name, vg="algae", seed=rng.randint(0, 9999),
             normal=0.25, align=(0.0, 0.0, -0.07), rand=0.06, clump=0.5, rough=0.002, steps=4, tip=0.1, display=5,
             n_hint=(1.0, 0.0, 0.0))


def build_pilings():
    c = coll("30_Pilings")
    rng = random.Random(P["seed"] + 4)
    n = 0
    for idx, (nm, x, y, top, r, lean, ero, hair) in enumerate(piling_specs()):
        ob = make_piling("%s_%02d" % (nm, idx), x, y, -3.2, top, r, rng, lean, ero, c,
                         [MATS["piling"], MATS["algae_hair"]])
        if hair and P["use_hair"]:
            add_algae_hair(ob, rng)                   # particle system BEFORE the subdivision...
        add_subsurf(ob, levels=0, render=2, adaptive=True, pixel=1.5)   # ...which must stay last
        n += 1
    # rope wrapped around the dolphin
    make_curve("Dolphin_Rope", [(8.6 + 0.36 * cos(a), 8.2 + 0.36 * sin(a), 1.25 + 0.012 * a)
                                for a in [i * 0.2 for i in range(95)]], c, 0.016, MATS["rope"])
    return n


# ============================================================================
#  FISH HOUSE
# ============================================================================
def add_clapboard(bm, lay, O, U, N, u0b, u1b, zb, zt, rng, u0t=None, u1t=None):
    """Tapered, overlapping clapboard (thin at the top, thick at the drip edge)."""
    if u0t is None:
        u0t, u1t = u0b, u1b
    rnd = rng.random()
    Lb = max(u1b, u1t) - min(u0b, u0t)
    nseg = max(1, int(Lb / 0.45))
    dz = rng.gauss(0, 0.003)
    wav = rng.uniform(0.0, 0.004)
    ph = rng.uniform(0, 6.28)
    sprung = rng.random() < 0.07
    lift0 = rng.uniform(0.01, 0.045) if (sprung and rng.random() < 0.5) else 0.0
    lift1 = rng.uniform(0.01, 0.045) if (sprung and lift0 == 0.0) else 0.0
    cs = [(0.0, zt), (0.007, zt), (0.030, zb), (0.012, zb)]
    Z = Vector((0, 0, 1))
    rings = []
    for i in range(nseg + 1):
        s = i / nseg
        ub = u0b + (u1b - u0b) * s
        ut = u0t + (u1t - u0t) * s
        dd = wav * sin(ph + s * 6.0) + lift0 * (1 - s) ** 2 + lift1 * s ** 2
        ring = []
        for k, (d, zz) in enumerate(cs):
            uu = ut if k < 2 else ub
            v = bm.verts.new(O + U * uu + N * (d + dd + 0.004) + Z * (zz + dz))
            v[lay] = rnd
            ring.append(v)
        rings.append(ring)
    for a, b in zip(rings[:-1], rings[1:]):
        for k in range(4):
            k2 = (k + 1) % 4
            bm.faces.new((a[k], b[k], b[k2], a[k2]))
    bm.faces.new(rings[0])
    bm.faces.new(list(reversed(rings[-1])))


def wall_panel(bm, O, U, N, L, z0, z1, holes, d=-0.004):
    Z = Vector((0, 0, 1))
    us = sorted(set([0.0, L] + [min(L, max(0.0, h[0])) for h in holes] + [min(L, max(0.0, h[1])) for h in holes]))
    zs = sorted(set([z0, z1] + [h[2] for h in holes if z0 < h[2] < z1] + [h[3] for h in holes if z0 < h[3] < z1]))
    for ua, ub in zip(us[:-1], us[1:]):
        for za, zb in zip(zs[:-1], zs[1:]):
            um, zm = 0.5 * (ua + ub), 0.5 * (za + zb)
            if any(h[0] <= um <= h[1] and h[2] <= zm <= h[3] for h in holes):
                continue
            pts = [O + U * ua + N * d + Z * za, O + U * ub + N * d + Z * za,
                   O + U * ub + N * d + Z * zb, O + U * ua + N * d + Z * zb]
            bm.faces.new([bm.verts.new(p) for p in pts])


def add_window(bmf, bmg, O, U, N, u0, u1, z0, z1, rng, cols=2, rows=2, broken=True):
    Z = Vector((0, 0, 1))
    w = 0.07
    wbox(bmf, O, U, N, u0 - w, u1 + w, z1, z1 + w, 0.03, 0.052)                      # head casing
    wbox(bmf, O, U, N, u0 - w - 0.03, u1 + w + 0.03, z0 - 0.045, z0, 0.02, 0.085)    # sill
    wbox(bmf, O, U, N, u0 - w, u0, z0, z1, 0.03, 0.052)                              # side casings
    wbox(bmf, O, U, N, u1, u1 + w, z0, z1, 0.03, 0.052)
    wbox(bmf, O, U, N, u0, u1, z0, z0 + 0.03, -0.06, 0.03)                           # jambs
    wbox(bmf, O, U, N, u0, u0 + 0.025, z0, z1, -0.06, 0.03)
    wbox(bmf, O, U, N, u1 - 0.025, u1, z0, z1, -0.06, 0.03)
    wbox(bmf, O, U, N, u0, u1, z1 - 0.025, z1, -0.06, 0.03)
    fr = 0.035
    su0, su1, sz0, sz1 = u0 + 0.025, u1 - 0.025, z0 + 0.03, z1 - 0.025
    wbox(bmf, O, U, N, su0, su1, sz0, sz0 + fr, -0.035, -0.005)                     # sash
    wbox(bmf, O, U, N, su0, su1, sz1 - fr, sz1, -0.035, -0.005)
    wbox(bmf, O, U, N, su0, su0 + fr, sz0, sz1, -0.035, -0.005)
    wbox(bmf, O, U, N, su1 - fr, su1, sz0, sz1, -0.035, -0.005)
    for k in range(1, cols):                                                         # muntins
        uc = su0 + (su1 - su0) * k / cols
        wbox(bmf, O, U, N, uc - 0.011, uc + 0.011, sz0, sz1, -0.03, -0.01)
    for k in range(1, rows):
        zc = sz0 + (sz1 - sz0) * k / rows
        wbox(bmf, O, U, N, su0, su1, zc - 0.011, zc + 0.011, -0.03, -0.01)
    bad = (rng.randrange(cols), rng.randrange(rows)) if broken else None
    d = -0.02
    for ci in range(cols):
        for ri in range(rows):
            a = su0 + (su1 - su0) * ci / cols
            b = su0 + (su1 - su0) * (ci + 1) / cols
            za = sz0 + (sz1 - sz0) * ri / rows
            zb = sz0 + (sz1 - sz0) * (ri + 1) / rows
            if (ci, ri) == bad:                          # broken pane: one jagged shard left
                pts = [O + U * a + N * d + Z * za, O + U * (a + 0.7 * (b - a)) + N * d + Z * za,
                       O + U * (a + 0.2 * (b - a)) + N * d + Z * (za + 0.3 * (zb - za)),
                       O + U * a + N * d + Z * (za + 0.55 * (zb - za))]
            else:
                pts = [O + U * a + N * d + Z * za, O + U * b + N * d + Z * za,
                       O + U * b + N * d + Z * zb, O + U * a + N * d + Z * zb]
            bmg.faces.new([bmg.verts.new(p) for p in pts])


def add_door(bm, lay, bmh, O, U, N, u0, u1, z0, z1, angle, rng):
    """Plank door hinged at u0, swung open outward by 'angle'."""
    verts = []
    n = 5
    pw = (u1 - u0) / n
    for k in range(n):
        a = u0 + k * pw + 0.002
        b = a + pw - 0.004
        zt = z1 - 0.01 - rng.uniform(0.0, 0.03)
        verts += wbox(bm, O, U, N, a, b, z0 + 0.02, zt, -0.012, 0.012, lay, rng.random())
    for zc in (z0 + 0.3, z1 - 0.35):                                   # battens on the inside
        verts += wbox(bm, O, U, N, u0 + 0.03, u1 - 0.03, zc - 0.06, zc + 0.06, -0.04, -0.012, lay, rng.random())
    hv = []
    for zc in (z0 + 0.3, z1 - 0.35):                                   # strap hinges
        hv += wbox(bmh, O, U, N, u0 - 0.02, u0 + 0.28, zc - 0.022, zc + 0.022, 0.012, 0.017)
    H = O + U * u0
    R = Matrix.Rotation(-angle, 3, 'Z')
    bmesh.ops.rotate(bm, cent=H, matrix=R, verts=verts)
    bmesh.ops.rotate(bmh, cent=H, matrix=R, verts=hv)


def roof_slab(bm, side, XR, run, Y0, Y1, GO, ztop, TANS, sag, thick=0.07, nu=4, nv=18):
    top = []
    for i in range(nu + 1):
        s = i / nu
        row = []
        for j in range(nv + 1):
            t = j / nv
            x = XR + side * run * s
            y = (Y0 - GO) + (Y1 - Y0 + 2 * GO) * t
            z = ztop - run * s * TANS - sag * sin(pi * t) * (1.0 - 0.6 * s)
            row.append(Vector((x, y, z)))
        top.append(row)
    nrm = Vector((side * TANS, 0.0, 1.0)).normalized()
    vt = [[bm.verts.new(p) for p in row] for row in top]
    vb = [[bm.verts.new(p - nrm * thick) for p in row] for row in top]
    for i in range(nu):
        for j in range(nv):
            bm.faces.new((vt[i][j], vt[i + 1][j], vt[i + 1][j + 1], vt[i][j + 1]))
            bm.faces.new((vb[i][j], vb[i][j + 1], vb[i + 1][j + 1], vb[i + 1][j]))
    for i in range(nu):
        bm.faces.new((vt[i][0], vb[i][0], vb[i + 1][0], vt[i + 1][0]))
        bm.faces.new((vt[i][nv], vt[i + 1][nv], vb[i + 1][nv], vb[i][nv]))
    for j in range(nv):
        bm.faces.new((vt[0][j], vt[0][j + 1], vb[0][j + 1], vb[0][j]))
        bm.faces.new((vt[nu][j], vb[nu][j], vb[nu][j + 1], vt[nu][j + 1]))


def build_house():
    c = coll("20_FishHouse")
    rng = random.Random(P["seed"] + 5)
    X0, X1, Y0, Y1 = P["house"]
    FZ = P["deck_z"]
    EH = FZ + 2.4                    # eaves
    HW = 0.5 * (X1 - X0)
    XR = 0.5 * (X0 + X1)             # ridge line x
    TANS = 1.0 / HW                  # ridge 1.0 m above the eaves
    RH = EH + 1.0
    OV, GO = 0.3, 0.28               # eave / gable overhang
    Zv = Vector((0, 0, 1))
    root = new_empty("FishHouse_Root", c, (X1, Y0, FZ), 0.6)
    inv = Matrix.Translation(Vector((X1, Y0, FZ))).inverted()
    parts = []
    walls = [
        ("Front", Vector((X0, Y0, 0)), Vector((1, 0, 0)), Vector((0, -1, 0)), X1 - X0, True,
         [(1.35, 1.95, FZ + 1.25, FZ + 1.95)]),
        ("Right", Vector((X1, Y0, 0)), Vector((0, 1, 0)), Vector((1, 0, 0)), Y1 - Y0, False,
         [(1.0, 1.86, FZ - 0.2, FZ + 1.96), (3.6, 4.4, FZ + 1.05, FZ + 1.85)]),
        ("Back", Vector((X1, Y1, 0)), Vector((-1, 0, 0)), Vector((0, 1, 0)), X1 - X0, True,
         [(1.3, 2.0, FZ + 1.2, FZ + 1.9)]),
        ("Left", Vector((X0, Y1, 0)), Vector((0, -1, 0)), Vector((-1, 0, 0)), Y1 - Y0, False, []),
    ]

    def half_w(z):
        return min(HW, HW - (z - EH) / TANS)

    # --- clapboard siding (peeling red paint)
    bm = bmesh.new()
    lay = bm.verts.layers.float.new("board_rand")
    E, HB = 0.13, 0.165
    for (wn, O, U, N, L, gable, holes) in walls:
        j = 0
        while True:
            zb = FZ + 0.03 + j * E
            zt = zb + HB
            j += 1
            if gable:
                dt, db = half_w(zt), half_w(zb)
                if dt < 0.04:
                    break
                if zt > EH:
                    add_clapboard(bm, lay, O, U, N, HW - db, HW + db, zb, zt, rng, HW - dt, HW + dt)
                    continue
            elif zt > EH + 0.02:
                break
            spans = [(0.0, L)]
            for (h0, h1, hz0, hz1) in holes:
                if zb < hz1 and zt > hz0:
                    cut = []
                    for a, b in spans:
                        if b <= h0 or a >= h1:
                            cut.append((a, b))
                            continue
                        if a < h0:
                            cut.append((a, h0 - 0.004))
                        if b > h1:
                            cut.append((h1 + 0.004, b))
                    spans = cut
            for a, b in spans:
                u = a
                while u < b - 0.05:
                    ue = min(b, u + rng.uniform(2.2, 4.2))
                    if not (rng.random() < 0.035 and zb > FZ + 0.4):
                        add_clapboard(bm, lay, O, U, N, u, ue, zb, zt, rng)
                    u = ue + 0.004
    parts.append(bm_to_obj("FishHouse_Siding", bm, c, MATS["paint"], recalc=True))

    # --- sheathing shell, gables and interior floor (dark, seen through gaps and openings)
    bm = bmesh.new()
    for (wn, O, U, N, L, gable, holes) in walls:
        wall_panel(bm, O, U, N, L, FZ, EH, holes)
        if gable:
            vs = [bm.verts.new(O + N * -0.004 + Zv * EH), bm.verts.new(O + U * L + N * -0.004 + Zv * EH),
                  bm.verts.new(O + U * HW + N * -0.004 + Zv * RH)]
            bm.faces.new(vs)
    bm.faces.new([bm.verts.new((X0, Y0, FZ + 0.005)), bm.verts.new((X1, Y0, FZ + 0.005)),
                  bm.verts.new((X1, Y1, FZ + 0.005)), bm.verts.new((X0, Y1, FZ + 0.005))])
    parts.append(bm_to_obj("FishHouse_Shell", bm, c, MATS["dark"]))

    # --- trims, windows, door
    bmf = bmesh.new()
    bmg = bmesh.new()
    for (wn, O, U, N, L, gable, holes) in walls:
        wbox(bmf, O, U, N, -0.035, 0.085, FZ, EH, 0.03, 0.052)        # corner boards
        wbox(bmf, O, U, N, L - 0.085, L + 0.035, FZ, EH, 0.03, 0.052)
        if not gable:
            wbox(bmf, O, U, N, -0.03, L + 0.03, EH - 0.16, EH + 0.02, 0.03, 0.05)   # frieze board
    fo = walls[0]
    add_window(bmf, bmg, fo[1], fo[2], fo[3], 1.35, 1.95, FZ + 1.25, FZ + 1.95, rng, 2, 2, True)
    ri = walls[1]
    add_window(bmf, bmg, ri[1], ri[2], ri[3], 3.6, 4.4, FZ + 1.05, FZ + 1.85, rng, 3, 2, True)
    bk = walls[2]
    add_window(bmf, bmg, bk[1], bk[2], bk[3], 1.3, 2.0, FZ + 1.2, FZ + 1.9, rng, 2, 2, False)
    O, U, N = ri[1], ri[2], ri[3]
    wbox(bmf, O, U, N, 1.0 - 0.08, 1.0, FZ, FZ + 2.03, 0.03, 0.052)             # door casing
    wbox(bmf, O, U, N, 1.86, 1.86 + 0.08, FZ, FZ + 2.03, 0.03, 0.052)
    wbox(bmf, O, U, N, 1.0 - 0.08, 1.86 + 0.08, FZ + 1.96, FZ + 2.05, 0.03, 0.052)
    parts.append(bm_to_obj("FishHouse_Trims", bmf, c, MATS["wood_trim"], recalc=True))
    parts.append(bm_to_obj("FishHouse_WindowGlass", bmg, c, MATS["glass"]))
    bmd = bmesh.new()
    layd = bmd.verts.layers.float.new("board_rand")
    bmh = bmesh.new()
    add_door(bmd, layd, bmh, O, U, N, 1.0, 1.86, FZ, FZ + 1.96, radians(28.0), rng)
    parts.append(bm_to_obj("FishHouse_Door", bmd, c, MATS["paint"], recalc=True))
    parts.append(bm_to_obj("FishHouse_DoorHinges", bmh, c, MATS["rust"], recalc=True))

    # --- sagging roof, fascia, barge boards, ragged shingle edge
    run = HW + OV
    ztop = RH + 0.1
    bm = bmesh.new()
    for side in (-1, 1):
        roof_slab(bm, side, XR, run, Y0, Y1, GO, ztop, TANS, sag=0.07)
    ob = bm_to_obj("FishHouse_Roof", bm, c, MATS["roof"], recalc=True)
    parts.append(ob)
    bm = bmesh.new()
    zeave = ztop - run * TANS
    for side in (-1, 1):
        xe = XR + side * run
        obox(bm, (xe, Y0 - GO, zeave - 0.07), (xe, Y1 + GO, zeave - 0.07), (0, 0, 1), 0.16, 0.025)
        for yb in (Y0 - GO - 0.012, Y1 + GO + 0.012):
            obox(bm, (xe, yb, zeave - 0.02), (XR, yb, ztop + 0.02), (0, 0, 1), 0.2, 0.024)
    obox(bm, (XR, Y0 - GO, ztop + 0.012), (XR, Y1 + GO, ztop + 0.012), (1, 0, 0), 0.2, 0.025)   # ridge cap
    parts.append(bm_to_obj("FishHouse_RoofTrim", bm, c, MATS["wood_trim"], recalc=True))
    bm = bmesh.new()
    for side in (-1, 1):
        for k in range(21):
            if rng.random() < 0.15:
                continue
            yk = Y0 - GO + 0.05 + k * (Y1 - Y0 + 2 * GO - 0.1) / 21.0
            s = 0.97 - rng.uniform(0.0, 0.04)
            x = XR + side * run * s
            z = ztop - run * s * TANS + 0.012
            lift = rng.uniform(0.0, 0.05) if rng.random() < 0.25 else 0.0
            obox(bm, (x - side * 0.16, yk, z + 0.16 * TANS),
                 (x + side * 0.02, yk + rng.uniform(-0.02, 0.02), z - 0.02 * TANS + lift),
                 (0, 1, 0), 0.3, 0.006)
    parts.append(bm_to_obj("FishHouse_ShingleEdge", bm, c, MATS["roof"], recalc=True))

    # --- barrel stack on the ridge (as in the reference), stovepipe, mast with wires
    bm = bmesh.new()
    bx, by, bz = XR, Y1 - 1.3, ztop - 0.03
    bm_lathe(bm, [(0.26, 0.0), (0.29, 0.12), (0.31, 0.42), (0.29, 0.72), (0.26, 0.84)], loc=(bx, by, bz),
             seg=28, cap_bottom=True, cap_top=True)
    parts.append(bm_to_obj("FishHouse_BarrelStack", bm, c, MATS["wood_trim"], smooth=True, recalc=True))
    bm = bmesh.new()
    for zh, rh in ((0.1, 0.285), (0.3, 0.307), (0.55, 0.307), (0.75, 0.283)):
        bm_torus(bm, (bx, by, bz + zh), rh, 0.009, seg=40, rseg=6)
    bm_cyl(bm, (bx + 0.05, by, bz + 1.1), 0.065, 0.55, segs=16)
    bm_cyl(bm, (bx + 0.05, by, bz + 1.42), 0.14, 0.08, segs=16, r2=0.02)
    parts.append(bm_to_obj("FishHouse_Stovepipe", bm, c, MATS["rust"], smooth=True, recalc=True))
    mx, my = X1 + 0.3, Y1 + 0.45
    bm = bmesh.new()
    bm_cyl(bm, (mx, my, FZ + 2.5), 0.07, 5.0, segs=12, r2=0.045)
    obox(bm, (mx - 0.35, my, FZ + 4.75), (mx + 0.35, my, FZ + 4.75), (0, 0, 1), 0.06, 0.05)
    parts_mast = bm_to_obj("FishHouse_Mast", bm, c, MATS["wood_mast"], smooth=True, recalc=True)
    top = (mx, my, FZ + 4.95)
    make_curve("Wire_A", sag_wire(top, (XR, Y1 + GO, ztop), 0.12), c, 0.004, MATS["rust"])
    make_curve("Wire_B", sag_wire(top, (mx + 0.5, my + 2.4, FZ + 0.05), 0.08), c, 0.004, MATS["rust"])
    make_curve("Wire_C", sag_wire((mx - 0.35, my, FZ + 4.75), (-9.0, Y1 + 4.0, 3.2), 0.6, 24), c, 0.004, MATS["rust"])

    for ob in parts:
        ob.parent = root
        ob.matrix_parent_inverse = inv
    root.rotation_euler = (radians(-0.5), radians(0.9), 0.0)      # a tired lean
    return root


def build_props():
    c = coll("20_FishHouse")
    rng = random.Random(P["seed"] + 6)
    DZ = P["deck_z"]
    bm = bmesh.new()
    lay = bm.verts.layers.float.new("board_rand")

    def crate(cx, cy, z0, sx, sy, sz, rz):
        R = Matrix.Rotation(rz, 3, 'Z')
        parts = []
        ps = 0.03
        for (px, py) in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            parts.append(((px * (sx / 2 - ps / 2), py * (sy / 2 - ps / 2), sz / 2), (ps, ps, sz)))
        for k in range(3):
            zc = 0.05 + k * (sz - 0.07) / 2.0
            for sd in (-1, 1):
                parts.append(((0.0, sd * (sy / 2 - 0.006), zc), (sx - 0.01, 0.012, 0.055)))
                parts.append(((sd * (sx / 2 - 0.006), 0.0, zc), (0.012, sy - 0.01, 0.055)))
        for k in range(4):
            parts.append(((-sx / 2 + (k + 0.5) * sx / 4, 0.0, 0.008), (sx / 4 - 0.01, sy - 0.01, 0.014)))
        for loc, size in parts:
            p = R @ Vector(loc) + Vector((cx, cy, z0))
            bm_box(bm, p, size, rot=(0, 0, rz), layer=lay, val=rng.random())

    crate(1.08, 6.3, DZ, 0.62, 0.42, 0.30, radians(8))
    crate(1.10, 6.28, DZ + 0.305, 0.62, 0.42, 0.30, radians(-5))
    crate(1.25, 7.05, DZ, 0.62, 0.42, 0.30, radians(71))
    bm_lathe(bm, [(0.24, 0.0), (0.27, 0.12), (0.29, 0.4), (0.27, 0.68), (0.24, 0.8)], loc=(1.2, 8.35, DZ),
             seg=24, cap_bottom=True, cap_top=True)
    parts = [bm_to_obj("Props_Wood", bm, c, MATS["wood_trim"], recalc=True)]
    bm = bmesh.new()
    for zh, rh in ((0.1, 0.262), (0.3, 0.285), (0.5, 0.285), (0.7, 0.262)):
        bm_torus(bm, (1.2, 8.35, DZ + zh), rh, 0.008, seg=36, rseg=6)
    parts.append(bm_to_obj("Props_Hoops", bm, c, MATS["rust"], smooth=True))
    # coiled rope on the walk in the foreground
    pts = []
    for i in range(260):
        a = i * 0.14
        r = 0.07 + 0.026 * a / (2 * pi)
        if r > 0.26:
            break
        pts.append((1.42 + r * cos(a), 3.25 + r * sin(a), DZ + 0.012 + 0.004 * sin(a * 3.1)))
    last = Vector(pts[-1])
    for i in range(1, 9):                              # short loose end curling away
        a = i * 0.35
        pts.append((last.x + 0.05 * i, last.y - 0.06 * sin(a) + 0.02 * i, DZ + 0.012))
    make_curve("Props_RopeCoil", pts, c, 0.011, MATS["rope"])
    bm = bmesh.new()
    bm_sphere(bm, (0.98, 5.4, DZ + 0.18), 0.16, scale=(1.0, 1.0, 1.15), useg=24, vseg=14)
    parts.append(bm_to_obj("Props_Buoy", bm, c, MATS["buoy"], smooth=True))
    return parts


# ============================================================================
#  OCEAN
# ============================================================================
def polar_grid(cx, cy, r0=0.5, r1=9500.0, n_ang=192):
    """Radially graded disc: fine near the camera, coarse at the horizon (ideal base for adaptive dicing)."""
    bm = bmesh.new()
    step = 2 * pi / n_ang
    radii = [r0]
    while radii[-1] < r1:
        radii.append(radii[-1] * (1.0 + step))
    c0 = bm.verts.new((cx, cy, 0.0))
    rv = [[bm.verts.new((cx + r * cos(i * step), cy + r * sin(i * step), 0.0)) for i in range(n_ang)]
          for r in radii]
    for i in range(n_ang):
        bm.faces.new((c0, rv[0][i], rv[0][(i + 1) % n_ang]))
    for a, b in zip(rv[:-1], rv[1:]):
        for i in range(n_ang):
            j = (i + 1) % n_ang
            bm.faces.new((a[i], b[i], b[j], a[j]))
    return bm


def build_ocean():
    c = coll("40_Ocean")
    bm = polar_grid(P["cam_loc"][0], P["cam_loc"][1])
    ob = bm_to_obj("Ocean", bm, c, MATS["ocean"], smooth=True)
    add_subsurf(ob, levels=0, render=2, adaptive=True, simple=True, pixel=1.0)
    return ob


# ============================================================================
#  BOAT, FISHERMAN, KEROSENE LANTERN
# ============================================================================
HULL_L, HULL_B = 4.3, 1.55


def hull_station(u):
    x = -HULL_L / 2 + u * HULL_L
    if u <= 0.42:
        f = 0.72 + 0.28 * sin(0.5 * pi * u / 0.42)
    else:
        t = (u - 0.42) / 0.58
        f = max(0.0, 1.0 - t * t) ** 0.55
    b = 0.5 * HULL_B * f
    sheer = 0.42 + 0.22 * u ** 2.4
    keel = -0.16 + 0.14 * u ** 2.6
    chz = -0.06 + 0.12 * u ** 2.6
    c = 0.8 * b
    return x, [(0.0, keel), (0.45 * c, keel + 0.35 * (chz - keel) + 0.01), (c, chz),
               (0.93 * b, chz + 0.55 * (sheer - chz)), (b, sheer)]


def hull_halfwidth(u, z):
    x, pts = hull_station(u)
    for (y0, z0), (y1, z1) in zip(pts[:-1], pts[1:]):
        if z0 <= z <= z1 and z1 > z0:
            return y0 + (y1 - y0) * (z - z0) / (z1 - z0)
    return pts[-1][0] if z > pts[-1][1] else 0.0


def build_lantern(c, pivot):
    """Hurricane lantern hanging from 'pivot' (the bail top is at the pivot)."""
    base = Vector((0.0, 0.0, -0.35))
    Bx, By, Bz = base
    bm = bmesh.new()
    bm_lathe(bm, [(0.07, 0.0), (0.084, 0.01), (0.088, 0.03), (0.08, 0.046), (0.045, 0.056), (0.03, 0.06)],
             loc=base, seg=28, cap_bottom=True)                                   # fuel fount
    bm_cyl(bm, (Bx, By, Bz + 0.074), 0.03, 0.028, segs=16)                      # burner
    bm_torus(bm, (Bx, By, Bz + 0.088), 0.045, 0.004, seg=28, rseg=6)             # globe seat
    for k in range(5):                                                           # wire guard
        a = 2 * pi * k / 5 + 0.3
        prof = [(0.058, 0.09), (0.075, 0.12), (0.08, 0.15), (0.078, 0.18), (0.064, 0.21), (0.052, 0.222)]
        bm_tube(bm, [(Bx + r * cos(a), By + r * sin(a), Bz + z) for (r, z) in prof], 0.0022, seg=5)
    bm_torus(bm, (Bx, By, Bz + 0.15), 0.08, 0.0022, seg=32, rseg=5)
    bm_torus(bm, (Bx, By, Bz + 0.195), 0.072, 0.0022, seg=32, rseg=5)
    bm_lathe(bm, [(0.047, 0.222), (0.062, 0.232), (0.058, 0.25), (0.035, 0.27), (0.014, 0.282), (0.014, 0.30)],
             loc=base, seg=28, cap_top=True)                                      # chimney cap
    for sx in (-1, 1):                                                           # air tubes (farm lantern)
        bm_tube(bm, [(Bx + sx * 0.088, By, Bz + 0.03), (Bx + sx * 0.092, By, Bz + 0.12),
                     (Bx + sx * 0.086, By, Bz + 0.23), (Bx + sx * 0.06, By, Bz + 0.255),
                     (Bx + sx * 0.035, By, Bz + 0.265)], 0.0075, seg=8)
    bm_tube(bm, [(Bx + 0.11 * cos(a), By, Bz + 0.24 + 0.11 * sin(a)) for a in [i * pi / 16 for i in range(17)]],
            0.003, seg=5)                                                        # bail handle
    metal = bm_to_obj("Lantern_Metal", bm, c, MATS["lamp_paint"], smooth=True, parent=pivot)
    bm = bmesh.new()
    bm_lathe(bm, [(0.043, 0.088), (0.058, 0.10), (0.068, 0.135), (0.066, 0.175), (0.053, 0.205), (0.040, 0.222)],
             loc=base, seg=32)
    glass = bm_to_obj("Lantern_Globe", bm, c, MATS["lamp_glass"], smooth=True, parent=pivot)
    so = glass.modifiers.new("Solidify", 'SOLIDIFY')
    so.thickness = 0.0025
    glass.visible_shadow = False            # let the flame's light out without caustics
    bm = bmesh.new()
    bm_sphere(bm, (0.0, 0.0, 0.0), 0.008, scale=(1.0, 1.0, 2.6), useg=12, vseg=8)
    flame = bm_to_obj("Lantern_Flame", bm, c, MATS["flame"], smooth=True, parent=pivot)
    flame.location = (Bx, By, Bz + 0.108)
    flame.visible_shadow = False
    add_driver(flame, "scale", "1+0.08*sin(frame*2.3)+0.05*sin(frame*6.1+0.7)", 2)
    ld = bpy.data.lights.new("H3_KeroseneLamp", 'POINT')
    ld.energy = P["lamp_power"]
    ld.shadow_soft_size = P["lamp_radius"]
    if not (setp(ld, "use_temperature", True) and setp(ld, "temperature", P["lamp_kelvin"])):
        ld.color = (1.0, 0.36, 0.08)
    lamp = link_obj("KeroseneLamp", ld, coll("80_Lighting"), pivot)
    lamp.location = (Bx, By, Bz + 0.11)
    setp(lamp, "visible_camera", False)
    add_driver(ld, "energy", "%.3f*(1+0.06*sin(frame*1.93)+0.04*sin(frame*4.71+1.3)+0.025*sin(frame*11.3+0.4))"
               % P["lamp_power"])
    return lamp


def build_fisherman(c, rig, sx, sz):
    """Seated figure built from a stick skeleton + Skin modifier (layout stand-in)."""
    J = [("pelvis", (0.00, 0.00, 0.10), (0.16, 0.13)), ("spine", (0.03, 0.00, 0.30), (0.16, 0.12)),
         ("chest", (0.07, 0.00, 0.50), (0.19, 0.13)), ("neck", (0.10, 0.00, 0.64), (0.055, 0.055)),
         ("sh_l", (0.07, 0.19, 0.58), (0.075, 0.075)), ("el_l", (0.26, 0.25, 0.36), (0.06, 0.06)),
         ("ha_l", (0.42, 0.16, 0.20), (0.045, 0.04)), ("sh_r", (0.07, -0.19, 0.58), (0.075, 0.075)),
         ("el_r", (0.03, -0.30, 0.34), (0.06, 0.06)), ("ha_r", (0.07, -0.31, 0.15), (0.045, 0.04)),
         ("hip_l", (0.04, 0.10, 0.05), (0.10, 0.09)), ("kn_l", (0.44, 0.12, 0.10), (0.075, 0.07)),
         ("an_l", (0.48, 0.13, -0.19), (0.055, 0.05)), ("ft_l", (0.60, 0.13, -0.23), (0.06, 0.045)),
         ("hip_r", (0.04, -0.10, 0.05), (0.10, 0.09)), ("kn_r", (0.45, -0.13, 0.10), (0.075, 0.07)),
         ("an_r", (0.49, -0.14, -0.19), (0.055, 0.05)), ("ft_r", (0.61, -0.14, -0.23), (0.06, 0.045))]
    E = [("pelvis", "spine"), ("spine", "chest"), ("chest", "neck"), ("chest", "sh_l"), ("sh_l", "el_l"),
         ("el_l", "ha_l"), ("chest", "sh_r"), ("sh_r", "el_r"), ("el_r", "ha_r"), ("pelvis", "hip_l"),
         ("hip_l", "kn_l"), ("kn_l", "an_l"), ("an_l", "ft_l"), ("pelvis", "hip_r"), ("hip_r", "kn_r"),
         ("kn_r", "an_r"), ("an_r", "ft_r")]
    names = [j[0] for j in J]
    verts = [(p[0] + sx, p[1], p[2] + sz) for (_, p, _) in J]
    me = bpy.data.meshes.new("H3_Fisherman")
    me.from_pydata(verts, [(names.index(a), names.index(b)) for a, b in E], [])
    body = link_obj("Fisherman_Body", me, c, rig)
    sk = body.modifiers.new("Skin", 'SKIN')
    setp(sk, "use_smooth_shade", True)
    setp(sk, "branch_smoothing", 0.4)
    if len(me.skin_vertices) == 0:
        me.skin_vertices.new()
    sv = me.skin_vertices[0].data
    for i, (_, _, rad) in enumerate(J):
        sv[i].radius = rad
        sv[i].use_root = (i == 0)
    add_subsurf(body, levels=1, render=2)
    MATS["fisher"] = mat_fisherman(sz + 0.16)
    me.materials.append(MATS["fisher"])
    bm = bmesh.new()
    bm_sphere(bm, (sx + 0.12, 0.0, sz + 0.78), 0.105, scale=(1.0, 0.9, 1.12), useg=20, vseg=12)
    for (nm, p, _) in J:
        if nm.startswith("ha_"):
            bm_sphere(bm, (p[0] + sx, p[1], p[2] + sz), 0.045, useg=10, vseg=8)
    head = bm_to_obj("Fisherman_HeadHands", bm, c, MATS["skin"], smooth=True, parent=rig)
    bm = bmesh.new()
    vs = bm_sphere(bm, (sx + 0.115, 0.0, sz + 0.80), 0.113, scale=(1.0, 0.92, 1.0), useg=20, vseg=12)
    bmesh.ops.delete(bm, geom=[v for v in vs if v.co.z < sz + 0.80 - 0.01], context='VERTS')
    cap = bm_to_obj("Fisherman_KnitCap", bm, c, MATS["knit"], smooth=True, parent=rig)
    add_driver(body, "rotation_euler", "0.015*sin(frame*0.11)+0.006*sin(frame*0.37)", 0)
    return body


def build_boat():
    c = coll("50_Boat")
    rig = new_empty("BoatRig", c, (0, 0, 0), 1.2, 'ARROWS')
    # hull (open shell) -> solidify -> subdivision
    bm = bmesh.new()
    NS = 30
    rows = []
    for i in range(NS + 1):
        x, pts = hull_station(i / NS)
        full = [(-y, z) for (y, z) in reversed(pts)] + [(y, z) for (y, z) in pts[1:]]
        rows.append([bm.verts.new((x, y, z)) for (y, z) in full])
    for ra, rb in zip(rows[:-1], rows[1:]):
        for k in range(len(ra) - 1):
            bm.faces.new((ra[k], ra[k + 1], rb[k + 1], rb[k]))
    bm.faces.new(list(reversed(rows[0])))                           # transom
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
    bm.normal_update()
    low = min(bm.faces, key=lambda f: f.calc_center_median().z)
    if low.normal.z > 0.0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
    hull = bm_to_obj("Boat_Hull", bm, c, [MATS["hull_paint"], MATS["hull_inside"], MATS["wood_trim"]],
                     smooth=True, parent=rig)
    so = hull.modifiers.new("Solidify", 'SOLIDIFY')
    so.thickness = 0.02
    so.offset = -1.0
    so.material_offset = 1
    so.material_offset_rim = 2
    setp(so, "use_even_offset", True)
    add_subsurf(hull, levels=1, render=2)
    # floorboards, thwarts, gunwale rails
    bm = bmesh.new()
    zf = 0.04
    for yk in (-0.28, -0.14, 0.0, 0.14, 0.28):
        us = [i / 100.0 for i in range(3, 96) if hull_halfwidth(i / 100.0, zf + 0.02) > abs(yk) + 0.08]
        if len(us) < 2:
            continue
        x0 = -HULL_L / 2 + us[0] * HULL_L
        x1 = -HULL_L / 2 + us[-1] * HULL_L
        bm_box(bm, (0.5 * (x0 + x1), yk, zf), (x1 - x0, 0.125, 0.02))
    for u, zt in ((0.10, 0.30), (0.45, 0.30), (0.75, 0.32)):
        hwid = hull_halfwidth(u, zt) - 0.02
        bm_box(bm, (-HULL_L / 2 + u * HULL_L, 0.0, zt - 0.0125), (0.24, 2 * hwid, 0.025))
    for sd in (-1, 1):
        pts = []
        for i in range(0, 41):
            u = i / 40.0
            x, st = hull_station(u)
            pts.append((x, sd * st[-1][0], st[-1][1] + 0.012))
        bm_tube(bm, pts, 0.022, seg=8)
    bm_to_obj("Boat_Woodwork", bm, c, MATS["wood_trim"], smooth=False, recalc=True, parent=rig)
    # outboard motor with a tiller reaching the fisherman's hand
    bm = bmesh.new()
    xs = -HULL_L / 2
    bm_box(bm, (xs - 0.26, 0.0, 0.64), (0.42, 0.30, 0.34))
    bm_box(bm, (xs - 0.19, 0.0, 0.14), (0.08, 0.10, 0.80))
    bm_box(bm, (xs - 0.17, 0.0, -0.33), (0.34, 0.07, 0.12))
    bm_box(bm, (xs - 0.17, 0.0, -0.25), (0.30, 0.20, 0.012))
    bm_box(bm, (xs - 0.05, 0.0, 0.40), (0.10, 0.12, 0.10))
    obox(bm, (xs - 0.08, -0.02, 0.63), (xs + 0.49, -0.30, 0.47), (0, 0, 1), 0.04, 0.04)
    motor = bm_to_obj("Boat_Outboard", bm, c, MATS["outboard"], recalc=True, parent=rig)
    bev = motor.modifiers.new("Bevel", 'BEVEL')
    bev.width = 0.02
    bev.segments = 3
    # bow lamp mast (lamp forward, fisherman aft: warm key light on his face)
    bm = bmesh.new()
    mx = -HULL_L / 2 + 0.80 * HULL_L
    bm_cyl(bm, (mx, 0.0, 0.9), 0.032, 1.72, segs=10)
    obox(bm, (mx - 0.03, 0.0, 1.73), (mx + 0.3, 0.0, 1.73), (0, 0, 1), 0.04, 0.035)
    bm_tube(bm, [(mx + 0.28, 0.0, 1.71), (mx + 0.28, 0.0, 1.66), (mx + 0.25, 0.0, 1.645)], 0.004, seg=5, cap=False)
    bm_to_obj("Boat_LampMast", bm, c, MATS["wood_mast"], smooth=True, recalc=True, parent=rig)
    pivot = new_empty("LanternPivot", c, (mx + 0.25, 0.0, 1.65), 0.1, 'SPHERE', rig)
    add_driver(pivot, "rotation_euler", "0.05*sin(frame*0.19+0.3)+0.02*sin(frame*0.47)", 0)
    add_driver(pivot, "rotation_euler", "0.035*sin(frame*0.23+1.1)+0.015*sin(frame*0.61)", 1)
    build_lantern(c, pivot)
    build_fisherman(c, rig, -HULL_L / 2 + 0.44, 0.30)
    animate_boat(rig)
    # the ocean shader cuts the water out of the hull using the rig's space
    try:
        MATS["ocean"].node_tree.nodes["BoatMaskCoords"].object = rig
    except Exception as e:
        log("boat mask", e)
    return rig


def animate_boat(rig):
    """Bake heave / pitch / roll from the same Gerstner field the ocean shader displaces with."""
    fs, fe = P["frame_start"], P["frame_end"]
    (x0, y0), (x1, y1) = P["boat_start"], P["boat_end"]
    heading = atan2(y1 - y0, x1 - x0)
    ch, sh = cos(heading), sin(heading)
    for f in range(fs, fe + 1):
        s = (f - fs) / max(1, fe - fs)
        t = f / P["fps"]
        x = x0 + (x1 - x0) * s
        y = y0 + (y1 - y0) * s

        def h(dx, dy):
            return ocean_height(x + dx * ch - dy * sh, y + dx * sh + dy * ch, t)
        hb, hs, hp, hst, hc = h(1.6, 0), h(-1.6, 0), h(0, 0.6), h(0, -0.6), h(0, 0)
        heave = 0.4 * hc + 0.15 * (hb + hs + hp + hst)
        pitch = atan2(hb - hs, 3.2) * 0.85
        roll = atan2(hp - hst, 1.2) * 0.8
        rig.location = (x, y, heave - 0.02)
        rig.rotation_euler = (roll, -pitch, heading)
        rig.keyframe_insert("location", frame=f)
        rig.keyframe_insert("rotation_euler", frame=f)


# ============================================================================
#  MOUNTAINS
# ============================================================================
def build_mountains():
    c = coll("60_Mountains")
    X0m, X1m, Y0m, Y1m = -7000.0, 9000.0, 2300.0, 8800.0
    NX, NY = 900, 280
    raw1, raw2, pts = [], [], []
    for j in range(NY + 1):
        y = Y0m + (Y1m - Y0m) * j / NY
        for i in range(NX + 1):
            x = X0m + (X1m - X0m) * i / NX
            p = Vector((x / 1900.0, y / 1900.0, 0.37))
            w = noise.noise_vector(p * 0.8) * 0.35                      # domain warp
            raw1.append(noise.ridged_multi_fractal(p + w, 1.0, 2.1, 8, 1.0, 2.0))
            q2 = Vector((x / 2600.0 + 11.3, y / 2600.0 + 3.1, 1.7)) + w * 1.3
            raw2.append(noise.ridged_multi_fractal(q2, 0.9, 2.2, 9, 1.0, 2.4))
            pts.append((x, y))

    def norm(a):
        s = sorted(a)
        lo, hi = s[int(len(s) * 0.02)], s[int(len(s) * 0.995)]
        return [min(1.2, max(0.0, (v - lo) / (hi - lo))) for v in a]
    n1, n2 = norm(raw1), norm(raw2)
    verts = []
    for (x, y), a, b in zip(pts, n1, n2):
        d = y - 2450.0
        env_near = smoothstep(0.0, 500.0, d) * (1.0 - 0.3 * smoothstep(1000.0, 1700.0, d))
        env_far = smoothstep(1100.0, 2300.0, d)
        near = env_near * (60.0 + 520.0 * a ** 1.35)                    # forested foothills
        far = env_far * (180.0 + 1650.0 * max(0.0, b - 0.25) ** 1.55)   # jagged high peaks
        verts.append((x, y, max(near, far) - 40.0 * (1.0 - smoothstep(0.0, 150.0, d))))
    W = NX + 1
    faces = [(j * W + i, j * W + i + 1, (j + 1) * W + i + 1, (j + 1) * W + i) for j in range(NY) for i in range(NX)]
    me = bpy.data.meshes.new("H3_Mountains")
    me.from_pydata(verts, [], faces)
    try:
        me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    except Exception:
        setp(me, "shade_smooth", None)
    me.update()
    me.materials.append(MATS["rock"])
    return link_obj("Mountains", me, c)


# ============================================================================
#  ATMOSPHERE, WORLD, CAMERA
# ============================================================================
def build_atmosphere():
    if not P["use_volumes"]:
        return
    c = coll("70_Atmosphere")

    def dom(name, center, size, mat):
        bm = bmesh.new()
        bm_box(bm, center, size)
        ob = bm_to_obj(name, bm, c, mat)
        ob.display_type = 'BOUNDS'
        return ob
    dom("Fog_Sea", (40.0, 190.0, 18.0), (360.0, 440.0, 38.0), MATS["fog"])
    dom("Haze", (1000.0, 4600.0, 1700.0), (19000.0, 10200.0, 3420.0), MATS["haze"])
    dom("CloudBank_Low", (1000.0, 3150.0, 170.0), (16000.0, 1600.0, 420.0), MATS["cloud_low"])
    dom("CloudBank_High", (1500.0, 5300.0, 850.0), (15000.0, 2800.0, 820.0), MATS["cloud_high"])


def setup_world():
    """Overcast 3 AM sky: barely-there skyglow (no moon, no stars), dark below the horizon."""
    w = bpy.data.worlds.get("H3_World") or bpy.data.worlds.new("H3_World")
    SC.world = w
    if bpy.app.version < (5, 0, 0):
        setp(w, "use_nodes", True)
    nt = w.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    g = NT(nt)
    out = g.new('ShaderNodeOutputWorld', 900, 0)
    tc = g.new('ShaderNodeTexCoord', -1500, 0)
    d = tc.outputs["Generated"]
    z = g.separate(d, x=-1300, y=0)[2]
    glow = g.value(P["sky_glow"], -1500, -500, "Sky Glow (0 = black)")
    grad = g.mrange(z, -0.02, 0.45, 0.0, 1.0, interp='SMOOTHSTEP', x=-1100, y=0)
    sky = g.mix(grad, (0.00045, 0.00050, 0.00060), (0.00014, 0.00017, 0.00023), x=-900, y=0)
    zc = g.math('MAXIMUM', z, 0.03, x=-1100, y=-250)
    proj = g.vmath('DIVIDE', d, g.combine(zc, zc, zc, x=-950, y=-250), x=-800, y=-250)
    cn = g.noise(g.vmath('SCALE', proj, s=0.6, x=-650, y=-250), scale=1.0, detail=7.0, rough=0.6, dist=0.4,
                 x=-500, y=-250)
    cl = g.mrange(g.fac(cn), 0.3, 0.75, 0.55, 1.35, x=-300, y=-250)
    sky = g.mix(1.0, sky, cl, blend='MULTIPLY', x=-100, y=0)
    below = g.mrange(z, -0.02, 0.0, x=-100, y=-400)
    sky = g.mix(below, (0.00002, 0.000025, 0.00003), sky, x=150, y=0)
    bg = g.new('ShaderNodeBackground', 500, 0)
    g.feed(bg.inputs["Color"], sky)
    g.feed(bg.inputs["Strength"], glow.outputs[0])
    g.link(bg.outputs[0], out.inputs["Surface"])
    ms = w.mist_settings
    ms.start = 3.0
    ms.depth = 600.0
    ms.falloff = 'QUADRATIC'
    return w


def build_camera():
    c = coll("00_Camera")
    rig = new_empty("CamRig", c, P["cam_loc"], 0.3, 'ARROWS')
    rig.rotation_euler = [radians(v) for v in P["cam_rot_deg"]]
    cd = bpy.data.cameras.new("H3_Camera")
    cd.lens = P["lens"]
    cd.sensor_fit = 'HORIZONTAL'
    cd.sensor_width = P["sensor"]
    cd.clip_start = 0.02
    cd.clip_end = 30000.0
    cam = link_obj("Camera", cd, c, rig)
    focus = new_empty("FocusTarget", c, P["focus_loc"], 0.15, 'SPHERE')
    cd.dof.use_dof = True
    cd.dof.focus_object = focus
    cd.dof.aperture_fstop = P["fstop"]
    cd.dof.aperture_blades = 7
    cd.dof.aperture_rotation = radians(12.0)
    mode = P.get("cam_motion", "STATIC")
    fs, fe = P["frame_start"], P["frame_end"]
    deck = P["deck_cam"]
    deck_loc = Vector(deck["loc"])
    deck_rot = Euler((radians(90.0 + deck["pitch"]), 0.0, radians(-deck["yaw"])), 'XYZ')
    if mode == "GLIDE":
        # eased move from the tripod pose down to the low deck framing, then hold
        fa = min(P.get("cam_arrive", fe), fe)
        for ob_, path in ((rig, "location"), (rig, "rotation_euler"), (cd, "lens")):
            ob_.keyframe_insert(path, frame=fs)
        rig.location, rig.rotation_euler, cd.lens = deck_loc, deck_rot, deck["lens"]
        for f in sorted({fa, fe}):
            for ob_, path in ((rig, "location"), (rig, "rotation_euler"), (cd, "lens")):
                ob_.keyframe_insert(path, frame=f)
    elif mode == "PUSH":
        # slow dolly along the view direction
        fwd = rig.rotation_euler.to_matrix() @ Vector((0.0, 0.0, -1.0))
        fwd.z = 0.0
        fwd.normalize()
        rig.keyframe_insert("location", frame=fs)
        rig.location = Vector(P["cam_loc"]) + fwd * P["push_in"]
        rig.keyframe_insert("location", frame=fe)
    # STATIC: locked off. Nothing is keyed or driven on the rig, the camera or the lens,
    # and the transforms are locked so the view can't be nudged by accident.
    lock = (mode == "STATIC",) * 3
    for ob_ in (rig, cam):
        ob_.lock_location = lock
        ob_.lock_rotation = lock
    SC.camera = cam
    return cam


def use_viewport_as_camera():
    """Make the current 3D-viewport view the (static) render camera.
    Frame your view in the viewport (not through the camera), then in Blender's Python console:
        use_viewport_as_camera(); build_camera()"""
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                sp = area.spaces.active
                M = sp.region_3d.view_matrix.inverted()
                e = M.to_euler('XYZ')
                P["cam_loc"] = tuple(M.translation)
                P["cam_rot_deg"] = tuple(math.degrees(a) for a in e)
                P["lens"] = sp.lens / 2.0     # viewport lens -> camera lens (36 mm sensor)
                return dict(loc=P["cam_loc"], rot_deg=P["cam_rot_deg"], lens=P["lens"])
    return None


# ============================================================================
#  RENDER SETTINGS, COMPOSITOR, VIEWPORT
# ============================================================================
def setup_render():
    sc = SC
    r = sc.render
    r.engine = 'CYCLES'
    cy = sc.cycles
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        if prefs.compute_device_type in ('NONE', ''):
            for t in ('METAL', 'OPTIX', 'CUDA', 'HIP', 'ONEAPI'):
                try:
                    prefs.compute_device_type = t
                    break
                except Exception:
                    pass
        prefs.refresh_devices()
        gpus = [d for d in prefs.devices if d.type != 'CPU']
        for d in gpus:
            d.use = True
        cy.device = 'GPU' if gpus else 'CPU'
    except Exception as e:
        log("gpu setup", e)
    # sampling: adaptive + OpenImageDenoise (albedo/normal guided)
    cy.samples = P["samples"]
    cy.preview_samples = P["preview_samples"]
    cy.use_adaptive_sampling = True
    cy.adaptive_threshold = P["adaptive_threshold"]
    cy.adaptive_min_samples = 64
    cy.use_denoising = True
    setp(cy, "denoiser", 'OPENIMAGEDENOISE')
    setp(cy, "denoising_input_passes", 'RGB_ALBEDO_NORMAL')
    setp(cy, "denoising_prefilter", 'ACCURATE')
    setp(cy, "denoising_quality", 'HIGH')
    setp(cy, "denoising_use_gpu", True)
    cy.use_preview_denoising = True
    # light paths
    cy.max_bounces = 12
    cy.diffuse_bounces = 3
    cy.glossy_bounces = 4
    cy.transmission_bounces = 8
    cy.volume_bounces = 2
    cy.transparent_max_bounces = 16
    cy.sample_clamp_direct = 0.0          # never clamp the lamp's direct glints
    cy.sample_clamp_indirect = 3.0        # tame caustic fireflies from the tiny bright source
    cy.blur_glossy = 0.8
    cy.caustics_reflective = False
    cy.caustics_refractive = False
    setp(cy, "use_light_tree", True)
    # volumes: unbiased null-scattering (5.x default); step settings only matter if 'biased' is on
    setp(cy, "volume_biased", False)
    setp(cy, "volume_step_rate", 1.0)
    setp(cy, "volume_preview_step_rate", 4.0)
    setp(cy, "volume_max_steps", 1024)
    # adaptive subdivision (dicing)
    setp(cy, "dicing_rate", 1.0)
    setp(cy, "preview_dicing_rate", 8.0)
    setp(cy, "offscreen_dicing_scale", 4.0)
    setp(cy, "max_subdivisions", 12)
    # film / performance
    cy.pixel_filter_type = 'BLACKMAN_HARRIS'
    cy.filter_width = 1.5
    cy.use_animated_seed = True
    setp(cy, "use_auto_tile", True)
    setp(cy, "tile_size", 2048)
    r.use_persistent_data = True
    setp(sc.cycles_curves, "shape", 'THICK')
    # format / motion blur
    r.resolution_x = P["res_x"]
    r.resolution_y = P["res_y"]
    r.resolution_percentage = 100
    r.fps = P["fps"]
    sc.frame_start = P["frame_start"]
    sc.frame_end = P["frame_end"]
    r.use_motion_blur = True
    r.motion_blur_shutter = 0.5           # 180-degree shutter
    setp(r, "motion_blur_position", 'CENTER')
    # colour management
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
    ims = r.image_settings
    if P.get("output", "VIDEO") == "VIDEO":
        # MP4: graded picture (AgX + compositor baked in) with the soundtrack from the Sequencer
        setp(ims, "media_type", 'VIDEO')
        setp(ims, "file_format", 'FFMPEG')
        ff = r.ffmpeg
        setp(ff, "format", 'MPEG4')
        setp(ff, "codec", 'H264')
        setp(ff, "constant_rate_factor", 'PERC_LOSSLESS')
        setp(ff, "ffmpeg_preset", 'GOOD')
        setp(ff, "audio_codec", 'AAC')
        setp(ff, "audio_bitrate", 320)
        setp(ff, "audio_channels", 'STEREO')
        setp(ff, "audio_mixrate", 48000)
        r.filepath = "//renders/3AM_Harbor_"
    else:
        # multilayer EXR frames (scene-linear, for grading; no audio - use Render > Render Audio)
        setp(ims, "media_type", 'MULTI_LAYER_IMAGE')
        for fmt in ('OPEN_EXR_MULTILAYER', 'OPEN_EXR'):
            if setp(ims, "file_format", fmt):
                break
        setp(ims, "color_depth", '16')
        setp(ims, "exr_codec", 'DWAA')
        r.filepath = "//renders/3AM_Harbor_####"
    vl = sc.view_layers[0]
    vl.use_pass_z = True
    vl.use_pass_mist = True
    setp(vl, "use_pass_cryptomatte_object", True)
    setp(vl.cycles, "denoising_store_passes", True)
    setp(vl.cycles, "use_pass_volume_direct", True)


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
    """Render Layers -> Glare (fog glow) -> Lens distortion/dispersion -> grade -> vignette -> output."""
    sc = SC
    setp(sc.render, "use_compositing", True)
    ng = getattr(sc, "compositing_node_group", None)
    if ng is None or not ng.name.startswith("H3_"):
        ng = bpy.data.node_groups.get("H3_Comp") or bpy.data.node_groups.new("H3_Comp", 'CompositorNodeTree')
        sc.compositing_node_group = ng
    for n in list(ng.nodes):
        ng.nodes.remove(n)
    if not [it for it in ng.interface.items_tree if getattr(it, "in_out", "") == 'OUTPUT']:
        ng.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')
    g = NT(ng)
    rl = g.new('CompositorNodeRLayers', -1100, 0)
    setp(rl, "scene", sc)
    out = g.new('NodeGroupOutput', 700, 0)
    img = rl.outputs["Image"]
    try:
        glare = g.new('CompositorNodeGlare', -800, 0)
        g.link(img, glare.inputs["Image"])
        menu_set(glare, "Type", ("Fog Glow", "FOG_GLOW", "Bloom", "BLOOM"))
        menu_set(glare, "Quality", ("High", "HIGH"))
        g.set(glare, {"Threshold": 1.0, "Smoothness": 0.3, "Strength": 0.4, "Size": 0.75})
        img = glare.outputs["Image"]
    except Exception as e:
        log("glare", e)
    try:
        lens = g.new('CompositorNodeLensdist', -550, 0)
        g.link(img, lens.inputs["Image"])
        g.set(lens, {"Distortion": -0.006, "Dispersion": 0.012})
        if lens.inputs.get("Fit") is not None:
            lens.inputs["Fit"].default_value = True
        img = lens.outputs["Image"]
    except Exception as e:
        log("lens", e)
    try:
        # grade in 'exposed' space: +exposure -> Lift/Gamma/Gain -> -exposure, so the colour
        # management exposure (and the un-composited viewport) stay the single brightness control
        ex1 = g.new('CompositorNodeExposure', -350, 150)
        g.link(img, ex1.inputs["Image"])
        g.set(ex1, {"Exposure": P["exposure"]})
        cb = g.new('CompositorNodeColorBalance', -150, 0)
        g.link(ex1.outputs["Image"], cb.inputs["Image"])
        lift, gain = g.i(cb, "Lift", 'RGBA'), g.i(cb, "Gain", 'RGBA')
        dl = tuple(lift.default_value)
        if all(abs(v - 1.0) < 0.01 for v in dl[:3]):
            lift.default_value = (0.985, 1.0, 1.03, 1.0)     # cool, teal-leaning shadows
            gain.default_value = (1.03, 1.0, 0.965, 1.0)      # warm highlights (the lamp)
        else:
            log("color balance defaults", dl)
        ex2 = g.new('CompositorNodeExposure', 50, 150)
        g.link(cb.outputs["Image"], ex2.inputs["Image"])
        g.set(ex2, {"Exposure": -P["exposure"]})
        img = ex2.outputs["Image"]
    except Exception as e:
        log("color balance", e)
    try:
        em = g.new('CompositorNodeEllipseMask', -300, -400)
        g.set(em, {"Position": (0.5, 0.5), "Size": (1.05, 1.0)})
        bl = g.new('CompositorNodeBlur', -50, -400)
        g.link(g.o(em), bl.inputs["Image"])
        g.set(bl, {"Size": (260.0, 260.0)})
        img = g.mix(0.4, img, g.o(bl), blend='MULTIPLY', x=300, y=0)
    except Exception as e:
        log("vignette", e)
    g.link(img, out.inputs[0])


AUDIO_STEMS = [  # (file, channel, volume) - real field recordings, prepared by make_soundtrack.py
    ("01_shore_waves.wav", 1, 1.0),        # small night waves on the stones (Luftrum, CC BY 4.0 - credit)
    ("02_pier_wood_creaks.wav", 2, 1.0),   # moored boat, rope + timber creaks, water under the boards (Falcet, CC0)
    ("03_night_crickets.wav", 3, 1.0),     # two crickets in the grass behind camera (Lisa Redfern, public domain)
]


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


def setup_audio():
    """Lay the soundtrack stems into the Sequencer (sound-only strips: the 3D render is untouched)."""
    sc = SC
    d = audio_folder()
    if not d:
        log("audio: no 'audio' folder found - skipped (run make_soundtrack.py first)")
        return 0
    se = sc.sequence_editor or sc.sequence_editor_create()
    for s in list(se.strips_all):
        if s.name.startswith("H3_"):
            se.strips.remove(s)
    n = 0
    for fname, ch, vol in AUDIO_STEMS:
        path = os.path.join(d, fname)
        if not os.path.isfile(path):
            log("audio: missing", fname)
            continue
        s = se.strips.new_sound("H3_" + os.path.splitext(fname)[0], path, ch, P["frame_start"])
        s.volume = vol
        n += 1
    setp(sc, "sync_mode", 'AUDIO_SYNC')          # viewport playback follows the sound clock
    setp(sc.render, "use_sequencer", True)
    return n


def setup_viewport():
    """Look through the camera in Solid mode (cheap on laptops); push the clip range out to the mountains."""
    try:
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                if area.type != 'VIEW_3D':
                    continue
                sp = area.spaces.active
                sp.shading.type = 'SOLID'
                setp(sp.shading, "color_type", 'MATERIAL')
                sp.clip_start = 0.02
                sp.clip_end = 30000.0
                sp.region_3d.view_perspective = 'CAMERA'
                region = next((rg for rg in area.regions if rg.type == 'WINDOW'), None)
                if region is not None:
                    with bpy.context.temp_override(window=win, area=area, region=region):
                        bpy.ops.view3d.view_center_camera()
    except Exception as e:
        log("viewport", e)


VIEW_COLORS = {
    "deck": (0.30, 0.27, 0.22, 1), "wood_trim": (0.35, 0.33, 0.30, 1), "wood_dark": (0.15, 0.13, 0.11, 1),
    "wood_mast": (0.32, 0.30, 0.27, 1), "moss_bed": (0.10, 0.18, 0.05, 1), "moss_hair": (0.15, 0.30, 0.06, 1),
    "algae_hair": (0.10, 0.30, 0.05, 1), "leaf": (0.15, 0.40, 0.08, 1), "piling": (0.25, 0.26, 0.20, 1),
    "ocean": (0.03, 0.06, 0.08, 1), "paint": (0.45, 0.08, 0.06, 1), "roof": (0.12, 0.12, 0.12, 1),
    "glass": (0.5, 0.55, 0.55, 1), "rust": (0.35, 0.15, 0.06, 1), "rope": (0.40, 0.35, 0.25, 1),
    "buoy": (0.8, 0.3, 0.1, 1), "hull_paint": (0.08, 0.25, 0.30, 1), "hull_inside": (0.55, 0.52, 0.48, 1),
    "outboard": (0.08, 0.08, 0.09, 1), "skin": (0.7, 0.45, 0.35, 1), "knit": (0.4, 0.08, 0.06, 1),
    "dark": (0.04, 0.04, 0.04, 1), "lamp_paint": (0.6, 0.08, 0.05, 1),
    "lamp_glass": (0.9, 0.9, 0.85, 1), "flame": (1.0, 0.7, 0.2, 1), "rock": (0.22, 0.22, 0.23, 1),
    "fisher": (0.30, 0.28, 0.12, 1),
}


def apply_viewport_colors():
    for k, m in MATS.items():
        if k in VIEW_COLORS:
            try:
                m.diffuse_color = VIEW_COLORS[k]
            except Exception:
                pass


def remove_default_scene():
    """In a fresh, unsaved file, drop the default cube scene so only the harbour remains."""
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
             ("camera", build_camera), ("deck", build_deck), ("moss", build_moss), ("sprouts", build_sprouts),
             ("pilings", build_pilings), ("house", build_house), ("props", build_props), ("ocean", build_ocean),
             ("boat", build_boat), ("mountains", build_mountains), ("atmosphere", build_atmosphere),
             ("compositor", setup_compositor), ("audio", setup_audio), ("viewport", setup_viewport)]
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
    SC.frame_set(P["frame_start"])
    try:
        remove_default_scene()
    except Exception as e:
        log("default scene", e)
    if save_path:
        try:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            bpy.ops.wm.save_as_mainfile(filepath=save_path, check_existing=False)
            log("saved", save_path)
        except Exception as e:
            log("save failed", e)
    log("total %.1fs, %d objects" % (time.time() - t0, len([o for o in SC.objects if o.name.startswith("H3_")])))
    return LOG


if __name__ == "__main__":
    build_all()
