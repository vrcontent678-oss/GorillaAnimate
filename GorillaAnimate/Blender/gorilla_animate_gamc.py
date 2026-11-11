bl_info = {
    "name": "Gorilla Animate (.gamc)",
    "author": "Rayan",
    "version": (2, 6, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar (N) > Gorilla Animate; File > Import",
    "description": "Retarget a Gorilla Animate recording onto the Gorilla_IK_Rig (or selected rig)",
    "category": "Import-Export",
}

import array
import math
import os
import re
import struct
import sys

import bpy
from bpy_extras.io_utils import ImportHelper
from mathutils import Matrix, Quaternion, Vector

GAMC_MAGIC = b"GAMC"
GAMC_VERSION = 1
FLOATS_PER_BONE = 10  # px py pz qx qy qz qw sx sy sz

# flags bit 0: every bone of the skinned skeleton was recorded. Files written
# by 1.0.0 only hold head, hands and fingers, which is why the torso, arms and
# legs of those recordings cannot be retargeted.
GAMC_FLAG_FULL_SKELETON = 1

# Unity (left-handed, Y up) -> Blender (right-handed, Z up).
# Positions: (x, y, z) -> (x, z, y)
# Rotations: R_blender = C * R_unity * C, with C symmetric and C^-1 = C.
CONVERT = Matrix((
    (1.0, 0.0, 0.0),
    (0.0, 0.0, 1.0),
    (0.0, 1.0, 0.0),
))


# ---------------------------------------------------------------------------
# .gamc reader
# ---------------------------------------------------------------------------

def _read_string(data, offset):
    (length,) = struct.unpack_from("<H", data, offset)
    offset += 2
    value = data[offset:offset + length].decode("utf-8", errors="replace")
    return value, offset + length


def read_gamc(path):
    """Load a .gamc file into a plain dict."""
    with open(path, "rb") as handle:
        data = handle.read()

    if len(data) < 16 or data[:4] != GAMC_MAGIC:
        raise ValueError("Not a Gorilla Animate .gamc file")

    version, fps, frame_count, bone_count, flags = struct.unpack_from("<HHIHH", data, 4)
    if version != GAMC_VERSION:
        raise ValueError("Unsupported .gamc version %d (expected %d)" % (version, GAMC_VERSION))
    if frame_count <= 0 or bone_count <= 0:
        raise ValueError("Recording contains no frames or no bones")

    offset = 16
    created_utc, offset = _read_string(data, offset)
    room, offset = _read_string(data, offset)

    bones = []
    for _ in range(bone_count):
        name, offset = _read_string(data, offset)
        (parent,) = struct.unpack_from("<h", data, offset)
        offset += 2
        bones.append({"name": name, "parent": int(parent)})

    needed = offset + frame_count * 4 + frame_count * bone_count * FLOATS_PER_BONE * 4
    if len(data) < needed:
        raise ValueError("Truncated .gamc file")

    times = array.array("f")
    times.frombytes(data[offset:offset + frame_count * 4])
    offset += frame_count * 4

    count = frame_count * bone_count * FLOATS_PER_BONE
    values = array.array("f")
    values.frombytes(data[offset:offset + count * 4])

    if sys.byteorder != "little":
        times.byteswap()
        values.byteswap()

    return {
        "path": path,
        "createdUtc": created_utc,
        "room": room,
        "fps": int(fps),
        "frameCount": frame_count,
        "boneCount": bone_count,
        "flags": int(flags),
        "bones": bones,
        "times": times,
        "values": values,
    }


# ---------------------------------------------------------------------------
# Bone name classification
# ---------------------------------------------------------------------------

_ROOTS = {"root", "armature", "origin", "motion"}
_SIDE_LEFT = {"left", "l", "lf", "lt"}
_SIDE_RIGHT = {"right", "r", "rt", "rg"}

_PART_KEYWORDS = (
    ("thumb", ("thumb",)),
    ("index", ("index", "pointer")),
    ("middle", ("middle", "midfinger")),
    ("ring", ("ring",)),
    ("pinky", ("pinky", "little")),
    ("forearm", ("forearm", "lowerarm", "elbow")),
    ("upper_arm", ("upperarm", "bicep")),
    ("shoulder", ("shoulder", "clavicle", "collar")),
    ("hand", ("hand", "wrist", "palm")),
    ("head", ("head",)),
    ("neck", ("neck",)),
    ("chest", ("chest",)),
    ("spine", ("spine", "torso", "lumbar")),
    ("hips", ("hips", "hip", "pelvis", "body", "waist")),
    ("thigh", ("thigh", "upperleg", "upleg")),
    ("shin", ("shin", "calf", "lowerleg", "lowleg", "leg")),
    ("foot", ("foot", "ankle")),
    ("toe", ("toe",)),
    ("arm", ("arm",)),
)

# Which target parts may receive a source part, best first.
_FAMILY = {
    "root": ("root", "hips"),
    "hips": ("hips", "spine", "root"),
    "spine": ("spine", "chest"),
    "chest": ("chest", "spine"),
    "neck": ("neck",),
    "head": ("head",),
    "shoulder": ("shoulder",),
    "upper_arm": ("upper_arm", "arm"),
    "forearm": ("forearm",),
    "hand": ("hand",),
    "thumb": ("thumb",),
    "index": ("index",),
    "middle": ("middle",),
    "ring": ("ring",),
    "pinky": ("pinky",),
    "thigh": ("thigh",),
    "shin": ("shin",),
    "foot": ("foot",),
    "toe": ("toe",),
}

_PRIORITY = {
    "root": 0, "hips": 1, "spine": 2, "chest": 3, "neck": 4, "head": 5,
    "shoulder": 6, "upper_arm": 7, "forearm": 8, "hand": 9,
    "thumb": 10, "index": 11, "middle": 12, "ring": 13, "pinky": 14,
    "thigh": 15, "shin": 16, "foot": 17, "toe": 18,
}

# The joint a body part hangs off in a real skeleton, best first, as
# (part, segment) with segment None meaning "any". Used to keep hands on the
# ends of the arms even when the target rig does not parent them that way (the
# game itself attaches hands to the body and uses IK for the arm).
_CHAIN = {
    "hips": (("root", None),),
    "spine": (("hips", None), ("root", None)),
    "chest": (("spine", None), ("hips", None), ("root", None)),
    "neck": (("chest", None), ("spine", None)),
    "head": (("neck", None), ("chest", None)),
    "shoulder": (("chest", None), ("spine", None)),
    "upper_arm": (("shoulder", None), ("chest", None)),
    "forearm": (("upper_arm", None),),
    "hand": (("forearm", None),),
    "thumb": (("thumb", 1), ("hand", None)),
    "index": (("index", 1), ("hand", None)),
    "middle": (("middle", 1), ("hand", None)),
    "ring": (("ring", 1), ("hand", None)),
    "pinky": (("pinky", 1), ("hand", None)),
    "thigh": (("hips", None),),
    "shin": (("thigh", None),),
    "foot": (("shin", None),),
    "toe": (("foot", None),),
}


def _tokens(name):
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    spaced = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", spaced)
    spaced = re.sub(r"(?<=\d)(?=[A-Za-z])", " ", spaced)
    return [t for t in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if t]


def _side(tokens):
    if "left" in tokens:
        return "L"
    if "right" in tokens:
        return "R"
    for token in reversed(tokens):
        if token in ("l", "lf", "lt"):
            return "L"
        if token in ("r", "rt", "rg"):
            return "R"
        if token.isdigit():
            continue
        # Gorilla Tag suffixes bones with "_new"; that is not part of the side.
        if token in ("new", "old"):
            continue
        break
    return None


def _segment(tokens):
    for token in tokens:
        if token.isdigit():
            value = int(token)
            if 1 <= value <= 5:
                return value
    return None


def classify(name):
    """Return (part, side, segment) for a bone or joint name."""
    tokens = _tokens(name)
    joined = "".join(tokens)
    side = _side(tokens)
    segment = _segment(tokens)

    if any(t in _ROOTS for t in tokens) or "rig" in tokens:
        return "root", side, segment

    for part, keys in _PART_KEYWORDS:
        for key in keys:
            if key in joined:
                return part, side, segment

    return "unknown", side, segment


def _similarity(a, b):
    a = a.lower()
    b = b.lower()
    if a == b:
        return 1.0
    shorter = min(len(a), len(b))
    if shorter == 0:
        return 0.0
    same = sum(1 for x, y in zip(a, b) if x == y)
    return same / float(max(len(a), len(b)))


# ---------------------------------------------------------------------------
# Source data helpers
# ---------------------------------------------------------------------------

def _frame_values(doc, frame_index, bone_index):
    base = (frame_index * doc["boneCount"] + bone_index) * FLOATS_PER_BONE
    values = doc["values"]
    return values[base:base + FLOATS_PER_BONE]


def _source_position(doc, frame_index, bone_index):
    o = (frame_index * doc["boneCount"] + bone_index) * FLOATS_PER_BONE
    v = doc["values"]
    return CONVERT @ Vector((v[o], v[o + 1], v[o + 2]))


def _source_rotation(doc, frame_index, bone_index):
    o = (frame_index * doc["boneCount"] + bone_index) * FLOATS_PER_BONE
    v = doc["values"]
    unity = Quaternion((v[o + 6], v[o + 3], v[o + 4], v[o + 5]))
    if unity.magnitude < 1e-6:
        unity = Quaternion((1.0, 0.0, 0.0, 0.0))
    else:
        unity.normalize()
    return (CONVERT @ unity.to_matrix() @ CONVERT).to_quaternion()


def _negate(quat):
    return Quaternion((-quat.w, -quat.x, -quat.y, -quat.z))


def smooth_rotations(rotations, window):
    """Triangular moving average of a quaternion series.

    The game drops samples when the frame rate dips, so a joint can jump two
    frames of rotation in one sample. Averaging neighbours removes that jitter
    without the lag of a plain low-pass filter. Every neighbour is put in the
    same hemisphere as the centre first, and each result is aligned with the
    previous one so no key flips by 360 degrees.
    """
    window = int(window)
    if window <= 0 or len(rotations) < 3:
        return rotations

    result = []
    previous = None
    for i in range(len(rotations)):
        center = rotations[i]
        lo = max(0, i - window)
        hi = min(len(rotations) - 1, i + window)
        acc_w = acc_x = acc_y = acc_z = 0.0
        total = 0.0
        for j in range(lo, hi + 1):
            weight = float(window + 1 - abs(j - i))
            quat = rotations[j]
            if center.dot(quat) < 0.0:
                quat = _negate(quat)
            acc_w += quat.w * weight
            acc_x += quat.x * weight
            acc_y += quat.y * weight
            acc_z += quat.z * weight
            total += weight
        out = Quaternion((acc_w / total, acc_x / total, acc_y / total, acc_z / total))
        out.normalize()
        if previous is not None and previous.dot(out) < 0.0:
            out = _negate(out)
        result.append(out)
        previous = out
    return result


def smooth_positions(points, window):
    """Triangular moving average of a position series."""
    window = int(window)
    if window <= 0 or len(points) < 3:
        return points

    result = []
    for i in range(len(points)):
        lo = max(0, i - window)
        hi = min(len(points) - 1, i + window)
        total = Vector((0.0, 0.0, 0.0))
        weight_sum = 0.0
        for j in range(lo, hi + 1):
            weight = float(window + 1 - abs(j - i))
            total += points[j] * weight
            weight_sum += weight
        result.append(total / weight_sum)
    return result


# Bones whose header should point at a mapped child joint. The recording's own
# hierarchy is incomplete on the game rig (the wrist is parented to the rig
# root), so the pairs are derived from body parts instead.
_AIM_CHILDREN = {
    "hips": ("spine", "chest", "neck", "head", "thigh"),
    "spine": ("chest", "neck", "head"),
    "chest": ("neck", "head"),
    "neck": ("head",),
    "shoulder": ("upper_arm",),
    "upper_arm": ("forearm",),
    "forearm": ("hand",),
    "hand": ("index", "middle", "thumb", "ring", "pinky"),
    "thumb": ("thumb",),
    "index": ("index",),
    "middle": ("middle",),
    "ring": ("ring",),
    "pinky": ("pinky",),
    "thigh": ("shin",),
    "shin": ("foot",),
    "foot": ("toe",),
}

_FINGER_PARTS = {"thumb", "index", "middle", "ring", "pinky"}


def aim_targets(source_bones, mapping):
    """Mapped joint -> the mapped joint its bone should point at."""
    parts = {joint: classify(source_bones[joint]["name"]) for joint in mapping}
    by_part_side = {}
    for joint, (part, side, segment) in parts.items():
        by_part_side.setdefault((part, side), []).append((segment, joint))

    targets = {}
    for joint, (part, side, segment) in parts.items():
        for child_part in _AIM_CHILDREN.get(part, ()):
            candidates = list(by_part_side.get((child_part, side), ()))
            if child_part == part:
                # index.01 -> index.02, not index.01 -> index.03.
                if segment is None:
                    continue
                candidates = [(s, j) for s, j in candidates
                              if s is not None and s == segment + 1]
            elif part == "hand" and child_part in _FINGER_PARTS:
                # The hand points down the first finger segment.
                candidates = [(s, j) for s, j in candidates if s in (None, 1)]
            if not candidates:
                continue
            candidates.sort(key=lambda item: (item[0] is None, item[0] or 0))
            targets[joint] = candidates[0][1]
            break
    return targets


def aim_directions(source_bones, mapping, positions):
    """Unit direction from every joint to the mapped child joint it points at.

    `positions` maps joint index -> [Vector] per sample. The result maps a
    joint to a list of directions (None where the two joints coincide). Bones
    use this to swing their head-to-tail axis onto the recorded child, while
    their twist still comes from the recorded rotation delta.
    """
    result = {}
    for joint, child in aim_targets(source_bones, mapping).items():
        points = positions.get(joint)
        child_points = positions.get(child)
        if points is None or child_points is None:
            continue
        directions = []
        for start, end in zip(points, child_points):
            direction = end - start
            length = direction.length
            directions.append(direction / length if length > 1e-6 else None)
        result[joint] = directions
    return result


def heading_rotation(source_matrix, rest_forward):
    """Rotation about Z that turns the recorded body heading onto the rig's.

    The game records world-space transforms, so the player's frame-0 facing is
    whatever direction they happened to face. The rig has a fixed forward
    (`rest_forward`, usually the body bone's local `+Z`). Returning the rotation
    from the recorded heading to the rig heading lets the import rotate
    positions and rotation deltas together, which is what stops the head/torso
    facing backwards relative to the limbs. Returns None when either heading is
    degenerate.
    """
    # In converted (Blender) space the Unity local +Z, which the game treats as
    # forward, is the matrix's Y column.
    source = source_matrix @ Vector((0.0, 1.0, 0.0))
    source = Vector((source.x, source.y, 0.0))
    rest = Vector((rest_forward.x, rest_forward.y, 0.0))
    if source.length < 0.1 or rest.length < 0.1:
        return None
    source.normalize()
    rest.normalize()
    angle = math.atan2(source.x * rest.y - source.y * rest.x, source.dot(rest))
    if abs(angle) < 1e-6:
        return None
    return Matrix.Rotation(angle, 3, 'Z')


def rotation_speed(doc, indices, joint):
    """(worst step, mean step, worst sample, net turn) in degrees.

    Uses the shortest arc: a stored quaternion and its negation are the same
    rotation, and Unity writes both interchangeably. Without this a sign flip
    would be reported as a 360 degree jump even though nothing moved.
    """
    previous = _source_rotation(doc, indices[0], joint)
    first = previous
    worst = 0.0
    worst_at = indices[0]
    total = 0.0
    for index in indices[1:]:
        current = _source_rotation(doc, index, joint)
        step = abs(previous.rotation_difference(current).angle)
        if step > math.pi:
            step = 2.0 * math.pi - step
        total += step
        if step > worst:
            worst = step
            worst_at = index
        previous = current
    mean = total / max(1, len(indices) - 1)
    net = abs(first.rotation_difference(previous).angle)
    if net > math.pi:
        net = 2.0 * math.pi - net
    return math.degrees(worst), math.degrees(mean), worst_at, math.degrees(net)


# ---------------------------------------------------------------------------
# Target rig helpers
# ---------------------------------------------------------------------------

TARGET_RIG_NAME = "Gorilla_IK_Rig"

# Shown in the sidebar after an import.
_last_import = {"path": "", "rig": "", "mapped": 0, "total": 0, "frames": 0,
                "muted": 0, "constraints": 0, "connected": 0, "gaps": [],
                "legacy": False, "unmapped": 0, "unweighted": 0, "pose": ""}


def resolve_target(context):
    """Pick the armature to retarget onto.

    Preference: the selected armature (exact Gorilla_IK_Rig first), then any
    armature in the scene with that name. Returns None if there is no rig.
    """
    selected = [obj for obj in context.selected_objects if obj.type == 'ARMATURE']
    for obj in selected:
        if obj.name == TARGET_RIG_NAME:
            return obj
    if selected:
        return selected[0]

    armatures = [obj for obj in context.scene.objects if obj.type == 'ARMATURE']
    for obj in armatures:
        if obj.name == TARGET_RIG_NAME:
            return obj
    for obj in armatures:
        if TARGET_RIG_NAME in obj.name:
            return obj
    return None


def _bone_order(arm):
    """Parents before children."""
    order = []
    seen = set()

    def visit(bone):
        if bone.name in seen:
            return
        if bone.parent is not None:
            visit(bone.parent)
        seen.add(bone.name)
        order.append(bone)

    for bone in arm.data.bones:
        visit(bone)
    return order


def _topmost(bone):
    while bone.parent is not None:
        bone = bone.parent
    return bone


def describe_constraints(arm):
    """(bone name, readable detail) for every pose-bone constraint on the rig."""
    rows = []
    for bone in arm.pose.bones:
        for constraint in bone.constraints:
            detail = constraint.type
            target = getattr(constraint, "target", None)
            if target is not None:
                subtarget = getattr(constraint, "subtarget", "") or ""
                detail += " -> %s%s" % (target.name, ("/" + subtarget) if subtarget else "")
            rows.append((bone.name, detail))
    return rows


def skinned_bones(arm):
    """Bone names that actually move at least one vertex of a mesh bound to arm.

    A rig can animate a control bone while the visible mesh is skinned to a
    separate deform bone. The action then looks correct on the bones and the
    mesh does not move, so the import reports the difference instead of leaving
    the user to guess.
    """
    bound = []
    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue
        uses_modifier = any(mod.type == 'ARMATURE' and mod.object is arm for mod in obj.modifiers)
        uses_parent = obj.parent_type == 'ARMATURE' and obj.parent is arm
        if uses_modifier or uses_parent:
            bound.append(obj)

    if not bound:
        return None

    names = set()
    for obj in bound:
        groups = {group.index: group.name for group in obj.vertex_groups}
        for vertex in obj.data.vertices:
            for membership in vertex.groups:
                if membership.weight > 1e-4:
                    name = groups.get(membership.group)
                    if name:
                        names.add(name)
    return names


def _data_path(name, prop):
    return 'pose.bones["%s"].%s' % (name.replace("\\", "\\\\").replace('"', '\\"'), prop)


def disconnect_bones(arm, names):
    """Let mapped bones be moved by their location channels.

    Blender ignores the location of a connected bone, so a connected mapped
    bone could not sit exactly on its recorded joint. Disconnecting keeps the
    bone where it is and only allows the action to move it. Returns the names
    that were connected.
    """
    connected = [name for name in names
                 if name in arm.data.bones and arm.data.bones[name].use_connect]
    if not connected:
        return []

    view_layer = bpy.context.view_layer
    previous = view_layer.objects.active
    view_layer.objects.active = arm
    try:
        bpy.ops.object.mode_set(mode='EDIT')
        for name in connected:
            edit_bone = arm.data.edit_bones.get(name)
            if edit_bone is not None:
                edit_bone.use_connect = False
        bpy.ops.object.mode_set(mode='OBJECT')
    except RuntimeError as exc:
        print("[Gorilla Animate] could not disconnect connected bones: %s" % exc)
        return []
    finally:
        view_layer.objects.active = previous
    return connected


def deforming_subtrees(arm, deform):
    """Bones whose own or whose children's movement reaches a weighted bone.

    Bone transforms propagate down the hierarchy, so a weighted joint is moved
    by itself and by every ancestor. Walking up from each weighted bone gives
    exactly the bones that can deform the mesh.
    """
    moving = set()
    for bone in arm.data.bones:
        if bone.name not in deform:
            continue
        node = bone
        while node is not None:
            moving.add(node.name)
            node = node.parent
    return moving


# Parts too small and too numerous to trust for the automatic scale.
_SMALL_PARTS = {"thumb", "index", "middle", "ring", "pinky", "toe"}


def chain_anchors(source_bones, mapping, target_bones):
    """Target bone name -> the target bone its body part should hang off.

    Only returns an entry when the recording really has the predecessor joint
    (a recorded `forearm.L` for `hand.L`). A bone is then positioned on its
    anchor's end instead of wherever its own (possibly IK/control) parent puts
    it.
    """
    parts = {}
    for index, bone in enumerate(source_bones):
        parts[index] = classify(bone["name"])

    target_names = set(bone.name for bone in target_bones)
    anchors = {}
    for joint, target in mapping.items():
        part, side, segment = parts[joint]
        wanted = _CHAIN.get(part)
        if not wanted:
            continue

        best = None
        for other, other_target in mapping.items():
            if other == joint:
                continue
            other_part, other_side, other_segment = parts[other]
            for rank, (wanted_part, wanted_segment) in enumerate(wanted):
                if other_part != wanted_part:
                    continue
                if wanted_segment is not None:
                    # Same part, one segment back: index.03 rides on index.02.
                    if segment is not None:
                        if other_segment is None or other_segment >= segment:
                            continue
                    elif other_segment != wanted_segment:
                        continue
                if other_side and side and other_side != side:
                    continue
                # Prefer the nearest previous segment: index.03 -> index.02.
                distance = 0
                if wanted_segment is not None and segment is not None and other_segment is not None:
                    distance = abs(other_segment - (segment - 1))
                key = (rank, distance)
                if best is None or key < best[0]:
                    best = (key, other_target)
                break

        if best is None:
            continue
        anchor = best[1]
        if anchor == target or anchor not in target_names:
            continue
        anchors[target] = anchor

    return anchors


def auto_scale(doc, source_bones, mapping, target_bones, rest_head):
    """Median ratio of recorded joint spacing to the rig's rest spacing.

    Compares semantic pairs (forearm->hand, upper_arm->forearm, ...) instead of
    the recording's own hierarchy, which is incomplete on the game rig: the
    wrist is a child of the rig root there, not of the forearm. Returns 0.0
    when the recording and the rig share no usable pair.
    """
    anchors = chain_anchors(source_bones, mapping, target_bones)
    joint_for = {name: joint for joint, name in mapping.items()}

    strong = []
    weak = []
    for child, anchor in anchors.items():
        child_joint = joint_for.get(child)
        anchor_joint = joint_for.get(anchor)
        if child_joint is None or anchor_joint is None:
            continue
        recorded = (_source_position(doc, 0, child_joint)
                    - _source_position(doc, 0, anchor_joint)).length
        rest = (rest_head[child] - rest_head[anchor]).length
        if recorded <= 1e-4 or rest <= 1e-4:
            continue
        ratio = rest / recorded
        if classify(child)[0] in _SMALL_PARTS:
            weak.append(ratio)
        else:
            strong.append(ratio)

    ratios = strong or weak
    if not ratios:
        return 0.0
    ratios.sort()
    middle = len(ratios) // 2
    if len(ratios) % 2:
        return ratios[middle]
    return 0.5 * (ratios[middle - 1] + ratios[middle])


def build_mapping(source_bones, target_bones):
    """Map source joint index -> target bone name using part/side/segment."""
    info = []
    for bone in target_bones:
        part, side, segment = classify(bone.name)
        info.append({"name": bone.name, "part": part, "side": side, "segment": segment})

    used = set()
    mapping = {}

    source_order = sorted(
        range(len(source_bones)),
        key=lambda i: _PRIORITY.get(classify(source_bones[i]["name"])[0], 99),
    )

    for index in source_order:
        src_name = source_bones[index]["name"]
        part, side, segment = classify(src_name)

        candidates = []
        family = _FAMILY.get(part)
        if family is None:
            # Unknown joint: only an exact name match is safe.
            for entry in info:
                if entry["name"].lower() == src_name.lower():
                    candidates.append((0, entry))
        else:
            for rank, wanted in enumerate(family):
                for entry in info:
                    if entry["part"] != wanted:
                        continue
                    if entry["side"] and side and entry["side"] != side:
                        continue
                    if entry["side"] and not side:
                        continue
                    if segment and entry["segment"] and entry["segment"] != segment:
                        continue

                    score = 1000.0 - 100.0 * rank
                    if entry["side"] and side:
                        score += 60.0
                    if segment and entry["segment"]:
                        score += 40.0
                    if segment and not entry["segment"]:
                        score -= 50.0
                    if entry["name"].lower() == src_name.lower():
                        score += 500.0
                    score += 25.0 * _similarity(src_name, entry["name"])
                    candidates.append((score, entry))

        candidates.sort(key=lambda pair: -pair[0])
        for _score, entry in candidates:
            if entry["name"] in used:
                continue
            used.add(entry["name"])
            mapping[index] = entry["name"]
            break

    return mapping


# ---------------------------------------------------------------------------
# Animation
# ---------------------------------------------------------------------------

def _make_action(arm, name):
    arm.animation_data_create()
    action = bpy.data.actions.new(name + "_Action")
    arm.animation_data.action = action

    # Blender 4.4+ slotted actions.
    try:
        if hasattr(action, "slots"):
            if len(action.slots) == 0:
                action.slots.new(id_type='OBJECT', name=arm.name)
            if arm.animation_data.action_slot is None:
                arm.animation_data.action_slot = action.slots[0]
    except Exception:
        pass

    return action


def _write_curve(action, data_path, index, group, frames, values):
    curve = action.fcurves.new(data_path=data_path, index=index, action_group=group)
    count = len(frames)
    curve.keyframe_points.add(count)
    flat = [0.0] * (count * 2)
    flat[0::2] = frames
    flat[1::2] = values
    curve.keyframe_points.foreach_set("co", flat)
    curve.keyframe_points.foreach_set("interpolation", [1] * count)  # LINEAR
    curve.update()


def _quat_continuous(previous, current):
    if previous is not None and previous.dot(current) < 0.0:
        return Quaternion((-current.w, -current.x, -current.y, -current.z))
    return current


def _sample_frames(doc, fps, step):
    """Frame numbers for each sampled recording index."""
    times = doc["times"]
    step = max(1, int(step))
    indices = list(range(0, doc["frameCount"], step))
    if indices[-1] != doc["frameCount"] - 1:
        indices.append(doc["frameCount"] - 1)

    frames = []
    last = 0
    for index in indices:
        seconds = float(times[index]) if index < len(times) else index / float(fps)
        number = int(round(seconds * fps)) + 1
        if number <= last:
            number = last + 1
        frames.append(number)
        last = number
    return indices, frames


def retarget(arm, doc, mapping, root_motion=True, motion_scale=0.0, step=1, scene_fps=None,
             mute_constraints=True, connect_bones=True, smooth=0, pose_source="rotations"):
    """Bake the recording onto an existing armature as a new Action.

    pose_source="positions" places every mapped bone on the recorded world
    position (scaled to the rig) and follows the recorded rotation change,
    swinging each bone onto the next recorded joint so the chain connects.
    Placement therefore comes from the recording, while rotations stay
    relative to the rig's bind pose (a tracker's absolute rotation is not a
    bone rotation).
    pose_source="rotations" keeps the older behaviour: rotation deltas applied
    to the rig's rest pose, without recorded joint positions.
    """
    pose_source = str(pose_source).lower()
    if pose_source not in ("positions", "rotations"):
        raise ValueError("pose_source must be 'positions' or 'rotations', not %r" % (pose_source,))
    bones = doc["bones"]
    frame_count = doc["frameCount"]
    fps = doc["fps"]
    out_fps = scene_fps if scene_fps else fps

    _last_import["mapped"] = len(mapping)
    _last_import["total"] = len(bones)
    _last_import["rig"] = arm.name
    _last_import["heading"] = 0.0

    disconnected = []
    if pose_source == "positions":
        disconnected = disconnect_bones(arm, set(mapping.values()))
        if disconnected:
            print("[Gorilla Animate] disconnected %d connected bone(s) so their recorded positions "
                  "can be applied: %s" % (len(disconnected), ", ".join(sorted(disconnected)[:8])))

    rest = {}
    rest_rot = {}
    rest_head = {}
    for bone in arm.data.bones:
        rest[bone.name] = bone.matrix_local.copy()
        rest_rot[bone.name] = bone.matrix_local.to_3x3()
        rest_head[bone.name] = bone.head_local.copy()

    order = _bone_order(arm)
    by_name = {bone.name: bone for bone in arm.data.bones}
    rest_rot_inv = {name: matrix.inverted() for name, matrix in rest_rot.items()}

    # Rest offset of every bone head inside its own parent's rest frame.
    rest_offset = {}
    for bone in arm.data.bones:
        if bone.parent is None:
            rest_offset[bone.name] = Vector((0.0, 0.0, 0.0))
        else:
            parent_rest = rest_rot[bone.parent.name]
            rest_offset[bone.name] = parent_rest.inverted() @ (
                rest_head[bone.name] - rest_head[bone.parent.name])

    # World -> armature space for the rotation deltas and root offset.
    obj_rot = arm.matrix_world.to_3x3()
    obj_rot_inv = obj_rot.inverted()

    # Source rest data + root/head reference lengths.
    source_rest_rot = [_source_rotation(doc, 0, i).to_matrix() for i in range(len(bones))]
    source_rest_inv = [matrix.inverted() for matrix in source_rest_rot]
    source_root = next((i for i, b in enumerate(bones) if b["parent"] < 0), 0)
    source_head = next((i for i, b in enumerate(bones) if classify(b["name"])[0] == "head"), None)

    # Root motion goes on the topmost ancestor of whichever bone the source
    # root was mapped to, so it works with rigs that have a separate root bone.
    if source_root in mapping:
        motion_bone = _topmost(by_name[mapping[source_root]])
    else:
        motion_bone = order[0] if order else None
    motion_name = motion_bone.name if motion_bone is not None else None

    scale = float(motion_scale)
    if scale <= 0.0:
        scale = auto_scale(doc, bones, mapping, arm.data.bones, rest_head)
    if scale <= 0.0 and source_head is not None and motion_bone is not None:
        source_height = (_source_position(doc, 0, source_head) - _source_position(doc, 0, source_root)).length
        head_bone = by_name.get(mapping.get(source_head))
        if head_bone is not None and source_height > 0.05:
            target_height = (head_bone.head_local - motion_bone.head_local).length
            if target_height > 0.05:
                scale = target_height / source_height
                scale = max(0.1, min(10.0, scale))
    if scale <= 0.0:
        scale = 1.0
    scale = max(0.1, min(10.0, scale))

    indices, frame_numbers = _sample_frames(doc, out_fps, step)

    # What the file actually contains. A 1.0.0 recording has no torso, arms or
    # legs, so it cannot drive them; say so instead of silently leaving the rig
    # in its rest pose.
    legacy = not (doc.get("flags", 0) & GAMC_FLAG_FULL_SKELETON)
    _last_import["legacy"] = legacy
    _last_import["pose"] = pose_source
    if legacy:
        print(
            "[Gorilla Animate] legacy recording: made by Gorilla Animate 1.0.0, so it only holds "
            "the head, hands and fingers. The torso, arms and legs cannot be animated from it. "
            "Re-record with plugin 1.1.0 or newer for the whole body."
        )

    unmapped = [bones[i]["name"] for i in range(len(bones)) if i not in mapping]
    _last_import["unmapped"] = len(unmapped)
    if unmapped:
        shown = ", ".join(unmapped[:20]) + (", ..." if len(unmapped) > 20 else "")
        print("[Gorilla Animate] no bone on this rig for: " + shown)

    times = doc["times"]
    if len(times) > 1:
        span = float(times[-1]) - float(times[0])
        if span > 1e-3:
            print(
                "[Gorilla Animate] %d samples over %.2f s = %.1f Hz (header says %d fps, scene plays at %.0f fps)"
                % (frame_count, span, (frame_count - 1) / span, fps, out_fps)
            )

    # Source rotation of every mapped joint at every sampled frame. Smoothing
    # is applied to these world rotations, so a noisy hand or finger joint
    # cannot inject a 180 degree jump into the bake.
    series = {}
    for joint in mapping:
        series[joint] = [_source_rotation(doc, index, joint) for index in indices]
    if smooth > 0:
        for joint in series:
            series[joint] = smooth_rotations(series[joint], smooth)
        # The first sampled frame is the reference pose, so it has to be the
        # smoothed one as well or frame 0 would not be the rest pose.
        for joint in series:
            source_rest_rot[joint] = series[joint][0].to_matrix()
            source_rest_inv[joint] = source_rest_rot[joint].inverted()

    root_positions = [_source_position(doc, index, source_root) for index in indices]
    if smooth > 0:
        root_positions = smooth_positions(root_positions, smooth)
    motion_at = {index: root_positions[k] for k, index in enumerate(indices)}
    motion_origin = motion_at[indices[0]]

    # Positions mode anchors the recording on the joint that maps to the rig's
    # body. The game's rig root sits at head height, so anchoring there would
    # drop the whole avatar to the floor; MonkeMocap anchors on the body
    # position for the same reason. If there is no body joint, use the root.
    anchor_joint = next(
        (i for i, bone in enumerate(bones)
         if classify(bone["name"])[0] == "hips" and i in mapping), None)
    if anchor_joint is None:
        anchor_joint = source_root
    anchor_name = mapping.get(anchor_joint)
    pivot_bone = by_name.get(anchor_name) if anchor_name else motion_bone
    pivot = pivot_bone.head_local.copy() if pivot_bone is not None else Vector((0.0, 0.0, 0.0))

    # Absolute joint positions for pose_source="positions". The recording holds
    # every bone's world position, so these do not depend on the rig's rest
    # pose; only a uniform scale to the rig's size has to be applied.
    joint_positions = {}
    if pose_source == "positions":
        for joint in mapping:
            points = [_source_position(doc, index, joint) for index in indices]
            if smooth > 0:
                points = smooth_positions(points, smooth)
            joint_positions[joint] = points

    # Recorded world transforms keep the player's real facing at frame 0, which
    # can be any direction. Rotate the whole recording so that facing becomes
    # the rig's rest facing. Without this the recorded joint positions face one
    # way while the bone orientations keep the rig's rest heading, which makes
    # the head and torso look backwards.
    heading = None
    if pose_source == "positions":
        if anchor_name in rest_rot:
            rest_forward = obj_rot @ rest_rot[anchor_name] @ Vector((0.0, 0.0, 1.0))
        else:
            rest_forward = obj_rot @ Vector((0.0, -1.0, 0.0))
        source_heading = (
            series[anchor_joint][0].to_matrix() if anchor_joint in series
            else source_rest_rot[anchor_joint])
        heading = heading_rotation(source_heading, rest_forward)

    anchor_positions = root_positions
    if pose_source == "positions" and anchor_joint != source_root:
        anchor_positions = [_source_position(doc, index, anchor_joint) for index in indices]
        if smooth > 0:
            anchor_positions = smooth_positions(anchor_positions, smooth)

    if heading is not None:
        # Rotate positions about the body's frame-0 point; the pivot converts
        # them into armature space later, so any fixed origin would do.
        anchor_points = joint_positions.get(anchor_joint)
        if anchor_points is None:
            anchor_points = anchor_positions
        origin = anchor_points[0]
        for joint in joint_positions:
            joint_positions[joint] = [heading @ (point - origin) + origin
                                      for point in joint_positions[joint]]
        anchor_positions = [heading @ (point - origin) + origin for point in anchor_positions]
        _last_import["heading"] = math.degrees(heading.to_euler().z)
        print("[Gorilla Animate] recording heading recentered by %.1f degrees onto the rig's "
              "rest facing" % _last_import["heading"])

    joint_aim = {}
    if pose_source == "positions":
        joint_aim = aim_directions(bones, mapping, joint_positions)

    if pose_source == "positions":
        print("[Gorilla Animate] pose source: recorded positions, anchored on %s of %s, scale %.3f"
              % (anchor_name or "-", arm.name, scale))

    # Flag joints whose rotation jumps between samples. A big step usually
    # means the game was not tracking that joint, not that the retarget failed.
    for joint, bone_name in sorted(mapping.items()):
        worst, mean, at, net = rotation_speed(doc, indices, joint)
        if worst >= 60.0:
            print(
                "[Gorilla Animate] fast rotation: %-16s -> %-16s max %3.0f deg/sample, avg %5.1f, net %5.0f, worst at sample %d"
                % (bones[joint]["name"], bone_name, worst, mean, net, at)
            )

    # Pre-compute the source deltas we need, for mapped joints only. The
    # heading recenter rotates the deltas as well, so the twist follows the
    # rotated positions instead of fighting them.
    heading_inv = heading.inverted() if heading is not None else None
    deltas = {}
    for position, index in enumerate(indices):
        deltas[index] = {}
        for joint in mapping:
            delta = series[joint][position].to_matrix() @ source_rest_inv[joint]
            if heading is not None:
                delta = heading @ delta @ heading_inv
            deltas[index][joint] = delta

    # Chain pairs the recording implies (hand.L rides forearm.L, ...). Bones
    # whose rig parent is not that bone are positioned on the anchor's end.
    chain_pairs = chain_anchors(bones, mapping, arm.data.bones)
    glue = {}
    if connect_bones and pose_source == "rotations":
        for child, anchor in chain_pairs.items():
            bone = by_name.get(child)
            if bone is None:
                continue
            if bone.parent is not None and bone.parent.name == anchor:
                continue
            glue[child] = anchor

    glue_offset = {}
    for name, anchor in glue.items():
        glue_offset[name] = rest_rot[anchor].inverted() @ (rest_head[name] - rest_head[anchor])

    # A chain gap that already exists in the rest pose is a rig property, not
    # something the import can remove; say so instead of letting it look broken.
    # Only hands, feet and the head are reported: fingers, root and spine bones
    # legitimately start away from their parent's end.
    gap_parts = {"hand", "foot", "head"}
    gaps = []
    if pose_source == "rotations":
        for child, anchor in chain_pairs.items():
            if classify(child)[0] not in gap_parts:
                continue
            gap = (rest_head[child] - by_name[anchor].tail_local).length
            if gap > max(1e-4, 0.02 * by_name[anchor].length):
                gaps.append((gap, child, anchor))
        gaps.sort(reverse=True)
        for gap, child, anchor in gaps[:3]:
            print("[Gorilla Animate] rig gap: the rest pose leaves %.3f between %s and the end of %s"
                  % (gap, child, anchor))

    # Parents/anchors before the bones that ride on them.
    effective = {}
    for bone in order:
        anchor = glue.get(bone.name)
        if anchor is not None:
            effective[bone.name] = anchor
        elif bone.parent is not None:
            effective[bone.name] = bone.parent.name
        else:
            effective[bone.name] = None

    position_order = []
    placed = set()
    pending = [bone.name for bone in order]
    while pending:
        remaining = []
        for name in pending:
            anchor = effective[name]
            if anchor is None or anchor in placed:
                position_order.append(name)
                placed.add(name)
            else:
                remaining.append(name)
        if len(remaining) == len(pending):
            # The anchor is a descendant of this bone: drop the connection
            # rather than hang the skeleton in a circle.
            for name in remaining:
                bone = by_name[name]
                effective[name] = bone.parent.name if bone.parent is not None else None
                glue.pop(name, None)
        pending = remaining

    joint_of_target = {name: joint for joint, name in mapping.items()}
    rotation_bones = sorted(set(mapping.values()))
    # Fingers have no meaningful tracked orientation: only their recorded joint
    # positions are anatomy. Their direction comes from those positions, and
    # their roll stays the rig's rest roll relative to the hand instead of a
    # tracker rotation that would spin the finger about its own axis.
    finger_joints = {joint for joint in mapping
                     if classify(bones[joint]["name"])[0] in _FINGER_PARTS}
    if pose_source == "positions":
        # Every mapped bone is placed absolutely, so it needs a location offset
        # from its rig parent. Unmapped bones still ride their parent normally.
        location_bones = sorted(set(mapping.values()))
    else:
        location_bones = sorted(set(glue) | ({motion_name} if motion_name is not None else set()))

    if glue:
        print("[Gorilla Animate] connected %d bone(s) to the end of their chain:" % len(glue))
        for name in sorted(glue):
            print("    %-16s -> %-16s (was parented elsewhere in the rig)" % (name, glue[name]))

    sampled = []
    for position, (index, frame_number) in enumerate(zip(indices, frame_numbers)):
        frame_deltas = deltas[index]

        # 1. Rotation of every target bone, parents first.
        posed_rot = {}
        for bone in order:
            name = bone.name
            parent = bone.parent
            joint = joint_of_target.get(name)
            if joint is not None:
                if pose_source == "positions":
                    # The recorded rotation is a tracker orientation, not a
                    # bone orientation: its local axes do not match a Blender
                    # bone. Use its change since the first sample (so the rig's
                    # bind pose is preserved) and swing the bone onto the next
                    # recorded joint so the chain actually connects. Fingers are
                    # the exception: their roll rides the hand.
                    if joint in finger_joints and parent is not None:
                        final = (posed_rot[parent.name]
                                 @ rest_rot_inv[parent.name] @ rest_rot[name])
                    else:
                        delta_arm = obj_rot_inv @ frame_deltas[joint] @ obj_rot
                        final = delta_arm @ rest_rot[name]
                    directions = joint_aim.get(joint)
                    direction = directions[position] if directions is not None else None
                    if direction is not None:
                        want = (obj_rot_inv @ direction).normalized()
                        axis = final @ Vector((0.0, 1.0, 0.0))
                        turn = axis.rotation_difference(want)
                        final = turn.to_matrix() @ final
                    posed_rot[name] = final
                else:
                    delta_arm = obj_rot_inv @ frame_deltas[joint] @ obj_rot
                    posed_rot[name] = delta_arm @ rest_rot[name]
            elif parent is not None:
                posed_rot[name] = (
                    posed_rot[parent.name]
                    @ rest_rot_inv[parent.name]
                    @ rest_rot[name]
                )
            else:
                posed_rot[name] = rest_rot[name].copy()

        # 2. Head position of every bone: on the parent it actually has, or on
        #    the end of the chain parent the recording says it belongs to. In
        #    positions mode every recorded joint lands on its recorded spot.
        posed_head = {}
        if pose_source == "positions":
            anchor_now = anchor_positions[position]
            for joint, name in mapping.items():
                point = joint_positions[joint][position]
                offset = point - (anchor_positions[0] if root_motion else anchor_now)
                posed_head[name] = pivot + obj_rot_inv @ (offset * scale)
            for name in position_order:
                if name in posed_head:
                    continue
                anchor = effective[name]
                if anchor is None:
                    posed_head[name] = rest_head[name].copy()
                else:
                    posed_head[name] = (
                        posed_head[anchor] + posed_rot[anchor] @ rest_offset[name])
        else:
            motion_shift = None
            if motion_name is not None and (root_motion or source_root in mapping):
                offset = motion_at[index] - motion_origin
                if not root_motion:
                    offset = Vector((0.0, 0.0, 0.0))
                motion_shift = obj_rot_inv @ (offset * scale)

            for name in position_order:
                anchor = effective[name]
                if anchor is None:
                    head = rest_head[name].copy()
                elif name in glue:
                    head = posed_head[anchor] + posed_rot[anchor] @ glue_offset[name]
                else:
                    head = posed_head[anchor] + posed_rot[anchor] @ rest_offset[name]
                if name == motion_name and motion_shift is not None:
                    head = head + motion_shift
                posed_head[name] = head

        # 3. Pose basis values (what the animation channels store).
        rotations = {}
        locations = {}
        pose_matrix = {}
        for bone in order:
            name = bone.name
            final = Matrix.Translation(posed_head[name]) @ posed_rot[name].to_4x4()
            parent = bone.parent
            if parent is None:
                basis = rest[name].inverted() @ final
            else:
                parent_pose = pose_matrix[parent.name]
                basis = (parent_pose @ rest[parent.name].inverted() @ rest[name]).inverted() @ final
            pose_matrix[name] = final

            if name in rotation_bones:
                rotations[name] = basis.to_quaternion().normalized()
            if name in location_bones:
                locations[name] = basis.translation.copy()

        sampled.append((frame_number, rotations, locations))

    action = _make_action(arm, os.path.splitext(os.path.basename(doc["path"]))[0])

    # An IK rig will ignore baked FK rotations: mute constraints so the action
    # is actually visible. Muting is reversible from the bone's constraint panel.
    constraints = describe_constraints(arm)
    if constraints:
        print("[Gorilla Animate] rig check: %d constraints on %s" % (len(constraints), arm.name))
        for bone_name, detail in constraints:
            print("    %-16s %s" % (bone_name, detail))

    muted = 0
    if mute_constraints:
        # Every constraint, not just the animated bones: an IK/control bone
        # (usually unmapped) sits between a hand and its arm, and if it is still
        # evaluated the hand cannot be placed relative to its real parent.
        for pose_bone in arm.pose.bones:
            for constraint in pose_bone.constraints:
                if not constraint.mute:
                    constraint.mute = True
                    muted += 1
    _last_import["muted"] = muted
    _last_import["constraints"] = len(constraints)
    _last_import["connected"] = len(glue)

    # Which of the animated bones actually deform something? A rig can drive
    # control bones while the mesh is weighted to a second set of deform bones.
    deform = skinned_bones(arm)
    unweighted = []
    if deform is not None:
        reaches_mesh = deforming_subtrees(arm, deform)
        inert = sorted(name for name in rotation_bones if name not in reaches_mesh)
        # A single inert bone is normal (a control bone with no weights, or a
        # root the torso is not parented to). Only complain when nothing we
        # animate can move the mesh at all, which means it is skinned elsewhere.
        if inert and not (set(rotation_bones) & reaches_mesh):
            unweighted = inert
            shown = ", ".join(inert[:12]) + (", ..." if len(inert) > 12 else "")
            print(
                "[Gorilla Animate] none of the %d animated bone(s) move a weighted vertex: %s"
                % (len(inert), shown)
            )
            print(
                "[Gorilla Animate] the mesh is skinned to other (deform) bones; "
                "those bones move, the mesh will not"
            )
    _last_import["unweighted"] = len(unweighted)

    frames = [entry[0] for entry in sampled]

    for bone_name in rotation_bones:
        pose_bone = arm.pose.bones.get(bone_name)
        if pose_bone is None:
            continue
        pose_bone.rotation_mode = 'QUATERNION'

        previous = None
        quats = []
        for _frame_number, rotations, _locations in sampled:
            quat = _quat_continuous(previous, rotations.get(bone_name, Quaternion((1, 0, 0, 0))))
            previous = quat
            quats.append(quat)

        path = _data_path(bone_name, "rotation_quaternion")
        for component in range(4):
            values = [quat[component] for quat in quats]
            _write_curve(action, path, component, bone_name, frames, values)

    # Location curves: every bone that positions mode places itself, plus the
    # bones pulled onto a chain parent and the bone carrying the root motion.
    for bone_name in location_bones:
        pose_bone = arm.pose.bones.get(bone_name)
        if pose_bone is None:
            continue

        locations = [entry[2].get(bone_name, Vector((0.0, 0.0, 0.0))) for entry in sampled]
        if bone_name not in glue and not any(loc.length > 1e-9 for loc in locations):
            continue

        path = _data_path(bone_name, "location")
        for component in range(3):
            values = [loc[component] for loc in locations]
            _write_curve(action, path, component, bone_name, frames, values)

    _set_scene_range(doc, out_fps, frame_numbers)

    # Verify only what we intended to line up: bones we connected, plus bones
    # the rig already parents correctly. Deliberately untouched bones are not
    # reported as detached.
    verify_pairs = {}
    if pose_source == "rotations":
        for child, anchor in chain_pairs.items():
            bone = by_name.get(child)
            if bone is None:
                continue
            attached = bone.parent is not None and bone.parent.name == anchor
            if attached or child in glue:
                verify_pairs[child] = anchor

    reported = verified_chain_gaps(arm, verify_pairs, frame_numbers, gap_parts)
    _last_import["gaps"] = reported
    return action


def verified_chain_gaps(arm, chain_pairs, frame_numbers, parts):
    """Measure hands/feet/head against their arm/leg/neck after evaluation.

    The keyed channels are only half the story: constraints, drivers and
    Stretch To modifiers can still move a bone away from where it was baked.
    Returns [(gap, child, anchor)] for anything that is still detached.
    """
    pairs = [(child, anchor) for child, anchor in chain_pairs.items()
             if classify(child)[0] in parts]

    if not pairs or not frame_numbers:
        return []

    worst = {}
    step = max(1, len(frame_numbers) // 6)
    for frame_number in frame_numbers[::step][:6] + [frame_numbers[-1]]:
        bpy.context.scene.frame_set(frame_number)
        bpy.context.view_layer.update()
        for child, anchor in pairs:
            child_pose = arm.pose.bones.get(child)
            anchor_pose = arm.pose.bones.get(anchor)
            if child_pose is None or anchor_pose is None:
                continue
            gap = (child_pose.head - anchor_pose.tail).length
            if gap > worst.get(child, (0.0, ""))[0]:
                worst[child] = (gap, anchor)

    detached = []
    for child, (gap, anchor) in worst.items():
        limit = max(1e-3, 0.02 * arm.pose.bones[anchor].length)
        if gap > limit:
            detached.append((gap, child, anchor))

    detached.sort(reverse=True)
    for gap, child, anchor in detached:
        print("[Gorilla Animate] still detached: %s is %.3f from the end of %s after evaluation"
              % (child, gap, anchor))
    if detached:
        print("[Gorilla Animate] a constraint, driver or Stretch To modifier is still moving those "
              "bones; mute it or apply the visual transform")

    bpy.context.scene.frame_set(bpy.context.scene.frame_start)
    bpy.context.view_layer.update()
    return detached


def _set_scene_range(doc, fps, frame_numbers):
    scene = bpy.context.scene
    scene.render.fps = int(fps) if fps >= 1 else 30
    scene.render.fps_base = 1.0
    last = max(frame_numbers) if frame_numbers else 1
    scene.frame_start = 1
    if scene.frame_end < last:
        scene.frame_end = last
    scene.frame_set(1)


def build_source_armature(doc, name):
    """Create a standalone armature from the recording (preview mode)."""
    bones = doc["bones"]
    count = len(bones)

    rest_positions = [_source_position(doc, 0, i) for i in range(count)]
    children = [[] for _ in range(count)]
    for i, bone in enumerate(bones):
        parent = int(bone["parent"])
        if 0 <= parent < count and parent != i:
            children[parent].append(i)

    bpy.ops.object.select_all(action='DESELECT')
    data = bpy.data.armatures.new(name + "_Armature")
    arm = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)

    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = []
    for i, bone in enumerate(bones):
        edit_bone = data.edit_bones.new(bone["name"])
        head = rest_positions[i]
        tail = None
        if children[i]:
            candidate = rest_positions[children[i][0]]
            if (candidate - head).length > 0.0001:
                tail = candidate
        if tail is None:
            parent = int(bone["parent"])
            if 0 <= parent < count:
                direction = head - rest_positions[parent]
                if direction.length > 0.0001:
                    tail = head + direction.normalized() * 0.05
        if tail is None:
            tail = head + Vector((0.0, 0.0, 0.05))
        edit_bone.head = head
        edit_bone.tail = tail
        edit_bone.use_connect = False
        edit_bones.append(edit_bone)

    for i, bone in enumerate(bones):
        parent = int(bone["parent"])
        if 0 <= parent < count:
            edit_bones[i].parent = edit_bones[parent]

    bpy.ops.object.mode_set(mode='OBJECT')

    mapping = {i: bones[i]["name"] for i in range(count)}
    return arm, mapping


# ---------------------------------------------------------------------------
# Operator
# ---------------------------------------------------------------------------

class IMPORT_OT_gorilla_animate_gamc(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.gorilla_animate_gamc"
    bl_label = "Import Gorilla Animate (.gamc)"
    bl_description = "Retarget a .gamc recording onto Gorilla_IK_Rig (or the selected armature)"
    bl_options = {'PRESET', 'UNDO'}

    filename_ext = ".gamc"
    filter_glob: bpy.props.StringProperty(default="*.gamc", options={'HIDDEN'})

    target: bpy.props.EnumProperty(
        name="Target",
        items=(
            ('SELECTED', "Selected armature", "Bake onto the armature selected in the 3D view"),
            ('NEW', "New preview armature", "Create a new armature from the recording"),
        ),
        default='SELECTED',
    )
    pose_source: bpy.props.EnumProperty(
        name="Pose",
        items=(
            ('POSITIONS', "Recorded positions",
             "Put every joint on its recorded world position, follow the recorded rotation change "
             "and aim each bone at the next joint. The rig's bind pose is kept for rotation, the way "
             "MonkeMocap drives its trackers"),
            ('ROTATIONS', "Rotation deltas",
             "Apply the recorded rotation changes on top of the rig's rest pose (older behaviour)"),
        ),
        default='POSITIONS',
        description="How the recording drives the rig",
    )
    do_root_motion: bpy.props.BoolProperty(
        name="Root motion",
        default=True,
        description="Move the rig with the player's recorded world movement",
    )
    motion_scale: bpy.props.FloatProperty(
        name="Motion scale",
        default=0.0,
        min=0.0,
        soft_max=4.0,
        description="Scale for the recording. 0 automatically matches the recorded joint spacing "
                    "to the selected rig's joint spacing",
    )
    frame_step: bpy.props.IntProperty(
        name="Frame step",
        default=1,
        min=1,
        max=10,
        description="Import every Nth sample",
    )
    set_scene_fps: bpy.props.BoolProperty(
        name="Set scene FPS",
        default=True,
        description="Set the scene frame rate to the recording's frame rate",
    )
    mute_constraints: bpy.props.BoolProperty(
        name="Mute constraints",
        default=True,
        description="Mute every bone constraint on the rig (IK, Copy Rotation, Child Of, ...) "
                    "so the baked animation is what you see. Reversible in the bone's constraint panel",
    )
    connect_bones: bpy.props.BoolProperty(
        name="Connect chains",
        default=True,
        description="Rotation deltas only: keep hands, feet and the head on the ends of their arms, "
                    "legs and neck even when the rig parents them somewhere else (common with IK "
                    "control bones). Recorded positions already place every joint itself",
    )
    smooth: bpy.props.IntProperty(
        name="Smoothing",
        default=1,
        min=0,
        max=10,
        description="Average this many neighbouring samples to remove jitter in fast hand and "
                    "finger rotation. 0 keeps the raw recording",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "target")
        layout.prop(self, "pose_source")
        layout.prop(self, "do_root_motion")
        layout.prop(self, "motion_scale")
        layout.prop(self, "frame_step")
        layout.prop(self, "set_scene_fps")
        layout.prop(self, "mute_constraints")
        row = layout.row()
        row.enabled = self.pose_source == 'ROTATIONS'
        row.prop(self, "connect_bones")
        layout.prop(self, "smooth")

    def execute(self, context):
        try:
            doc = read_gamc(self.filepath)
        except Exception as exc:
            self.report({'ERROR'}, "Could not read %s: %s" % (os.path.basename(self.filepath), exc))
            return {'CANCELLED'}

        if self.target == 'SELECTED':
            arm = resolve_target(context)
            if arm is None:
                self.report(
                    {'ERROR'},
                    "Select the %s armature first, or import into a new preview armature" % TARGET_RIG_NAME,
                )
                return {'CANCELLED'}

            if arm.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')

            mapping = build_mapping(doc["bones"], arm.data.bones)
            if not mapping:
                self.report({'ERROR'}, "No bones on '%s' matched the recording" % arm.name)
                return {'CANCELLED'}
        else:
            base = os.path.splitext(os.path.basename(self.filepath))[0]
            arm, mapping = build_source_armature(doc, base)
            for obj in context.selected_objects:
                obj.select_set(False)

        scene_fps = None
        if not self.set_scene_fps:
            scene_fps = context.scene.render.fps / max(1e-6, context.scene.render.fps_base)

        retarget(
            arm,
            doc,
            mapping,
            root_motion=self.do_root_motion,
            motion_scale=self.motion_scale,
            step=self.frame_step,
            scene_fps=scene_fps,
            mute_constraints=self.mute_constraints,
            connect_bones=self.connect_bones,
            smooth=self.smooth,
            pose_source=self.pose_source.lower(),
        )

        mapped = len(mapping)
        total = len(doc["bones"])
        muted = _last_import.get("muted", 0)
        connected = _last_import.get("connected", 0)
        _last_import.update({
            "path": self.filepath,
            "rig": arm.name,
            "mapped": mapped,
            "total": total,
            "frames": doc["frameCount"],
        })
        print("[Gorilla Animate] %s -> %s" % (os.path.basename(self.filepath), arm.name))
        print("[Gorilla Animate] mapped %d/%d joints" % (mapped, total))
        if muted:
            print("[Gorilla Animate] muted %d bone constraints so the action is visible" % muted)
        if connected:
            print("[Gorilla Animate] connected %d bone(s) onto their chain parent" % connected)
        for index in range(total):
            source = doc["bones"][index]["name"]
            target = mapping.get(index)
            print("    %-16s -> %s" % (source, target if target else "-"))

        message = "Imported %s: %d frames, %d/%d joints onto %s" % (
            os.path.basename(self.filepath), doc["frameCount"], mapped, total, arm.name)
        if muted:
            message += " (muted %d constraints)" % muted
        if connected:
            message += " (connected %d bones)" % connected
        self.report({'INFO'}, message)

        screen = context.screen
        if screen is not None:
            for area in screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()

        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Sidebar panel (N)
# ---------------------------------------------------------------------------

class VIEW3D_PT_gorilla_animate(bpy.types.Panel):
    bl_label = "Gorilla Animate"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Gorilla Animate"

    def draw(self, context):
        layout = self.layout
        target = resolve_target(context)

        column = layout.column(align=True)
        column.scale_y = 1.5
        button = column.operator(
            "import_scene.gorilla_animate_gamc",
            text="Import .gamc File",
            icon='IMPORT',
        )
        button.target = 'SELECTED'

        box = layout.box()
        box.label(text="Target rig", icon='ARMATURE_DATA')
        if target is not None:
            box.label(text=target.name, icon='CHECKMARK')
            box.label(text="%d bones" % len(target.data.bones))
            action = target.animation_data.action if target.animation_data else None
            if action is not None:
                box.label(text=action.name, icon='ACTION')
            constraints = describe_constraints(target)
            if constraints:
                box.label(text="%d constraints (muted on import)" % len(constraints),
                          icon='CONSTRAINT')
        else:
            box.label(text="No armature found", icon='ERROR')
            box.label(text='Select the "%s" rig' % TARGET_RIG_NAME)

        if _last_import["path"]:
            info = layout.box()
            info.label(text="Last import", icon='FILE')
            info.label(text=os.path.basename(_last_import["path"]))
            info.label(
                text="%s  |  %d/%d joints  |  %d frames"
                % (
                    _last_import["rig"],
                    _last_import["mapped"],
                    _last_import["total"],
                    _last_import["frames"],
                )
            )
            if _last_import.get("connected"):
                info.label(text="%d bones connected to their chain" % _last_import["connected"],
                           icon='BONE_DATA')
            if _last_import.get("pose") == "positions":
                info.label(text="Pose: recorded joint positions", icon='BONE_DATA')
            if _last_import.get("legacy"):
                info.label(text="Legacy recording: no torso/arms/legs", icon='ERROR')
                info.label(text="Re-record with plugin 1.1.0 or newer")
            if _last_import.get("unmapped"):
                info.label(text="%d recorded joints had no bone here"
                           % _last_import["unmapped"], icon='INFO')
            if _last_import.get("unweighted"):
                info.label(text="%d animated bones move no vertices"
                           % _last_import["unweighted"], icon='ERROR')
                info.label(text="The mesh is skinned to other bones")
            gaps = _last_import.get("gaps") or []
            if gaps:
                info.label(text="%d bones still detached (%s)" % (len(gaps), gaps[0][1]),
                           icon='ERROR')


def menu_func_import(self, context):
    self.layout.operator(IMPORT_OT_gorilla_animate_gamc.bl_idname,
                         text="Gorilla Animate Mocap (.gamc)")


_classes = (
    IMPORT_OT_gorilla_animate_gamc,
    VIEW3D_PT_gorilla_animate,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
