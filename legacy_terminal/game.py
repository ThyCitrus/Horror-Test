from commands import clear, wait_keypress
from display import print_color, parse_color, show_hud


def game_screen(character: dict) -> None:
    clear()
    r, g, b = parse_color(character["color"])
    print_color(
        f"Welcome, {character['name']} the {character['class'].title()}!", r, g, b
    )
    show_hud(character)
    print()
    print("(Gameplay not implemented yet.)")
    wait_keypress()
