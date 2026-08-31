import random
import pygame

from dungeon_gen import (
    generate_dungeon,
    WALL,
    FLOOR,
    seed_rng,
    compute_visible_tiles,
    reveal_boundary_walls,
    get_fog_brightness,
    find_adjacent_spawn,
)
from enemies import Enemy, ENEMY_TYPES
from terminal_ui import TerminalUI, TEXT_WHITE, TEXT_DIM
from save_utils import load_json, save_json, slot_path

WINDOW_WIDTH, WINDOW_HEIGHT = 1200, 660
VIEWPORT_TILES_X, VIEWPORT_TILES_Y = 15, 11
FONT_NAME = "consolas"

WALL_COLOR = (150, 150, 150)
FLOOR_COLOR = (60, 60, 60)

DIRECTIONS = {"N": (0, -1), "S": (0, 1), "E": (1, 0), "W": (-1, 0)}
DIRECTION_KEYS = {
    pygame.K_w: ((0, -1), "^"),
    pygame.K_s: ((0, 1), "v"),
    pygame.K_a: ((-1, 0), "<"),
    pygame.K_d: ((1, 0), ">"),
}


def get_stretch_factor(player_x, player_y, wall_x, wall_y, max_range=4):
    dist = ((wall_x - player_x) ** 2 + (wall_y - player_y) ** 2) ** 0.5
    if dist <= 0 or dist > max_range:
        return None
    return 1.0 - (dist - 1) / max_range


