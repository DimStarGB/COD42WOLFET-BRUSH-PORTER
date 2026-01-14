#!/usr/bin/env python3

"""

COD4 Prefab to Wolf:ET Map Converter with GUI



Features:

- Strips IW header (anything before first '{')

- Optional: Expand misc_prefab entities by inlining referenced prefab .map brushwork (origin + yaw)

- Removes standalone "contents*" metadata lines (e.g. contents_detail; contents details;)

- Removes mesh/patch/curve brushes (not compatible with Wolf:ET classic brush format)

- Texture output modes:

    1) Force everything to caulk

    2) Placeholders: map each unique COD texture -> placeholder/<n>

       + writes <output>_placeholder_map.csv

- Tool textures are always treated specially (kept as ET tools, never placeholder):

    clip, clip_snow -> common/clip

    hint, hintskip  -> common/hint

    portal_nodraw   -> common/portal_nodraw

    lightgrid_volume-> common/lightgrid

- IMPORTANT: caulk is ALWAYS preserved:

    caulk/common/caulk/textures/common/caulk -> common/caulk

  (regardless of mode)

- Optional: Remove tool brushes entirely (dangerous; can break collision/vis/lightgrid)



Author: DimStar

"""



import os

import re

import traceback

import tkinter as tk

from tkinter import filedialog, scrolledtext, ttk, messagebox

import math

import csv



# ----------------------------

# Constants / regex

# ----------------------------



TAIL_DEFAULT = "0 0 0 0.25 0.25 0 0 0"



NUM = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"



FACE_LINE_RE = re.compile(

    rf"""^(\s*

          \(\s*{NUM}\s+{NUM}\s+{NUM}\s*\)\s*

          \(\s*{NUM}\s+{NUM}\s+{NUM}\s*\)\s*

          \(\s*{NUM}\s+{NUM}\s+{NUM}\s*\)

        )\s+.*$""",

    re.VERBOSE

)



PLANE_POINT_RE = re.compile(rf"\(\s*({NUM})\s+({NUM})\s+({NUM})\s*\)")

KV_RE = re.compile(r'"([^"]+)"\s+"([^"]*)"')



# Tool tokens we care about (COD side) -> ET common/*

TOOL_MAP = {

    "clip": "common/clip",

    "clip_snow": "common/clip",

    "hint": "common/hint",

    "hintskip": "common/hint",

    "portal_nodraw": "common/portal_nodraw",

    "lightgrid_volume": "common/lightgrid",

}



TOOL_TOKEN_RE = re.compile(r"(^|/|\b)(clip_snow|lightgrid_volume|portal_nodraw|hintskip|hint|clip)\b", re.IGNORECASE)



# Always-preserve textures (never placeholders, never removed by settings)

# All normalized (no "textures/" prefix, forward slashes)

ALWAYS_PRESERVE = {

    "common/caulk",

}



# Also treat plain 'caulk' as caulk (some maps use it bare)

ALWAYS_PRESERVE_ALIASES = {

    "caulk": "common/caulk",

    "common/caulk": "common/caulk",

}



# ----------------------------

# Basic transforms

# ----------------------------



def strip_iw_header(text: str) -> str:

    idx = text.find("{")

    return text[idx:] if idx != -1 else text



def remove_contents_detail(lines):
    """Remove standalone COD 'contents*' metadata lines.

    Some COD4 .map / prefab exports include a single-line directive like:
        contents detail;
        contents_detail;
        contents details;
        contents_somethingElse;
        contents'
    ...where the whole line is just that directive (optionally ending with ';').
    These lines are not valid in Wolf:ET classic .map and should be dropped.
    """
    rx = re.compile(r"^\s*contents(?:['\"])?(?:[ _-]?[A-Za-z0-9_'-]+)?\s*;?\s*$", re.IGNORECASE)
    out = []
    removed = 0
    for line in lines:
        if rx.match(line):
            removed += 1
            continue
        out.append(line)
    return out



