import sys
import math
import array
import random
import pygame

from save_utils import (
    add_character_event,
    add_notepad_entry,
    list_slots,
    load_json,
    normalize_character,
    save_json,
    slot_path,
    delete_slot,
)

TEXT_WHITE = (230, 230, 230)
TEXT_DIM = (120, 120, 120)
PANEL_BG = (15, 15, 15)
PANEL_DIVIDER = (80, 80, 80)

ENABLE_MOUSE_NAVIGATION = False

COLOR_PALETTE = [
    (220, 60, 60),
    (255, 140, 0),
    (230, 210, 80),
    (140, 220, 60),
    (40, 180, 120),
    (40, 210, 210),
    (60, 160, 255),
    (80, 80, 240),
    (170, 60, 230),
    (220, 70, 150),
    (255, 80, 190),
    (200, 130, 60),
    (255, 255, 255),
    (180, 180, 180),
    (100, 70, 210),
    (70, 140, 70),
]
COLOR_GRID_COLS = 4


class TerminalUI:
    def __init__(
        self,
        font,
        bold_font,
        on_slot_hover_callback,
        on_join_address=None,
        on_mp_color_confirm=None,
        on_multiplayer_quit=None,
        on_drop_item=None,
        on_drop_loot=None,
        on_shop_purchase=None,
        on_objective_action=None,
        on_equip_item=None,
        on_deposit_loot=None,
    ):
        self.font = font
        self.bold_font = bold_font
        self.on_slot_hover_callback = on_slot_hover_callback
        self.on_join_address = on_join_address
        self.on_mp_color_confirm = on_mp_color_confirm
        self.on_multiplayer_quit = on_multiplayer_quit
        self.transient_message = None

        self.state = "START"
        self.selected_index = 0
        self.option_rects = []

        self.logs = [
            "-----------------------------------------",
            "             THE ARCHIVE",
            "-----------------------------------------",
            "SYSTEM TERMINAL // READY",
        ]
        self.options = []
        self.option_colors = []
        self._hover_sound = None

        self.creation_slot = None
        self.creation_name = ""
        self.creation_color = "255 255 255"
        self.active_character = None

        self.color_grid_index = 0
        self.color_grid_cols = COLOR_GRID_COLS
        self.color_rects = []
        self._sync_color_from_index()

        # --- multiplayer state ---
        self.pending_mode = None  # None | "singleplayer" | "host" | "join"
        self.join_address = ""
        self.mp_creation_name = ""
        self.mp_creation_color = "255 255 255"
        self.mp_taken_names = []
        self.mp_taken_colors = []
        self.mp_color_grid_index = 0
        self.network_mode = False
        self.mp_hud_player = None  # {"name", "color", "alive"} for guests only
        self.hosting_info = None
        self.inventory_index = 0
        self.inventory_section = None
        self.current_inventory_loot = []
        self.current_inventory_items = []
        self.on_drop_item = on_drop_item
        self.on_drop_loot = on_drop_loot
        self.on_shop_purchase = on_shop_purchase
        self.on_objective_action = on_objective_action
        self.on_equip_item = on_equip_item
        self.on_deposit_loot = on_deposit_loot
        self.objective = None
        self.floor_number = 0
        self.shared_bytes = None
        self.objective_game = None
        self.signal_tracker = None
        self._objective_callback_called = False

        self.load_start_menu()

    def add_log(self, text, color=TEXT_WHITE):
        self.logs.append((text, color))
        if len(self.logs) > 22:
            self.logs.pop(0)

    def clear_logs(self):
        self.logs = []

    def set_options(self, options, colors=None):
        self.options = options
        self.option_colors = colors if colors else [TEXT_WHITE] * len(options)
        self.selected_index = 0
        self.notify_hover()

    def notify_hover(self):
        if self.state == "SLOT_SELECT":
            slots = list_slots()
            if self.selected_index < len(slots):
                self.on_slot_hover_callback(slots[self.selected_index])

    def _play_hover_sound(self):
        if self._hover_sound is None:
            mixer_settings = pygame.mixer.get_init()
            if mixer_settings:
                frequency, sample_format, channels = mixer_settings
                if sample_format == -16:
                    sample_count = max(1, int(frequency * 0.045))
                    samples = array.array("h")
                    for i in range(sample_count):
                        envelope = 1.0 - (i / sample_count)
                        value = int(
                            5000
                            * envelope
                            * math.sin(2 * math.pi * 440 * i / frequency)
                        )
                        samples.extend([value] * channels)
                    self._hover_sound = pygame.mixer.Sound(buffer=samples.tobytes())
        if self._hover_sound is not None:
            self._hover_sound.play()

    # --- menu navigation ---

    def load_start_menu(self):
        self.state = "START"
        self.pending_mode = None
        self.hosting_info = None  # new
        self.set_options(["Singleplayer", "Multiplayer", "Quit"])

    def load_multiplayer_menu(self):
        self.state = "MULTIPLAYER_MENU"
        self.set_options(["Host", "Join", "Back"])

    def load_slot_menu(self):
        self.state = "SLOT_SELECT"
        slots = list_slots()
        labels, colors = [], []

        for s in slots:
            if s["filled"]:
                labels.append(f"Slot {s['slot']} — {s['name']} | Lv.{s['level']}")
                r, g, b = map(int, s["color"].split())
                colors.append((r, g, b))
            else:
                labels.append(f"Slot {s['slot']} — Empty")
                colors.append(TEXT_DIM)

        labels.extend(["Delete Save", "Back"])
        colors.extend([TEXT_WHITE, TEXT_WHITE])
        self.set_options(labels, colors)

    def load_delete_menu(self):
        self.state = "DELETE_SELECT"
        slots = [s for s in list_slots() if s["filled"]]
        if not slots:
            self.set_transient(
                "No saves available to delete.", (255, 80, 80), duration_ms=1500
            )
            self.load_slot_menu()
            return

        labels = [f"Slot {s['slot']} — {s['name']}" for s in slots] + ["Cancel"]
        colors = [tuple(map(int, s["color"].split())) for s in slots] + [TEXT_WHITE]
        self.set_options(labels, colors)

    # --- singleplayer color grid (unchanged) ---

    def _move_color_selection(self, dcol, drow):
        rows = len(COLOR_PALETTE) // self.color_grid_cols
        row = self.color_grid_index // self.color_grid_cols
        col = self.color_grid_index % self.color_grid_cols
        col = (col + dcol) % self.color_grid_cols
        row = (row + drow) % rows
        self.color_grid_index = row * self.color_grid_cols + col
        self._sync_color_from_index()

    def _sync_color_from_index(self):
        r, g, b = COLOR_PALETTE[self.color_grid_index]
        self.creation_color = f"{r} {g} {b}"

    def confirm_color_selection(self):
        current_seed, current_pos = self.on_slot_hover_callback(None, get_current=True)
        character = {
            "slot": self.creation_slot,
            "name": self.creation_name,
            "color": self.creation_color,
            "seed": current_seed,
            "player_x": current_pos[0],
            "player_y": current_pos[1],
            "floor": 0 if current_seed == 0 else 1,
            "level": 1,
            "hp": 100,
            "max_hp": 100,
            "gold": 0,
            "bytes": 100,
            "shop_free_light_used": False,
            "items": [],
            "equipped_light": None,
            "light_on": False,
            "notepad": [],
            "events": [],
            "objective": None,
        }
        save_json(character, slot_path(self.creation_slot))
        self.active_character = character
        self.enter_playing_state(f"Character {character['name']} created!")

    # --- multiplayer color grid (skips taken swatches) ---

    def _mp_color_string(self, index):
        r, g, b = COLOR_PALETTE[index]
        return f"{r} {g} {b}"

    def _sync_mp_color_from_index(self):
        self.mp_creation_color = self._mp_color_string(self.mp_color_grid_index)

    def _first_free_color_index(self):
        for i in range(len(COLOR_PALETTE)):
            if self._mp_color_string(i) not in self.mp_taken_colors:
                return i
        return 0  # degenerate: everyone's taken every swatch

    def _move_mp_color_selection(self, dcol, drow):
        rows = len(COLOR_PALETTE) // self.color_grid_cols
        row = self.mp_color_grid_index // self.color_grid_cols
        col = self.mp_color_grid_index % self.color_grid_cols
        for _ in range(len(COLOR_PALETTE)):  # bounded so this can't spin forever
            col = (col + dcol) % self.color_grid_cols
            row = (row + drow) % rows
            idx = row * self.color_grid_cols + col
            if self._mp_color_string(idx) not in self.mp_taken_colors:
                self.mp_color_grid_index = idx
                self._sync_mp_color_from_index()
                return

    def _confirm_mp_color_selection(self):
        if self.on_mp_color_confirm:
            self.on_mp_color_confirm(
                self.mp_creation_name.strip(), self.mp_creation_color
            )
        self.state = "JOINING"
        self.set_options([])

    # --- transitions driven by main.py in response to network events ---

    def show_connecting(self):
        self.state = "CONNECTING"
        self.set_options([])
        self.set_transient("Connecting...", (80, 200, 255), duration_ms=60000)

    def connection_failed(self, reason):
        self.state = "ADDRESS_INPUT"
        self.set_transient(
            f"Connection failed: {reason}", (255, 80, 80), duration_ms=2500
        )

    def enter_mp_name_input(self, taken_names, taken_colors):
        self.mp_taken_names = taken_names
        self.mp_taken_colors = taken_colors
        self.mp_creation_name = ""
        self.state = "MP_NAME_INPUT"
        self.set_options([])
        self.set_transient(
            "Type character name and press Enter:", (255, 255, 255), duration_ms=1500
        )

    def mp_join_rejected(self, reason, taken_names, taken_colors):
        self.mp_taken_names = taken_names
        self.mp_taken_colors = taken_colors
        if reason in ("name_taken", "name_empty"):
            msg = (
                "Name already taken."
                if reason == "name_taken"
                else "Name cannot be empty!"
            )
            self.state = "MP_NAME_INPUT"
            self.set_options([])
            self.set_transient(msg, (255, 80, 80), duration_ms=2000)
        else:  # color_taken
            self.mp_color_grid_index = self._first_free_color_index()
            self._sync_mp_color_from_index()
            self.state = "MP_COLOR_SELECT"
            self.set_options([])
            self.set_transient(
                "Someone else just took that color.", (255, 80, 80), duration_ms=2000
            )

    def enter_multiplayer_playing(self, name, color):
        self.network_mode = True
        self.mp_hud_player = {
            "name": name,
            "color": color,
            "alive": True,
            "bytes": 0,
        }
        self.return_to_playing()
        self.set_transient(f"Connected as {name}!", (80, 255, 80), duration_ms=2000)

    def set_world_state(self, floor_number, objective=None, shared_bytes=None):
        """Update the small, render-only slice of authoritative world state."""
        self.floor_number = floor_number
        self.objective = objective
        self.shared_bytes = shared_bytes

    def add_system_event(self, text, lore=None):
        """Show a narrative event and optionally persist it on the active save."""
        self.clear_logs()
        self.add_log(f"[SYSTEM] {text}", (130, 220, 255))
        if self.active_character:
            add_character_event(self.active_character, text)
            if lore:
                add_notepad_entry(self.active_character, lore)
            save_json(
                self.active_character,
                slot_path(self.active_character["slot"]),
            )

    def open_objective_terminal(self, objective):
        """Start the objective's self-contained puzzle."""
        self.objective = objective
        self.state = "TERMINAL_GAME"
        self._objective_callback_called = False
        self._start_objective_game(objective)

    def _start_objective_game(self, objective):
        game = objective.get("game", "Arrow Sequence")
        rng = random.Random(objective.get("id", game))
        arrows = [pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT, pygame.K_RIGHT]
        if game == "Arrow Sequence":
            self.objective_game = {
                "kind": "arrows",
                "sequence": [rng.choice(arrows) for _ in range(6)],
                "index": 0,
            }
        elif game in ("Sine Wave Tuner", "Sine Wave Signal Tuner"):
            self.objective_game = {
                "kind": "sine",
                "values": [5, 5, 5],
                "targets": [rng.randint(3, 8) for _ in range(3)],
                "selected": 0,
            }
        elif game in ("Flow Puzzle", "Grid Fill/Flow Puzzle"):
            width = height = 4
            self.objective_game = {
                "kind": "grid",
                "width": width,
                "height": height,
                "cursor": [0, 0],
                "visited": {(0, 0)},
                "total": width * height,
            }
        elif game == "Number Calibration":
            self.objective_game = {
                "kind": "numbers",
                "values": [0, 0, 0],
                "targets": [rng.randint(2, 8) for _ in range(3)],
                "selected": 0,
            }
        elif game == "Memory Pattern / Simon":
            sequence = [rng.choice(arrows) for _ in range(5)]
            self.objective_game = {
                "kind": "simon",
                "sequence": sequence,
                "index": 0,
                "show_index": 0,
                "show_until": pygame.time.get_ticks() + 650,
                "phase": "show",
            }
        elif game == "Active Hold / Pong":
            self.objective_game = {
                "kind": "hold",
                "position": [0.5, 0.5],
                "target": [0.45 + rng.random() * 0.1, 0.45 + rng.random() * 0.1],
                "progress": 0.0,
                "elapsed": 0.0,
                "drift": [rng.choice((-1, 1)), rng.choice((-1, 1))],
            }
        else:
            self.objective_game = {"kind": "arrows", "sequence": arrows, "index": 0}
        self.set_options([])

    def is_modal_game(self):
        return self.state == "TERMINAL_GAME" and self.objective_game is not None

    def _finish_objective_game(self):
        if self._objective_callback_called:
            return
        self._objective_callback_called = True
        message = self.on_objective_action(0) if self.on_objective_action else None
        self.objective_game = None
        self.return_to_playing()
        if message:
            self.set_transient(message, (80, 255, 80), duration_ms=1800)

    def _cancel_objective_game(self):
        self.objective_game = None
        self.return_to_playing()
        self.set_transient("Puzzle cancelled; terminal disconnected.", (255, 180, 80), duration_ms=1400)

    def _objective_key(self, key):
        game = self.objective_game
        if not game or key not in (pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT, pygame.K_RIGHT):
            return
        kind = game["kind"]
        if kind == "arrows":
            if key == game["sequence"][game["index"]]:
                game["index"] += 1
                if game["index"] >= len(game["sequence"]):
                    self._finish_objective_game()
            else:
                game["index"] = 0
                self.set_transient("Sequence mismatch — start again.", (255, 100, 100), duration_ms=900)
        elif kind == "sine":
            if key == pygame.K_LEFT:
                game["selected"] = (game["selected"] - 1) % 3
            elif key == pygame.K_RIGHT:
                game["selected"] = (game["selected"] + 1) % 3
            elif key == pygame.K_UP:
                game["values"][game["selected"]] = min(10, game["values"][game["selected"]] + 1)
            elif key == pygame.K_DOWN:
                game["values"][game["selected"]] = max(0, game["values"][game["selected"]] - 1)
            if game["values"] == game["targets"]:
                self._finish_objective_game()
        elif kind == "grid":
            dx, dy = {
                pygame.K_UP: (0, -1),
                pygame.K_DOWN: (0, 1),
                pygame.K_LEFT: (-1, 0),
                pygame.K_RIGHT: (1, 0),
            }[key]
            nx, ny = game["cursor"][0] + dx, game["cursor"][1] + dy
            if not (0 <= nx < game["width"] and 0 <= ny < game["height"]):
                self.set_transient("That conduit is outside the grid.", (255, 180, 80), duration_ms=700)
            elif (nx, ny) in game["visited"]:
                self.set_transient("Flow cannot revisit a covered tile.", (255, 100, 100), duration_ms=900)
            else:
                game["cursor"] = [nx, ny]
                game["visited"].add((nx, ny))
                if len(game["visited"]) == game["total"]:
                    self._finish_objective_game()
        elif kind == "numbers":
            if key == pygame.K_LEFT:
                game["selected"] = (game["selected"] - 1) % 3
            elif key == pygame.K_RIGHT:
                game["selected"] = (game["selected"] + 1) % 3
            elif key == pygame.K_UP:
                game["values"][game["selected"]] = min(9, game["values"][game["selected"]] + 1)
            elif key == pygame.K_DOWN:
                game["values"][game["selected"]] = max(0, game["values"][game["selected"]] - 1)
            if game["values"] == game["targets"]:
                self._finish_objective_game()
        elif kind == "simon" and game["phase"] == "input":
            if key == game["sequence"][game["index"]]:
                game["index"] += 1
                if game["index"] >= len(game["sequence"]):
                    self._finish_objective_game()
            else:
                game["index"] = 0
                self.set_transient("Memory lost — repeat from the first tone.", (255, 100, 100), duration_ms=900)
        elif kind == "hold":
            push = {
                pygame.K_UP: (0, -0.13),
                pygame.K_DOWN: (0, 0.13),
                pygame.K_LEFT: (-0.13, 0),
                pygame.K_RIGHT: (0.13, 0),
            }[key]
            game["position"][0] = max(0.0, min(1.0, game["position"][0] + push[0]))
            game["position"][1] = max(0.0, min(1.0, game["position"][1] + push[1]))

    def update_objective_game(self, dt_ms):
        """Advance timed modal games and the non-modal roaming tracker."""
        if self.is_modal_game():
            game = self.objective_game
            if game["kind"] == "simon" and game["phase"] == "show":
                if pygame.time.get_ticks() >= game["show_until"]:
                    game["show_index"] += 1
                    if game["show_index"] >= len(game["sequence"]):
                        game["phase"] = "input"
                    else:
                        game["show_until"] = pygame.time.get_ticks() + 650
            elif game["kind"] == "hold":
                game["elapsed"] += dt_ms
                t = game["elapsed"] / 1000.0
                game["position"][0] = max(0.0, min(1.0, game["position"][0] + math.sin(t * 2.3) * dt_ms * 0.00004 * game["drift"][0]))
                game["position"][1] = max(0.0, min(1.0, game["position"][1] + math.cos(t * 1.9) * dt_ms * 0.00004 * game["drift"][1]))
                inside = all(abs(game["position"][i] - game["target"][i]) < 0.16 for i in (0, 1))
                game["progress"] = max(0.0, game["progress"] + (dt_ms if inside else -dt_ms * 1.5))
                if game["progress"] >= 7000:
                    self._finish_objective_game()
        elif self.signal_tracker:
            target = self.signal_tracker["target"]
            self.signal_tracker["last_distance"] = self._tracker_distance(self.signal_tracker["player"], target)

    @staticmethod
    def _tracker_distance(a, b):
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    def start_signal_tracker(self, objective, target, player_pos):
        self.objective = objective
        self._objective_callback_called = False
        self.signal_tracker = {"target": tuple(target), "player": tuple(player_pos), "last_distance": 999999}
        self.set_transient("HOTSPOT TRACKER ONLINE — follow hot/cold readings.", (80, 220, 255), duration_ms=1800)

    def update_signal_tracker(self, player_pos):
        if not self.signal_tracker:
            return
        self.signal_tracker["player"] = tuple(player_pos)
        distance = self._tracker_distance(player_pos, self.signal_tracker["target"])
        self.signal_tracker["last_distance"] = distance
        if distance == 0:
            self.signal_tracker = None
            self._objective_callback_called = False
            self._finish_objective_game()

    def cancel_signal_tracker(self):
        if self.signal_tracker:
            self.signal_tracker = None
            self.set_transient("Hotspot tracker disconnected.", (255, 180, 80), duration_ms=1200)

    def open_notepad(self):
        if self.network_mode:
            self.set_transient("Notepad is available on local character saves.", (255, 180, 80))
            return
        normalize_character(self.active_character or {})
        self.state = "NOTEPAD"
        self.set_options(["Back"])

    # --- input handling ---

    def handle_input(self, event):
        # 1. Passive states main.py will transition us out of — nothing to do
        if self.state in ("CONNECTING", "JOINING"):
            return

        # Modal objective games own all arrow keys.  The map movement loop must
        # never see these events while a terminal is connected.
        if self.is_modal_game():
            if event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    self._cancel_objective_game()
                else:
                    self._objective_key(event.key)
            return

        if self.signal_tracker and event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_ESCAPE, pygame.K_q):
                self.cancel_signal_tracker()
            return

        # 2. Text input phases
        if self.state == "NAME_INPUT":
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_RETURN:
                    if self.creation_name.strip():
                        self.state = "CONFIRM_NAME"
                        self.set_options(["Confirm", "Rename"])
                    else:
                        self.set_transient(
                            "Name cannot be empty!", (255, 80, 80), duration_ms=1500
                        )
                elif event.key == pygame.K_BACKSPACE:
                    self.creation_name = self.creation_name[:-1]
                elif event.unicode.isprintable() and len(self.creation_name) < 16:
                    self.creation_name += event.unicode
            return

        if self.state == "ADDRESS_INPUT":
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_RETURN:
                    if self.join_address.strip():
                        if self.on_join_address:
                            self.on_join_address(self.join_address.strip())
                    else:
                        self.set_transient(
                            "Address cannot be empty!", (255, 80, 80), duration_ms=1500
                        )
                elif event.key == pygame.K_BACKSPACE:
                    self.join_address = self.join_address[:-1]
                elif event.unicode.isprintable() and len(self.join_address) < 40:
                    self.join_address += event.unicode
            return

        if self.state == "MP_NAME_INPUT":
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_RETURN:
                    name = self.mp_creation_name.strip()
                    if not name:
                        self.set_transient(
                            "Name cannot be empty!", (255, 80, 80), duration_ms=1500
                        )
                    elif name in self.mp_taken_names:
                        self.set_transient(
                            "Name already taken.", (255, 80, 80), duration_ms=1500
                        )
                    else:
                        self.mp_color_grid_index = self._first_free_color_index()
                        self._sync_mp_color_from_index()
                        self.state = "MP_COLOR_SELECT"
                        self.set_options([])
                elif event.key == pygame.K_BACKSPACE:
                    self.mp_creation_name = self.mp_creation_name[:-1]
                elif event.unicode.isprintable() and len(self.mp_creation_name) < 16:
                    self.mp_creation_name += event.unicode
            return

        # 3. Color grids — own nav, separate from the vertical option list
        if self.state == "COLOR_SELECT":
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_LEFT:
                    self._move_color_selection(-1, 0)
                elif event.key == pygame.K_RIGHT:
                    self._move_color_selection(1, 0)
                elif event.key == pygame.K_UP:
                    self._move_color_selection(0, -1)
                elif event.key == pygame.K_DOWN:
                    self._move_color_selection(0, 1)
                elif event.key == pygame.K_RETURN:
                    self.confirm_color_selection()
            elif ENABLE_MOUSE_NAVIGATION and event.type == pygame.MOUSEMOTION:
                mx, my = event.pos
                for i, rect in enumerate(self.color_rects):
                    if rect.collidepoint(mx, my):
                        self.color_grid_index = i
                        self._sync_color_from_index()
            elif (
                ENABLE_MOUSE_NAVIGATION
                and event.type == pygame.MOUSEBUTTONDOWN
                and event.button == 1
            ):
                mx, my = event.pos
                for i, rect in enumerate(self.color_rects):
                    if rect.collidepoint(mx, my):
                        self.color_grid_index = i
                        self._sync_color_from_index()
                        self.confirm_color_selection()
            return

        if self.state == "MP_COLOR_SELECT":
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_LEFT:
                    self._move_mp_color_selection(-1, 0)
                elif event.key == pygame.K_RIGHT:
                    self._move_mp_color_selection(1, 0)
                elif event.key == pygame.K_UP:
                    self._move_mp_color_selection(0, -1)
                elif event.key == pygame.K_DOWN:
                    self._move_mp_color_selection(0, 1)
                elif event.key == pygame.K_RETURN:
                    self._confirm_mp_color_selection()
            elif ENABLE_MOUSE_NAVIGATION and event.type == pygame.MOUSEMOTION:
                mx, my = event.pos
                for i, rect in enumerate(self.color_rects):
                    if (
                        rect.collidepoint(mx, my)
                        and self._mp_color_string(i) not in self.mp_taken_colors
                    ):
                        self.mp_color_grid_index = i
                        self._sync_mp_color_from_index()
            elif (
                ENABLE_MOUSE_NAVIGATION
                and event.type == pygame.MOUSEBUTTONDOWN
                and event.button == 1
            ):
                mx, my = event.pos
                for i, rect in enumerate(self.color_rects):
                    if (
                        rect.collidepoint(mx, my)
                        and self._mp_color_string(i) not in self.mp_taken_colors
                    ):
                        self.mp_color_grid_index = i
                        self._sync_mp_color_from_index()
                        self._confirm_mp_color_selection()
            return

        if self.state == "INVENTORY":
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_LEFT:
                    if self.inventory_section is None:
                        self.return_to_playing()
                    else:
                        self.inventory_section = None
                        self.inventory_index = 0
                    return
                if self.inventory_section is None:
                    if event.key == pygame.K_UP:
                        self.inventory_index = (self.inventory_index - 1) % 3
                    elif event.key == pygame.K_DOWN:
                        self.inventory_index = (self.inventory_index + 1) % 3
                    elif event.key == pygame.K_RIGHT:
                        self.inventory_section = ("Tools", "Scrap", "Data")[
                            self.inventory_index
                        ]
                        self.inventory_index = 0
                    return
                source = (
                    self.current_inventory_items
                    if self.inventory_section == "Tools"
                    else self.current_inventory_loot
                )
                count = len(source or [])
                if count > 0:
                    if event.key == pygame.K_UP:
                        self.inventory_index = (self.inventory_index - 1) % count
                    elif event.key == pygame.K_DOWN:
                        self.inventory_index = (self.inventory_index + 1) % count
                    elif event.key == pygame.K_RIGHT:
                        self.inventory_section = None
                        self.inventory_index = 0
                    elif event.key == pygame.K_BACKSPACE:
                        if self.inventory_section == "Tools" and self.on_drop_item:
                            self.on_drop_item(self.inventory_index)
                        elif self.inventory_section == "Scrap" and self.on_drop_loot:
                            self.on_drop_loot(self.inventory_index)
                        self.inventory_index = 0
                    elif event.key == pygame.K_RETURN and self.inventory_section == "Tools":
                        item_id = (self.current_inventory_items or [])[self.inventory_index]
                        if self.on_equip_item:
                            self.set_transient(
                                self.on_equip_item(item_id),
                                (80, 255, 80),
                                duration_ms=1400,
                            )
            return

        if self.state == "TERMINAL_GAME":
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                self.return_to_playing()
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_RETURN:
                self.execute_selection()
            elif event.type == pygame.KEYDOWN and self.options:
                if event.key == pygame.K_UP:
                    self.selected_index = (self.selected_index - 1) % len(self.options)
                elif event.key == pygame.K_DOWN:
                    self.selected_index = (self.selected_index + 1) % len(self.options)
            return

        if self.state == "SHOP":
            if event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_RIGHT, pygame.K_RETURN):
                    self.execute_selection()
                elif event.key == pygame.K_LEFT:
                    self.return_to_playing()
                elif event.key == pygame.K_UP and self.options:
                    self.selected_index = (self.selected_index - 1) % len(self.options)
                elif event.key == pygame.K_DOWN and self.options:
                    self.selected_index = (self.selected_index + 1) % len(self.options)
            return

        if self.state == "NOTEPAD":
            if event.type == pygame.KEYDOWN and event.key == pygame.K_LEFT:
                self.return_to_playing()
            return

        # 4. General key navigation
        if event.type == pygame.KEYDOWN:
            if self.state == "PLAYING" and event.key == pygame.K_n:
                self.open_notepad()
                return
            if (
                self.state in ("PLAYING", "INVENTORY", "MAP", "NOTEPAD", "TERMINAL_GAME")
                and event.key == pygame.K_ESCAPE
            ):
                if self.network_mode:
                    if self.active_character:
                        save_json(
                            self.active_character,
                            slot_path(self.active_character["slot"]),
                        )
                    if self.on_multiplayer_quit:
                        self.on_multiplayer_quit()
                    self.network_mode = False
                    self.mp_hud_player = None
                    self.active_character = None
                    self.hosting_info = None  # new
                    self.load_start_menu()
                    self.set_transient(
                        "Disconnected.", (80, 160, 255), duration_ms=1500
                    )
                else:
                    save_json(
                        self.active_character, slot_path(self.active_character["slot"])
                    )
                    self.active_character = None
                    self.load_slot_menu()
                    self.set_transient("Game saved.", (80, 160, 255), duration_ms=1500)
            elif event.key == pygame.K_UP and self.options:
                self.selected_index = (self.selected_index - 1) % len(self.options)
                self._play_hover_sound()
                self.notify_hover()
            elif event.key == pygame.K_DOWN and self.options:
                self.selected_index = (self.selected_index + 1) % len(self.options)
                self._play_hover_sound()
                self.notify_hover()
            elif event.key == pygame.K_RIGHT and self.options:
                self.execute_selection()
            elif event.key == pygame.K_LEFT:
                if self.state in ("MAP", "NOTEPAD", "SHOP"):
                    self.return_to_playing()

        elif ENABLE_MOUSE_NAVIGATION and event.type == pygame.MOUSEMOTION:
            mx, my = event.pos
            for i, rect in enumerate(self.option_rects):
                if rect.collidepoint(mx, my) and self.selected_index != i:
                    self.selected_index = i
                    self._play_hover_sound()
                    self.notify_hover()

        elif (
            ENABLE_MOUSE_NAVIGATION
            and event.type == pygame.MOUSEBUTTONDOWN
            and event.button == 1
        ):
            mx, my = event.pos
            for i, rect in enumerate(self.option_rects):
                if rect.collidepoint(mx, my):
                    self.selected_index = i
                    self.execute_selection()

    def execute_selection(self):
        sel = self.selected_index

        if self.state == "START":
            if sel == 0:
                self.pending_mode = "singleplayer"
                self.load_slot_menu()
            elif sel == 1:
                self.load_multiplayer_menu()
            elif sel == 2:
                pygame.quit()
                sys.exit()

        elif self.state == "MULTIPLAYER_MENU":
            if sel == 0:
                self.pending_mode = "host"
                self.load_slot_menu()
            elif sel == 1:
                self.pending_mode = "join"
                self.join_address = ""
                self.state = "ADDRESS_INPUT"
                self.set_transient(
                    "Enter host address (ip:port) and press Enter:",
                    (255, 255, 255),
                    duration_ms=2000,
                )
            elif sel == 2:
                self.load_start_menu()

        elif self.state == "SLOT_SELECT":
            slots = list_slots()
            if sel == len(slots) + 1:
                self.load_start_menu()
            elif sel == len(slots):
                self.load_delete_menu()
            else:
                chosen = slots[sel]
                self.creation_slot = chosen["slot"]
                if chosen["filled"]:
                    self.active_character = normalize_character(load_json(chosen["path"]))
                    save_json(self.active_character, chosen["path"])
                    self.enter_playing_state(f"Loaded {self.active_character['name']}!")
                else:
                    self.creation_name = ""
                    self.state = "NAME_INPUT"
                    self.set_transient(
                        "Type character name and press Enter:",
                        (255, 255, 255),
                        duration_ms=1500,
                    )

        elif self.state == "DELETE_SELECT":
            filled = [s for s in list_slots() if s["filled"]]
            if sel == len(filled):
                self.load_slot_menu()
            else:
                target = filled[sel]
                delete_slot(target["slot"])
                self.set_transient(
                    f"Slot {target['slot']} deleted.", (255, 100, 100), duration_ms=1500
                )
                self.load_slot_menu()

        elif self.state == "PLAYING":
            if sel == 0:
                self.state = "INVENTORY"
                self.inventory_index = 0
                self.inventory_section = None
                self.set_options(["Tools", "Scrap", "Data"])
            elif sel == 1:
                items = self.active_character.get("items", []) if self.active_character else []
                if "Map" in items:
                    self.state = "MAP"
                    self.set_options([])
                else:
                    self.set_transient("Map not installed.", (255, 180, 80), duration_ms=1500)

        elif self.state == "SHOP":
            if sel == len(self.options) - 1:
                self.return_to_playing()
            elif sel == len(self.options) - 2 and self.on_deposit_loot:
                self.set_transient(
                    self.on_deposit_loot(),
                    (80, 255, 80),
                    duration_ms=1600,
                )
                self.open_shop(self.active_character, self.shared_bytes)
            elif self.on_shop_purchase:
                item_ids = ["Flashlight", "Lantern", "Map"]
                message = self.on_shop_purchase(item_ids[sel])
                color = (80, 255, 80) if "Purchased" in message else (255, 180, 80)
                self.set_transient(message, color, duration_ms=1800)
                self.open_shop(self.active_character)

        elif self.state == "TERMINAL_GAME":
            if sel == len(self.options) - 1:
                self.return_to_playing()
            elif self.on_objective_action:
                message = self.on_objective_action(sel)
                if message:
                    self.set_transient(message, (80, 255, 80), duration_ms=1800)

        elif self.state == "NOTEPAD":
            self.return_to_playing()

        elif self.state == "INVENTORY":
            self.return_to_playing()

        elif self.state == "MAP":
            self.return_to_playing()

        elif self.state == "CONFIRM_NAME":
            if sel == 0:
                self.color_grid_index = 0
                self._sync_color_from_index()
                self.state = "COLOR_SELECT"
                self.set_options([])
            else:
                self.creation_name = ""
                self.state = "NAME_INPUT"

    # --- rendering ---

    def render(
        self,
        surface,
        rect,
        dungeon=None,
        discovered=None,
        player_pos=None,
        other_players=None,
        local_items=None,
        local_loot=None,
        interact_prompt=None,
        objective=None,
        floor_number=None,
        shared_bytes=None,
    ):
        pygame.draw.rect(surface, PANEL_BG, rect)
        pygame.draw.line(surface, PANEL_DIVIDER, (rect.x, 0), (rect.x, rect.height), 2)

        line_height = self.font.get_linesize() + 4
        y = rect.y + 20

        for item in self.logs:
            text, color = item if isinstance(item, tuple) else (item, TEXT_WHITE)
            lbl = self.font.render(text, True, color)
            surface.blit(lbl, (rect.x + 20, y))
            y += line_height

        if self.transient_message is not None:
            text, color, expire_at = self.transient_message
            if pygame.time.get_ticks() < expire_at:
                lbl = self.font.render(text, True, color)
                surface.blit(lbl, (rect.x + 20, y))
                y += line_height
            else:
                self.transient_message = None

        if interact_prompt and self.state in ("PLAYING", "INVENTORY", "MAP"):
            prompt_lbl = self.font.render(interact_prompt, True, (255, 220, 120))
            surface.blit(prompt_lbl, (rect.x + 20, y))
            y += line_height

        if floor_number is not None and self.state in (
            "PLAYING",
            "INVENTORY",
            "MAP",
            "TERMINAL_GAME",
            "NOTEPAD",
        ):
            floor_lbl = self.bold_font.render(
                f"FACILITY FLOOR {floor_number}", True, (180, 220, 255)
            )
            surface.blit(floor_lbl, (rect.x + 20, y))
            y += line_height
            if shared_bytes is not None:
                bytes_lbl = self.font.render(
                    f"Shared bytes: {shared_bytes}", True, (255, 220, 100)
                )
                surface.blit(bytes_lbl, (rect.x + 20, y))
                y += line_height

        if objective and self.state in (
            "PLAYING",
            "INVENTORY",
            "MAP",
            "TERMINAL_GAME",
            "NOTEPAD",
        ):
            objective_text = objective.get("title", "Unknown objective")
            progress = objective.get("progress", 0)
            required = objective.get("required", 1)
            if objective.get("completed"):
                objective_text = "COMPLETE: " + objective_text
            obj_lbl = self.font.render(
                f"Objective: {objective_text} [{progress}/{required}]",
                True,
                (120, 255, 160) if objective.get("completed") else (255, 220, 120),
            )
            surface.blit(obj_lbl, (rect.x + 20, y))
            y += line_height

        if self.signal_tracker and self.state == "PLAYING":
            distance = self.signal_tracker.get("last_distance", 0)
            direction_x = self.signal_tracker["target"][0] - self.signal_tracker["player"][0]
            direction_y = self.signal_tracker["target"][1] - self.signal_tracker["player"][1]
            if distance <= 2:
                heat = "SCALDING"
                color = (255, 100, 80)
            elif distance <= 5:
                heat = "HOT"
                color = (255, 190, 80)
            elif distance <= 10:
                heat = "WARM"
                color = (255, 220, 120)
            else:
                heat = "COLD"
                color = (100, 180, 255)
            horizontal = "east" if direction_x > 0 else "west" if direction_x < 0 else ""
            vertical = "south" if direction_y > 0 else "north" if direction_y < 0 else ""
            toward = " and ".join(part for part in (vertical, horizontal) if part) or "here"
            tracker_lbl = self.bold_font.render(
                f"HOTSPOT: {heat} — move {toward} ({distance} sectors)", True, color
            )
            surface.blit(tracker_lbl, (rect.x + 20, y))
            y += line_height

        if self.network_mode and self.state == "PLAYING" and other_players:
            y += 5
            for cid, p in other_players.items():
                label = p["name"]
                if not p.get("connected", True):
                    label += " (disconnected)"
                if not p.get("alive", True):
                    color = TEXT_DIM
                else:
                    color = tuple(map(int, p["color"].split()))
                lbl = self.font.render(f"* {label}", True, color)
                surface.blit(lbl, (rect.x + 20, y))
                y += line_height
            y += 5

        y += 10
        if self.state == "NAME_INPUT":
            prompt = self.font.render(
                f"> Name: {self.creation_name}_", True, (80, 200, 255)
            )
            surface.blit(prompt, (rect.x + 20, y))

        elif self.state == "ADDRESS_INPUT":
            prompt = self.font.render(
                f"> Address: {self.join_address}_", True, (80, 200, 255)
            )
            surface.blit(prompt, (rect.x + 20, y))

        elif self.state == "MP_NAME_INPUT":
            prompt = self.font.render(
                f"> Name: {self.mp_creation_name}_", True, (80, 200, 255)
            )
            surface.blit(prompt, (rect.x + 20, y))

        elif self.state == "CONFIRM_NAME":
            name_lbl = self.font.render(
                f"Name: {self.creation_name}", True, (80, 200, 255)
            )
            surface.blit(name_lbl, (rect.x + 20, y))
            y += line_height + 5

        elif self.state in ("COLOR_SELECT", "MP_COLOR_SELECT"):
            is_mp = self.state == "MP_COLOR_SELECT"
            name_for_prompt = self.mp_creation_name if is_mp else self.creation_name
            taken_colors = self.mp_taken_colors if is_mp else []
            grid_index = self.mp_color_grid_index if is_mp else self.color_grid_index

            hint_lbl = self.font.render(
                f"Choose a color, {name_for_prompt}:", True, TEXT_WHITE
            )
            surface.blit(hint_lbl, (rect.x + 20, y))
            y += line_height + 10

            swatch_size = 32
            gap = 8
            self.color_rects.clear()
            for i, (r, g, b) in enumerate(COLOR_PALETTE):
                col = i % self.color_grid_cols
                row = i // self.color_grid_cols
                sx = rect.x + 20 + col * (swatch_size + gap)
                sy = y + row * (swatch_size + gap)
                swatch_rect = pygame.Rect(sx, sy, swatch_size, swatch_size)
                self.color_rects.append(swatch_rect)

                taken = f"{r} {g} {b}" in taken_colors
                draw_color = tuple(c // 3 for c in (r, g, b)) if taken else (r, g, b)
                pygame.draw.rect(surface, draw_color, swatch_rect)
                if not taken and i == grid_index:
                    pygame.draw.rect(surface, (255, 255, 255), swatch_rect, 3)

            grid_rows = len(COLOR_PALETTE) // self.color_grid_cols
            y += grid_rows * (swatch_size + gap) + 10

            preview_color = COLOR_PALETTE[grid_index]
            marker_preview = self.bold_font.render(" (Marker: v)", True, preview_color)
            surface.blit(marker_preview, (rect.x + 20, y))
            y += line_height + 5

            instr_lbl = self.font.render(
                "[Arrows] Move   [Enter/Click] Confirm", True, TEXT_DIM
            )
            surface.blit(instr_lbl, (rect.x + 20, y))
            y += line_height + 5

        elif self.state == "INVENTORY":
            surface.blit(
                self.bold_font.render("> Inventory", True, (120, 255, 160)),
                (rect.x + 20, y),
            )
            y += line_height
            items_list = local_items if local_items is not None else (
                self.active_character["items"] if self.active_character else []
            )
            self.current_inventory_items = list(items_list)
            self.current_inventory_loot = list(local_loot or [])
            if self.inventory_section is None:
                entries = ["Tools", "Scrap", "Data"]
            else:
                if self.inventory_section == "Tools":
                    entries = self.current_inventory_items
                elif self.inventory_section == "Scrap":
                    entries = [
                        f"{entry.get('name', 'Data')} — {entry.get('value', 0)} bytes"
                        for entry in self.current_inventory_loot
                    ]
                else:
                    entries = list((self.active_character or {}).get("notepad", []))
                if not entries:
                    entries = ["(Empty)"]
            for i, item_name in enumerate(entries):
                prefix = " > " if i == self.inventory_index else "   "
                active_font = self.bold_font if i == self.inventory_index else self.font
                surface.blit(
                    active_font.render(f"{prefix}{item_name}", True, TEXT_WHITE),
                    (rect.x + 20, y),
                )
                y += line_height
            y += 5
            hint = "[Up/Down] Select   [Left] Back"
            if self.inventory_section == "Tools":
                hint += "   [Enter] Equip   [Backspace] Drop"
            elif self.inventory_section == "Scrap":
                hint += "   [Backspace] Drop"
            surface.blit(self.font.render(hint, True, TEXT_DIM), (rect.x + 20, y))
            y += 10

        elif self.state == "TERMINAL_GAME":
            self._render_objective_game(surface, rect, y, line_height)

        elif self.state == "NOTEPAD":
            entries = (self.active_character or {}).get("notepad", [])
            if not entries:
                entries = ["No lore recovered."]
            for entry in entries[-12:]:
                lbl = self.font.render(f"- {entry}", True, TEXT_WHITE)
                surface.blit(lbl, (rect.x + 20, y))
                y += line_height
            y += 5
            hint = self.font.render("[Left] Back", True, TEXT_DIM)
            surface.blit(hint, (rect.x + 20, y))
            y += line_height

        elif self.state == "MAP" and dungeon is not None and discovered is not None:
            surface.blit(
                self.bold_font.render("> Map", True, (120, 255, 160)),
                (rect.x + 20, y),
            )
            y += line_height
            hud_color = (80, 200, 255)
            if self.active_character:
                hud_color = tuple(map(int, self.active_character["color"].split()))
            elif self.mp_hud_player:
                hud_color = tuple(map(int, self.mp_hud_player["color"].split()))
            self.render_minimap(
                surface, rect.x + 20, y, dungeon, discovered, player_pos, hud_color
            )
            y += 21 * 5 + 10

        self.option_rects.clear()
        if self.state not in (
            "NAME_INPUT",
            "COLOR_SELECT",
            "ADDRESS_INPUT",
            "MP_NAME_INPUT",
            "MP_COLOR_SELECT",
            "CONNECTING",
            "JOINING",
            "INVENTORY",
            "TERMINAL_GAME",
            "NOTEPAD",
        ):
            for i, opt in enumerate(self.options):
                color = self.option_colors[i]
                is_selected = i == self.selected_index
                prefix = " > " if is_selected else "   "

                active_font = self.bold_font if is_selected else self.font
                lbl = active_font.render(f"{prefix}{opt}", True, color)
                lbl_rect = lbl.get_rect(topleft=(rect.x + 20, y))
                self.option_rects.append(lbl_rect)

                surface.blit(lbl, lbl_rect)
                y += line_height

        if self.active_character:
            hud_y = rect.height - 80
            # if self.hosting_info:
            # code_lbl = self.font.render(
            #    f"Join code: {self.hosting_info}", True, TEXT_DIM
            # )
            # surface.blit(code_lbl, (rect.x + 20, hud_y - line_height * 2))

            if self.state == "SHOP":
                hint_lbl = self.font.render("[Esc] Save & Quit to Menu", True, TEXT_DIM)
                surface.blit(hint_lbl, (rect.x + 20, hud_y - line_height))

            c = self.active_character
            r, g, b = map(int, c["color"].split())

            name_lbl = self.bold_font.render(c["name"], True, (r, g, b))
            surface.blit(name_lbl, (rect.x + 20, hud_y))
            self.render_hud_line(surface, rect.x + 20, hud_y + line_height, c)

        elif self.mp_hud_player:
            hud_y = rect.height - 80
            hint_lbl = self.font.render("[Esc] Disconnect", True, TEXT_DIM)
            surface.blit(hint_lbl, (rect.x + 20, hud_y - line_height))

            p = self.mp_hud_player
            r, g, b = map(int, p["color"].split())
            name_lbl = self.bold_font.render(p["name"], True, (r, g, b))
            surface.blit(name_lbl, (rect.x + 20, hud_y))
            bytes_lbl = self.font.render(
                f"Shared bytes: {self.shared_bytes or 0}", True, (255, 220, 100)
            )
            surface.blit(bytes_lbl, (rect.x + 20, hud_y + line_height))

    def render_hud_line(self, surface, x, y, character):
        health_percent = character["hp"] / character["max_hp"]

        if health_percent > 0.75:
            h_color = (50, 255, 50)
        elif health_percent > 0.50:
            h_color = (255, 255, 50)
        elif health_percent > 0.25:
            h_color = (255, 165, 50)
        else:
            h_color = (255, 50, 50)

        segments = [(f"HP: {character['hp']}/{character['max_hp']}", h_color)]

        currency = character.get("bytes", character.get("gold", 0))
        drain = currency // 10
        g_r = 255
        g_g = max(150, 255 - max(0, drain - 255))
        g_b = max(0, 255 - drain)
        segments.append((f"Bytes: {currency}", (g_r, g_g, g_b)))

        segments.append((f"Lv. {character['level']}", (0, 255, 255)))

        cursor_x = x
        for text, color in segments:
            lbl = self.font.render(text, True, color)
            surface.blit(lbl, (cursor_x, y))
            cursor_x += lbl.get_width()

            sep = self.font.render("  |  ", True, TEXT_DIM)
            surface.blit(sep, (cursor_x, y))
            cursor_x += sep.get_width()

    def set_transient(self, text, color=TEXT_WHITE, duration_ms=1000):
        self.transient_message = (text, color, pygame.time.get_ticks() + duration_ms)

    def return_to_playing(self):
        self.state = "PLAYING"
        self.set_options(["Inventory", "Map"])

    def open_shop(self, character=None, shared_bytes=None, on_deposit=None):
        self.state = "SHOP"
        if on_deposit is not None:
            self.on_deposit_loot = on_deposit
        free_light = character is not None and not character.get(
            "shop_free_light_used", False
        )
        light_price = "FREE (first light)" if free_light else "100 bytes"
        if shared_bytes is not None:
            light_price = "100 bytes (shared)"
        self.set_options(
            [
                f"Flashlight — {light_price}",
                f"Lantern — {light_price}",
                "Map — 100 bytes",
                "Deposit floor loot",
                "Leave terminal",
            ]
        )

    def set_hosting_info(self, info):
        self.hosting_info = info

    def enter_playing_state(self, message):
        self.return_to_playing()
        self.set_transient(message, (80, 255, 80))

    def _render_objective_game(self, surface, rect, y, line_height):
        game = self.objective_game or {}
        kind = game.get("kind")
        title = (self.objective or {}).get("game", "OBJECTIVE PUZZLE")
        surface.blit(self.bold_font.render(title.upper(), True, (80, 220, 255)), (rect.x + 20, y))
        y += line_height + 8
        arrow_names = {
            pygame.K_UP: "↑",
            pygame.K_DOWN: "↓",
            pygame.K_LEFT: "←",
            pygame.K_RIGHT: "→",
        }
        if kind == "arrows":
            sequence = " ".join(arrow_names[key] for key in game["sequence"])
            entered = " ".join(arrow_names[key] for key in game["sequence"][:game["index"]])
            surface.blit(self.font.render("Match this sequence:", True, TEXT_WHITE), (rect.x + 20, y))
            y += line_height
            surface.blit(self.bold_font.render(sequence, True, (255, 220, 100)), (rect.x + 35, y))
            y += line_height
            surface.blit(self.font.render(f"Input: {entered or '(none)'}", True, (120, 255, 160)), (rect.x + 35, y))
        elif kind == "sine":
            labels = ("Amplitude", "Wavelength", "Frequency")
            surface.blit(self.font.render("Tune every control to its target.", True, TEXT_WHITE), (rect.x + 20, y))
            y += line_height
            for i, label in enumerate(labels):
                color = (255, 220, 100) if i == game["selected"] else TEXT_WHITE
                surface.blit(self.font.render(f"{'>' if i == game['selected'] else ' '} {label:<11} {game['values'][i]:2d} / {game['targets'][i]:2d}", True, color), (rect.x + 30, y))
                y += line_height
        elif kind == "grid":
            surface.blit(self.font.render("Route the flow through every tile exactly once.", True, TEXT_WHITE), (rect.x + 20, y))
            y += line_height
            for gy in range(game["height"]):
                row = []
                for gx in range(game["width"]):
                    pos = (gx, gy)
                    if pos == tuple(game["cursor"]):
                        cell = "[@]"
                    elif pos in game["visited"]:
                        cell = "[·]"
                    else:
                        cell = "[ ]"
                    row.append(cell)
                surface.blit(self.bold_font.render(" ".join(row), True, (120, 255, 160)), (rect.x + 35, y))
                y += line_height
            surface.blit(self.font.render(f"Covered {len(game['visited'])}/{game['total']} tiles", True, TEXT_DIM), (rect.x + 35, y))
        elif kind == "numbers":
            surface.blit(self.font.render("Select a column, then calibrate its value.", True, TEXT_WHITE), (rect.x + 20, y))
            y += line_height
            for i, (value, target) in enumerate(zip(game["values"], game["targets"])):
                color = (255, 220, 100) if i == game["selected"] else TEXT_WHITE
                surface.blit(self.font.render(f"{'>' if i == game['selected'] else ' '} Dial {i + 1}: {value} / {target}", True, color), (rect.x + 35, y))
                y += line_height
        elif kind == "simon":
            if game["phase"] == "show":
                shown = arrow_names[game["sequence"][game["show_index"]]]
                surface.blit(self.bold_font.render(f"MEMORIZE: {shown}", True, (255, 220, 100)), (rect.x + 30, y))
            else:
                surface.blit(self.font.render("Repeat the pattern with the arrow keys.", True, TEXT_WHITE), (rect.x + 20, y))
                y += line_height
                surface.blit(self.font.render(f"Input: {game['index']}/{len(game['sequence'])}", True, TEXT_DIM), (rect.x + 35, y))
        elif kind == "hold":
            surface.blit(self.font.render("Tap arrows to hold the signal inside the target.", True, TEXT_WHITE), (rect.x + 20, y))
            y += line_height
            box = pygame.Rect(rect.x + 35, y, 280, 150)
            pygame.draw.rect(surface, (25, 25, 35), box)
            target_x = box.x + int(game["target"][0] * box.width)
            target_y = box.y + int(game["target"][1] * box.height)
            pygame.draw.circle(surface, (80, 220, 120), (target_x, target_y), 24, 2)
            dot_x = box.x + int(game["position"][0] * box.width)
            dot_y = box.y + int(game["position"][1] * box.height)
            pygame.draw.circle(surface, (255, 220, 100), (dot_x, dot_y), 8)
            y += box.height + 8
            surface.blit(self.font.render(f"Hold progress: {min(100, int(game['progress'] / 70))}%", True, TEXT_WHITE), (rect.x + 35, y))
        y += line_height + 8
        surface.blit(self.font.render("[Arrow keys] Play    [Esc/Q] Disconnect", True, TEXT_DIM), (rect.x + 20, y))

    def render_minimap(
        self,
        surface,
        x,
        y,
        dungeon,
        discovered,
        player_pos,
        player_color,
        radius=10,
        cell=5,
    ):
        if player_pos is None:
            return
        px, py = player_pos

        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                wx, wy = px + dx, py + dy
                if (wx, wy) not in discovered:
                    continue

                char = dungeon.get((wx, wy))
                if char is None:
                    continue

                color = (100, 100, 100) if char == "#" else (60, 60, 60)
                if (wx, wy) == (px, py):
                    color = player_color

                rx = x + (dx + radius) * cell
                ry = y + (dy + radius) * cell
                pygame.draw.rect(surface, color, (rx, ry, cell - 1, cell - 1))
