import os
import sys


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def flush_input() -> None:
    """Discard any buffered keystrokes so they don't bleed into the next prompt."""
    if os.name == "nt":
        import msvcrt

        while msvcrt.kbhit():
            msvcrt.getch()
    else:
        import termios

        termios.tcflush(sys.stdin, termios.TCIFLUSH)


if os.name == "nt":
    import msvcrt

    def get_key():
        return msvcrt.getch().decode("utf-8", errors="ignore")

else:
    import termios
    import tty

    def get_key():
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return ch


def wait_keypress():
    flush_input()
    print("Press any key to continue...")
    get_key()


def wait_keypress_silent():
    flush_input()
    get_key()


def wait_keypress_specific(key):
    flush_input()
    print(f"Press '{key}' to continue...")
    while True:
        if get_key().lower() == key.lower():
            break


def read_key_event() -> str:
    """
    Reads one input event and normalizes it to: "up", "down", "enter", "back",
    or the raw character for anything else.
    """
    if os.name == "nt":
        ch = msvcrt.getch()
        if ch in (b"\xe0", b"\x00"):
            ch2 = msvcrt.getch()
            if ch2 == b"H":
                return "up"
            if ch2 == b"P":
                return "down"
            return "other"
        if ch in (b"\r", b"\n"):
            return "enter"
        if ch == b"\x1b":
            return "back"
        decoded = ch.decode("utf-8", errors="ignore").lower()
        if decoded == "w":
            return "up"
        if decoded == "s":
            return "down"
        if decoded == " ":
            return "enter"
        return decoded
    else:
        import select

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                if select.select([sys.stdin], [], [], 0.01)[0]:
                    ch2 = sys.stdin.read(1)
                    if ch2 == "[" and select.select([sys.stdin], [], [], 0.01)[0]:
                        ch3 = sys.stdin.read(1)
                        if ch3 == "A":
                            return "up"
                        if ch3 == "B":
                            return "down"
                    return "other"
                return "back"
            if ch in ("\r", "\n"):
                return "enter"
            lowered = ch.lower()
            if lowered == "w":
                return "up"
            if lowered == "s":
                return "down"
            if ch == " ":
                return "enter"
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def cycle_choice(options: list[str], colors: list[tuple[int, int, int]] = None) -> int:
    flush_input()
    index = 0
    first_draw = True

    if colors is None:
        colors = [(255, 255, 255)] * len(options)

    while True:
        if not first_draw:
            print(f"\033[{len(options)}A", end="")
        first_draw = False

        for i, option in enumerate(options):
            r, g, b = colors[i]
            if i == index:
                print(f"\033[1m\033[38;2;{r};{g};{b}m > {option}\033[0m\033[K")
            else:
                print(f"\033[38;2;{r};{g};{b}m   {option}\033[0m\033[K")

        event = read_key_event()
        if event == "up":
            index = (index - 1) % len(options)
        elif event == "down":
            index = (index + 1) % len(options)
        elif event == "enter":
            return index + 1
