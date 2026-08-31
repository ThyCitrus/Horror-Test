import sys
import pygame

from save_utils import list_slots, load_json, save_json, slot_path, delete_slot

TEXT_WHITE = (230, 230, 230)
TEXT_DIM = (120, 120, 120)
PANEL_BG = (15, 15, 15)
PANEL_DIVIDER = (80, 80, 80)

ENABLE_MOUSE_NAVIGATION = True

COLOR_PALETTE = [
    (220, 60, 60),
    (255, 140, 0),
    (230, 210, 80),
    (140, 220, 60),
    (60, 200, 100),
    (60, 220, 220),
    (80, 200, 255),
    (100, 100, 255),
    (160, 60, 220),
    (220, 80, 160),
    (255, 90, 200),
    (200, 130, 60),
    (255, 255, 255),
    (200, 200, 200),
    (150, 90, 220),
    (90, 160, 90),
]
COLOR_GRID_COLS = 4


# --- Virtual Terminal UI ---
class TerminalUI:
    def __init__(self, font, bold_font, on_slot_hover_callback):
        self.font = font
        self.bold_font = bold_font
        self.on_slot_hover_callback = on_slot_hover_callback
        self.transient_message = None

        self.state = "START"
        self.selected_index = 0
        self.option_rects = []

        self.logs = [
            "-----------------------------------------",
            "             Dungeon Crawler",
            "-----------------------------------------",
            "Press Enter/Arrow keys to navigate...",
        ]
        self.options = []
        self.option_colors = []

        self.creation_slot = None
        self.creation_name = ""
        self.creation_color = "255 255 255"
        self.active_character = None

        self.color_grid_index = 0
        self.color_grid_cols = COLOR_GRID_COLS
        self.color_rects = []
        self._sync_color_from_index()

        self.load_start_menu()

    def add_log(self, text, color=TEXT_WHITE):
        self.logs.append((text, color))
        if len(self.logs) > 22:
            self.logs.pop(0)

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

    def load_start_menu(self):
        self.state = "START"
        self.set_options(["Play Game", "Quit"])

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
            self.add_log("No saves available to delete.", (255, 80, 80))
            self.load_slot_menu()
            return

        labels = [f"Slot {s['slot']} — {s['name']}" for s in slots] + ["Cancel"]
        colors = [tuple(map(int, s["color"].split())) for s in slots] + [TEXT_WHITE]
        self.set_options(labels, colors)

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
            "level": 1,
            "hp": 100,
            "max_hp": 100,
            "gold": 0,
            "items": [],
        }
        save_json(character, slot_path(self.creation_slot))
        self.active_character = character
        self.enter_playing_state(f"Character {character['name']} created!")

    def handle_input(self, event):
        # 1. Isolating Text Input Phase
        if self.state == "NAME_INPUT":
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_RETURN:
                    if self.creation_name.strip():
                        self.state = "CONFIRM_NAME"
                        self.set_options(["Confirm", "Rename"])
                    else:
                        self.add_log("Name cannot be empty!", (255, 80, 80))
                elif event.key == pygame.K_BACKSPACE:
                    self.creation_name = self.creation_name[:-1]
                elif event.unicode.isprintable() and len(self.creation_name) < 16:
                    self.creation_name += event.unicode
            return

        # 2. Color grid has its own nav — separate from the vertical option list
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

        # 3. General Key Navigation
        if event.type == pygame.KEYDOWN:
            if (
                self.state in ("PLAYING", "INVENTORY", "MAP")
                and event.key == pygame.K_ESCAPE
            ):
                save_json(
                    self.active_character, slot_path(self.active_character["slot"])
                )
                self.active_character = None
                self.load_slot_menu()
                self.set_transient("Game saved.", (80, 160, 255))
            elif event.key == pygame.K_UP:
                self.selected_index = (self.selected_index - 1) % len(self.options)
                self.notify_hover()
            elif event.key == pygame.K_DOWN:
                self.selected_index = (self.selected_index + 1) % len(self.options)
                self.notify_hover()
            elif event.key == pygame.K_RETURN:
                self.execute_selection()

        # 4. Mouse Navigation
        elif ENABLE_MOUSE_NAVIGATION and event.type == pygame.MOUSEMOTION:
            mx, my = event.pos
            for i, rect in enumerate(self.option_rects):
                if rect.collidepoint(mx, my) and self.selected_index != i:
                    self.selected_index = i
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
                self.load_slot_menu()
            elif sel == 1:
                pygame.quit()
                sys.exit()

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
                    self.active_character = load_json(chosen["path"])
                    self.enter_playing_state(f"Loaded {self.active_character['name']}!")
                else:
                    self.creation_name = ""
                    self.state = "NAME_INPUT"
                    self.add_log("Type character name and press Enter:")

        elif self.state == "DELETE_SELECT":
            filled = [s for s in list_slots() if s["filled"]]
            if sel == len(filled):
                self.load_slot_menu()
            else:
                target = filled[sel]
                delete_slot(target["slot"])
                self.add_log(f"Slot {target['slot']} deleted.", (255, 100, 100))
                self.load_slot_menu()

        elif self.state == "PLAYING":
            if sel == 0:
                self.state = "INVENTORY"
                self.set_options(["Back"])
            elif sel == 1:
                self.state = "MAP"
                self.set_options(["Back"])

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

    def render(self, surface, rect, dungeon=None, discovered=None, player_pos=None):
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

        y += 10
        if self.state == "NAME_INPUT":
            prompt = self.font.render(
                f"> Name: {self.creation_name}_", True, (80, 200, 255)
            )
            surface.blit(prompt, (rect.x + 20, y))

        elif self.state == "CONFIRM_NAME":
            name_lbl = self.font.render(
                f"Name: {self.creation_name}", True, (80, 200, 255)
            )
            surface.blit(name_lbl, (rect.x + 20, y))
            y += line_height + 5

        elif self.state == "COLOR_SELECT":
            hint_lbl = self.font.render(
                f"Choose a color, {self.creation_name}:", True, TEXT_WHITE
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
                pygame.draw.rect(surface, (r, g, b), swatch_rect)
                if i == self.color_grid_index:
                    pygame.draw.rect(surface, (255, 255, 255), swatch_rect, 3)

            grid_rows = len(COLOR_PALETTE) // self.color_grid_cols
            y += grid_rows * (swatch_size + gap) + 10

            marker_preview = self.bold_font.render(
                " (Marker: v)", True, COLOR_PALETTE[self.color_grid_index]
            )
            surface.blit(marker_preview, (rect.x + 20, y))
            y += line_height + 5

            instr_lbl = self.font.render(
                "[Arrows] Move   [Enter/Click] Confirm", True, TEXT_DIM
            )
            surface.blit(instr_lbl, (rect.x + 20, y))
            y += line_height + 5

        elif self.state == "INVENTORY":
            items = self.active_character["items"] if self.active_character else []
            if not items:
                empty_lbl = self.font.render("(Empty)", True, TEXT_DIM)
                surface.blit(empty_lbl, (rect.x + 20, y))
                y += line_height
            else:
                for item_name in items:
                    item_lbl = self.font.render(f"- {item_name}", True, TEXT_WHITE)
                    surface.blit(item_lbl, (rect.x + 20, y))
                    y += line_height
            y += 10

        elif self.state == "MAP" and dungeon is not None and discovered is not None:
            self.render_minimap(
                surface,
                rect.x + 20,
                y,
                dungeon,
                discovered,
                player_pos,
                self.active_character
                and tuple(map(int, self.active_character["color"].split()))
                or (80, 200, 255),
            )
            y += 21 * 5 + 10

        self.option_rects.clear()
        if self.state not in ("NAME_INPUT", "COLOR_SELECT"):
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
            hint_lbl = self.font.render("[Esc] Save & Quit to Menu", True, TEXT_DIM)
            surface.blit(hint_lbl, (rect.x + 20, hud_y - line_height))

            c = self.active_character
            r, g, b = map(int, c["color"].split())

            name_lbl = self.bold_font.render(c["name"], True, (r, g, b))
            surface.blit(name_lbl, (rect.x + 20, hud_y))
            self.render_hud_line(surface, rect.x + 20, hud_y + line_height, c)

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

        drain = character["gold"] // 10
        g_r = 255
        g_g = max(150, 255 - max(0, drain - 255))
        g_b = max(0, 255 - drain)
        segments.append((f"Gold: {character['gold']}", (g_r, g_g, g_b)))

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

    def enter_playing_state(self, message):
        self.return_to_playing()
        self.set_transient(message, (80, 255, 80))

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
