"""
5 AM PRE-DAWN - the rower
=========================

Builds the fisherman who rows the boat: a rigged, dressed, detailed human made from
MakeHuman's CC0 data (the MPFB2 project: github.com/makehumancommunity/mpfb2,
src/mpfb/data - base mesh, macro targets, default skeleton and skin weights; the data is
CC0, see LICENSE.ASSETS.md there). No MPFB code is used, only its data files.

  * body: MakeHuman base mesh shaped by the macro targets into a weathered, strong man in
    his fifties (age / muscle / weight / height / proportion targets + a few face targets)
  * skeleton: MakeHuman's 163-bone default rig with its skin weights (fingers, face, twist
    bones), joints placed from the shaped mesh exactly the way MakeHuman places them
  * eyes (wet cornea over sclera / iris / pupil), eyelids from the base mesh
  * clothes tailored from the body surface itself, so they deform with the same weights:
    worn yellow oilskin jacket with a stand-up collar, heavy canvas trousers, rubber sea
    boots, a knitted wool watch cap
  * hair: grey-flecked beard and moustache, eyebrows, nape hair under the cap
  * skin: subsurface scattering, weathered colour variation, pores, lips from MakeHuman's
    region masks

Run with Blender 5.x (or the bpy module):
    blender -b --python tools/make_rower.py -- <mpfb2 data dir> [out.blend]
Default output: assets/5AM_rower.blend (collection "H5R_Rower", armature "H5R_Rig").
The rest pose is MakeHuman's A-pose; build_5am_predawn.py seats him and rows.
"""
import bpy
import bmesh
import gzip
import json
import os
import sys
from mathutils import Matrix, Vector

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
DATA = ARGS[0] if ARGS else os.path.expanduser("~/makehumancommunity/mpfb2/src/mpfb/data")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = ARGS[1] if len(ARGS) > 1 else os.path.join(HERE, "..", "assets", "5AM_rower.blend")
PFX = "H5R_"

# macro sliders, MakeHuman conventions (0..1)
MACRO = dict(gender=1.0, age=0.69, muscle=0.72, weight=0.56, height=0.56, proportions=0.6)
FACE = {  # extra shape targets (file under targets/, weight)
    "head/head-age-incr": 0.6, "head/head-square": 0.35, "head/head-fat-decr": 0.2,
    "nose/nose-scale-horiz-incr": 0.25, "nose/nose-hump-incr": 0.35,
    "chin/chin-jutting-incr": 0.2, "cheek/r-cheek-bones-incr": 0.3, "cheek/l-cheek-bones-incr": 0.3,
    "eyebrows/eyebrows-trans-down": 0.3, "neck/neck-scale-horiz-incr": 0.3,
}


# ----------------------------------------------------------------------------
#  MakeHuman data
# ----------------------------------------------------------------------------
def read_obj(path):
    vs, vts, faces, fuv, fgrp, groups = [], [], [], [], [], []
    g = None
    for line in open(path):
        if line.startswith("v "):
            vs.append([float(t) for t in line.split()[1:4]])
        elif line.startswith("vt "):
            vts.append([float(t) for t in line.split()[1:3]])
        elif line.startswith("g "):
            g = line.split()[1]
            if g not in groups:
                groups.append(g)
        elif line.startswith("f "):
            ids, uvs = [], []
            for tok in line.split()[1:]:
                p = tok.split("/")
                ids.append(int(p[0]) - 1)
                uvs.append(int(p[1]) - 1 if len(p) > 1 and p[1] else -1)
            faces.append(ids)
            fuv.append(uvs)
            fgrp.append(groups.index(g))
    return vs, vts, faces, fuv, fgrp, groups


def read_target(name):
    path = os.path.join(DATA, "targets", name + ".target.gz")
    if not os.path.isfile(path):
        print("  (no target %s)" % name)
        return []
    out = []
    with gzip.open(path, "rt") as f:
        for line in f:
            p = line.split()
            if len(p) == 4 and not line.startswith("#"):
                out.append((int(p[0]), float(p[1]), float(p[2]), float(p[3])))
    return out


