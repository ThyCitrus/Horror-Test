import json
from pathlib import Path

SAVE_DIR = Path("saves")
NUM_SLOTS = 5


def normalize_character(character: dict) -> dict:
    """Fill in fields introduced by later game systems without resetting saves."""
    character.setdefault("floor", 0 if character.get("seed") == 0 else 1)
    character.setdefault("bytes", character.get("gold", 0))
    character.setdefault("gold", character.get("bytes", 0))
    character.setdefault("notepad", [])
    character.setdefault("events", [])
    character.setdefault("items", [])
    character.setdefault("loot", [])
    character.setdefault("equipped_light", None)
    character.setdefault("objective", None)
    return character


def add_notepad_entry(character: dict, entry: str) -> None:
    """Append a unique lore entry and keep save data compact."""
    normalize_character(character)
    if entry and entry not in character["notepad"]:
        character["notepad"].append(entry)


def add_character_event(character: dict, event: str) -> None:
    """Store a short narrative event for the character's system history."""
    normalize_character(character)
    if event:
        character["events"].append(event)
        character["events"] = character["events"][-40:]


def save_json(obj, path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def load_json(path):
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def slot_path(slot_num: int) -> Path:
    return SAVE_DIR / f"slot{slot_num}.json"


def list_slots() -> list[dict]:
    """Returns info for every slot, whether filled or empty."""
    slots = []
    for i in range(1, NUM_SLOTS + 1):
        path = slot_path(i)
        if path.exists():
            data = normalize_character(load_json(path))
            slots.append(
                {
                    "slot": i,
                    "path": path,
                    "filled": True,
                    "name": data["name"],
                    "level": data.get("level", 1),
                    "color": data["color"],
                }
            )
        else:
            slots.append({"slot": i, "path": path, "filled": False})
    return slots


def delete_slot(slot_num: int) -> None:
    path = slot_path(slot_num)
    if path.exists():
        path.unlink()
