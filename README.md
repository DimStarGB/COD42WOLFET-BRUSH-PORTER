# DimsCod2Q3ET — CoD4 .map / prefab → Wolf:ET (idTech3) .map Converter (GUI)

A small Windows-friendly GUI tool that converts **Call of Duty 4 (IW3) prefab / map-style .map text** into a **Wolfenstein: Enemy Territory (idTech3) “classic” .map** that NetRadiant/GtkRadiant can load.

> Author: **DimStar**  
> Email: **Dimstar.kd@gmail.com**  
> Website: **www.truecombatelite.com**

---

## What it does

- Strips IW “header” text (anything before the first `{`)
- Removes standalone CoD metadata lines that break ET parsing, e.g.
  - `contents detail;`
  - `contents_detail;`
  - `contents details;`
  - `contents_somethingElse;`
- Removes IW mesh/patch/curve brush blocks (Wolf:ET classic uses standard brushes)
- Optional: expands `misc_prefab` entities by inlining referenced prefab `.map` brushwork (origin + yaw)
- Converts / remaps common **tool shaders** to ET equivalents:
  - `clip`, `clip_snow` → `common/clip`
  - `hint`, `hintskip` → `common/hint`
  - `portal_nodraw` → `common/portal_nodraw`
  - `lightgrid_volume` → `common/lightgrid`
- Texture output modes (depending on the options you choose in the UI):
  - **Force everything to caulk**, or
  - **Placeholder mode**: each unique CoD texture becomes `placeholder/<n>` and a CSV is written for remapping

> **Important:** `common/caulk` is preserved and is **not** replaced by placeholder logic.

---

## Requirements

- **Python 3.x**
- Tkinter (usually included with standard Python on Windows)

---

## How to use

1. Download `DimsCod2Q3ET.pyw` (or your renamed file) and run it:
   - Double-click the `.pyw` file on Windows, or run:
     - `python DimsCod2Q3ET.pyw`

2. In the GUI:
   - Select your **CoD4 .map / prefab export** file
   - Choose options:
     - Expand `misc_prefab` (optional)
     - Output texture mode (caulk / placeholder mapping)
   - Pick an **output folder** (or accept the default, if provided)

3. Click **Convert**.

4. Open the exported `.map` in **NetRadiant / GtkRadiant** (Wolf:ET gamepack).

---

## Output files

Depending on options, you may see:

- `yourfile_converted.map` (Wolf:ET classic .map)
- `yourfile_placeholder_map.csv` (only in placeholder mode)

---

## Tips / Notes

- If a particular CoD4 map still fails to load, it’s usually due to:
  - Broken / non-planar / degenerate brush faces in the original
  - Unsupported mesh/patch constructs (the tool removes them rather than attempting conversion)
- Placeholder mode is designed to make large-scale texture replacement easier:
  1. Convert once
  2. Edit the CSV to map placeholders to real ET shaders
  3. Re-run / batch replace as desired

---

## License

Recommended for sharing on GitHub: **MIT License** (simple, permissive, keeps attribution).

Place the included `LICENSE` file in the root of your repository.

---

## Credits

Created by **DimStar** — www.truecombatelite.com