def macro_targets():
    """MakeHuman's macro modifier weighting (gender x age x muscle x weight [x height/proportions])."""
    m = MACRO
    gw = {"male": m["gender"], "female": 1.0 - m["gender"]}
    a = m["age"]
    if a < 0.5:
        aw = {"young": 2.0 * a, "child": 1.0 - 2.0 * a}
    else:
        aw = {"young": 2.0 * (1.0 - a), "old": 2.0 * a - 1.0}

    def tri(v):
        return {"min": max(0.0, 1.0 - 2.0 * v), "average": 1.0 - abs(2.0 * v - 1.0), "max": max(0.0, 2.0 * v - 1.0)}
    mw = tri(m["muscle"])
    ww = tri(m["weight"])
    out = []
    for g, wg in gw.items():
        for ag, wa in aw.items():
            for mu, wm in mw.items():
                for we, wwt in ww.items():
                    w = wg * wa * wm * wwt
                    if w > 1e-4:
                        out.append(("macrodetails/universal-%s-%s-%smuscle-%sweight" % (g, ag, mu, we), w))
                        h = m["height"]
                        if h > 0.5:
                            out.append(("macrodetails/height/%s-%s-%smuscle-%sweight-maxheight" % (g, ag, mu, we),
                                        w * (2.0 * h - 1.0)))
                        p = m["proportions"]
                        if p > 0.5:
                            out.append(("macrodetails/proportions/%s-%s-%smuscle-%sweight-idealproportions"
                                        % (g, ag, mu, we), w * (2.0 * p - 1.0)))
            out.append(("macrodetails/caucasian-%s-%s" % (g, ag), wg * wa))
    return out


def to_blender(p):
    """MakeHuman obj space (Y up, decimetres) -> Blender (Z up, metres)."""
    return Vector((p[0] * 0.1, -p[2] * 0.1, p[1] * 0.1))


# ----------------------------------------------------------------------------
#  build
# ----------------------------------------------------------------------------
def clear_file():
    for c in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.armatures,
              bpy.data.collections, bpy.data.particles, bpy.data.textures, bpy.data.images):
        for d in list(c):
            c.remove(d)


def shaped_vertices():
    vs, vts, faces, fuv, fgrp, groups = read_obj(os.path.join(DATA, "3dobjs", "base.obj"))
    co = [list(v) for v in vs]
    tl = macro_targets() + list(FACE.items())
    for name, w in tl:
        for i, dx, dy, dz in read_target(name):
            co[i][0] += w * dx
            co[i][1] += w * dy
            co[i][2] += w * dz
    co = [to_blender(p) for p in co]
    zmin = min(co[i].z for f, g in zip(faces, fgrp) if groups[g] == "body" for i in f)
    for p in co:                                     # soles on z = 0
        p.z -= zmin
    return co, vts, faces, fuv, fgrp, groups


def build_full_mesh(co, vts, faces, fuv, fgrp):
    me = bpy.data.meshes.new(PFX + "Full")
    me.from_pydata([tuple(p) for p in co], [], faces)
    uv = me.uv_layers.new(name="UVMap")
    li = 0
    for f, uvs in zip(faces, fuv):
        for k in uvs:
            uv.data[li].uv = vts[k] if k >= 0 else (0.0, 0.0)
            li += 1
    at = me.attributes.new("mh_group", 'INT', 'FACE')
    at.data.foreach_set("value", fgrp)
    ob = bpy.data.objects.new(PFX + "Full", me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def joint_positions(co, faces, fgrp, groups):
    verts_of = {}
    for f, g in zip(faces, fgrp):
        verts_of.setdefault(groups[g], set()).update(f)

    def pos(spec):
        s = spec["strategy"]
        if s == "CUBE" and spec.get("cube_name") in verts_of:
            vv = verts_of[spec["cube_name"]]
            return sum((co[i] for i in vv), Vector()) / len(vv)
        if s == "VERTEX":
            return co[spec["vertex_index"]].copy()
        if s == "MEAN":
            vv = spec["vertex_indices"]
            return sum((co[i] for i in vv), Vector()) / len(vv)
        return Vector(spec["default_position"])
    return pos


def build_rig(co, faces, fgrp, groups, coll):
    rig = json.load(open(os.path.join(DATA, "rigs", "standard", "rig.default.json")))
    pos = joint_positions(co, faces, fgrp, groups)
    arm = bpy.data.armatures.new(PFX + "Rig")
    ob = bpy.data.objects.new(PFX + "Rig", arm)
    coll.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode='EDIT')
    eb = {}
    for name, d in rig.items():
        b = arm.edit_bones.new(name)
        b.head = pos(d["head"])
        b.tail = pos(d["tail"])
        if (b.tail - b.head).length < 1e-4:
            b.tail = b.head + Vector((0, 0, 0.01))
        eb[name] = b
    for name, d in rig.items():
        b = eb[name]
        if d.get("parent"):
            b.parent = eb[d["parent"]]
            b.use_connect = bool(d.get("use_connect", False)) and (b.parent.tail - b.head).length < 1e-4
        b.roll = d.get("roll", 0.0)
        b.use_inherit_rotation = d.get("use_inherit_rotation", True)
        b.use_local_location = d.get("use_local_location", True)
    bpy.ops.object.mode_set(mode='OBJECT')
    arm.display_type = 'STICK'
    ob.show_in_front = True
    return ob


