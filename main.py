from pathlib import Path
import gzip
import re
import xml.etree.ElementTree as ET
from configparser import ConfigParser

BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
SETTINGS_PATH = BASE_DIR / "settings.ini"


# ---------- Settings ----------

def load_settings():
    """
    Load settings from settings.ini.
    If the file doesn't exist, create it with default values.
    Returns (empty_items: bool, exclude_identifiers: set[str]).
    """
    cfg = ConfigParser()

    if not SETTINGS_PATH.exists():
        cfg["Settings"] = {
            "EMPTY_ITEMS": "false",
            "EXCLUDE_ITEMS": "",
        }
        with SETTINGS_PATH.open("w", encoding="utf-8") as f:
            cfg.write(f)
        print(f"Created default settings.ini at {SETTINGS_PATH}")

    cfg.read(SETTINGS_PATH, encoding="utf-8")

    empty_items = cfg.getboolean("Settings", "EMPTY_ITEMS", fallback=False)
    raw_exclude = cfg.get("Settings", "EXCLUDE_ITEMS", fallback="")

    # Split on commas, semicolons, or whitespace
    exclude_identifiers = {
        token.strip().lower()
        for token in re.split(r"[,\s;]+", raw_exclude)
        if token.strip()
    }

    return empty_items, exclude_identifiers


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


# ---------- Upgrade removal (unchanged) ----------

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
      - add all children they contain (downwards through ItemContainer.contained)
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


# Tags that mark items as candidates for deletion (if not safe)
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

# Subnodes that indicate "held/usable" items; if none of these exist,
# we treat the item as static and make it safe.
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
      2) Have Tags containing at least one of:
         smallitem, mediumitem, ammobox, railgunammo, human,
         mobilecontainer, depthchargeammo, crate
      3) NOT have a <Holdable Attached="True" ...> subnode
         (attached items must stay)
    """
    item_id = item.get("ID")
    if not item_id:
        return False
    if item_id in safe_ids:
        return False

    # Check tags
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

    # Check Holdable / Attached
    for holdable in item.findall("Holdable"):
        attached = holdable.get("Attached")
        if attached is not None and attached.lower() == "true":
            # Attached holdables must not be deleted
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

    1. Build safe-from-deletion ID set from EXCLUDE_ITEMS,
       recursively adding all children and parents via containment.
    2. Add to safe set:
         - all IDs referenced by any <link w="...">
         - all IDs of Items that have NO behavior component subnodes
           (Holdable, Throwable, Growable, Pickable, MeleeWeapon, Wearable).
    3. Delete any <Item> whose ID is not safe and which:
         - has required tags, and
         - is not a Holdable with Attached="True".
    4. Remove deleted IDs from all ItemContainer.contained attributes
       by deleting only the digits and preserving separators.

    Returns the number of <Item> elements deleted.
    """
    root = tree.getroot()

    parent_map, id_to_item, container_to_children, child_to_containers = build_maps_for_items(root)
    safe_ids = build_safe_ids(id_to_item, container_to_children, child_to_containers, exclude_identifiers)

    # Step 2a: also protect all items referenced by <link w="...">
    link_safe_ids = collect_link_w_ids(root, id_to_item)
    safe_ids.update(link_safe_ids)

    # Step 2b: protect all items that have NO behavior components
    for item_id, item in id_to_item.items():
        if item_id in safe_ids:
            continue
        if not has_behavior_component(item):
            safe_ids.add(item_id)

    deleted_ids: set[str] = set()

    # Step 3: delete suitable items
    for item in list(root.iter("Item")):
        item_id = item.get("ID")
        if not item_id:
            continue

        if item_is_deletable(item, safe_ids):
            parent = parent_map.get(item)
            if parent is not None:
                parent.remove(item)
                deleted_ids.add(item_id)

    # Step 4: clean up ItemContainer.contained references (preserve separators)
    if deleted_ids:
        for item in root.iter("Item"):
            for ic in item.findall("ItemContainer"):
                contained = ic.get("contained", "")
                if not contained:
                    continue
                cleaned = remove_deleted_ids_from_contained(contained, deleted_ids)
                ic.set("contained", cleaned)

    return len(deleted_ids)


# ---------- File-level processing ----------

def process_sub_file(
    path: Path,
    output_dir: Path,
    empty_items: bool,
    exclude_identifiers: set,
) -> None:
    print(f"Processing {path.name} ...")

    tree = read_sub_as_xml(path)

    # 1) Upgrades
    upgrade_changes = process_upgrades(tree)
    print(f"  -> reverted {upgrade_changes} upgraded attribute(s)")

    # 2) Item deletion layer
    deleted_items = 0
    if empty_items:
        deleted_items = delete_tagged_items(tree, exclude_identifiers)
        print(
            f"  -> deleted {deleted_items} item(s) "
            f"(safe IDs from excluded identifiers, links, and non-behavior items)"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / path.name
    write_xml_as_sub(tree, out_path)

    print(f"  -> wrote {out_path.name}")


def main() -> None:
    empty_items, exclude_identifiers = load_settings()
    print(f"EMPTY_ITEMS = {empty_items}")
    if exclude_identifiers:
        print(f"EXCLUDE_ITEMS = {', '.join(sorted(exclude_identifiers))}")
    else:
        print("EXCLUDE_ITEMS = (none)")

    if not INPUT_DIR.exists():
        print(f"Input folder not found: {INPUT_DIR}")
        print("Create an 'input' folder next to main.py and drop your .sub files in it.")
        return

    sub_files = sorted(INPUT_DIR.glob("*.sub"))

    if not sub_files:
        print(f"No .sub files found in {INPUT_DIR}")
        return

    print(f"Found {len(sub_files)} .sub file(s) in {INPUT_DIR}")
    for sub_path in sub_files:
        try:
            process_sub_file(sub_path, OUTPUT_DIR, empty_items, exclude_identifiers)
        except Exception as e:
            print(f"  -> !! Error processing {sub_path.name}: {e}")

    print("Done.")


if __name__ == "__main__":
    main()
