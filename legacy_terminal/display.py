import sys
import time


def set_terminal_title(title: str) -> None:
    import os

    if sys.platform == "win32":
        os.system(f"title {title}")
    else:
        print(f"\033]2;{title}\007", end="", flush=True)


def print_color(text: str, r: int, g: int, b: int) -> None:
    print(f"\033[38;2;{r};{g};{b}m{text}\033[0m")


def parse_color(color_str: str) -> tuple[int, int, int]:
    r, g, b = color_str.split()
    return int(r), int(g), int(b)


def write_slow(
    text: str, delay_ms: int = 50, r: int = 255, g: int = 255, b: int = 255
) -> None:
    for char in text:
        print(f"\033[38;2;{r};{g};{b}m{char}\033[0m", end="", flush=True)
        time.sleep(delay_ms / 1000.0)
    print()


def show_hud(character: dict) -> None:
    health_percent = character["hp"] / character["max_hp"]

    if health_percent > 0.75:
        h_r, h_g, h_b = 50, 255, 50
    elif health_percent > 0.50:
        h_r, h_g, h_b = 255, 255, 50
    elif health_percent > 0.25:
        h_r, h_g, h_b = 255, 165, 50
    else:
        h_r, h_g, h_b = 255, 50, 50

    parts = [
        f"\033[38;2;{h_r};{h_g};{h_b}mHP: {character['hp']}/{character['max_hp']}\033[0m"
    ]

    if character["max_mana"] > 0:
        parts.append(
            f"\033[38;2;255;0;255mMP: {character['mana']}/{character['max_mana']}\033[0m"
        )

    if character["max_stamina"] > 0:
        parts.append(
            f"\033[38;2;255;140;0mSP: {character['stamina']}/{character['max_stamina']}\033[0m"
        )

    drain = character["gold"] // 10
    g_r = 255
    g_g = max(150, 255 - max(0, drain - 255))
    g_b = max(0, 255 - drain)
    parts.append(f"\033[38;2;{g_r};{g_g};{g_b}mGold: {character['gold']}\033[0m")

    parts.append(f"\033[38;2;0;255;255mLv. {character['level']}\033[0m")

    print("  |  ".join(parts))