def add_weights(ob):
    w = json.load(open(os.path.join(DATA, "rigs", "standard", "weights.default.json")))["weights"]
    for bone, pairs in w.items():
        vg = ob.vertex_groups.new(name=bone)
        by = {}
        for vid, wt in pairs:
            by.setdefault(round(wt, 4), []).append(vid)
        for wt, ids in by.items():
            vg.add(ids, wt, 'REPLACE')


def dominant_bones(ob):
    names = {vg.index: vg.name for vg in ob.vertex_groups}
    dom = []
    for v in ob.data.vertices:
        best, bw = "", 0.0
        for g in v.groups:
            if g.weight > bw:
                bw, best = g.weight, names[g.group]
        dom.append(best)
    return dom


def split_object(src, name, keep_face, coll, mats=None):
    """Copy of src with only the faces keep_face(poly) says yes to (weights and UVs carried over)."""
    me = src.data.copy()
    me.name = PFX + name
    bm = bmesh.new()
    bm.from_mesh(me)
    lay = bm.faces.layers.int.get("mh_group")
    kill = [f for f in bm.faces if not keep_face(f, lay)]
    bmesh.ops.delete(bm, geom=kill, context='FACES')
    loose = [v for v in bm.verts if not v.link_faces]
    bmesh.ops.delete(bm, geom=loose, context='VERTS')
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(PFX + name, me)
    coll.objects.link(ob)
    me.materials.clear()
    for m in (mats or []):
        me.materials.append(m)
    for p in me.polygons:
        p.use_smooth = True
    return ob


def copy_groups(src, dst):
    """Vertex groups travel with mesh data only as names; recreate them on the new object."""
    for vg in src.vertex_groups:
        if dst.vertex_groups.get(vg.name) is None:
            dst.vertex_groups.new(name=vg.name)


# ----------------------------------------------------------------------------
#  materials
# ----------------------------------------------------------------------------
def nodes(m):
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    out.location = (900, 0)
    return nt, out


def noise(nt, vec, scale, detail=4.0, rough=0.55, loc=(0, 0)):
    n = nt.nodes.new('ShaderNodeTexNoise')
    n.location = loc
    n.inputs["Scale"].default_value = scale
    n.inputs["Detail"].default_value = detail
    n.inputs["Roughness"].default_value = rough
    if vec is not None:
        nt.links.new(vec, n.inputs["Vector"])
    return n


def mixc(nt, fac, a, b, blend='MIX', loc=(0, 0)):
    n = nt.nodes.new('ShaderNodeMix')
    n.data_type = 'RGBA'
    n.blend_type = blend
    n.location = loc
    if isinstance(fac, (int, float)):
        n.inputs[0].default_value = fac
    else:
        nt.links.new(fac, n.inputs[0])
    for sock, v in ((n.inputs[6], a), (n.inputs[7], b)):
        if isinstance(v, tuple):
            sock.default_value = v + (1.0,) if len(v) == 3 else v
        else:
            nt.links.new(v, sock)
    return n.outputs[2]


def mask_image(nt, fname, uvsock, loc):
    path = os.path.join(DATA, "textures", fname)
    if not os.path.isfile(path):
        return None
    img = bpy.data.images.load(path, check_existing=True)
    img.colorspace_settings.name = 'Non-Color'
    img.pack()
    t = nt.nodes.new('ShaderNodeTexImage')
    t.image = img
    t.location = loc
    nt.links.new(uvsock, t.inputs["Vector"])
    return t.outputs["Color"]


