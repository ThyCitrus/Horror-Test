import math
import pygame
import socket

from dungeon_gen import (
    generate_dungeon,
    WALL,
    FLOOR,
    seed_rng,
    compute_visible_tiles,
    reveal_boundary_walls,
    get_fog_brightness,
    find_adjacent_spawn,
    LOBBY_SEED,
    build_lobby_dungeon,
)
from enemies import Enemy, ENEMY_TYPES  # TODO: currently unused — no spawn logic yet
from terminal_ui import TerminalUI
from save_utils import load_json, save_json, slot_path
from network import GameServer, GameClient, DEFAULT_PORT

WINDOW_WIDTH, WINDOW_HEIGHT = 1200, 660
VIEWPORT_TILES_X, VIEWPORT_TILES_Y = 15, 11
FONT_NAME = "consolas"

WALL_COLOR = (150, 150, 150)
FLOOR_COLOR = (60, 60, 60)

DIRECTION_KEYS = {
    pygame.K_w: ((0, -1), "^"),
    pygame.K_s: ((0, 1), "v"),
    pygame.K_a: ((-1, 0), "<"),
    pygame.K_d: ((1, 0), ">"),
}
FACING_FOR_DELTA = {(0, -1): "^", (0, 1): "v", (-1, 0): "<", (1, 0): ">"}
TEXT_INPUT_STATES = {"NAME_INPUT", "ADDRESS_INPUT", "MP_NAME_INPUT"}


def get_stretch_factor(player_x, player_y, wall_x, wall_y, max_range=4):
    dist = ((wall_x - player_x) ** 2 + (wall_y - player_y) ** 2) ** 0.5
    if dist <= 0 or dist > max_range:
        return None
    return 1.0 - (dist - 1) / max_range


