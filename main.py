from __future__ import annotations

import sys
from pathlib import Path
import gzip
import re
import xml.etree.ElementTree as ET
from configparser import ConfigParser

# ---------- Constants / paths ----------

VERSION = "1.0.0"
APP_NAME = "Barotrauma Submarine Stripper"

# Base dir: works both as .py and as PyInstaller EXE
if getattr(sys, "frozen", False):
    # Running as a bundled EXE
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    # Running as a normal Python script
    BASE_DIR = Path(__file__).resolve().parent

INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
SETTINGS_PATH = BASE_DIR / "settings.ini"


# ---------- Settings ----------

def safe_getboolean(cfg: ConfigParser, section: str, option: str, default: bool) -> bool:
    """
    Get a boolean from the config safely.
    Any error (missing section/option, invalid value) -> print and return default.
    """
    try:
        value = cfg.getboolean(section, option)
        return value
    except Exception as e:
        print(f"[WARN] Invalid or missing boolean for [{section}] {option}: {e!r}. Using default {default}")
        return default


def load_settings():
    """
    Load settings from settings.ini.
    If the file doesn't exist, create it with default values.
    Returns (strip_items: bool, exclude_identifiers: set[str], strip_upgrades: bool).
    """
    cfg = ConfigParser()

    if not SETTINGS_PATH.exists():
        cfg["Settings"] = {
            "STRIP_ITEMS": "false",
            "STRIP_UPGRADES": "true",
            "EXCLUDE_ITEMS": "",
        }
        with SETTINGS_PATH.open("w", encoding="utf-8") as f:
            cfg.write(f)
        print(f"[INFO] settings.ini not found, created default at {SETTINGS_PATH}")

    cfg.read(SETTINGS_PATH, encoding="utf-8")

    strip_items = safe_getboolean(cfg, "Settings", "STRIP_ITEMS", False)
    strip_upgrades = safe_getboolean(cfg, "Settings", "STRIP_UPGRADES", True)

    try:
        raw_exclude = cfg.get("Settings", "EXCLUDE_ITEMS")
    except Exception as e:
        print(f"[WARN] EXCLUDE_ITEMS missing or invalid in settings.ini: {e!r}. Using empty list.")
        raw_exclude = ""

    exclude_identifiers = {
        token.strip().lower()
        for token in re.split(r"[,\s;]+", raw_exclude)
        if token.strip()
    }

    print(f"[INFO] STRIP_ITEMS = {strip_items}")
    print(f"[INFO] STRIP_UPGRADES = {strip_upgrades}")
    if exclude_identifiers:
        print(f"[INFO] EXCLUDE_ITEMS = {', '.join(sorted(exclude_identifiers))}")
    else:
        print("[INFO] EXCLUDE_ITEMS = (none)")

    return strip_items, exclude_identifiers, strip_upgrades


# ---------- File IO ----------

def read_sub_as_xml(path: Path) -> ET.ElementTree:
    """
    Read a .sub file (usually gzipped XML) and return an ElementTree.
    Falls back to treating it as plain XML if it's not gzipped.
    """
    raw = path.read_bytes()

    try:
        xml_bytes = gzip.decompress(raw)
    except OSError:
        xml_bytes = raw

    text = xml_bytes.decode("utf-8-sig")
    root = ET.fromstring(text)
    return ET.ElementTree(root)


def write_xml_as_sub(tree: ET.ElementTree, out_path: Path) -> None:
    """
    Serialize the XML and write it back as a gzipped .sub file.
    """
    xml_bytes = ET.tostring(
        tree.getroot(),
        encoding="utf-8",
        xml_declaration=True,
    )
    compressed = gzip.compress(xml_bytes)
    out_path.write_bytes(compressed)


# ---------- Upgrade removal ----------

