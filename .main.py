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
    DOOR,
    DOOR_ANIM_MS,
    is_walkable,
    materialize_door,
    begin_door_toggle,
    advance_door_animations,
    door_render_info,
    find_drop_position,
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
DELTA_FOR_FACING = {"^": (0, -1), "v": (0, 1), "<": (-1, 0), ">": (1, 0)}
TEXT_INPUT_STATES = {"NAME_INPUT", "ADDRESS_INPUT", "MP_NAME_INPUT"}
INTERACT_KEY = pygame.K_e
ITEM_GLYPH = "?"
ITEM_COLOR = (230, 200, 60)
ITEM_NAMES = {"TestItem": "Test Item"}  # item_id -> display name, falls back to item_id


def get_stretch_factor(player_x, player_y, wall_x, wall_y, max_range=4):
    dist = ((wall_x - player_x) ** 2 + (wall_y - player_y) ** 2) ** 0.5
    if dist <= 0 or dist > max_range:
        return None
    return 1.0 - (dist - 1) / max_range


def main():
    pygame.init()
    pygame.mixer.music.load("music/Slow_Creepy_Strings.wav")
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
    doors = {}  # (x,y) -> {"state": "closed"|"opening"|"open", "anim_start": timestamp}
    items = {}

    def handle_slot_hover(slot_info, get_current=False):
        nonlocal active_seed, dungeon, player_x, player_y, player_color, enemies, doors, items
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
            doors = {}
            items = {}
        else:
            if terminal.pending_mode == "host":
                active_seed = LOBBY_SEED
                dungeon, doors, items = build_lobby_dungeon()
            else:
                active_seed = seed_rng.randint(0, 999999)
                dungeon = generate_dungeon(max_structures=60, seed=active_seed)
                doors = {}
                items = {}
            preview_floors = [pos for pos, c in dungeon.items() if c == FLOOR]
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

    def drop_item(index):
        if terminal.network_mode:
            if net_client:
                net_client.send_drop(index)
        else:
            if terminal.active_character and 0 <= index < len(
                terminal.active_character["items"]
            ):
                item_id = terminal.active_character["items"].pop(index)
                drop_pos = find_drop_position(dungeon, items, player_x, player_y)
                items[drop_pos] = {"item_id": item_id}
                save_json(
                    terminal.active_character,
                    slot_path(terminal.active_character["slot"]),
                )

    def teardown_multiplayer():
        nonlocal net_server, net_client, local_client_id, players, doors, items
        if net_client:
            net_client.disconnect()
        if net_server:
            net_server.stop()
        net_server = None
        net_client = None
        local_client_id = None
        players = {}
        doors = {}
        items = {}

    terminal = TerminalUI(
        font,
        bold_font,
        handle_slot_hover,
        on_join_address=attempt_join,
        on_mp_color_confirm=confirm_mp_color,
        on_multiplayer_quit=teardown_multiplayer,
        on_drop_item=drop_item,
    )

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
                    "items": list(pdata.get("items", [])),
                }
            else:
                p = players[cid]
                p["x"], p["y"] = pdata["x"], pdata["y"]
                if cid != local_client_id:
                    p["facing"] = pdata["facing"]
                p["color"] = pdata["color"]
                p["name"] = pdata["name"]
                p["alive"] = pdata["alive"]
                p["connected"] = pdata["connected"]
                p["items"] = list(pdata.get("items", []))

    def handle_network_message(msg):
        nonlocal dungeon, doors, items
        mtype = msg.get("type")

        if mtype == "roster":
            seed = msg["seed"]
            if seed == LOBBY_SEED:
                dungeon, doors, items = build_lobby_dungeon()
            else:
                dungeon = generate_dungeon(max_structures=60, seed=seed)
                doors = {}
                items = {}
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
                    "items": [],
                }
                terminal.enter_multiplayer_playing(you["name"], you["color"])
            elif terminal.state == "CONNECTING":
                terminal.enter_mp_name_input(
                    msg.get("taken_names", []), msg.get("taken_colors", [])
                )

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
                "items": [],
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
            if net_server is None and "doors" in msg:
                doors.clear()
                for key, door_data in msg["doors"].items():
                    x_str, y_str = key.split(",")
                    doors[(int(x_str), int(y_str))] = door_data
            if net_server is None and "items" in msg:
                items.clear()
                for key, item_data in msg["items"].items():
                    x_str, y_str = key.split(",")
                    items[(int(x_str), int(y_str))] = item_data

        elif mtype == "disconnected":
            teardown_multiplayer()
            terminal.network_mode = False
            terminal.mp_hud_player = None
            terminal.active_character = None
            terminal.hosting_info = None
            terminal.load_start_menu()
            terminal.set_transient(
                "Disconnected from host.", (255, 80, 80), duration_ms=2500
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

        # --- screen geometry (needed early: facing depends on where the player renders) ---
        panel_width = screen.get_width() // 2
        map_width = screen.get_width() - panel_width
        tile_size = max(
            min(map_width // VIEWPORT_TILES_X, screen.get_height() // VIEWPORT_TILES_Y),
            1,
        )
        cell_spacing_x, cell_spacing_y = tile_size * 0.6, tile_size * 0.9
        offset_x = (map_width - (VIEWPORT_TILES_X * cell_spacing_x)) / 2
        offset_y = (screen.get_height() - (VIEWPORT_TILES_Y * cell_spacing_y)) / 2
        player_screen_x = (
            offset_x + (VIEWPORT_TILES_X / 2) * cell_spacing_x + cell_spacing_x / 2
        )
        player_screen_y = (
            offset_y + (VIEWPORT_TILES_Y / 2) * cell_spacing_y + cell_spacing_y / 2
        )

        # --- mouse-driven facing ---
        mouse_x, mouse_y = pygame.mouse.get_pos()
        look_angle = math.atan2(mouse_y - player_screen_y, mouse_x - player_screen_x)
        deg = math.degrees(look_angle) % 360
        if 45 <= deg < 135:
            mouse_facing = "v"
        elif 135 <= deg < 225:
            mouse_facing = "<"
        elif 225 <= deg < 315:
            mouse_facing = "^"
        else:
            mouse_facing = ">"

        if terminal.network_mode:
            if (
                local_client_id in players
                and players[local_client_id]["facing"] != mouse_facing
            ):
                players[local_client_id]["facing"] = mouse_facing
                if net_client:
                    net_client.send_turn(mouse_facing)
        else:
            player_facing = mouse_facing

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
            elif (
                event.type == pygame.KEYDOWN
                and event.key == INTERACT_KEY
                and terminal.state in ("PLAYING", "INVENTORY", "MAP")
            ):
                if terminal.network_mode and local_client_id in players:
                    interact_x = players[local_client_id]["x"]
                    interact_y = players[local_client_id]["y"]
                    facing = players[local_client_id]["facing"]
                else:
                    interact_x, interact_y = player_x, player_y
                    facing = player_facing

                ddx, ddy = DELTA_FOR_FACING.get(facing, (0, 1))
                pos = (interact_x + ddx, interact_y + ddy)

                if pos in items:
                    if terminal.network_mode:
                        net_client.send_pickup(*pos)
                    else:
                        item = items.pop(pos)
                        terminal.active_character["items"].append(item["item_id"])
                        save_json(
                            terminal.active_character,
                            slot_path(terminal.active_character["slot"]),
                        )
                elif dungeon.get(pos) == DOOR:
                    if terminal.network_mode:
                        net_client.send_interact(*pos)
                    else:
                        door = materialize_door(dungeon, doors, *pos)
                        begin_door_toggle(door, pygame.time.get_ticks())
                    break
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
                (pending_moves.get(k, now), k) for k in DIRECTION_KEYS if keys[k]
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

                if terminal.network_mode:
                    net_client.send_input(dx, dy)
                else:
                    target_x, target_y = player_x + dx, player_y + dy
                    if is_walkable(dungeon, doors, target_x, target_y, dx, dy):
                        player_x, player_y = target_x, target_y

                time_since_last_move = 0

        # --- host-only: resolve pending interacts, door animation, and movement ---
        if net_server is not None:
            net_server.set_doors_snapshot(doors)
            net_server.set_items_snapshot(items)
            for cid, ix, iy in net_server.consume_pending_interacts():
                if dungeon.get((ix, iy)) != DOOR:
                    continue
                door = materialize_door(dungeon, doors, ix, iy)
                begin_door_toggle(door, pygame.time.get_ticks())
            for cid, ix, iy in net_server.consume_pending_pickups():
                pos = (ix, iy)
                if pos in items:
                    item = items.pop(pos)
                    net_server.add_item_to_player(cid, item["item_id"])
            for cid, index in net_server.consume_pending_drops():
                snap = net_server.get_players_snapshot().get(cid)
                if not snap:
                    continue
                item_id = net_server.pop_item_from_player(cid, index)
                if item_id is not None:
                    drop_pos = find_drop_position(dungeon, items, snap["x"], snap["y"])
                    items[drop_pos] = {"item_id": item_id}
            advance_door_animations(doors, pygame.time.get_ticks())
            for cid, pdata in net_server.get_players_snapshot().items():
                mdx, mdy = net_server.consume_and_clear_input(cid)
                if mdx == 0 and mdy == 0:
                    continue
                target_x, target_y = pdata["x"] + mdx, pdata["y"] + mdy
                if is_walkable(dungeon, doors, target_x, target_y, mdx, mdy):
                    net_server.update_player_position(
                        cid, target_x, target_y, pdata["facing"]
                    )
                else:
                    net_server.update_player_position(
                        cid, pdata["x"], pdata["y"], pdata["facing"]
                    )
        elif not terminal.network_mode:
            advance_door_animations(doors, pygame.time.get_ticks())

            now_ms = pygame.time.get_ticks()
            for door in doors.values():
                if door.get("anim_until") and now_ms >= door["anim_until"]:
                    if door["state"] == "opening":
                        door["state"] = "open"
                        door["anim_until"] = None
                    elif door["state"] == "closing":
                        door["state"] = "closed"
                        door["anim_until"] = None

        # Offline games need the same animation completion handling as the
        # host. Otherwise doors remain permanently in opening/closing state.
        if net_server is None:
            now_ms = pygame.time.get_ticks()
            for door in doors.values():
                if door.get("anim_until") and now_ms >= door["anim_until"]:
                    if door["state"] == "opening":
                        door["state"] = "open"
                    elif door["state"] == "closing":
                        door["state"] = "closed"
                    door["anim_until"] = None

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

        interact_prompt = None
        if can_move:
            fddx, fddy = DELTA_FOR_FACING.get(display_facing, (0, 1))
            face_pos = (px + fddx, py + fddy)
            if face_pos in items:
                item_id = items[face_pos]["item_id"]
                name = ITEM_NAMES.get(item_id, item_id)
                interact_prompt = f"[E] Pick up {name}"
            elif dungeon.get(face_pos) == DOOR:
                interact_prompt = "[E] Open/close door"

        visible_tiles = compute_visible_tiles(dungeon, doors, px, py, radius=10)
        visible_tiles = reveal_boundary_walls(dungeon, doors, visible_tiles)
        discovered.update(visible_tiles)
        for enemy in enemies:
            enemy.update(dt, dungeon, px, py, WALL)

        screen.fill((0, 0, 0))
        map_font = pygame.font.SysFont(FONT_NAME, int(tile_size * 0.9))

        camera_start_x = cam_x - VIEWPORT_TILES_X / 2
        camera_start_y = cam_y - VIEWPORT_TILES_Y / 2

        draw_queue = []

        for wx, wy in visible_tiles:
            tile_here = dungeon.get((wx, wy), WALL)
            if wx == px and wy == py and tile_here == FLOOR and (wx, wy) not in items:
                continue
            char = tile_here
            if char == DOOR:
                draw_char, animating = door_render_info(dungeon, doors, wx, wy)
                is_wall_like, should_stretch = False, False
            elif (wx, wy) in items:
                draw_char, is_wall_like, should_stretch = ITEM_GLYPH, False, False
            else:
                draw_char, is_wall_like = char, (char == WALL)
                should_stretch = is_wall_like

            stretch = (
                get_stretch_factor(px, py, wx, wy, max_range=10)
                if should_stretch
                else None
            )
            brightness = get_fog_brightness(px, py, wx, wy)

            dist = math.hypot(wx - px, wy - py)

            if stretch is not None:
                dx, dy = wx - px, wy - py
                dir_x, dir_y = dx / dist, dy / dist

                base_wall_color = (
                    WALL_COLOR if (is_wall_like or char == DOOR) else FLOOR_COLOR
                )
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

                    stack_fade = max(0.15, 1.0 - (i / max(1, stack_count - 1)) * 0.9)
                    seg_color = tuple(
                        int(c * brightness * stack_fade) for c in base_wall_color
                    )

                    rect_center = (gx + cell_spacing_x / 2, gy + cell_spacing_y / 2)
                    draw_queue.append(
                        (dist - i * 0.1, draw_char, seg_color, rect_center, font_size)
                    )
            else:
                if (wx, wy) in items and char != DOOR:
                    base_tile_color = ITEM_COLOR
                else:
                    base_tile_color = (
                        WALL_COLOR if is_wall_like or char == DOOR else FLOOR_COLOR
                    )
                color = tuple(int(c * brightness) for c in base_tile_color)
                cx = offset_x + (wx - camera_start_x) * cell_spacing_x
                cy = offset_y + (wy - camera_start_y) * cell_spacing_y
                center_x = cx + cell_spacing_x / 2
                if draw_char == "|":
                    center_x += cell_spacing_x * 0.18
                rect_center = (center_x, cy + cell_spacing_y / 2)
                draw_queue.append(
                    (dist, draw_char, color, rect_center, int(tile_size * 0.9))
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

        p_surf = map_font.render(display_facing, True, display_color)
        screen.blit(
            p_surf,
            p_surf.get_rect(center=(player_screen_x, player_screen_y)),
        )

        # --- remote teammates (MVP: no fog/occlusion, always drawn if on-screen) ---
        if terminal.network_mode:
            for cid, p in players.items():
                if cid == local_client_id or not p.get("connected", True):
                    continue
                if (p["x"], p["y"]) not in visible_tiles:
                    continue
                rel_x = p["visual_x"] - camera_start_x
                rel_y = p["visual_y"] - camera_start_y
                if not (
                    0 <= rel_x <= VIEWPORT_TILES_X and 0 <= rel_y <= VIEWPORT_TILES_Y
                ):
                    continue
                gcx = offset_x + rel_x * cell_spacing_x
                gcy = offset_y + rel_y * cell_spacing_y
                brightness = get_fog_brightness(px, py, p["x"], p["y"])
                base_color = (
                    tuple(map(int, p["color"].split()))
                    if p.get("alive", True)
                    else (90, 90, 90)
                )
                glyph_color = tuple(int(c * brightness) for c in base_color)
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
        if terminal.network_mode:
            local_items_for_render = players.get(local_client_id, {}).get("items", [])
        elif terminal.active_character:
            local_items_for_render = terminal.active_character["items"]
        else:
            local_items_for_render = []

        terminal.render(
            screen,
            terminal_rect,
            dungeon,
            discovered,
            (px, py),
            other_players=other_players_for_hud,
            local_items=local_items_for_render,
            interact_prompt=interact_prompt,
        )
        pygame.display.flip()
        # print(f"seed={active_seed}")  # debug

    pygame.quit()


if __name__ == "__main__":
    main()