def main():
    pygame.init()
    pygame.mixer.music.load("music/JiggyTime.wav")
    screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.RESIZABLE)
    pygame.display.set_caption("Dungeon Crawler")
    clock = pygame.time.Clock()

    font_cache = {}
    stretch_font_cache = {}
    font = pygame.font.SysFont(FONT_NAME, 18)
    bold_font = pygame.font.SysFont(FONT_NAME, 18, bold=True)
    pause_font = pygame.font.SysFont(FONT_NAME, 48, bold=True)

    active_seed = seed_rng.randint(0, 999999)
    dungeon = generate_dungeon(max_structures=60, seed=active_seed)
    floor_tiles = [pos for pos, char in dungeon.items() if char == FLOOR]
    player_x, player_y = floor_tiles[0] if floor_tiles else (0, 0)
    player_color = (80, 200, 255)
    player_facing = "v"
    enemies = []  # TODO: never populated yet — enemy spawn logic not implemented

    # --- multiplayer state ---
    net_server = None  # GameServer — only set on the host
    net_client = None  # GameClient — set on host and joiners alike
    local_client_id = None
    players = (
        {}
    )  # client_id -> {x,y,visual_x,visual_y,facing,color,name,alive,connected}

    def handle_slot_hover(slot_info, get_current=False):
        nonlocal active_seed, dungeon, player_x, player_y, player_color, enemies
        if get_current:
            return active_seed, (player_x, player_y)

        if slot_info is None:
            return

        if slot_info["filled"]:
            char_data = load_json(slot_info["path"])
            active_seed = char_data.get("seed", seed_rng.randint(0, 999999))
            dungeon = generate_dungeon(max_structures=60, seed=active_seed)
            player_x = char_data.get("player_x", floor_tiles[0][0])
            player_y = char_data.get("player_y", floor_tiles[0][1])
            player_color = tuple(map(int, char_data["color"].split()))
        else:
            if terminal.pending_mode == "host":
                active_seed = LOBBY_SEED
                dungeon = build_lobby_dungeon()
            else:
                active_seed = seed_rng.randint(0, 999999)
                dungeon = generate_dungeon(max_structures=60, seed=active_seed)
            preview_floors = [pos for pos, char in dungeon.items() if char == FLOOR]
            player_x, player_y = preview_floors[0] if preview_floors else (0, 0)
            player_color = (120, 120, 120)

        enemies = []

    # --- multiplayer glue ---

    def start_host():
        nonlocal net_server, net_client, local_client_id
        net_server = GameServer(
            seed=active_seed,
            port=DEFAULT_PORT,
            spawn_fn=lambda: find_adjacent_spawn(dungeon, player_x, player_y),
        )
        net_server.start()
        net_client = GameClient()
        net_client.connect("127.0.0.1", DEFAULT_PORT)
        local_client_id = net_client.client_id
        char = terminal.active_character
        net_client.send_join(char["name"], char["color"])
        terminal.set_hosting_info(f"{get_local_ip()}:{DEFAULT_PORT}")

    def attempt_join(address_text):
        nonlocal net_client, local_client_id
        host_part, _, port_part = address_text.partition(":")
        try:
            port = int(port_part) if port_part else DEFAULT_PORT
            client = GameClient()
            client.connect(host_part, port)
            net_client = client
            local_client_id = client.client_id
            terminal.show_connecting()
        except (OSError, ValueError) as e:
            terminal.connection_failed(str(e))

    def confirm_mp_color(name, color):
        if net_client:
            net_client.send_join(name, color)

    def teardown_multiplayer():
        nonlocal net_server, net_client, local_client_id, players
        if net_client:
            net_client.disconnect()
        if net_server:
            net_server.stop()
        net_server = None
        net_client = None
        local_client_id = None
        players = {}

    def sync_players_from_state(state_players):
        for cid, pdata in state_players.items():
            if cid not in players:
                players[cid] = {
                    "x": pdata["x"],
                    "y": pdata["y"],
                    "visual_x": float(pdata["x"]),
                    "visual_y": float(pdata["y"]),
                    "facing": pdata["facing"],
                    "color": pdata["color"],
                    "name": pdata["name"],
                    "alive": pdata["alive"],
                    "connected": pdata["connected"],
                }
            else:
                p = players[cid]
                p["x"], p["y"] = pdata["x"], pdata["y"]
                p["facing"] = pdata["facing"]
                p["color"] = pdata["color"]
                p["name"] = pdata["name"]
                p["alive"] = pdata["alive"]
                p["connected"] = pdata["connected"]

    def handle_network_message(msg):
        nonlocal dungeon
        mtype = msg.get("type")

        if mtype == "roster":
            seed = msg["seed"]
            dungeon = (
                build_lobby_dungeon()
                if seed == LOBBY_SEED
                else generate_dungeon(max_structures=60, seed=seed)
            )
            if msg.get("reconnect"):
                you = msg["you"]
                players[local_client_id] = {
                    "x": 0,
                    "y": 0,
                    "visual_x": 0.0,
                    "visual_y": 0.0,
                    "facing": "v",
                    "color": you["color"],
                    "name": you["name"],
                    "alive": True,
                    "connected": True,
                }
                terminal.enter_multiplayer_playing(you["name"], you["color"])
            elif terminal.state == "CONNECTING":
                terminal.enter_mp_name_input(
                    msg.get("taken_names", []), msg.get("taken_colors", [])
                )
            # else: this is the host's own loopback roster — host auto-joins
            # separately in start_host(), nothing to do here.

        elif mtype == "join_ack":
            is_host = net_server is not None
            players[local_client_id] = {
                "x": player_x if is_host else 0,
                "y": player_y if is_host else 0,
                "visual_x": float(player_x if is_host else 0),
                "visual_y": float(player_y if is_host else 0),
                "facing": player_facing,
                "color": msg["color"],
                "name": msg["name"],
                "alive": True,
                "connected": True,
            }
            if is_host:
                net_server.update_player_position(
                    local_client_id, player_x, player_y, player_facing
                )
            terminal.enter_multiplayer_playing(msg["name"], msg["color"])

        elif mtype == "join_reject":
            terminal.mp_join_rejected(
                msg.get("reason"),
                msg.get("taken_names", []),
                msg.get("taken_colors", []),
            )

        elif mtype == "state":
            sync_players_from_state(msg["players"])

        elif mtype == "disconnected":
            teardown_multiplayer()
            terminal.network_mode = False
            terminal.mp_hud_player = None
            terminal.active_character = None
            terminal.hosting_info = None  # new
            terminal.load_start_menu()
            terminal.set_transient(
                "Disconnected from host.", (255, 80, 80), duration_ms=2500
            )

    terminal = TerminalUI(
        font,
        bold_font,
        handle_slot_hover,
        on_join_address=attempt_join,
        on_mp_color_confirm=confirm_mp_color,
        on_multiplayer_quit=teardown_multiplayer,
    )

    def play_menu_music():
        pygame.mixer.music.play(-1)

    def stop_menu_music():
        pygame.mixer.music.stop()

    play_menu_music()

    visual_x, visual_y = float(player_x), float(player_y)
    discovered = set()

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
            elif terminal.state in TEXT_INPUT_STATES:
                terminal.handle_input(event)
            elif event.type == pygame.KEYDOWN and event.key in DIRECTION_KEYS:
                pending_moves[event.key] = now
            elif event.type == pygame.KEYUP and event.key in pending_moves:
                del pending_moves[event.key]
            else:
                terminal.handle_input(event)

        # --- network inbox ---
        if net_client:
            for msg in net_client.poll_messages():
                handle_network_message(msg)

        if terminal.state == "PLAYING":
            stop_menu_music()

        # --- host: auto-start server once slot-creation flow lands in PLAYING ---
        if (
            terminal.pending_mode == "host"
            and net_server is None
            and terminal.state == "PLAYING"
            and terminal.active_character
            and not terminal.network_mode
        ):
            start_host()

        # --- movement input ---
        can_move = (terminal.active_character and not terminal.network_mode) or (
            terminal.network_mode and local_client_id in players
        )
        if can_move and terminal.state != "NAME_INPUT" and time_since_last_move >= 150:
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

                if terminal.network_mode:
                    net_client.send_input(dx, dy)
                    if local_client_id in players:
                        players[local_client_id]["facing"] = glyph
                else:
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

        # --- host-only: resolve every connected player's pending move ---
        if net_server is not None:
            for cid, pdata in net_server.get_players_snapshot().items():
                mdx, mdy = net_server.consume_and_clear_input(cid)
                if mdx == 0 and mdy == 0:
                    continue
                target_x, target_y = pdata["x"] + mdx, pdata["y"] + mdy
                blocked_by_enemy = any(
                    (e.x, e.y) == (target_x, target_y) for e in enemies
                )
                if (
                    dungeon.get((target_x, target_y), WALL) != WALL
                    and not blocked_by_enemy
                ):
                    facing = FACING_FOR_DELTA.get((mdx, mdy), pdata["facing"])
                    net_server.update_player_position(cid, target_x, target_y, facing)

        def get_local_ip():
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
            except OSError:
                return "127.0.0.1"
            finally:
                s.close()

        # --- visual smoothing ---
        if terminal.network_mode:
            for p in players.values():
                p["visual_x"] += (p["x"] - p["visual_x"]) * min(1.0, dt * 0.008)
                p["visual_y"] += (p["y"] - p["visual_y"]) * min(1.0, dt * 0.008)
        else:
            visual_x += (player_x - visual_x) * min(1.0, dt * 0.008)
            visual_y += (player_y - visual_y) * min(1.0, dt * 0.008)

        # --- which position/camera drives rendering this frame ---
        if terminal.network_mode:
            local_p = players[local_client_id]
            cam_x, cam_y = local_p["visual_x"], local_p["visual_y"]
            px, py = local_p["x"], local_p["y"]
            display_facing = local_p["facing"]
            display_color = tuple(map(int, local_p["color"].split()))
        else:
            cam_x, cam_y = visual_x, visual_y
            px, py = player_x, player_y
            display_facing = player_facing
            display_color = player_color

        visible_tiles = compute_visible_tiles(dungeon, px, py, radius=10)
        visible_tiles = reveal_boundary_walls(dungeon, visible_tiles)
        discovered.update(visible_tiles)
        for enemy in enemies:
            enemy.update(dt, dungeon, px, py, WALL)

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

        camera_start_x = cam_x - VIEWPORT_TILES_X / 2
        camera_start_y = cam_y - VIEWPORT_TILES_Y / 2

        draw_queue = []

        for wx, wy in visible_tiles:
            if wx == px and wy == py:
                continue
            char = dungeon.get((wx, wy), WALL)

            stretch = (
                get_stretch_factor(px, py, wx, wy, max_range=10)
                if char == WALL
                else None
            )
            brightness = get_fog_brightness(px, py, wx, wy)

            dist = math.hypot(wx - px, wy - py)

            if stretch is not None:
                dx, dy = wx - px, wy - py
                dir_x, dir_y = dx / dist, dy / dist

                base_color = tuple(int(c * brightness) for c in WALL_COLOR)
                base_cx = offset_x + (wx - camera_start_x) * cell_spacing_x
                base_cy = offset_y + (wy - camera_start_y) * cell_spacing_y

                stack_count = 1 + int(stretch * 9)
                for i in range(stack_count):
                    grow = 1.0 + (i / stack_count) * stretch * 3.0
                    font_size = int(tile_size * 0.9 * grow)
                    if font_size not in stretch_font_cache:
                        stretch_font_cache[font_size] = pygame.font.SysFont(
                            FONT_NAME, font_size
                        )

                    push = i * cell_spacing_x * 0.6
                    gx = base_cx + dir_x * push
                    gy = base_cy + dir_y * push

                    rect_center = (gx + cell_spacing_x / 2, gy + cell_spacing_y / 2)
                    draw_queue.append(
                        (dist + i * 0.1, WALL, base_color, rect_center, font_size)
                    )
            else:
                base_color = WALL_COLOR if char == WALL else FLOOR_COLOR
                color = tuple(int(c * brightness) for c in base_color)
                cx = offset_x + (wx - camera_start_x) * cell_spacing_x
                cy = offset_y + (wy - camera_start_y) * cell_spacing_y
                rect_center = (cx + cell_spacing_x / 2, cy + cell_spacing_y / 2)
                draw_queue.append(
                    (dist, char, color, rect_center, int(tile_size * 0.9))
                )

        # TODO: enemies aren't added to draw_queue yet — needs its own dist entry per enemy

        draw_queue.sort(key=lambda item: item[0], reverse=True)  # far -> near

        for dist, char, color, center, font_size in draw_queue:
            if font_size not in font_cache:
                font_cache[font_size] = pygame.font.SysFont(FONT_NAME, font_size)
            f = font_cache[font_size]

            surf = f.render(char, True, color)
            rect = surf.get_rect(center=center)
            if (
                0 <= rect.centerx <= map_width
                and 0 <= rect.centery <= screen.get_height()
            ):
                screen.blit(surf, rect)

        px_screen = offset_x + (VIEWPORT_TILES_X / 2) * cell_spacing_x
        py_screen = offset_y + (VIEWPORT_TILES_Y / 2) * cell_spacing_y
        p_surf = map_font.render(display_facing, True, display_color)
        screen.blit(
            p_surf,
            p_surf.get_rect(
                center=(px_screen + cell_spacing_x / 2, py_screen + cell_spacing_y / 2)
            ),
        )

        # --- remote teammates (MVP: no fog/occlusion, always drawn if on-screen) ---
        if terminal.network_mode:
            for cid, p in players.items():
                if cid == local_client_id or not p.get("connected", True):
                    continue
                rel_x = p["visual_x"] - camera_start_x
                rel_y = p["visual_y"] - camera_start_y
                if not (
                    0 <= rel_x <= VIEWPORT_TILES_X and 0 <= rel_y <= VIEWPORT_TILES_Y
                ):
                    continue
                gcx = offset_x + rel_x * cell_spacing_x
                gcy = offset_y + rel_y * cell_spacing_y
                glyph_color = (
                    tuple(map(int, p["color"].split()))
                    if p.get("alive", True)
                    else (90, 90, 90)
                )
                other_surf = map_font.render(p["facing"], True, glyph_color)
                screen.blit(
                    other_surf,
                    other_surf.get_rect(
                        center=(gcx + cell_spacing_x / 2, gcy + cell_spacing_y / 2)
                    ),
                )

        if terminal.state in (
            "NAME_INPUT",
            "ADDRESS_INPUT",
            "CONNECTING",
            "MP_NAME_INPUT",
            "MP_COLOR_SELECT",
            "JOINING",
        ):
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

        other_players_for_hud = (
            {cid: p for cid, p in players.items() if cid != local_client_id}
            if terminal.network_mode
            else None
        )
        terminal_rect = pygame.Rect(map_width, 0, panel_width, screen.get_height())
        terminal.render(
            screen,
            terminal_rect,
            dungeon,
            discovered,
            (px, py),
            other_players=other_players_for_hud,
        )

        pygame.display.flip()
        # print(f"seed={active_seed}")  # debug

    pygame.quit()


if __name__ == "__main__":
    main()