def remove_mesh_brushes(text: str):

    """

    Remove brush blocks (depth==2) that contain IW mesh/patch constructs.

    """

    lines = text.splitlines(True)

    out = []

    removed = 0



    depth = 0

    in_brush = False

    brush_buf = []

    brush_has_mesh = False



    def brush_contains_mesh(line: str) -> bool:

        s = line.strip()

        if not s or s.startswith("//"):

            return False

        keywords = ("mesh", "patchDef2", "patchDef3", "curve")

        return any(k in s for k in keywords)



    for line in lines:

        opens = line.count("{")

        closes = line.count("}")



        if line.strip() == "{" and depth == 1 and not in_brush:

            in_brush = True

            brush_buf = [line]

            brush_has_mesh = False

        elif in_brush:

            brush_buf.append(line)

            if brush_contains_mesh(line):

                brush_has_mesh = True

        else:

            out.append(line)



        depth += opens - closes



        if in_brush and line.strip() == "}" and depth == 1:

            if brush_has_mesh:

                removed += 1

            else:

                out.extend(brush_buf)

            in_brush = False

            brush_buf = []

            brush_has_mesh = False



    if in_brush and brush_buf:

        out.extend(brush_buf)



    return "".join(out), removed



# ----------------------------

# Entity parsing / prefab expansion

# ----------------------------



def iter_top_level_entities(map_text: str):

    s = strip_iw_header(map_text)

    i = 0

    n = len(s)



    while i < n and s[i].isspace():

        i += 1



    depth = 0

    start = None



    while i < n:

        ch = s[i]

        if ch == "{":

            if depth == 0:

                start = i

            depth += 1

        elif ch == "}":

            depth -= 1

            if depth == 0 and start is not None:

                end = i + 1

                yield start, end, s[start:end]

                start = None

        i += 1



def parse_kv(entity_text: str):

    return {k: v for (k, v) in KV_RE.findall(entity_text)}



def extract_worldspawn_brush_blocks(map_text: str):

    s = strip_iw_header(map_text)

    entities = list(iter_top_level_entities(s))

    if not entities:

        return []



    world = entities[0][2]

    lines = world.splitlines(True)



    brush_blocks = []

    depth = 0

    in_brush = False

    buf = []



    for line in lines:

        stripped = line.strip()



        if stripped == "{" and depth == 1 and not in_brush:

            in_brush = True

            buf = [line]

        elif in_brush:

            buf.append(line)



        depth += line.count("{") - line.count("}")



        if in_brush and stripped == "}" and depth == 1:

            brush_blocks.append("".join(buf))

            in_brush = False

            buf = []



    return brush_blocks



def apply_yaw_and_origin_to_face_line(line: str, yaw_deg: float, origin_xyz):

    pts = PLANE_POINT_RE.findall(line)

    if len(pts) < 3:

        return line



    ox, oy, oz = origin_xyz

    yaw = math.radians(yaw_deg)

    cy, sy = math.cos(yaw), math.sin(yaw)



    def tx(x, y, z):

        xr = x * cy - y * sy

        yr = x * sy + y * cy

        zr = z

        return xr + ox, yr + oy, zr + oz



    repls = []

    for (x, y, z) in pts[:3]:

        xf, yf, zf = float(x), float(y), float(z)

        xr, yr, zr = tx(xf, yf, zf)

        repls.append((xr, yr, zr))



    def fmt(v):

        return f"{v:.6f}".rstrip("0").rstrip(".")



    def sub_first_n(match_iter, text, n=3):

        out_chars = []

        last = 0

        count = 0

        for m in match_iter:

            out_chars.append(text[last:m.start()])

            if count < n:

                xr, yr, zr = repls[count]

                out_chars.append(f"( {fmt(xr)} {fmt(yr)} {fmt(zr)} )")

            else:

                out_chars.append(m.group(0))

            last = m.end()

            count += 1

        out_chars.append(text[last:])

        return "".join(out_chars)



    return sub_first_n(PLANE_POINT_RE.finditer(line), line, n=3)



def transform_brush_block(brush_block: str, yaw_deg: float, origin_xyz):

    lines = brush_block.splitlines(True)

    out = []

    for line in lines:

        if line.lstrip().startswith("("):

            out.append(apply_yaw_and_origin_to_face_line(line, yaw_deg, origin_xyz))

        else:

            out.append(line)

    return "".join(out)



def resolve_prefab_path(model_value: str, map_dir: str, prefab_root: str):

    rel = model_value.replace("\\", "/").lstrip("/")

    candidates = []

    if prefab_root:

        candidates.append(os.path.join(prefab_root, rel))

    candidates.append(os.path.join(map_dir, rel))



    for p in candidates:

        if os.path.isfile(p):

            return p



    if not rel.lower().endswith(".map"):

        rel2 = rel + ".map"

        if prefab_root:

            p = os.path.join(prefab_root, rel2)

            if os.path.isfile(p):

                return p

        p = os.path.join(map_dir, rel2)

        if os.path.isfile(p):

            return p



    return None



