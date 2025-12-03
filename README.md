# Barotrauma Submarine Stripper

Barotrauma Submarine Stripper is a small utility that helps you reset a submarine before starting a new Barotrauma campaign.

It works on extracted `.sub` files and can:

- Remove **upgrades** from your sub (walls, junction boxes, pumps, etc.).
- Remove most **loose and stored items** (gear, ammo, crates, clutter, etc.) while keeping wiring and important fixtures.

You don’t need to edit XML or touch the sub editor manually.

---

## 1. Usage

When you run the tool, it will ask you on runtime if you want to strip upgrades and/or items:

- Answering **`yes`** means **perform that action** on all submarines in the `input` folder.
- Answering **`no`** means **skip that action**.

---

## 2. What each option does

### Strip submarines' upgrades? (y/n)

- **`y` (yes)**  
  - Resets built-in upgrades on the submarine (for example upgraded walls, junction boxes, pumps, etc.).  
  - Removes "Jenga Master" assistant perk permanent stack upgrade in containers.
  - Removes upgrade data so the sub is back to its base stats.

- **`n` (no)**  
  - Leaves all upgrades exactly as they are in the `.sub` file.

### Strip submarines' items? (y/n)

- **`y` (yes)**  
  - Removes most loose / pickable items from the submarine:
    - Gear, ammo, crates, random clutter, etc.
  - Keeps important placed elements such as:
    - Wiring
    - Attached/placed devices, planters, shelves and components
    - Placed circuit boxes and their connected components/wires

- **`n` (no)**  
  - Leaves all items exactly as they are in the `.sub` file.

These two options are independent. You can strip only upgrades, only items, both, or neither.

---

## 3. How to use the tool (EXE)

If you downloaded a release, you will usually have an EXE and this README.

### Step 1 – Folder setup

Place the EXE somewhere, for example:

- `BarotraumaSubmarineStripper.exe`
- `input/` (you create this)
- `output/` (auto-created when needed)

Create an `input` folder **next to the EXE**, then put your `.sub` files into `input/`.

For example:

```text
BarotraumaSubmarineStripper.exe
input/
    MySub1.sub
    MySub2.sub
output/
    (created automatically after running)
```

### Step 2 – Run the tool

1. Double-click `BarotraumaSubmarineStripper.exe`.
2. A console window opens.
3. Answer the two questions (`y` or `n`).
4. The tool will process all `.sub` files in the `input` folder.
5. At the end it will show a summary of what was done.

### Step 3 – Get your cleaned subs

- Cleaned `.sub` files are written to the `output` folder (next to the EXE).
- Your original files in `input` are **never modified**.

You can then use the cleaned submarines for fresh campaigns in Barotrauma.
