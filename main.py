from pathlib import Path
import gzip
import re
import xml.etree.ElementTree as ET

# Toggle: also empty all contained items if True
EMPTY_ITEMS = True

BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"


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

    # Example structure:
    # <Upgrade identifier="increaseovervoltageresistance" level="3">
    #   <PowerTransfer>
    #       <fireprobability value="0.15"/>
    #       <overloadvoltage value="1.8"/>
    #   </PowerTransfer>
    # </Upgrade>

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

    # Build child -> parent map
    parent_map = {child: parent for parent in root.iter() for child in parent}

    upgrades = list(root.iter("Upgrade"))
    total_changes = 0

    for up in upgrades:
        parent = parent_map.get(up)
        if parent is None:
            continue
        total_changes += revert_upgrade(up, parent)

    return total_changes


# ---------- Item / inventory emptying ----------

def extract_ids_from_contained(value: str) -> list[str]:
    """
    Given a contained string like:
      "7989,8186,4161,511;141;874;...,,304,63,297"
    return all numeric IDs as strings.

    We don't care about exact slot/group structure, just the IDs.
    """
    if not value:
        return []
    return re.findall(r"\d+", value)


def build_parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    return {child: parent for parent in root.iter() for child in parent}


def build_id_map(root: ET.Element) -> dict[str, ET.Element]:
    id_map: dict[str, ET.Element] = {}
    for item in root.iter("Item"):
        item_id = item.get("ID")
        if item_id:
            id_map[item_id] = item
    return id_map


def recursively_delete_item(
    item_id: str,
    id_to_item: dict[str, ET.Element],
    parent_map: dict[ET.Element, ET.Element],
    visited: set[str],
) -> int:
    """
    Delete the Item with the given ID, and recursively delete all items
    contained inside it (if it has ItemContainer components with 'contained' set).

    Returns the number of <Item> elements actually removed.
    """
    if item_id in visited:
        return 0
    visited.add(item_id)

    item = id_to_item.get(item_id)
    if item is None:
        return 0

    deleted_count = 0

    # Recursively delete items this item contains
    for item_container in item.findall("ItemContainer"):
        contained = item_container.get("contained", "")
        nested_ids = extract_ids_from_contained(contained)
        if nested_ids:
            for nested_id in nested_ids:
                deleted_count += recursively_delete_item(
                    nested_id, id_to_item, parent_map, visited
                )
        # Clear contained list on this container item as well
        item_container.set("contained", "")

    # Remove this item from its parent
    parent = parent_map.get(item)
    if parent is not None:
        parent.remove(item)
        deleted_count += 1

    # Remove from id map so we don't try to delete it again
    id_to_item.pop(item_id, None)

    return deleted_count


def empty_items_in_tree(tree: ET.ElementTree) -> int:
    """
    Empty all container items:
      - For each ItemContainer.contained, delete all listed Items (and their nested contents).
      - Clear the contained attribute on all ItemContainer components.

    Returns the number of <Item> elements removed.
    """
    root = tree.getroot()
    parent_map = build_parent_map(root)
    id_to_item = build_id_map(root)

    total_deleted = 0
    visited: set[str] = set()

    # Iterate over all Items that have ItemContainer components
    for item in list(root.iter("Item")):
        for item_container in item.findall("ItemContainer"):
            contained = item_container.get("contained", "")
            if not contained:
                continue

            ids = extract_ids_from_contained(contained)
            if ids:
                for cid in ids:
                    total_deleted += recursively_delete_item(
                        cid, id_to_item, parent_map, visited
                    )

            # Finally, clear the container's 'contained' attribute
            item_container.set("contained", "")

    return total_deleted


# ---------- File-level processing ----------

def process_sub_file(path: Path, output_dir: Path) -> None:
    print(f"Processing {path.name} ...")

    tree = read_sub_as_xml(path)

    upgrade_changes = process_upgrades(tree)
    print(f"  -> reverted {upgrade_changes} upgraded attribute(s)")

    deleted_items = 0
    if EMPTY_ITEMS:
        deleted_items = empty_items_in_tree(tree)
        print(f"  -> deleted {deleted_items} contained item(s)")

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / path.name
    write_xml_as_sub(tree, out_path)

    print(f"  -> wrote {out_path.name}")


def main() -> None:
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
            process_sub_file(sub_path, OUTPUT_DIR)
        except Exception as e:
            print(f"  !! Error processing {sub_path.name}: {e}")

    print("Done.")


if __name__ == "__main__":
    main()
