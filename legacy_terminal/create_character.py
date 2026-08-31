from save_utils import save_json, slot_path
import random
from classes import CLASSES, compute_stats
from display import print_color
from commands import cycle_choice, clear


def choose_color() -> str:
    while True:
        r = random.randint(50, 255)
        g = random.randint(50, 255)
        b = random.randint(50, 255)
        print_color(f"Your color: [{r} {g} {b}]", r, g, b)
        choice = input("(r)eroll or (c)onfirm? ").strip().lower()
        if choice == "c":
            return f"{r} {g} {b}"
        # anything else, including 'r', just loops and rerolls


def choose_name() -> str:
    while True:
        name = input("Enter your character's name: ").strip()
        if not name:
            print("Name cannot be empty!")
            continue
        print(f"\nName: {name}")
        pick = cycle_choice(["Confirm", "Rename"])
        if pick == 1:
            return name


def choose_class() -> str:
    keys = list(CLASSES.keys())

    while True:
        labels = [f"{CLASSES[key].name} — {CLASSES[key].description}" for key in keys]
        colors = [CLASSES[key].color for key in keys]

        choice = cycle_choice(labels, colors)
        chosen_key = keys[choice - 1]
        c = CLASSES[chosen_key]

        confirm = cycle_choice([f"Confirm '{c.name}'", "Choose again"])
        if confirm == 1:
            return chosen_key


def create_character() -> dict:
    name = choose_name()
    color = choose_color()
    player_class_key = choose_class()

    base = compute_stats(CLASSES[player_class_key])

    character = {
        "name": name,
        "color": color,
        "class": player_class_key,
        "level": 1,
        "xp": 0,
        "stats": {"str": base["str"], "dex": base["dex"], "int": base["int"]},
        "hp": base["max_health"],
        "max_hp": base["max_health"],
        "mana": base["max_mana"],
        "max_mana": base["max_mana"],
        "stamina": base["max_stamina"],
        "max_stamina": base["max_stamina"],
        "gold": 0,
        "items": [],
    }
    return character


def create_and_save_to_slot(slot_num: int) -> dict:
    character = create_character()
    path = slot_path(slot_num)
    save_json(character, path)
    print(f"Character '{character['name']}' saved to slot {slot_num}.")
    return character


if __name__ == "__main__":
    create_and_save_to_slot(1)
