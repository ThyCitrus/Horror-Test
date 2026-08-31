from commands import (
    get_key,
    clear,
    flush_input,
    wait_keypress,
    wait_keypress_silent,
    cycle_choice,
)
from create_character import create_and_save_to_slot
from save_utils import list_slots, load_json, delete_slot
from display import print_color, write_slow, parse_color
from game import game_screen


def menu_choice(options: list[str]) -> int:
    flush_input()
    for i, option in enumerate(options, 1):
        print(f"{i}. {option}")
    print()

    if len(options) > 9:
        while True:
            choice = input("Enter choice: ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(options):
                return int(choice)
            print("Invalid choice. Try again.")

    valid_keys = [str(i) for i in range(1, len(options) + 1)]
    while True:
        key = get_key()
        if key in valid_keys:
            return int(key)
        print("Invalid choice. Try again.")


def show_slot_menu():
    clear()
    slots = list_slots()
    print()

    labels = []
    colors = []
    for s in slots:
        if s["filled"]:
            labels.append(
                f"Slot {s['slot']} — {s['name']} | {s['class'].title()} | {s['level']}"
            )
            colors.append(parse_color(s["color"]))
        else:
            labels.append(f"Slot {s['slot']} — Empty")
            colors.append((120, 120, 120))

    labels += ["Delete Save", "Back"]
    colors += [(255, 255, 255), (255, 255, 255)]

    choice = cycle_choice(labels, colors)

    if choice == len(slots) + 2:
        return  # Back
    if choice == len(slots) + 1:
        delete_save_menu(slots)
        return

    chosen = slots[choice - 1]
    if chosen["filled"]:
        character = load_json(chosen["path"])
        game_screen(character)
    else:
        clear()
        character = create_and_save_to_slot(chosen["slot"])
        game_screen(character)


def delete_save_menu(slots):
    filled = [s for s in slots if s["filled"]]
    if not filled:
        print("No saves to delete.")
        wait_keypress()
        return

    labels = [
        f"Slot {s['slot']} — {s['name']} | {s['class'].title()} | {s['level']}"
        for s in filled
    ]
    colors = [parse_color(s["color"]) for s in filled]

    choice = cycle_choice(labels + ["Cancel"], colors + [(255, 255, 255)])
    if choice == len(filled) + 1:
        return

    target = filled[choice - 1]
    confirm = cycle_choice(
        [f"Delete '{target['name']}'? This cannot be undone.", "Cancel"]
    )
    if confirm == 1:
        delete_slot(target["slot"])
        clear()
        print(f"Slot {target['slot']} deleted.")
        wait_keypress()


def start_menu():
    print("-----------------------------------------")
    print("             Dungeon Crawler")
    print("-----------------------------------------")
    wait_keypress_silent()
    print()
    print()

    while True:
        options = ["Play Game", "Quit"]
        choice = cycle_choice(options)
        selected = options[choice - 1]

        if selected == "Play Game":
            show_slot_menu()
        elif selected == "Quit":
            write_slow(
                "You climb the steps, leaving the dungeon behind...", 100, 200, 200, 255
            )
            exit(0)
