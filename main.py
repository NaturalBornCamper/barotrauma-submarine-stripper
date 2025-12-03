from __future__ import annotations

import sys
from pathlib import Path
import gzip
import re
import xml.etree.ElementTree as ET

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


# ---------- Small helpers ----------

def ask_yes_no(prompt: str) -> bool:
    """
    Ask a yes/no question and return True for 'y', False for 'n'.
    Any other input repeats the question.
    """
    while True:
        answer = input(prompt + " ").strip().lower()
        if answer == "y":
            return True
        if answer == "n":
            return False
        print("Please answer with 'y' or 'n'.")


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


# ---------- Item deletion (SAFE_LIST-based logic) ----------

BEHAVIOR_COMPONENT_TAGS = (
    "Holdable",
    "Throwable",
    "Growable",
    "Pickable",
    "MeleeWeapon",
    "Wearable",
    "Projectile",
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


def get_tags(item: ET.Element) -> set[str]:
    """
    Return a set of lowercase tag tokens from item.Tags / item.tags.
    """
    raw = (
        item.get("Tags")
        or item.get("tags")
        or ""
    )
    return {
        t.strip().lower()
        for t in re.split(r"[,\s]+", raw)
        if t.strip()
    }


def build_safe_ids(root: ET.Element) -> set[str]:
    """
    Build SAFE_LIST of item IDs that must NOT be deleted, based on:

    1. Circuit boxes:
       - <Item identifier="circuitbox" ...> with <Holdable Attached="True">
       - plus all IDs in their ItemContainer.contained attributes
         (both comma and semicolon separated).

    2. Placed components:
       - <Item Tags="...circuitboxcomponent..." ...> with <Holdable Attached="True">

    3. Placed/attached wires:
       - <Item Tags="...wire..." ...> with a <Wire nodes="..."> subnode.

    4. All attached items:
       - Any <Item> with <Holdable Attached="True">.
    """
    safe_ids: set[str] = set()

    for item in root.iter("Item"):
        item_id = item.get("ID")
        if not item_id:
            continue

        identifier = (item.get("identifier") or "").lower()
        tags = get_tags(item)

        # Helper: does this item have a Holdable with Attached="True"?
        def has_attached_holdable(it: ET.Element) -> bool:
            for h in it.findall("Holdable"):
                attached = h.get("Attached")
                if attached is not None and attached.lower() == "true":
                    return True
            return False

        # 1. Circuit boxes + their contained IDs
        if identifier == "circuitbox" and has_attached_holdable(item):
            safe_ids.add(item_id)
            for ic in item.findall("ItemContainer"):
                contained = ic.get("contained", "")
                if contained:
                    for cid in re.findall(r"\d+", contained):
                        safe_ids.add(cid)

        # 2. Placed components (tag=circuitboxcomponent + attached)
        if "circuitboxcomponent" in tags and has_attached_holdable(item):
            safe_ids.add(item_id)

        # 3. Placed/attached wires (tag=wire + Wire nodes)
        if "wire" in tags:
            for wire in item.findall("Wire"):
                nodes_attr = wire.get("nodes")
                if nodes_attr and nodes_attr.strip():
                    safe_ids.add(item_id)
                    break

        # 4. All attached items in general
        if has_attached_holdable(item):
            safe_ids.add(item_id)

    return safe_ids


def cleanup_contained(value: str, existing_ids: set[str]) -> str:
    """
    Remove IDs from a contained string by deleting only the digits of IDs
    that do not have a matching <Item ID="...">, preserving commas/semicolons.

    Example:
      value = "1,2,3,4"
      existing_ids = {"3", "4"}
      result = ",,,3,4" (digits removed where missing; separators kept)
    """
    if not value:
        return value

    def repl(match: re.Match) -> str:
        token = match.group(0)
        return token if token in existing_ids else ""

    return re.sub(r"\d+", repl, value)


def delete_items(tree: ET.ElementTree) -> int:
    """
    Implements the deletion logic:

    1. Build SAFE_LIST of IDs that must NOT be deleted.
    2. Delete any <Item> that:
         - has one of the behavior subnodes (Holdable/Throwable/Growable/Pickable/MeleeWeapon/Wearable), AND
         - has an ID not in SAFE_LIST.
    3. Cleanup:
         - For every ItemContainer.contained, remove IDs that don't exist anymore,
           but keep commas/semicolons.
         - Remove any <link w="..."> whose ID no longer exists.
    """
    root = tree.getroot()

    # Parent map for deletions
    parent_map = {child: parent for parent in root.iter() for child in parent}

    # 1. SAFE_LIST
    safe_ids = build_safe_ids(root)
    print(f"[DEBUG] SAFE_LIST size: {len(safe_ids)}")

    # 2. Delete items not in SAFE_LIST that have behavior components
    deleted_ids: set[str] = set()

    for item in list(root.iter("Item")):
        item_id = item.get("ID")
        if not item_id:
            continue

        if item_id in safe_ids:
            continue

        if has_behavior_component(item):
            parent = parent_map.get(item)
            if parent is not None:
                parent.remove(item)
                deleted_ids.add(item_id)

    print(f"[INFO] Items deleted this file: {len(deleted_ids)}")

    # 3. Cleanup references based on *existing* items after deletion
    existing_ids: set[str] = set()
    for item in root.iter("Item"):
        item_id = item.get("ID")
        if item_id:
            existing_ids.add(item_id)

    # 3.1 Cleanup ItemContainer.contained
    for item in root.iter("Item"):
        for ic in item.findall("ItemContainer"):
            contained = ic.get("contained", "")
            if not contained:
                continue
            cleaned = cleanup_contained(contained, existing_ids)
            ic.set("contained", cleaned)

    # 3.2 Cleanup <link w="..."> pointing to non-existing items
    for link in list(root.iter("link")):
        w = link.get("w")
        if not w or w not in existing_ids:
            parent = parent_map.get(link)
            if parent is not None:
                parent.remove(link)

    return len(deleted_ids)


# ---------- File-level processing ----------

def process_sub_file(
    path: Path,
    output_dir: Path,
    strip_items: bool,
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
        print("[INFO] Skipping upgrade stripping")

    if strip_items:
        deleted_items = delete_items(tree)
        print(
            f"[INFO] Finished item stripping for {path.name}, "
            f"deleted {deleted_items} item(s)"
        )
    else:
        print("[INFO] Skipping item stripping")

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / path.name
    write_xml_as_sub(tree, out_path)

    print(f"[INFO] Wrote output file: {out_path.name}")


def main() -> None:
    print(f"{APP_NAME} v{VERSION}")
    print("-" * 40)

    # Ask user what to do
    strip_upgrades = ask_yes_no("Strip submarines' upgrades? (y/n)")
    strip_items = ask_yes_no("Strip submarines' items? (y/n)")

    if not strip_upgrades and not strip_items:
        print("[INFO] Both options disabled. No changes will be made.")
        print("[INFO] No output files will be created.")
        if getattr(sys, "frozen", False):
            input("\nPress Enter to exit...")
        return

    # Input folder check
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
            process_sub_file(sub_path, OUTPUT_DIR, strip_items, strip_upgrades)
        except Exception as e:
            print(f"[ERROR] Error processing {sub_path.name}: {e!r}")

    print("[INFO] All done.")
    if getattr(sys, "frozen", False):
        input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()
