"""一次性批量修复三个标记器：MAPS_FILE → get_map_path + 参数"""
from pathlib import Path

base = Path("F:/Program Files/WorkBuddy Works/python-test/脚本开发工具/地图标记工具/markers")

for fname in ["rope_marker.py", "jump_marker.py", "flash_marker.py"]:
    path = base / fname
    content = path.read_text(encoding="utf-8")

    # 1. Import rename
    content = content.replace(
        "from .config import CAPTURE_FPS, MAPS_FILE, OUTPUT_DIR",
        "from .config import CAPTURE_FPS, get_map_path, OUTPUT_DIR")
    content = content.replace(
        "from config import CAPTURE_FPS, MAPS_FILE, OUTPUT_DIR  # type: ignore[no-redef]",
        "from config import CAPTURE_FPS, get_map_path, OUTPUT_DIR  # type: ignore[no-redef]")

    # 2. Remove batch-script leftover win_name injection
    content = content.replace(
        "        win_name = self._window_var.get().strip()\n        if not map_name or not",
        "        if not map_name or not")

    # 3. Replace all MAPS_FILE → get_map_path(win_name, map_name).name pattern
    #    and restructure save methods
    lines = content.splitlines(keepends=True)
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Replace MAPS_FILE in non-import contexts
        if "MAPS_FILE" in line and "import" not in line and "from" not in line:
            line = line.replace("MAPS_FILE", "get_map_path(win_name, map_name)")
        new_lines.append(line)
        i += 1

    content = "".join(new_lines)

    # 4. Fix: os.makedirs(OUTPUT_DIR...) → map_path.parent.mkdir
    content = content.replace(
        "os.makedirs(OUTPUT_DIR, exist_ok=True)",
        "win_name = self._window_var.get().strip()\n"
        "        map_path = get_map_path(win_name, map_name)\n"
        "        map_path.parent.mkdir(parents=True, exist_ok=True)")

    # 5. Fix: data.get(map_name, {}) → existing (single-map file, no nesting)
    content = content.replace("data.get(map_name, {})", "existing")

    # 6. Fix: data[map_name] = existing → remove this line
    content = content.replace("\n            data[map_name] = existing\n", "\n")

    # 7. Fix: "data = json.load(f)" → "existing = json.load(f)" (in load section)
    content = content.replace(
        "            data = json.load(f)\n"
        "        else:\n"
        "            data = {}",
        "            existing = json.load(f)\n"
        "        else:\n"
        "            existing = {}")

    # 8. Fix remaining "data" → "existing" in save-write calls
    content = content.replace("data = json.load(f)", "existing = json.load(f)")

    # 9. Fix: json.dump(data, ...) → json.dump(existing, ...) where applicable
    # (in the save section context, after modifications)
    content = content.replace(
        "json.dump(data, f, indent=2, ensure_ascii=False)",
        "json.dump(existing, f, indent=2, ensure_ascii=False)")

    # 10. Fix stray "data = {}" → "existing = {}"
    content = content.replace("\n            data = {}\n", "\n            existing = {}\n")

    path.write_text(content, encoding="utf-8")

    remaining = content.count("MAPS_FILE")
    print(f"✓ {fname}  MAPS_FILE={remaining}")