def parse_origin(origin_str: str):

    try:

        parts = origin_str.replace(",", " ").split()

        if len(parts) >= 3:

            return float(parts[0]), float(parts[1]), float(parts[2])

    except:

        pass

    return 0.0, 0.0, 0.0



def parse_yaw_from_entity(kv: dict):

    if "angles" in kv:

        try:

            parts = kv["angles"].replace(",", " ").split()

            if len(parts) >= 2:

                return float(parts[1])

        except:

            pass

    if "angle" in kv:

        try:

            return float(kv["angle"])

        except:

            pass

    return 0.0



def expand_misc_prefabs(main_map_text: str, main_map_path: str, prefab_root: str, max_depth: int = 5):

    map_dir = os.path.dirname(os.path.abspath(main_map_path))

    s = strip_iw_header(main_map_text)



    entities = list(iter_top_level_entities(s))

    if not entities:

        return s, {"prefabs_found": 0, "prefabs_expanded": 0, "prefab_brushes_added": 0, "missing_prefab_files": 0}



    world_text = entities[0][2]

    prefab_brushes_to_add = []



    prefabs_found = 0

    prefabs_expanded = 0

    missing_prefab_files = 0



    kept_entities = [world_text]

    visited = set()



    def expand_one(prefab_file_path: str, yaw_deg: float, origin_xyz, depth: int):

        nonlocal prefabs_expanded

        if depth > max_depth:

            return []



        key = (os.path.abspath(prefab_file_path), round(yaw_deg, 6), tuple(round(v, 6) for v in origin_xyz), depth)

        if key in visited:

            return []

        visited.add(key)



        with open(prefab_file_path, "r", encoding="utf-8", errors="replace") as f:

            prefab_text = f.read()



        brushes = extract_worldspawn_brush_blocks(prefab_text)

        transformed = [transform_brush_block(b, yaw_deg, origin_xyz) for b in brushes]

        prefabs_expanded += 1

        return transformed



    for idx in range(1, len(entities)):

        ent_text = entities[idx][2]

        kv = parse_kv(ent_text)

        if kv.get("classname") == "misc_prefab":

            prefabs_found += 1

            model = kv.get("model", "").strip()

            if not model:

                missing_prefab_files += 1

                continue



            origin = parse_origin(kv.get("origin", "0 0 0"))

            yaw = parse_yaw_from_entity(kv)



            prefab_path = resolve_prefab_path(model, map_dir, prefab_root)

            if not prefab_path:

                missing_prefab_files += 1

                continue



            prefab_brushes_to_add.extend(expand_one(prefab_path, yaw, origin, depth=1))

        else:

            kept_entities.append(ent_text)



    if prefab_brushes_to_add:

        insert_pos = kept_entities[0].rfind("}")

        if insert_pos != -1:

            world_new = kept_entities[0][:insert_pos] + "\n" + "".join(prefab_brushes_to_add) + "\n" + kept_entities[0][insert_pos:]

            kept_entities[0] = world_new



    expanded = "\n".join(kept_entities)

    return expanded, {

        "prefabs_found": prefabs_found,

        "prefabs_expanded": prefabs_expanded,

        "prefab_brushes_added": len(prefab_brushes_to_add),

        "missing_prefab_files": missing_prefab_files,

    }



# ----------------------------

# Tool detection + brush removal

# ----------------------------



def face_texture_token_from_line(line: str):

    """

    Extract the first token after the 3 plane point triples.

    Returns (norm_lower, norm_originalish) where:

      - forward slashes

      - strips leading 'textures/'

    """

    m = FACE_LINE_RE.match(line)

    if not m:

        return None, None



    rest = line[m.end(1):].strip()

    if not rest:

        return None, None



    raw = rest.split()[0]

    norm = raw.replace("\\", "/")

    if norm.lower().startswith("textures/"):

        norm = norm[9:]

    return norm.lower(), norm



def normalize_preserve_alias(norm_lower: str):

    """

    Convert known aliases like 'caulk' to canonical 'common/caulk'.

    """

    if not norm_lower:

        return None

    if norm_lower in ALWAYS_PRESERVE_ALIASES:

        return ALWAYS_PRESERVE_ALIASES[norm_lower]

    return None



def classify_tool_texture(norm_lower: str):

    if not norm_lower:

        return None

    m = TOOL_TOKEN_RE.search(norm_lower)

    if not m:

        return None

    tok = m.group(2).lower()

    return tok if tok in TOOL_MAP else None