def mat_skin():
    m = bpy.data.materials.new(PFX + "Skin")
    nt, out = nodes(m)
    tc = nt.nodes.new('ShaderNodeTexCoord')
    tc.location = (-1400, 0)
    uv = nt.nodes.new('ShaderNodeUVMap')
    uv.location = (-1400, -300)
    obj = tc.outputs["Object"]
    blot = noise(nt, obj, 38.0, 5.0, 0.6, (-1100, 200))
    base = mixc(nt, blot.outputs["Fac"], (0.36, 0.20, 0.145), (0.47, 0.285, 0.21), loc=(-850, 200))
    red = noise(nt, obj, 9.0, 3.0, 0.5, (-1100, -50))
    rr = nt.nodes.new('ShaderNodeMapRange')
    rr.location = (-900, -50)
    nt.links.new(red.outputs["Fac"], rr.inputs["Value"])
    rr.inputs[1].default_value, rr.inputs[2].default_value = 0.5, 0.75
    base = mixc(nt, rr.outputs[0], base, (0.45, 0.17, 0.13), loc=(-650, 150))       # wind-burnt blotches
    lips = mask_image(nt, "mpfb_lips.jpg", uv.outputs["UV"], (-1100, -350))
    if lips is not None:
        lf = nt.nodes.new('ShaderNodeMath')
        lf.operation = 'MULTIPLY'
        lf.inputs[1].default_value = 0.7
        lf.location = (-800, -350)
        nt.links.new(lips, lf.inputs[0])
        base = mixc(nt, lf.outputs[0], base, (0.33, 0.12, 0.10), loc=(-450, 100))
    nails = mask_image(nt, "mpfb_fingernails.jpg", uv.outputs["UV"], (-1100, -600))
    if nails is not None:
        base = mixc(nt, nails, base, (0.55, 0.40, 0.33), loc=(-300, 50))
    pores = noise(nt, obj, 900.0, 2.0, 0.5, (-800, -700))
    wr = noise(nt, obj, 60.0, 6.0, 0.7, (-800, -900))
    h = nt.nodes.new('ShaderNodeMath')
    h.operation = 'MULTIPLY_ADD'
    h.location = (-550, -800)
    nt.links.new(pores.outputs["Fac"], h.inputs[0])
    h.inputs[1].default_value = 0.35
    nt.links.new(wr.outputs["Fac"], h.inputs[2])
    bump = nt.nodes.new('ShaderNodeBump')
    bump.location = (-300, -700)
    bump.inputs["Strength"].default_value = 0.25
    bump.inputs["Distance"].default_value = 0.0008
    nt.links.new(h.outputs[0], bump.inputs["Height"])
    rough = nt.nodes.new('ShaderNodeMapRange')
    rough.location = (-300, -350)
    nt.links.new(blot.outputs["Fac"], rough.inputs["Value"])
    rough.inputs[3].default_value, rough.inputs[4].default_value = 0.38, 0.55
    b = nt.nodes.new('ShaderNodeBsdfPrincipled')
    b.location = (200, 0)
    nt.links.new(base, b.inputs["Base Color"])
    nt.links.new(rough.outputs[0], b.inputs["Roughness"])
    nt.links.new(bump.outputs[0], b.inputs["Normal"])
    b.inputs["Subsurface Weight"].default_value = 1.0
    b.inputs["Subsurface Radius"].default_value = (1.0, 0.38, 0.2)
    b.inputs["Subsurface Scale"].default_value = 0.012
    b.inputs["Specular IOR Level"].default_value = 0.45
    b.inputs["Sheen Weight"].default_value = 0.08
    nt.links.new(b.outputs[0], out.inputs["Surface"])
    return m