def find_attr_case_insensitive(attrib: dict, name_lower: str):
    """
    Given an attrib dict and a lowercase name, return the real key
    whose lowercase matches, or None if not found.
    """
    name_lower = name_lower.lower()
    for key in attrib.keys():
        if key.lower() == name_lower:
            return key
    return None


def revert_upgrade(upgrade_elem: ET.Element, parent_elem: ET.Element) -> int:
    """
    Revert a single <Upgrade> element's effects on its parent <Item> (or similar)
    and remove the <Upgrade> node.

    Returns the number of attribute values changed.
    """
    changes = 0

    for comp_change in list(upgrade_elem):
        comp_tag = comp_change.tag

        if comp_tag.lower() == "this":
            targets = [parent_elem]
        else:
            targets = [child for child in parent_elem if child.tag == comp_tag]

        if not targets:
            continue

        for stat_elem in list(comp_change):
            stat_name_lower = stat_elem.tag.lower()
            original_value = stat_elem.get("value")
            if original_value is None:
                continue

            for target in targets:
                attr_key = find_attr_case_insensitive(target.attrib, stat_name_lower)
                if not attr_key:
                    continue

                target.attrib[attr_key] = original_value
                changes += 1

    parent_elem.remove(upgrade_elem)
    return changes


def process_upgrades(tree: ET.ElementTree) -> int:
    """
    Find all <Upgrade> elements, revert them, and remove them.

    Returns total number of attributes reverted.
    """
    root = tree.getroot()
    parent_map = {child: parent for parent in root.iter() for child in parent}

    upgrades = list(root.iter("Upgrade"))
    total_changes = 0

    for up in upgrades:
        parent = parent_map.get(up)
        if parent is None:
            continue
        total_changes += revert_upgrade(up, parent)

    return total_changes


def remove_extra_stacksize_stats(tree: ET.ElementTree) -> int:
    """
    Remove every <Stat> node whose type/Type attribute is 'ExtraStackSize'
    (case-insensitive).
    Returns the number of Stat nodes removed.
    """
    root = tree.getroot()
    parent_map = {child: parent for parent in root.iter() for child in parent}

    removed = 0
    for stat in list(root.iter("Stat")):
        t = stat.get("type") or stat.get("Type")
        if t and t.lower() == "extrastacksize":
            parent = parent_map.get(stat)
            if parent is not None:
                parent.remove(stat)
                removed += 1
    return removed


# ---------- Item deletion utilities ----------

def extract_ids_from_contained(value: str):
    """
    Given a contained string like:
      "7989,8186,4161,511;141;874;...,304,63,297"
    return all numeric IDs as strings.
    """
    if not value:
        return []
    return re.findall(r"\d+", value)


def build_maps_for_items(root: ET.Element):
    """
    Build:
      - parent_map: element -> parent element
      - id_to_item: ID -> <Item> element
      - container_to_children: container ID -> set of contained IDs
      - child_to_containers: item ID -> set of container IDs that contain it
    """
    parent_map = {}
    for parent in root.iter():
        for child in parent:
            parent_map[child] = parent

    id_to_item = {}
    container_to_children = {}
    child_to_containers = {}

    for item in root.iter("Item"):
        item_id = item.get("ID")
        if item_id:
            id_to_item[item_id] = item

        for ic in item.findall("ItemContainer"):
            contained = ic.get("contained", "")
            ids = extract_ids_from_contained(contained)
            if not ids:
                continue

            if item_id:
                container_to_children.setdefault(item_id, set()).update(ids)

            for cid in ids:
                child_to_containers.setdefault(cid, set()).add(item_id)

    return parent_map, id_to_item, container_to_children, child_to_containers