def detect_tool_brush_type(brush_block: str):

    for line in brush_block.splitlines():

        if line.lstrip().startswith("("):

            low, _ = face_texture_token_from_line(line)

            if normalize_preserve_alias(low) == "common/caulk":

                return "common/caulk"  # treat as preserved (but not a "tool")

            tok = classify_tool_texture(low)

            if tok:

                return tok

    return None



def process_tool_brushes_in_worldspawn(text: str, remove_tools: bool):

    """

    If remove_tools True: delete any brush in worldspawn classified as tool (NOT caulk).

    Caulk is ALWAYS preserved.

    Returns: (text, removed_count, kept_tool_count)

    """

    s = strip_iw_header(text)

    entities = list(iter_top_level_entities(s))

    if not entities:

        return s, 0, 0



    world = entities[0][2]

    lines = world.splitlines(True)



    depth = 0

    in_brush = False

    buf = []

    out = []

    removed = 0

    kept_tools = 0



    for line in lines:

        stripped = line.strip()



        if stripped == "{" and depth == 1 and not in_brush:

            in_brush = True

            buf = [line]

        elif in_brush:

            buf.append(line)

        else:

            out.append(line)



        depth += line.count("{") - line.count("}")



        if in_brush and stripped == "}" and depth == 1:

            brush_block = "".join(buf)

            tool_tok = detect_tool_brush_type(brush_block)



            # If it's caulk, never remove it.

            if tool_tok == "common/caulk":

                out.append(brush_block)

            elif tool_tok and remove_tools:

                removed += 1

            else:

                if tool_tok:

                    kept_tools += 1

                out.append(brush_block)



            in_brush = False

            buf = []



    world_new = "".join(out)

    kept_entities = [world_new] + [ent[2] for ent in entities[1:]]

    return "\n".join(kept_entities), removed, kept_tools



# ----------------------------

# Placeholder mapping + conversion

# ----------------------------



def convert_map_text(cod_text: str, texture_mode: str, placeholder_prefix: str, placeholder_map: dict):

    """

    Convert COD4 map faces to Q3/ET face format:

      - ALWAYS preserve caulk: caulk/common/caulk/textures/common/caulk -> common/caulk

      - ALWAYS map tool textures using TOOL_MAP

      - then apply texture_mode for remaining materials:

          * 'caulk_all' -> common/caulk

          * 'placeholders' -> placeholder/<n>

    Returns: (out_text, converted_faces, skipped_face_like, tools_remapped, caulk_preserved)

    """

    cod_text = strip_iw_header(cod_text)

    lines = remove_contents_detail(cod_text.splitlines(True))



    converted = 0

    skipped = 0

    tools_remapped = 0

    caulk_preserved = 0

    out_lines = []



    for line in lines:

        if line.lstrip().startswith("("):

            m = FACE_LINE_RE.match(line)

            if not m:

                out_lines.append(line)

                skipped += 1

                continue



            prefix = m.group(1)



            norm_lower, norm = face_texture_token_from_line(line)



            # 1) Caulk preservation first

            preserve = normalize_preserve_alias(norm_lower)

            if preserve == "common/caulk" or (norm_lower in ALWAYS_PRESERVE) or (norm and norm.lower() in ALWAYS_PRESERVE):

                shader = "common/caulk"

                caulk_preserved += 1

            else:

                # 2) Tool mapping

                tool_tok = classify_tool_texture(norm_lower)

                if tool_tok:

                    shader = TOOL_MAP.get(tool_tok, "common/caulk")

                    tools_remapped += 1

                else:

                    # 3) Mode mapping

                    if texture_mode == "caulk_all":

                        shader = "common/caulk"

                    else:

                        # placeholders

                        if not norm:

                            shader = f"{placeholder_prefix}/1"

                            placeholder_map.setdefault("<unknown>", shader)

                        else:

                            if norm not in placeholder_map:

                                placeholder_map[norm] = f"{placeholder_prefix}/{len(placeholder_map) + 1}"

                            shader = placeholder_map[norm]



            # Preserve EOL style

            if line.endswith("\r\n"):

                eol = "\r\n"

            elif line.endswith("\n"):

                eol = "\n"

            elif line.endswith("\r"):

                eol = "\r"

            else:

                eol = "\n"



            out_lines.append(f"{prefix} {shader} {TAIL_DEFAULT}{eol}")

            converted += 1

        else:

            out_lines.append(line)



    return "".join(out_lines), converted, skipped, tools_remapped, caulk_preserved