def mat_eye():
    """Procedural eyeball in object space (+Y = gaze): sclera, grey-blue iris, pupil, wet cornea."""
    m = bpy.data.materials.new(PFX + "Eye")
    nt, out = nodes(m)
    tc = nt.nodes.new('ShaderNodeTexCoord')
    tc.location = (-1200, 0)
    sep = nt.nodes.new('ShaderNodeSeparateXYZ')
    sep.location = (-1000, 0)
    nt.links.new(tc.outputs["Object"], sep.inputs[0])
    ln = nt.nodes.new('ShaderNodeVectorMath')
    ln.operation = 'LENGTH'
    ln.location = (-1000, -200)
    nt.links.new(tc.outputs["Object"], ln.inputs[0])
    # angle from the gaze axis (object -Y): cos = -y / r
    cosang = nt.nodes.new('ShaderNodeMath')
    cosang.operation = 'DIVIDE'
    cosang.location = (-800, 0)
    neg = nt.nodes.new('ShaderNodeMath')
    neg.operation = 'MULTIPLY'
    neg.inputs[1].default_value = -1.0
    neg.location = (-900, 100)
    nt.links.new(sep.outputs[1], neg.inputs[0])
    nt.links.new(neg.outputs[0], cosang.inputs[0])
    nt.links.new(ln.outputs["Value"], cosang.inputs[1])
    ramp = nt.nodes.new('ShaderNodeValToRGB')
    ramp.location = (-600, 0)
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = 0.80, (0.62, 0.58, 0.54, 1)
    els[1].position, els[1].color = 0.905, (0.60, 0.56, 0.52, 1)
    for p, c in ((0.915, (0.12, 0.16, 0.18, 1)), (0.955, (0.18, 0.23, 0.26, 1)), (0.975, (0.01, 0.01, 0.01, 1))):
        e = els.new(p)
        e.color = c
    nt.links.new(cosang.outputs[0], ramp.inputs[0])
    b = nt.nodes.new('ShaderNodeBsdfPrincipled')
    b.location = (200, 0)
    nt.links.new(ramp.outputs[0], b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = 0.35
    b.inputs["Coat Weight"].default_value = 1.0
    b.inputs["Coat Roughness"].default_value = 0.02
    b.inputs["Coat IOR"].default_value = 1.376
    b.inputs["Subsurface Weight"].default_value = 0.3
    b.inputs["Subsurface Scale"].default_value = 0.003
    nt.links.new(b.outputs[0], out.inputs["Surface"])
    return m


def mat_cloth(name, col, rough, coat=0.0, sheen=0.0, knit=False, wrinkle=0.6, wear=None):
    m = bpy.data.materials.new(PFX + name)
    nt, out = nodes(m)
    tc = nt.nodes.new('ShaderNodeTexCoord')
    tc.location = (-1400, 0)
    obj = tc.outputs["Object"]
    dirt = noise(nt, obj, 6.0, 6.0, 0.6, (-1100, 250))
    base = mixc(nt, dirt.outputs["Fac"], tuple(c * 0.7 for c in col), col, loc=(-800, 250))
    if wear is not None:
        wn = noise(nt, obj, 22.0, 8.0, 0.7, (-1100, 0))
        wm = nt.nodes.new('ShaderNodeMapRange')
        wm.location = (-900, 0)
        nt.links.new(wn.outputs["Fac"], wm.inputs["Value"])
        wm.inputs[1].default_value, wm.inputs[2].default_value = 0.6, 0.72
        base = mixc(nt, wm.outputs[0], base, wear, loc=(-600, 200))
    # creases: anisotropic noise stretched across the limbs
    mp = nt.nodes.new('ShaderNodeMapping')
    mp.location = (-1200, -350)
    mp.inputs["Scale"].default_value = (2.5, 2.5, 9.0)
    nt.links.new(obj, mp.inputs["Vector"])
    cr = noise(nt, mp.outputs[0], 14.0, 5.0, 0.6, (-1000, -350))
    h = cr.outputs["Fac"]
    if knit:
        wv = nt.nodes.new('ShaderNodeTexWave')
        wv.location = (-1000, -600)
        wv.bands_direction = 'X'
        wv.inputs["Scale"].default_value = 650.0
        wv.inputs["Distortion"].default_value = 1.5
        nt.links.new(obj, wv.inputs["Vector"])
        ad = nt.nodes.new('ShaderNodeMath')
        ad.operation = 'MULTIPLY_ADD'
        ad.location = (-750, -500)
        nt.links.new(wv.outputs["Fac"], ad.inputs[0])
        ad.inputs[1].default_value = 0.8
        nt.links.new(h, ad.inputs[2])
        h = ad.outputs[0]
    fz = noise(nt, obj, 2500.0, 2.0, 0.5, (-750, -800))           # fibre fuzz
    hh = nt.nodes.new('ShaderNodeMath')
    hh.operation = 'MULTIPLY_ADD'
    hh.location = (-550, -600)
    nt.links.new(fz.outputs["Fac"], hh.inputs[0])
    hh.inputs[1].default_value = 0.15
    nt.links.new(h, hh.inputs[2])
    bump = nt.nodes.new('ShaderNodeBump')
    bump.location = (-300, -500)
    bump.inputs["Strength"].default_value = wrinkle
    bump.inputs["Distance"].default_value = 0.004
    nt.links.new(hh.outputs[0], bump.inputs["Height"])
    b = nt.nodes.new('ShaderNodeBsdfPrincipled')
    b.location = (200, 0)
    nt.links.new(base, b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = rough
    b.inputs["Coat Weight"].default_value = coat
    b.inputs["Coat Roughness"].default_value = 0.25
    b.inputs["Sheen Weight"].default_value = sheen
    b.inputs["Sheen Roughness"].default_value = 0.4
    nt.links.new(bump.outputs[0], b.inputs["Normal"])
    nt.links.new(b.outputs[0], out.inputs["Surface"])
    return m


def mat_hair(name, melanin, redness=0.3, grey=0.35):
    m = bpy.data.materials.new(PFX + name)
    nt, out = nodes(m)
    hi = nt.nodes.new('ShaderNodeHairInfo')
    hi.location = (-700, 0)
    h = nt.nodes.new('ShaderNodeBsdfHairPrincipled')
    h.location = (0, 0)
    try:
        h.parametrization = 'MELANIN'
    except Exception:
        pass
    # random grey hairs: melanin drops to near zero for a share of the strands
    gr = nt.nodes.new('ShaderNodeMath')
    gr.operation = 'GREATER_THAN'
    gr.inputs[1].default_value = 1.0 - grey
    gr.location = (-450, 0)
    nt.links.new(hi.outputs["Random"], gr.inputs[0])
    mel = nt.nodes.new('ShaderNodeMix')
    mel.data_type = 'FLOAT'
    mel.location = (-250, 0)
    nt.links.new(gr.outputs[0], mel.inputs[0])
    mel.inputs[2].default_value = melanin
    mel.inputs[3].default_value = 0.08
    nt.links.new(mel.outputs[0], h.inputs["Melanin"])
    h.inputs["Melanin Redness"].default_value = redness
    h.inputs["Roughness"].default_value = 0.35
    h.inputs["Random Roughness"].default_value = 0.3
    nt.links.new(h.outputs[0], out.inputs["Surface"])
    return m


# ----------------------------------------------------------------------------
#  clothes and hair
# ----------------------------------------------------------------------------
HEAD = ("head", "jaw", "eye", "levator", "oculi", "oris", "orbicularis", "risorius", "temporalis",
        "special", "tongue")
HAND = ("wrist", "metacarpal", "finger")
TORSO = ("spine", "clavicle", "breast")
ARM = ("shoulder", "upperarm", "lowerarm")
PELVIS = ("pelvis", "root")
LEG = ("upperleg", "lowerleg")
FEET = ("foot", "toe")


def region(name):
    for tag, pre in (("head", HEAD), ("neck", ("neck",)), ("hand", HAND), ("torso", TORSO), ("arm", ARM),
                     ("pelvis", PELVIS), ("leg", LEG), ("foot", FEET)):
        if name.startswith(pre):
            return tag
    return "torso"


def garment_test(lm):
    """Per-vertex membership tests. Garments overlap a little (jacket over trouser waist, trousers
    over boot tops) so no skin ever shows between them."""
    def cap_line(y):
        t = min(1.0, max(0.0, (y - lm["head_front"]) / (lm["head_back"] - lm["head_front"])))
        return lm["eye_z"] + 0.036 - 0.075 * t

    return {
        "jacket": lambda r, c: r == "arm" or (r == "neck" and c.z < lm["collar_z"])
        or (r in ("torso", "pelvis", "leg") and c.z > lm["hem_z"] + 0.015),
        "trousers": lambda r, c: r in ("torso", "pelvis", "leg") and lm["boot_z"] - 0.05 < c.z < lm["hem_z"] + 0.10,
        "boots": lambda r, c: r == "foot" or (r == "leg" and c.z < lm["boot_z"] + 0.03),
        "cap": lambda r, c: r == "head" and c.z > cap_line(c.y),
        "cuff": lambda r, c: r == "head" and cap_line(c.y) < c.z < cap_line(c.y) + 0.032,
    }


def garment(src, name, keep, lift, noise_str, mats, rig, coll, thick, relax=4):
    ob = split_object(src, name, keep, coll, mats)
    copy_groups(src, ob)
    tex = bpy.data.textures.new(PFX + name + "_Wrinkles", 'CLOUDS')
    tex.noise_scale = 0.06
    tex.noise_depth = 3
    d = ob.modifiers.new("Offset", 'DISPLACE')
    d.direction = 'NORMAL'
    d.mid_level = 0.0
    d.strength = lift
    d2 = ob.modifiers.new("Folds", 'DISPLACE')
    d2.texture = tex
    d2.texture_coords = 'LOCAL'
    d2.direction = 'NORMAL'
    d2.mid_level = 0.5
    d2.strength = noise_str
    if name == "Cap":
        cf = ob.modifiers.new("Cuff", 'DISPLACE')
        cf.direction = 'NORMAL'
        cf.mid_level = 0.0
        cf.strength = 0.007
        cf.vertex_group = "cuff"
    # relax everything except the open edges (hems, cuffs, collar), which would otherwise creep
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    edge = {v.index for v in bm.verts if v.is_boundary}
    ring = {n.index for v in bm.verts if v.is_boundary for e in v.link_edges for n in e.verts} - edge
    bm.free()
    vg = ob.vertex_groups.new(name="relax")
    vg.add([i for i in range(len(ob.data.vertices)) if i not in edge and i not in ring], 1.0, 'REPLACE')
    vg.add(list(ring), 0.4, 'REPLACE')
    sm = ob.modifiers.new("Relax", 'SMOOTH')           # cloth hangs over the body's detail
    sm.factor = 0.8
    sm.iterations = relax
    sm.vertex_group = "relax"
    a = ob.modifiers.new("Armature", 'ARMATURE')
    a.object = rig
    so = ob.modifiers.new("Solidify", 'SOLIDIFY')
    so.thickness = thick
    so.offset = 1.0
    so.use_even_offset = False
    so.use_rim = True
    sub = ob.modifiers.new("Subdivision", 'SUBSURF')
    sub.levels = 0
    sub.render_levels = 2
    return ob


def add_collar(ob, lm):
    """Stand-up collar: the jacket's neck opening extruded up and slightly outward."""
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    edges = [e for e in bm.edges if e.is_boundary and all(v.co.z > lm["collar_z"] - 0.05 for v in e.verts)
             and all(abs(v.co.x) < 0.12 for v in e.verts)]
    if edges:
        r = bmesh.ops.extrude_edge_only(bm, edges=edges)
        nv = [g for g in r["geom"] if isinstance(g, bmesh.types.BMVert)]
        cx = sum(v.co.x for v in nv) / len(nv)
        cy = sum(v.co.y for v in nv) / len(nv)
        ztop = max(v.co.z for v in nv) + 0.055
        for v in nv:
            out = Vector((v.co.x - cx, v.co.y - cy, 0.0))
            if out.length > 1e-6:
                out.normalize()
            v.co += out * 0.014
            v.co.z = ztop - 0.012 * (v.co.y - cy > 0)                # a touch lower at the back
    bm.to_mesh(ob.data)
    bm.free()


def hair_system(ob, name, vg, count, length, mat_index, children=20, radius=0.00006, clump=0.3, rough=0.0015,
                seed=0, align=(0.0, 0.0, -0.4)):
    mod = ob.modifiers.new(PFX + name, 'PARTICLE_SYSTEM')
    ps = mod.particle_system
    st = ps.settings
    st.name = PFX + name
    st.type = 'HAIR'
    st.count = count
    st.use_advanced_hair = True
    st.emit_from = 'FACE'
    st.use_even_distribution = True
    vel = Vector((0.0, 0.0, 1.0)) + Vector(align)
    k = length / (4.0 * max(1e-6, vel.length))
    st.normal_factor = k
    st.object_align_factor = tuple(a * k for a in align)
    st.factor_random = 0.15 * k
    st.hair_step = 4
    st.render_step = 3
    st.display_step = 2
    st.child_type = 'INTERPOLATED'
    st.rendered_child_count = children
    st.child_percent = 3
    st.clump_factor = clump
    st.roughness_1 = rough
    st.roughness_1_size = 0.002
    st.roughness_endpoint = rough * 0.8
    st.radius_scale = radius
    st.root_radius = 1.0
    st.tip_radius = 0.25
    st.material = mat_index + 1
    ps.vertex_group_density = vg
    ps.seed = seed
    return ps


def main():
    clear_file()
    sc = bpy.context.scene
    coll = bpy.data.collections.new(PFX + "Rower")
    sc.collection.children.link(coll)
    co, vts, faces, fuv, fgrp, groups = shaped_vertices()
    full = build_full_mesh(co, vts, faces, fuv, fgrp)
    rig = build_rig(co, faces, fgrp, groups, coll)
    add_weights(full)
    dom = dominant_bones(full)
    bones = rig.data.bones
    eye_z = 0.5 * (bones["eye.L"].head_local.z + bones["eye.R"].head_local.z)
    me = full.data
    body_g = groups.index("body")
    reg = [region(n) for n in dom]
    head_y = [me.vertices[i].co.y for i in range(len(me.vertices)) if reg[i] == "head"
              and me.vertices[i].co.z > eye_z - 0.02]
    lm = dict(eye_z=eye_z,
              head_front=min(head_y), head_back=max(head_y),
              collar_z=bones["neck02"].head_local.z,
              hem_z=bones["pelvis.L"].head_local.z + 0.02,
              boot_z=bones["lowerleg02.L"].head_local.z - 0.02)
    print("landmarks", {k: round(v, 3) for k, v in lm.items()}, "height %.2f m" % max(p.z for p in co))
    tests = garment_test(lm)
    member = {g: [t(reg[i], me.vertices[i].co) for i in range(len(me.vertices))] for g, t in tests.items()}
    cuff = full.vertex_groups.new(name="cuff")
    cuff.add([i for i, m in enumerate(member["cuff"]) if m], 1.0, 'REPLACE')

    skin = mat_skin()
    eye = mat_eye()
    jacket_m = mat_cloth("Oilskin", (0.028, 0.040, 0.030), 0.38, coat=0.45, wrinkle=0.9,
                         wear=(0.075, 0.080, 0.060))
    trousers_m = mat_cloth("Canvas", (0.030, 0.034, 0.042), 0.85, sheen=0.3, wrinkle=0.7)
    boots_m = mat_cloth("Rubber", (0.018, 0.026, 0.020), 0.32, coat=0.4, wrinkle=0.3)
    cap_m = mat_cloth("Wool", (0.26, 0.070, 0.030), 0.95, sheen=0.8, knit=True, wrinkle=1.0)
    beard_m = mat_hair("Beard", 0.78, 0.18, grey=0.42)
    brow_m = mat_hair("Brows", 0.7, 0.3, grey=0.25)

    # the whole body stays under the clothes (garment edges then never open onto nothing)
    body = split_object(full, "Body", lambda f, lay: f[lay] == body_g, coll, [skin, beard_m, brow_m])
    copy_groups(full, body)
    a = body.modifiers.new("Armature", 'ARMATURE')
    a.object = rig
    sub = body.modifiers.new("Subdivision", 'SUBSURF')
    sub.levels = 1
    sub.render_levels = 2

    def keep(g):
        return lambda f, lay: f[lay] == body_g and all(member[g][v.index] for v in f.verts)
    jacket = garment(full, "Jacket", keep("jacket"), 0.040, 0.014, [jacket_m], rig, coll, 0.005, relax=16)
    add_collar(jacket, lm)
    garment(full, "Trousers", keep("trousers"), 0.018, 0.010, [trousers_m], rig, coll, 0.003, relax=6)
    garment(full, "Boots", keep("boots"), 0.016, 0.002, [boots_m], rig, coll, 0.004, relax=14)
    garment(full, "Cap", keep("cap"), 0.012, 0.003, [cap_m], rig, coll, 0.006, relax=3)

    # eyes from the base mesh's eye helpers, each its own object with its origin at the eye centre
    for side, grp in (("L", "helper-l-eye"), ("R", "helper-r-eye")):
        gi = groups.index(grp)
        e = split_object(full, "Eye." + side, lambda f, lay, gi=gi: f[lay] == gi, coll, [eye])
        copy_groups(full, e)
        cen = sum((v.co for v in e.data.vertices), Vector()) / len(e.data.vertices)
        e.data.transform(Matrix.Translation(-cen))
        e.parent = rig
        e.parent_type = 'BONE'
        e.parent_bone = "eye." + side
        e.matrix_world = Matrix.Translation(cen)

    # hair: beard + moustache, brows, nape - density groups placed by landmarks on the body mesh
    head_b = bones["head"]
    face_front = head_b.head_local.y - 0.04
    mouth_z = eye_z - 0.078
    vg_beard = body.vertex_groups.new(name="hair_beard")
    vg_brow = body.vertex_groups.new(name="hair_brows")
    vg_nape = body.vertex_groups.new(name="hair_nape")
    for v in body.data.vertices:
        c = v.co
        # beard: jaw, chin, cheeks below the cheekbones, upper lip; lips themselves stay bare
        front = c.y < face_front + 0.03
        on_lips = abs(c.z - mouth_z) < 0.011 and abs(c.x) < 0.028 and c.y < face_front - 0.02
        if front and eye_z - 0.16 < c.z < eye_z - 0.035 and not on_lips and abs(c.x) < 0.075:
            w = 1.0 if c.z < eye_z - 0.05 else 0.45
            if c.z > mouth_z + 0.004 and abs(c.x) > 0.03 and c.z > eye_z - 0.06:
                w = 0.25                                         # thin out on the upper cheeks
            vg_beard.add([v.index], w, 'REPLACE')
        if front and eye_z + 0.012 < c.z < eye_z + 0.03 and 0.012 < abs(c.x) < 0.062 and c.y < face_front - 0.01:
            vg_brow.add([v.index], 1.0, 'REPLACE')
        if (not front) and eye_z - 0.075 < c.z < eye_z - 0.01 and c.y > head_b.head_local.y + 0.02:
            vg_nape.add([v.index], 1.0, 'REPLACE')
    hair_system(body, "Beard", "hair_beard", 2200, 0.018, 1, children=14, radius=0.00005, clump=0.25,
                rough=0.0025, seed=3, align=(0.0, -0.2, -0.6))
    hair_system(body, "Brows", "hair_brows", 500, 0.009, 2, children=6, radius=0.00004, clump=0.1,
                rough=0.0008, seed=5, align=(0.0, -0.3, 0.2))
    hair_system(body, "Nape", "hair_nape", 600, 0.014, 1, children=10, radius=0.00005, clump=0.3,
                rough=0.0015, seed=7, align=(0.0, 0.1, -1.2))

    bpy.data.objects.remove(full, do_unlink=True)
    sc.render.engine = 'CYCLES'
    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(OUT), check_existing=False, compress=True)
    print("saved", os.path.abspath(OUT), [o.name for o in coll.objects])


if __name__ == "__main__":
    main()