def build_safe_ids(
    id_to_item,
    container_to_children,
    child_to_containers,
    exclude_identifiers,
):
    """
    Build the set of 'safe from deletion' IDs:

    Start from all items whose identifier is in EXCLUDE_ITEMS, then recursively:
      - add all children they contain (downwards via ItemContainer.contained)
      - add all parents/containers that contain them (upwards)
    """
    safe_ids = set()
    queue = []

    # Seed: items whose identifier is in the exclude list
    for item_id, item in id_to_item.items():
        identifier = (item.get("identifier") or "").lower()
        if identifier in exclude_identifiers:
            safe_ids.add(item_id)
            queue.append(item_id)

    # BFS across the containment graph (both directions)
    while queue:
        current = queue.pop()

        # Children
        for child_id in container_to_children.get(current, ()):
            if child_id not in safe_ids:
                safe_ids.add(child_id)
                queue.append(child_id)

        # Parents
        for parent_id in child_to_containers.get(current, ()):
            if parent_id not in safe_ids:
                safe_ids.add(parent_id)
                queue.append(parent_id)

    return safe_ids


def collect_link_w_ids(root: ET.Element, id_to_item: dict[str, ET.Element]) -> set[str]:
    """
    Collect IDs referenced by <link w="..."> anywhere in the document.
    Only keep those that actually correspond to an existing Item ID.
    """
    safe_from_links: set[str] = set()
    for link in root.iter("link"):
        w = link.get("w")
        if not w:
            continue
        if w in id_to_item:
            safe_from_links.add(w)
    return safe_from_links


TARGET_TAGS = {
    "smallitem",
    "mediumitem",
    "ammobox",
    "railgunammo",
    "human",
    "mobilecontainer",
    "depthchargeammo",
    "crate",
}

BEHAVIOR_COMPONENT_TAGS = (
    "Holdable",
    "Throwable",
    "Growable",
    "Pickable",
    "MeleeWeapon",
    "Wearable",
)


def has_behavior_component(item: ET.Element) -> bool:
    """
    Return True if the item has at least one of the behavior component subnodes:
    Holdable, Throwable, Growable, Pickable, MeleeWeapon, Wearable.
    """
    for tag in BEHAVIOR_COMPONENT_TAGS:
        if item.find(tag) is not None:
            return True
    return False


def item_is_deletable(item: ET.Element, safe_ids: set) -> bool:
    """
    Items to delete must:
      1) Have an ID not in safe_ids
      2) Have Tags containing at least one of TARGET_TAGS
      3) NOT have a <Holdable Attached="True" ...> subnode
    """
    item_id = item.get("ID")
    if not item_id:
        return False
    if item_id in safe_ids:
        return False

    raw_tags = (
        item.get("Tags")
        or item.get("tags")
        or ""
    )
    tag_tokens = {
        t.strip().lower()
        for t in re.split(r"[,\s]+", raw_tags)
        if t.strip()
    }

    if not (tag_tokens & TARGET_TAGS):
        return False

    for holdable in item.findall("Holdable"):
        attached = holdable.get("Attached")
        if attached is not None and attached.lower() == "true":
            return False

    return True


def remove_deleted_ids_from_contained(value: str, deleted_ids: set[str]) -> str:
    """
    Remove IDs from a contained string by deleting only the digits of IDs
    that were deleted, preserving commas/semicolons exactly.
    """
    if not value or not deleted_ids:
        return value

    def repl(match: re.Match) -> str:
        token = match.group(0)
        return "" if token in deleted_ids else token

    return re.sub(r"\d+", repl, value)