def write_placeholder_csv(csv_path: str, placeholder_map: dict):

    items = [(ph, original) for original, ph in placeholder_map.items()]



    def ph_key(ph):

        try:

            return int(ph.split("/")[-1])

        except:

            return 10**9



    items.sort(key=lambda x: ph_key(x[0]))



    with open(csv_path, "w", newline="", encoding="utf-8") as f:

        w = csv.writer(f)

        w.writerow(["placeholder", "original_texture"])

        for ph, original in items:

            w.writerow([ph, original])



# ----------------------------

# Batch + single conversion

# ----------------------------



def batch_find_map_files(root_dir: str):

    for dirpath, _, filenames in os.walk(root_dir):

        for fn in filenames:

            if fn.lower().endswith(".map"):

                yield os.path.join(dirpath, fn)



def convert_one_file(

    in_path: str,

    out_path: str,

    expand_prefabs: bool,

    prefab_root: str,

    texture_mode: str,

    remove_tool_brushes: bool,

    placeholder_prefix: str = "placeholder",

):

    with open(in_path, "r", encoding="utf-8", errors="replace") as f:

        cod_text = f.read()



    prefab_stats = {"prefabs_found": 0, "prefabs_expanded": 0, "prefab_brushes_added": 0, "missing_prefab_files": 0}

    if expand_prefabs:

        cod_text, prefab_stats = expand_misc_prefabs(cod_text, in_path, prefab_root=prefab_root, max_depth=5)



    tool_removed = 0

    tools_kept = 0

    if remove_tool_brushes:

        cod_text, tool_removed, tools_kept = process_tool_brushes_in_worldspawn(cod_text, remove_tools=True)



    cleaned, mesh_count = remove_mesh_brushes(cod_text)



    placeholder_map = {}

    q3_text, converted, skipped, tools_remapped, caulk_preserved = convert_map_text(

        cleaned,

        texture_mode=texture_mode,

        placeholder_prefix=placeholder_prefix,

        placeholder_map=placeholder_map,

    )



    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    with open(out_path, "w", encoding="utf-8", newline="") as f:

        f.write(q3_text)



    csv_path = ""

    if texture_mode == "placeholders":

        base = os.path.splitext(out_path)[0]

        csv_path = f"{base}_placeholder_map.csv"

        write_placeholder_csv(csv_path, placeholder_map)



    return {

        "faces_converted": converted,

        "face_like_skipped": skipped,

        "mesh_removed": mesh_count,

        "tools_remapped": tools_remapped,

        "caulk_preserved_faces": caulk_preserved,

        "tool_brushes_removed": tool_removed,

        "tool_brushes_kept": tools_kept,

        "bytes": os.path.getsize(out_path),

        "placeholder_count": len(placeholder_map),

        "placeholder_csv": csv_path,

        **prefab_stats,

    }



# ----------------------------

# GUI

# ----------------------------