def main():
    pygame.init()
    screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.RESIZABLE)
    pygame.display.set_caption("Dungeon Crawler")
    clock = pygame.time.Clock()

    stretch_font_cache = {}
    font = pygame.font.SysFont(FONT_NAME, 18)
    bold_font = pygame.font.SysFont(FONT_NAME, 18, bold=True)
    pause_font = pygame.font.SysFont(FONT_NAME, 48, bold=True)

    active_seed = seed_rng.randint(0, 999999)
    dungeon = generate_dungeon(max_structures=15, seed=active_seed)
    floor_tiles = [pos for pos, char in dungeon.items() if char == FLOOR]
    player_x, player_y = floor_tiles[0] if floor_tiles else (0, 0)
    player_color = (80, 200, 255)
    enemies = []

    def handle_slot_hover(slot_info, get_current=False):
        nonlocal active_seed, dungeon, player_x, player_y, player_color, enemies
        if get_current:
            return active_seed, (player_x, player_y)

        if slot_info is None:
            return

        if slot_info["filled"]:
            char_data = load_json(slot_info["path"])
            active_seed = char_data.get("seed", seed_rng.randint(0, 999999))
            dungeon = generate_dungeon(max_structures=15, seed=active_seed)
            player_x = char_data.get("player_x", floor_tiles[0][0])
            player_y = char_data.get("player_y", floor_tiles[0][1])
            player_color = tuple(map(int, char_data["color"].split()))
        else:
            active_seed = seed_rng.randint(0, 999999)
            dungeon = generate_dungeon(max_structures=15, seed=active_seed)
            preview_floors = [pos for pos, char in dungeon.items() if char == FLOOR]
            player_x, player_y = preview_floors[0] if preview_floors else (0, 0)
            player_color = (120, 120, 120)

        ex, ey = find_adjacent_spawn(dungeon, player_x, player_y)
        enemies = []

    terminal = TerminalUI(font, bold_font, handle_slot_hover)

    visual_x, visual_y = float(player_x), float(player_y)
    discovered = set()
    player_facing = "v"

    pending_moves = {}
    time_since_last_move = 0
    last_diagonal_axis = None

    running = True
    while running:
        dt = clock.tick(60)
        time_since_last_move += dt
        now = pygame.time.get_ticks()

        if terminal.state == "PLAYING" and terminal.active_character:
            player_color = tuple(map(int, terminal.active_character["color"].split()))
        elif terminal.state in ("COLOR_SELECT", "CLASS_SELECT"):
            player_color = tuple(map(int, terminal.creation_color.split()))

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif terminal.state == "NAME_INPUT":
                terminal.handle_input(event)
            elif event.type == pygame.KEYDOWN and event.key in DIRECTION_KEYS:
                pending_moves[event.key] = now
            elif event.type == pygame.KEYUP and event.key in pending_moves:
                del pending_moves[event.key]
            else:
                terminal.handle_input(event)

        if (
            terminal.active_character
            and terminal.state != "NAME_INPUT"
            and time_since_last_move >= 150
        ):
            keys = pygame.key.get_pressed()
            candidates = [
                (pending_moves.get(k, now), k)
                for k in DIRECTION_KEYS
                if keys[k] or (k in pending_moves and (now - pending_moves[k]) <= 100)
            ]

            if candidates:
                held_keys = [k for _, k in candidates if keys[k]]

                horiz = next(
                    (k for k in held_keys if DIRECTION_KEYS[k][0][0] != 0), None
                )
                vert = next(
                    (k for k in held_keys if DIRECTION_KEYS[k][0][1] != 0), None
                )

                if horiz is not None and vert is not None:
                    if last_diagonal_axis == "x":
                        chosen_key = vert
                        last_diagonal_axis = "y"
                    else:
                        chosen_key = horiz
                        last_diagonal_axis = "x"
                else:
                    candidates.sort(reverse=True)
                    _, chosen_key = candidates[0]
                    last_diagonal_axis = None

                (dx, dy), glyph = DIRECTION_KEYS[chosen_key]
                player_facing = glyph
                target_x, target_y = player_x + dx, player_y + dy
                blocked_by_enemy = any(
                    (e.x, e.y) == (target_x, target_y) for e in enemies
                )
                if (
                    dungeon.get((target_x, target_y), WALL) != WALL
                    and not blocked_by_enemy
                ):
                    player_x, player_y = target_x, target_y

                    if terminal.active_character:
                        terminal.active_character["player_x"] = player_x
                        terminal.active_character["player_y"] = player_y
                        save_json(
                            terminal.active_character,
                            slot_path(terminal.active_character["slot"]),
                        )

                time_since_last_move = 0

        visual_x += (player_x - visual_x) * min(1.0, dt * 0.008)
        visual_y += (player_y - visual_y) * min(1.0, dt * 0.008)
        visible_tiles = compute_visible_tiles(dungeon, player_x, player_y, radius=10)
        visible_tiles = reveal_boundary_walls(dungeon, visible_tiles)
        discovered.update(visible_tiles)
        for enemy in enemies:
            enemy.update(dt, dungeon, player_x, player_y, WALL)

        screen.fill((0, 0, 0))
        panel_width = screen.get_width() // 2
        map_width = screen.get_width() - panel_width

        tile_size = max(
            min(map_width // VIEWPORT_TILES_X, screen.get_height() // VIEWPORT_TILES_Y),
            1,
        )
        map_font = pygame.font.SysFont(FONT_NAME, int(tile_size * 0.9))

        cell_spacing_x, cell_spacing_y = tile_size * 0.6, tile_size * 0.9
        offset_x = (map_width - (VIEWPORT_TILES_X * cell_spacing_x)) / 2
        offset_y = (screen.get_height() - (VIEWPORT_TILES_Y * cell_spacing_y)) / 2

        camera_start_x = visual_x - VIEWPORT_TILES_X / 2
        camera_start_y = visual_y - VIEWPORT_TILES_Y / 2

        for wx, wy in visible_tiles:
            if wx == player_x and wy == player_y:
                continue
            char = dungeon.get((wx, wy), WALL)

            stretch = (
                get_stretch_factor(player_x, player_y, wx, wy, max_range=10)
                if char == WALL
                else None
            )
            brightness = get_fog_brightness(player_x, player_y, wx, wy)

            if stretch is not None:
                dx, dy = wx - player_x, wy - player_y
                dist = (dx * dx + dy * dy) ** 0.5
                dir_x, dir_y = dx / dist, dy / dist

                base_color = tuple(int(c * brightness) for c in WALL_COLOR)
                base_cx = offset_x + (wx - camera_start_x) * cell_spacing_x
                base_cy = offset_y + (wy - camera_start_y) * cell_spacing_y

                stack_count = 1 + int(stretch * 9)
                for i in range(stack_count):
                    grow = 1.0 + (i / stack_count) * stretch * 3.0
                    font_size = int(tile_size * 0.9 * grow)
                    stack_font = stretch_font_cache.get(font_size)
                    if stack_font is None:
                        stack_font = pygame.font.SysFont(FONT_NAME, font_size)
                        stretch_font_cache[font_size] = stack_font
                    glyph_surf = stack_font.render(WALL, True, base_color)

                    push = i * cell_spacing_x * 0.6
                    gx = base_cx + dir_x * push
                    gy = base_cy + dir_y * push

                    rect = glyph_surf.get_rect(
                        center=(gx + cell_spacing_x / 2, gy + cell_spacing_y / 2)
                    )
                    if (
                        0 <= rect.centerx <= map_width
                        and 0 <= rect.centery <= screen.get_height()
                    ):
                        screen.blit(glyph_surf, rect)
                continue

            base_color = WALL_COLOR if char == WALL else FLOOR_COLOR
            color = tuple(int(c * brightness) for c in base_color)
            glyph_surf = map_font.render(char, True, color)

            cx = offset_x + (wx - camera_start_x) * cell_spacing_x
            cy = offset_y + (wy - camera_start_y) * cell_spacing_y
            rect = glyph_surf.get_rect(
                center=(cx + cell_spacing_x / 2, cy + cell_spacing_y / 2)
            )
            if (
                0 <= rect.centerx <= map_width
                and 0 <= rect.centery <= screen.get_height()
            ):
                screen.blit(glyph_surf, rect)

        for enemy in enemies:
            char = enemy.enemy_type.glyph
            brightness = get_fog_brightness(player_x, player_y, enemy.x, enemy.y)
            color = tuple(int(c * brightness) for c in enemy.enemy_type.color)
            glyph_surf = map_font.render(char, True, color)

            cx = offset_x + (enemy.x - camera_start_x) * cell_spacing_x
            cy = offset_y + (enemy.y - camera_start_y) * cell_spacing_y
            rect = glyph_surf.get_rect(
                center=(cx + cell_spacing_x / 2, cy + cell_spacing_y / 2)
            )
            if (
                0 <= rect.centerx <= map_width
                and 0 <= rect.centery <= screen.get_height()
                and (enemy.x, enemy.y) in visible_tiles
            ):
                screen.blit(glyph_surf, rect)

        px = offset_x + (VIEWPORT_TILES_X / 2) * cell_spacing_x
        py = offset_y + (VIEWPORT_TILES_Y / 2) * cell_spacing_y
        p_surf = map_font.render(player_facing, True, player_color)
        screen.blit(
            p_surf,
            p_surf.get_rect(center=(px + cell_spacing_x / 2, py + cell_spacing_y / 2)),
        )

        if terminal.state == "NAME_INPUT":
            pause_overlay = pygame.Surface(
                (map_width, screen.get_height()), pygame.SRCALPHA
            )
            pause_overlay.fill((30, 30, 30, 200))
            screen.blit(pause_overlay, (0, 0))

            paused_lbl = pause_font.render("PAUSED", True, (220, 220, 220))
            lbl_rect = paused_lbl.get_rect(
                center=(map_width // 2, screen.get_height() // 2)
            )
            screen.blit(paused_lbl, lbl_rect)

        terminal_rect = pygame.Rect(map_width, 0, panel_width, screen.get_height())
        terminal.render(
            screen, terminal_rect, dungeon, discovered, (player_x, player_y)
        )

        pygame.display.flip()

    pygame.quit()


if __name__ == "__main__":
    main()