def delete_tagged_items(tree: ET.ElementTree, exclude_identifiers: set) -> int:
    """
    Deletion logic:

    1. Build safe-from-deletion ID set from EXCLUDE_ITEMS graph.
    2. Add to safe set:
         - all IDs referenced by any <link w="...">
         - all IDs of Items that have NO behavior component subnodes.
    3. Delete any <Item> whose ID is not safe and which:
         - has required tags, and
         - is not a Holdable with Attached="True".
    4. Remove deleted IDs from all ItemContainer.contained attributes
       by deleting only the digits and preserving separators.
    """
    root = tree.getroot()

    parent_map, id_to_item, container_to_children, child_to_containers = build_maps_for_items(root)
    safe_ids = build_safe_ids(id_to_item, container_to_children, child_to_containers, exclude_identifiers)
    print(f"[DEBUG] Safe IDs from EXCLUDE_ITEMS graph: {len(safe_ids)}")

    # IDs referenced by links
    link_safe_ids = collect_link_w_ids(root, id_to_item)
    safe_ids.update(link_safe_ids)
    print(f"[DEBUG] Additional safe IDs from <link w=\"...\">: {len(link_safe_ids)}")

    # Items with no behavior components
    static_added = 0
    for item_id, item in id_to_item.items():
        if item_id in safe_ids:
            continue
        if not has_behavior_component(item):
            safe_ids.add(item_id)
            static_added += 1
    print(f"[DEBUG] Additional safe IDs from static items (no behavior components): {static_added}")

    deleted_ids: set[str] = set()

    for item in list(root.iter("Item")):
        item_id = item.get("ID")
        if not item_id:
            continue

        if item_is_deletable(item, safe_ids):
            parent = parent_map.get(item)
            if parent is not None:
                parent.remove(item)
                deleted_ids.add(item_id)

    if deleted_ids:
        for item in root.iter("Item"):
            for ic in item.findall("ItemContainer"):
                contained = ic.get("contained", "")
                if not contained:
                    continue
                cleaned = remove_deleted_ids_from_contained(contained, deleted_ids)
                ic.set("contained", cleaned)

    print(f"[INFO] Items deleted this file: {len(deleted_ids)}")
    return len(deleted_ids)


# ---------- File-level processing ----------

def process_sub_file(
    path: Path,
    output_dir: Path,
    strip_items: bool,
    exclude_identifiers: set,
    strip_upgrades: bool,
) -> None:
    print(f"[INFO] Processing {path.name} ...")

    tree = read_sub_as_xml(path)

    if strip_upgrades:
        upgrade_changes = process_upgrades(tree)
        print(f"[INFO] Reverted {upgrade_changes} upgraded attribute(s)")

        extra_stack_removed = remove_extra_stacksize_stats(tree)
        print(f"[INFO] Removed {extra_stack_removed} ExtraStackSize Stat node(s)")
    else:
        print("[INFO] Skipping upgrade stripping (STRIP_UPGRADES = false)")

    if strip_items:
        deleted_items = delete_tagged_items(tree, exclude_identifiers)
        print(
            f"[INFO] Finished item stripping for {path.name}, "
            f"deleted {deleted_items} item(s)"
        )
    else:
        print("[INFO] Skipping item stripping (STRIP_ITEMS = false)")

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / path.name
    write_xml_as_sub(tree, out_path)

    print(f"[INFO] Wrote output file: {out_path.name}")


def main() -> None:
    print(f"{APP_NAME} v{VERSION}")
    print("-" * 40)

    strip_items, exclude_identifiers, strip_upgrades = load_settings()

    if not INPUT_DIR.exists():
        print(f"[ERROR] Input folder not found: {INPUT_DIR}")
        print("[INFO] Create an 'input' folder next to the tool and drop your .sub files in it.")
        if getattr(sys, "frozen", False):
            input("\nPress Enter to exit...")
        return

    sub_files = sorted(INPUT_DIR.glob("*.sub"))

    if not sub_files:
        print(f"[WARN] No .sub files found in {INPUT_DIR}")
        if getattr(sys, "frozen", False):
            input("\nPress Enter to exit...")
        return

    print(f"[INFO] Found {len(sub_files)} .sub file(s) in {INPUT_DIR}")

    for sub_path in sub_files:
        try:
            process_sub_file(sub_path, OUTPUT_DIR, strip_items, exclude_identifiers, strip_upgrades)
        except Exception as e:
            print(f"[ERROR] Error processing {sub_path.name}: {e!r}")

    print("[INFO] All done.")
    if getattr(sys, "frozen", False):
        input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()