class ConverterGUI:

    def __init__(self, root):

        self.root = root

        self.root.title("COD4 to Wolf:ET Map Converter")

        self.root.geometry("980x760")



        self.input_file = tk.StringVar()

        self.output_dir = tk.StringVar()

        self.prefab_root = tk.StringVar()



        self.expand_prefabs_var = tk.BooleanVar(value=True)

        self.texture_mode_var = tk.StringVar(value="placeholders")  # "caulk_all" or "placeholders"

        self.remove_tools_var = tk.BooleanVar(value=False)

        self.placeholder_prefix_var = tk.StringVar(value="placeholder")



                # UI theme support
        self.style = ttk.Style(self.root)
        self.current_theme = "light"
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass
        self.apply_theme(self.current_theme)

        self.create_widgets()

        # Re-apply theme now that all widgets exist
        self.apply_theme(self.current_theme)



    def create_widgets(self):

        topbar = ttk.Frame(self.root, padding=(10, 10, 10, 0))
        topbar.pack(fill="x")
        self.theme_btn = ttk.Button(topbar, text="Theme: Light", command=self.toggle_theme)
        self.theme_btn.pack(side="left")
        ttk.Button(topbar, text="Credits", command=self.show_credits).pack(side="left", padx=8)

        input_frame = ttk.LabelFrame(self.root, text="Input File", padding=10)

        input_frame.pack(fill="x", padx=10, pady=6)

        ttk.Entry(input_frame, textvariable=self.input_file, width=86).pack(side="left", padx=5)

        ttk.Button(input_frame, text="Browse", command=self.browse_input).pack(side="left", padx=5)



        output_frame = ttk.LabelFrame(self.root, text="Output Directory (Optional)", padding=10)

        output_frame.pack(fill="x", padx=10, pady=6)

        ttk.Entry(output_frame, textvariable=self.output_dir, width=86).pack(side="left", padx=5)

        ttk.Button(output_frame, text="Browse", command=self.browse_output).pack(side="left", padx=5)

        ttk.Label(output_frame, text="If set: batch preserves subfolders into this dir").pack(side="left", padx=10)



        prefab_frame = ttk.LabelFrame(self.root, text="Prefab Expansion (misc_prefab)", padding=10)

        prefab_frame.pack(fill="x", padx=10, pady=6)

        ttk.Checkbutton(

            prefab_frame,

            text="Expand misc_prefab (inline referenced prefab .map brushes)",

            variable=self.expand_prefabs_var

        ).pack(anchor="w")



        inner = ttk.Frame(prefab_frame)

        inner.pack(fill="x", pady=6)

        ttk.Label(inner, text="Prefab Root Folder (optional):").pack(side="left", padx=(0, 6))

        ttk.Entry(inner, textvariable=self.prefab_root, width=64).pack(side="left", padx=5)

        ttk.Button(inner, text="Browse", command=self.browse_prefab_root).pack(side="left", padx=5)

        ttk.Label(prefab_frame, text="If blank: prefab paths are resolved relative to the input map folder.",

                  foreground="gray").pack(anchor="w")



        tex_frame = ttk.LabelFrame(self.root, text="Texture Output", padding=10)

        tex_frame.pack(fill="x", padx=10, pady=6)

        ttk.Radiobutton(

            tex_frame,

            text="Force everything to common/caulk",

            variable=self.texture_mode_var,

            value="caulk_all"

        ).pack(anchor="w")



        ttk.Radiobutton(

            tex_frame,

            text="Placeholders: map each unique COD texture -> placeholder/<n> (writes CSV legend)",

            variable=self.texture_mode_var,

            value="placeholders"

        ).pack(anchor="w")



        pref_line = ttk.Frame(tex_frame)

        pref_line.pack(fill="x", pady=6)

        ttk.Label(pref_line, text="Placeholder prefix folder:").pack(side="left", padx=(0, 6))

        ttk.Entry(pref_line, textvariable=self.placeholder_prefix_var, width=20).pack(side="left", padx=5)

        ttk.Label(pref_line, text="Example: placeholder/1, placeholder/2 ...", foreground="gray").pack(side="left", padx=10)



        tools_frame = ttk.LabelFrame(self.root, text="Tool Textures & Caulk", padding=10)

        tools_frame.pack(fill="x", padx=10, pady=6)

        ttk.Label(

            tools_frame,

            text=("Tool textures are always mapped to ET equivalents (never placeholders):\n"

                  "  clip, clip_snow -> common/clip\n"

                  "  hint, hintskip  -> common/hint\n"

                  "  portal_nodraw   -> common/portal_nodraw\n"

                  "  lightgrid_volume-> common/lightgrid\n\n"

                  "IMPORTANT: caulk is ALWAYS preserved:\n"

                  "  caulk / common/caulk / textures/common/caulk -> common/caulk"),

            justify="left"

        ).pack(anchor="w")



        ttk.Checkbutton(

            tools_frame,

            text="Remove tool brushes entirely (DANGEROUS: can break collision/vis/lightgrid) â€” caulk is never removed",

            variable=self.remove_tools_var

        ).pack(anchor="w", pady=4)



        btn_frame = ttk.Frame(self.root)

        btn_frame.pack(pady=8)

        ttk.Button(btn_frame, text="Convert Map", command=self.convert).pack(side="left", padx=6)

        ttk.Button(btn_frame, text="Batch Convert Folder", command=self.batch_convert).pack(side="left", padx=6)



        log_frame = ttk.LabelFrame(self.root, text="Log", padding=10)

        log_frame.pack(fill="both", expand=True, padx=10, pady=6)

        self.log = scrolledtext.ScrolledText(log_frame, height=18, wrap=tk.WORD)

        self.log.pack(fill="both", expand=True)


    def apply_theme(self, mode: str) -> None:
        mode = (mode or "light").lower()
        if mode not in ("light", "dark"):
            mode = "light"

        if mode == "dark":
            bg = "#1e1e1e"
            fg = "#e6e6e6"
            field_bg = "#2a2a2a"
            btn_bg = "#2f2f2f"
            btn_active = "#3a3a3a"
            sel_bg = "#444444"
            sel_fg = "#ffffff"
        else:
            bg = "#f4f4f4"
            fg = "#111111"
            field_bg = "#ffffff"
            btn_bg = "#e9e9e9"
            btn_active = "#dcdcdc"
            sel_bg = "#cfe8ff"
            sel_fg = "#000000"

        self.current_theme = mode

        # Root background (helps if any non-ttk widgets exist)
        try:
            self.root.configure(background=bg)
        except tk.TclError:
            pass

        s = self.style
        # Base
        s.configure(".", background=bg, foreground=fg)
        s.configure("TFrame", background=bg)
        s.configure("TLabel", background=bg, foreground=fg)
        s.configure("TLabelframe", background=bg, foreground=fg)
        s.configure("TLabelframe.Label", background=bg, foreground=fg)

        # Inputs
        s.configure("TEntry", fieldbackground=field_bg, foreground=fg, background=field_bg)

        # Buttons
        s.configure("TButton", background=btn_bg, foreground=fg)
        s.map("TButton",
              background=[("active", btn_active), ("pressed", btn_active)],
              foreground=[("disabled", "#888888")])

        # Checks / radios (clam respects background/foreground in most cases)
        s.configure("TCheckbutton", background=bg, foreground=fg)
        s.configure("TRadiobutton", background=bg, foreground=fg)

        # Log widget (tk.Text)
        if hasattr(self, "log"):
            try:
                self.log.configure(
                    background=field_bg,
                    foreground=fg,
                    insertbackground=fg,
                    selectbackground=sel_bg,
                    selectforeground=sel_fg,
                )
            except tk.TclError:
                pass

        # Theme button label
        if hasattr(self, "theme_btn"):
            try:
                self.theme_btn.configure(text=f"Theme: {'Dark' if mode == 'dark' else 'Light'}")
            except tk.TclError:
                pass


    def toggle_theme(self) -> None:
        new_mode = "dark" if self.current_theme != "dark" else "light"
        self.apply_theme(new_mode)


    def show_credits(self) -> None:
        messagebox.showinfo(
            "Credits / Author",
            "DimStar\n"
            "Dimstar.kd@gmail.com\n"
            "www.truecombatelite.com"
        )


    def browse_input(self):

        filename = filedialog.askopenfilename(

            title="Select COD4 .map file",

            filetypes=[("Map files", "*.map"), ("All files", "*.*")]

        )

        if filename:

            self.input_file.set(filename)



    def browse_output(self):

        dirname = filedialog.askdirectory(title="Select output directory")

        if dirname:

            self.output_dir.set(dirname)



    def browse_prefab_root(self):

        dirname = filedialog.askdirectory(title="Select prefab root folder")

        if dirname:

            self.prefab_root.set(dirname)



    def log_message(self, msg: str):

        self.log.insert(tk.END, msg + "\n")

        self.log.see(tk.END)

        self.root.update()



    def convert(self):

        self.log.delete(1.0, tk.END)

        self.log_message("=== COD4 to Wolf:ET Converter ===\n")



        in_path = self.input_file.get()

        if not in_path or not os.path.isfile(in_path):

            self.log_message("ERROR: Please select a valid input file")

            return



        out_dir = self.output_dir.get().strip() or os.path.dirname(os.path.abspath(in_path))

        base = os.path.splitext(os.path.basename(in_path))[0]

        out_path = os.path.join(out_dir, f"{base}_q3.map")



        expand_prefabs = bool(self.expand_prefabs_var.get())

        prefab_root = self.prefab_root.get().strip()



        texture_mode = self.texture_mode_var.get().strip()

        remove_tools = bool(self.remove_tools_var.get())

        placeholder_prefix = self.placeholder_prefix_var.get().strip() or "placeholder"



        self.log_message(f"Input:  {in_path}")

        self.log_message(f"Output: {out_path}")

        self.log_message(f"Expand misc_prefab: {expand_prefabs}")

        if expand_prefabs:

            self.log_message(f"Prefab root: {prefab_root or '(using input map folder)'}")

        self.log_message(f"Texture mode: {texture_mode}")

        self.log_message(f"Remove tool brushes: {remove_tools}")

        if texture_mode == "placeholders":

            self.log_message(f"Placeholder prefix: {placeholder_prefix}")

        self.log_message("")



        try:

            stats = convert_one_file(

                in_path, out_path,

                expand_prefabs=expand_prefabs,

                prefab_root=prefab_root,

                texture_mode=texture_mode,

                remove_tool_brushes=remove_tools,

                placeholder_prefix=placeholder_prefix,

            )



            self.log_message("âœ“ SUCCESS!")

            self.log_message(f"  Wrote: {stats['bytes']:,} bytes")

            self.log_message(f"  Faces converted:      {stats['faces_converted']}")

            self.log_message(f"  Face-like skipped:    {stats['face_like_skipped']}")

            self.log_message(f"  Mesh/patch removed:   {stats['mesh_removed']}")

            self.log_message(f"  Tool faces remapped:  {stats['tools_remapped']}")

            self.log_message(f"  Caulk faces preserved:{stats['caulk_preserved_faces']}")

            self.log_message(f"  Tool brushes removed: {stats['tool_brushes_removed']}")



            if texture_mode == "placeholders":

                self.log_message(f"  Placeholder count:    {stats['placeholder_count']}")

                if stats["placeholder_csv"]:

                    self.log_message(f"  Placeholder CSV:      {stats['placeholder_csv']}")



            if expand_prefabs:

                self.log_message("")

                self.log_message("Prefab expansion:")

                self.log_message(f"  misc_prefab found:     {stats['prefabs_found']}")

                self.log_message(f"  prefabs expanded:      {stats['prefabs_expanded']}")

                self.log_message(f"  prefab brushes added:  {stats['prefab_brushes_added']}")

                self.log_message(f"  missing prefab files:  {stats['missing_prefab_files']}")



            if stats["face_like_skipped"]:

                self.log_message("\nNOTE: Some face-like lines were not converted. If Radiant errors remain, paste one of those lines and Iâ€™ll extend the matcher.")



        except Exception as e:

            self.log_message(f"\nâœ— ERROR: {str(e)}")

            self.log_message("\nFull traceback:")

            self.log_message(traceback.format_exc())



    def batch_convert(self):

        self.log.delete(1.0, tk.END)

        self.log_message("=== Batch Convert (Folder + Subfolders) ===\n")



        root_dir = filedialog.askdirectory(title="Select folder containing COD4 .map files")

        if not root_dir:

            self.log_message("Cancelled.")

            return



        out_base = self.output_dir.get().strip()

        use_out_base = bool(out_base)



        expand_prefabs = bool(self.expand_prefabs_var.get())

        prefab_root = self.prefab_root.get().strip()



        texture_mode = self.texture_mode_var.get().strip()

        remove_tools = bool(self.remove_tools_var.get())

        placeholder_prefix = self.placeholder_prefix_var.get().strip() or "placeholder"



        self.log_message(f"Scanning: {root_dir}")

        if use_out_base:

            self.log_message(f"Output base: {out_base} (preserving subfolders)")

        else:

            self.log_message("Output: next to each input file")

        self.log_message(f"Expand misc_prefab: {expand_prefabs}")

        if expand_prefabs:

            self.log_message(f"Prefab root: {prefab_root or '(using each map folder)'}")

        self.log_message(f"Texture mode: {texture_mode}")

        self.log_message(f"Remove tool brushes: {remove_tools}")

        if texture_mode == "placeholders":

            self.log_message(f"Placeholder prefix: {placeholder_prefix}")

        self.log_message("")



        files = list(batch_find_map_files(root_dir))

        if not files:

            self.log_message("No .map files found.")

            return



        total = len(files)

        ok = 0

        failed = 0



        self.log_message(f"Found {total} map file(s). Converting...\n")



        for idx, in_path in enumerate(files, start=1):

            try:

                base = os.path.splitext(os.path.basename(in_path))[0]

                if use_out_base:

                    rel = os.path.relpath(os.path.dirname(in_path), root_dir)

                    out_dir = os.path.join(out_base, rel)

                else:

                    out_dir = os.path.dirname(in_path)

                out_path = os.path.join(out_dir, f"{base}_q3.map")



                convert_one_file(

                    in_path, out_path,

                    expand_prefabs=expand_prefabs,

                    prefab_root=prefab_root,

                    texture_mode=texture_mode,

                    remove_tool_brushes=remove_tools,

                    placeholder_prefix=placeholder_prefix,

                )



                ok += 1

                if idx % 10 == 0 or idx == total:

                    self.log_message(f"[{idx}/{total}] OK ({ok} ok / {failed} fail)")



            except Exception as e:

                failed += 1

                self.log_message(f"[{idx}/{total}] FAIL: {in_path}")

                self.log_message(f"           {e}")



        self.log_message("\n=== Batch Summary ===")

        self.log_message(f"Converted OK: {ok}")

        self.log_message(f"Failed:       {failed}")

        if texture_mode == "placeholders":

            self.log_message("NOTE: each file writes its own _placeholder_map.csv legend.")



def main():

    root = tk.Tk()

    ConverterGUI(root)

    root.mainloop()



if __name__ == "__main__":

    main()
