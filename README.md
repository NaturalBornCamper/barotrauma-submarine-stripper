# Barotrauma Submarine Stripper

Barotrauma Submarine Stripper is a small utility that helps you **reset a submarine** before starting a new Barotrauma campaign.

It works on extracted `.sub` files and can:

- Remove **upgrades** from your sub (walls, junction boxes, pumps, etc.).
- Remove most **loose items** (ammo, crates, clutter, etc.) while keeping wiring and important fixtures.

You don’t need to edit XML or touch the sub editor manually.

---

## 1. Settings (`settings.ini`)

When you run the tool the first time, it will create a file called `settings.ini` next to the EXE.

Example:

```ini
[Settings]
STRIP_ITEMS = false
STRIP_UPGRADES = true
EXCLUDE_ITEMS =
```

### STRIP_UPGRADES

- Type: `true` / `false`
- Default: `true`
- What it does:
  - `true` → Resets and removes all built-in upgrades on the the sub.
  - `false` → Leaves upgrades as they are.

### STRIP_ITEMS

- Type: `true` / `false`
- Default: `false`
- What it does:
  - `true` → Removes most loose / pickable items on the sub (gear, ammo, crates, etc.), while keeping wiring and placed fixtures.
  - `false` → Leaves all items as they are.

> Tip: Start with `STRIP_ITEMS = false` if you only want to test upgrade removal.

### EXCLUDE_ITEMS

- Type: list of **item identifiers** (comma, space, or semicolon separated).
- Default: empty.

Example:

```ini
EXCLUDE_ITEMS = suitcase, saltbulb, my_custom_item
```

What it does:

- Items with these identifiers are **never removed**, even if `STRIP_ITEMS = true`.
- Related containers/contents connected to those items may also be preserved to avoid breaking setups.

You don’t need to set this if you just want a clean, empty-ish sub.

---

## 2. How to use the tool (EXE)

You will typically only have the EXE and the README in a release.

### Step 1 – Folder setup

Place the EXE somewhere, for example:

```text
BarotraumaSubmarineStripper.exe
settings.ini        (optional, auto-created)
input/              (you create this)
output/             (auto-created)
```

- Create an `input` folder **next to the EXE**.
- Put your `.sub` files into `input/`.

### Step 2 – Run the tool

1. Double-click `BarotraumaSubmarineStripper.exe`.
2. A console window will open and show messages such as:
   - Which settings are in use.
   - How many `.sub` files were found.
   - Per-file progress and results.
3. At the end, it will say:

   ```text
   [INFO] All done.
   Press Enter to exit...
   ```

4. Press **Enter** to close the window.

### Step 3 – Get your cleaned subs

- Cleaned `.sub` files are written to the `output` folder (next to the EXE).
- Your original files in `input` are never modified.

---

## 3. Typical scenarios

**Reset upgrades only:**

```ini
STRIP_UPGRADES = true
STRIP_ITEMS = false
EXCLUDE_ITEMS =
```

**Reset upgrades and clear most items:**

```ini
STRIP_UPGRADES = true
STRIP_ITEMS = true
EXCLUDE_ITEMS =
```

**Reset upgrades, clear items, but keep certain special items:**

```ini
STRIP_UPGRADES = true
STRIP_ITEMS = true
EXCLUDE_ITEMS = suitcase, my_custom_item
```

Adjust the settings, drop your `.sub` files in `input`, run the EXE, and use the cleaned subs from `output` in your new campaign.
