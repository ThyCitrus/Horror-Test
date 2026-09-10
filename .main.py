import math
import pygame
import socket

from dungeon_gen import (
    generate_dungeon,
    WALL,
    FLOOR,
    seed_rng,
    compute_visible_tiles,
    has_line_of_sight,
    reveal_boundary_walls,
    get_fog_brightness,
    get_light_brightness,
    find_adjacent_spawn,
    LOBBY_SEED,
    build_lobby_dungeon,
    build_shop_dungeon,
    build_floor_dungeon,
    SHOP_SEED,
    SHOP_TERMINAL,
    OBJECTIVE_TERMINAL,
    OBJECTIVE_GLYPH,
    DATA_SCRAP,
    ROAMING_SIGNAL,
    LADDER,
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
from save_utils import (
    add_character_event,
    add_notepad_entry,
    load_json,
    normalize_character,
    save_json,
    slot_path,
)
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

NAKED_EYE_FORWARD_SHIFT = (
    1.4  # tiles the ambient light center leads your facing direction
)
NAKED_EYE_ELLIPSE_Y_RATIO = (
    0.8  # <1 = wider than tall; tune toward 1.0 for rounder, lower for flatter
)
LIGHT_LERP_SPEED = 0.006  # tune: higher = snappier, lower = more trailing/smooth

TEXT_INPUT_STATES = {"NAME_INPUT", "ADDRESS_INPUT", "MP_NAME_INPUT"}
INTERACT_KEY = pygame.K_e
DOOR_INTERACT_RANGE = 2
DOOR_COYOTE_TIME_MS = 500
ITEM_GLYPH = "?"
ITEM_COLOR = (230, 200, 60)
LIGHT_ITEM_IDS = {"Flashlight", "Lantern"}
ITEM_NAMES = {
    "TestItem": "Test Item",
    "Flashlight": "Flashlight",
    "Lantern": "Lantern",
    "Map": "Map",
    SHOP_TERMINAL: "Shop Terminal",
    OBJECTIVE_TERMINAL: "Objective Terminal",
    DATA_SCRAP: "Data Scrap",
    ROAMING_SIGNAL: "Roaming Signal",
}  # item_id -> display name, falls back to item_id
ITEM_GLYPHS = {
    SHOP_TERMINAL: "‰",
    OBJECTIVE_TERMINAL: OBJECTIVE_GLYPH,
    DATA_SCRAP: "$",
    ROAMING_SIGNAL: "S",
}
TOGGLE_LIGHT_KEY = pygame.BUTTON_LEFT
SHOP_COLOR = (205, 70, 70)
LADDER_COLOR = (255, 255, 255)
LOOK_SEND_INTERVAL_MS = (
    100  # throttle for continuous angle sync, separate from discrete facing changes
)
FLASHLIGHT_RANGE = 13
FLASHLIGHT_PLATEAU = 7
FLASHLIGHT_HALF_ANGLE_DEG = 30
FLASHLIGHT_FLARE_DEG = 12
FLASHLIGHT_CLOSE_RADIUS = 1
FLASHLIGHT_MIN_BRIGHTNESS = 0.35
LANTERN_PLATEAU = 1
LANTERN_FALLOFF_END = 7
LANTERN_MIN_BRIGHTNESS = 0.0
LANTERN_RADIUS_BONUS = 3


def get_stretch_factor(player_x, player_y, wall_x, wall_y, max_range=4):
    dist = ((wall_x - player_x) ** 2 + (wall_y - player_y) ** 2) ** 0.5
    if dist <= 0 or dist > max_range:
        return None
    return 1.0 - (dist - 1) / max_range


def make_light_brightness_fn(cx, cy, plateau, falloff_end, min_brightness):
    return lambda tx, ty: get_light_brightness(
        cx, cy, tx, ty, plateau, falloff_end, min_brightness
    )


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

    active_seed = seed_rng.randint(1, 999999)
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
    floor_number = 1
    objective = None
    shared_bytes = 0
    seen_events = set()
    last_door_target = None
    last_door_target_time = 0

    def make_floor_objective(number, floor_items):
        objective_type = ("fast", "long", "roaming")[(number - 1) % 3]
        target_count = sum(
            item["item_id"] in (OBJECTIVE_TERMINAL, ROAMING_SIGNAL)
            for item in floor_items.values()
        )
        game_name = {
            "fast": ["Arrow Sequence", "Number Calibration", "Sine Wave Tuner"][
                (number - 1) % 3
            ],
            "long": ["Flow Puzzle", "Memory Pattern / Simon", "Active Hold / Pong"][
                (number - 1) % 3
            ],
            "roaming": "Hotspot Signal Tracker",
        }[objective_type]
        if objective_type == "long":
            title, required = f"{game_name}: recover three data fragments", 3
        elif objective_type == "roaming":
            title, required = game_name, 1
        else:
            title, required = game_name, 1
        target = next(
            (
                pos
                for pos, item in floor_items.items()
                if item["item_id"]
                == (
                    ROAMING_SIGNAL
                    if objective_type == "roaming"
                    else OBJECTIVE_TERMINAL
                )
            ),
            None,
        )
        return {
            "id": f"floor-{number}-{objective_type}",
            "type": objective_type,
            "game": game_name,
            "title": title,
            "progress": 0,
            "required": required,
            "target_count": target_count,
            "target_progress": 0,
            "target": list(target) if target else None,
            "completed": False,
            "terminal_prompt": f"{game_name.upper()} // EXECUTE",
            "terminal_actions": ["Execute objective", "Leave terminal"],
        }

    def get_player_count():
        if net_server is not None:
            return max(1, len(net_server.get_players_snapshot()))
        return 1

    def load_floor(number, seed, player_count=None):
        nonlocal dungeon, doors, items, objective
        if player_count is None:
            player_count = get_player_count()
        dungeon, doors, items = build_floor_dungeon(number, seed, player_count)
        objective = make_floor_objective(number, items)

    def narrative_for_floor(number, objective_data=None):
        hooks = {
            "Archive": "ARCHIVE: The facility is still indexing the breach.",
            "Donnie": "DONNIE: A maintenance ping says someone is watching the lifts.",
            "Agnate": "AGNATE: Signal residue matches an impossible heartbeat.",
        }
        names = list(hooks)
        name = names[(number - 1) % len(names)]
        text = hooks[name]
        lore = f"{name} // floor {number}: {text.split(': ', 1)[1]}"
        terminal.add_system_event(text, lore)
        if net_server:
            net_server.add_event(text)
        if terminal.active_character and objective_data is not None:
            terminal.active_character["objective"] = objective_data
            save_json(
                terminal.active_character,
                slot_path(terminal.active_character["slot"]),
            )

    def sync_world_ui():
        terminal.set_world_state(floor_number, objective, shared_bytes)

    def equip_item(item_id):
        character = terminal.active_character
        if item_id not in LIGHT_ITEM_IDS:
            return "Only light sources can be equipped."
        if character is not None and item_id in character.get("items", []):
            character["equipped_light"] = item_id
            save_json(character, slot_path(character["slot"]))
            return f"Equipped {ITEM_NAMES[item_id]}."
        return "Light source is not in inventory."

    def deposit_loot():
        if terminal.network_mode and net_server is None:
            if net_client:
                net_client.send_deposit()
            return "Deposit request sent to Archive."
        character = terminal.active_character
        if not character or not character.get("loot"):
            return "No floor loot to deposit."
        deposited = sum(entry.get("value", 0) for entry in character["loot"])
        character["loot"] = []
        character["bytes"] = shared_bytes + deposited
        save_json(character, slot_path(character["slot"]))
        return f"Deposited loot for {deposited} bytes."

    def handle_slot_hover(slot_info, get_current=False):
        nonlocal active_seed, dungeon, player_x, player_y, player_color, enemies, doors, items, floor_number, objective, shared_bytes
        if get_current:
            return active_seed, (player_x, player_y)

        if slot_info is None:
            return

        if slot_info["filled"]:
            char_data = normalize_character(load_json(slot_info["path"]))
            active_seed = char_data.get("seed", seed_rng.randint(1, 999999))
            floor_number = char_data.get("floor", 0 if active_seed == SHOP_SEED else 1)
            if active_seed == SHOP_SEED:
                dungeon, doors, items = build_shop_dungeon()
                objective = None
            else:
                load_floor(floor_number, active_seed)
                objective = char_data.get("objective") or objective
            shared_bytes = char_data.get("bytes", char_data.get("gold", 0))
            preview_floors = [pos for pos, c in dungeon.items() if c in (FLOOR, LADDER)]
            default_pos = preview_floors[0] if preview_floors else (0, 0)
            player_x = char_data.get("player_x", default_pos[0])
            player_y = char_data.get("player_y", default_pos[1])
            player_color = tuple(map(int, char_data["color"].split()))
        else:
            if terminal.pending_mode == "host":
                active_seed = LOBBY_SEED
                dungeon, doors, items = build_lobby_dungeon()
                objective = None
            else:
                active_seed = SHOP_SEED
                dungeon, doors, items = build_shop_dungeon()
                objective = None
            floor_number = 0 if active_seed == SHOP_SEED else 1
            shared_bytes = 100
            preview_floors = [pos for pos, c in dungeon.items() if c in (FLOOR, LADDER)]
            player_x, player_y = preview_floors[0] if preview_floors else (0, 0)
            player_color = (120, 120, 120)

        enemies = []
        sync_world_ui()

    # --- multiplayer glue ---

    def start_host():
        nonlocal net_server, net_client, local_client_id
        net_server = GameServer(
            seed=active_seed,
            port=DEFAULT_PORT,
            spawn_fn=lambda: find_adjacent_spawn(dungeon, player_x, player_y),
            floor_number=floor_number,
            shared_bytes=shared_bytes,
            objective=objective,
            player_count=get_player_count(),
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

    def purchase_shop_item(item_id):
        nonlocal shared_bytes
        if terminal.network_mode and net_server is None:
            if net_client:
                net_client.send_purchase(item_id)
            return "Purchase request sent to Archive."
        character = terminal.active_character
        if not character:
            return "No active player."
        character.setdefault("items", [])
        character.setdefault("bytes", character.get("gold", 0))
        character.setdefault("shop_free_light_used", False)
        if item_id in character["items"]:
            return f"{ITEM_NAMES[item_id]} already owned."
        is_light = item_id in LIGHT_ITEM_IDS
        if is_light and not character["shop_free_light_used"]:
            character["shop_free_light_used"] = True
            price = 0
        else:
            price = 100
        if character["bytes"] < price:
            return "Insufficient bytes."
        character["bytes"] -= price
        shared_bytes = character["bytes"]
        character["items"].append(item_id)
        if is_light:
            character["equipped_light"] = item_id
        save_json(character, slot_path(character["slot"]))
        return f"Purchased {ITEM_NAMES[item_id]}."

    def complete_objective(action_index=0):
        nonlocal objective
        if not objective:
            return "No active objective."
        if (
            objective["type"] == "long"
            and objective["progress"] < objective["required"]
        ):
            return "Upload blocked: recover more data fragments."
        objective["target_progress"] = min(
            objective.get("target_count", 1),
            objective.get("target_progress", 0) + 1,
        )
        objective["completed"] = objective["target_progress"] >= objective.get(
            "target_count", 1
        )
        if objective["completed"]:
            objective["progress"] = objective["required"]
        text = f"Objective complete: {objective['title']}."
        terminal.add_system_event(text)
        if terminal.active_character:
            terminal.active_character["objective"] = objective
            save_json(
                terminal.active_character,
                slot_path(terminal.active_character["slot"]),
            )
        if net_server:
            net_server.set_world_state(objective=objective)
        return text

    def advance_floor():
        nonlocal active_seed, dungeon, doors, items, player_x, player_y, floor_number, objective, shared_bytes, discovered
        floor_number += 1
        if floor_number % 3 == 0:
            active_seed = SHOP_SEED
            dungeon, doors, items = build_shop_dungeon()
            objective = None
        else:
            active_seed = seed_rng.randint(1, 999999)
            load_floor(floor_number, active_seed, get_player_count())
        doors = {}
        discovered.clear()
        floor_tiles = [pos for pos, char in dungeon.items() if char in (FLOOR, LADDER)]
        player_x, player_y = floor_tiles[0] if floor_tiles else (0, 0)
        shared_bytes = (
            terminal.active_character.get("bytes", 0)
            if terminal.active_character
            else shared_bytes
        )
        if terminal.active_character:
            terminal.active_character.update(
                {
                    "seed": active_seed,
                    "floor": floor_number,
                    "player_x": player_x,
                    "player_y": player_y,
                    "objective": objective,
                    "bytes": shared_bytes,
                }
            )
            save_json(
                terminal.active_character,
                slot_path(terminal.active_character["slot"]),
            )
        if net_server:
            for cid, pdata in net_server.get_players_snapshot().items():
                spawn_x, spawn_y = (
                    (player_x, player_y)
                    if cid == local_client_id
                    else find_adjacent_spawn(dungeon, player_x, player_y)
                )
                net_server.update_player_position(
                    cid, spawn_x, spawn_y, pdata.get("facing", "v")
                )
                if cid in players:
                    players[cid]["x"] = spawn_x
                    players[cid]["y"] = spawn_y
        narrative_for_floor(floor_number, objective)
        if not objective:
            terminal.add_system_event(
                f"Shop floor {floor_number}: resupply before descent."
            )
        if net_server:
            net_server.seed = active_seed
            net_server.set_world_state(
                floor_number=floor_number,
                shared_bytes=shared_bytes,
                objective=objective,
                player_count=get_player_count(),
            )
        sync_world_ui()

    terminal = TerminalUI(
        font,
        bold_font,
        handle_slot_hover,
        on_join_address=attempt_join,
        on_mp_color_confirm=confirm_mp_color,
        on_multiplayer_quit=teardown_multiplayer,
        on_drop_item=drop_item,
        on_shop_purchase=purchase_shop_item,
        on_objective_action=complete_objective,
        on_equip_item=equip_item,
        on_deposit_loot=deposit_loot,
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
                    "loot": list(pdata.get("loot", [])),
                    "equipped_light": pdata.get("equipped_light"),
                    "light_on": pdata.get("light_on", False),
                    "look_angle": pdata.get("look_angle", 0.0),
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
                p["loot"] = list(pdata.get("loot", []))
                p["equipped_light"] = pdata.get("equipped_light")
                if cid != local_client_id:
                    p["light_on"] = pdata.get("light_on", False)
                    p["look_angle"] = pdata.get("look_angle", 0.0)

    def handle_network_message(msg):
        nonlocal active_seed, dungeon, doors, items, floor_number, objective, shared_bytes
        mtype = msg.get("type")

        if mtype == "roster":
            seed = msg["seed"]
            active_seed = seed
            floor_number = msg.get("floor", floor_number)
            shared_bytes = msg.get("shared_bytes", shared_bytes)
            objective = msg.get("objective")
            player_count = msg.get("player_count", 1)
            if seed == LOBBY_SEED:
                dungeon, doors, items = build_lobby_dungeon()
            elif seed == SHOP_SEED:
                dungeon, doors, items = build_shop_dungeon()
            else:
                dungeon, doors, items = build_floor_dungeon(
                    floor_number, seed, player_count
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
                    "items": [],
                    "loot": [],
                    "equipped_light": None,
                    "light_on": False,
                    "look_angle": 0.0,
                }
                terminal.enter_multiplayer_playing(you["name"], you["color"])
            elif terminal.state == "CONNECTING":
                terminal.enter_mp_name_input(
                    msg.get("taken_names", []), msg.get("taken_colors", [])
                )

        elif mtype == "purchase_result":
            terminal.set_transient(
                msg.get("message", "Purchase denied."),
                (
                    (80, 255, 80)
                    if "Purchased" in msg.get("message", "")
                    else (255, 180, 80)
                ),
                duration_ms=1800,
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
                "loot": [],
                "equipped_light": None,
                "light_on": False,
                "look_angle": look_angle,
            }
            floor_number = msg.get("floor", floor_number)
            shared_bytes = msg.get("shared_bytes", shared_bytes)
            objective = msg.get("objective", objective)
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
            incoming_seed = msg.get("seed", active_seed)
            if net_server is None and incoming_seed != active_seed:
                active_seed = incoming_seed
                if active_seed == SHOP_SEED:
                    dungeon, doors, items = build_shop_dungeon()
                else:
                    dungeon, doors, items = build_floor_dungeon(
                        msg.get("floor", floor_number),
                        active_seed,
                        msg.get("player_count", 1),
                    )
                doors = {}
            floor_number = msg.get("floor", floor_number)
            shared_bytes = msg.get("shared_bytes", shared_bytes)
            objective = msg.get("objective", objective)
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
            for event in msg.get("events", []):
                if event not in seen_events:
                    seen_events.add(event)
                    terminal.add_system_event(event)
            sync_world_ui()

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
    light_visual_x, light_visual_y = float(player_x), float(player_y)
    last_look_send_time = 0
    discovered = set()
    pending_moves = {}
    buffered_move = None
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
            if local_client_id in players:
                players[local_client_id]["look_angle"] = look_angle
                facing_changed = players[local_client_id]["facing"] != mouse_facing
                if facing_changed:
                    players[local_client_id]["facing"] = mouse_facing
                if net_client and (
                    facing_changed or now - last_look_send_time >= LOOK_SEND_INTERVAL_MS
                ):
                    net_client.send_turn(mouse_facing, look_angle)
                    last_look_send_time = now
        else:
            player_facing = mouse_facing

        if terminal.state == "PLAYING" and terminal.active_character:
            player_color = tuple(map(int, terminal.active_character["color"].split()))
        elif terminal.state in ("COLOR_SELECT", "CLASS_SELECT"):
            player_color = tuple(map(int, terminal.creation_color.split()))

        # --- update nearby door for coyote-time tracking ---
        if terminal.network_mode and local_client_id in players:
            check_x = players[local_client_id]["x"]
            check_y = players[local_client_id]["y"]
            check_facing = players[local_client_id]["facing"]
        else:
            check_x, check_y = player_x, player_y
            check_facing = player_facing

        cdx, cdy = DELTA_FOR_FACING.get(check_facing, (0, 1))
        front_tile = (check_x + cdx, check_y + cdy)
        extended_tile = (check_x + 2 * cdx, check_y + 2 * cdy)
        # Keep the nearest door as the coyote-time target, while retaining
        # the two-tile interaction leniency when no adjacent door is present.
        if dungeon.get(front_tile) == DOOR:
            last_door_target = front_tile
            last_door_target_time = now
        elif dungeon.get(extended_tile) == DOOR:
            last_door_target = extended_tile
            last_door_target_time = now

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif terminal.state in TEXT_INPUT_STATES:
                terminal.handle_input(event)
            elif event.type == pygame.KEYDOWN and event.key in DIRECTION_KEYS:
                pending_moves[event.key] = now
                buffered_move = event.key
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
                extended_pos = (interact_x + 2 * ddx, interact_y + 2 * ddy)

                if pos in items:
                    item_id = items[pos]["item_id"]
                    if item_id == SHOP_TERMINAL:
                        if terminal.network_mode and terminal.active_character:
                            terminal.open_shop(
                                terminal.active_character,
                                shared_bytes if terminal.network_mode else None,
                                on_deposit=deposit_loot,
                            )
                        elif terminal.network_mode:
                            net_client.send_interact(*pos)
                        else:
                            terminal.open_shop(
                                terminal.active_character,
                                shared_bytes if terminal.network_mode else None,
                                on_deposit=deposit_loot,
                            )
                    elif item_id in (OBJECTIVE_TERMINAL, ROAMING_SIGNAL):
                        if terminal.network_mode:
                            net_client.send_interact(*pos)
                        elif item_id == OBJECTIVE_TERMINAL:
                            terminal.open_objective_terminal(objective)
                        else:
                            items.pop(pos)
                            complete_objective()
                    elif item_id == DATA_SCRAP and terminal.network_mode:
                        net_client.send_pickup(*pos)
                    elif terminal.network_mode:
                        net_client.send_pickup(*pos)
                    else:
                        item = items.pop(pos)
                        if item_id == DATA_SCRAP:
                            value = item.get("value", 10)
                            terminal.active_character.setdefault("loot", []).append(
                                {
                                    "name": item.get("name", "Recovered data"),
                                    "value": value,
                                }
                            )
                            if objective and objective["type"] == "long":
                                objective["progress"] = min(
                                    objective["required"], objective["progress"] + 1
                                )
                            if terminal.active_character:
                                terminal.active_character["objective"] = objective
                                save_json(
                                    terminal.active_character,
                                    slot_path(terminal.active_character["slot"]),
                                )
                            terminal.add_system_event(
                                f"Recovered {item.get('name', 'data scrap')} ({value} bytes)."
                            )
                        else:
                            terminal.active_character["items"].append(item_id)
                            if item_id in LIGHT_ITEM_IDS:
                                terminal.active_character["equipped_light"] = item_id
                        terminal.active_character["objective"] = objective
                        save_json(
                            terminal.active_character,
                            slot_path(terminal.active_character["slot"]),
                        )
                elif pos in dungeon and dungeon[pos] == LADDER:
                    if active_seed != SHOP_SEED and (
                        not objective or not objective.get("completed")
                    ):
                        terminal.set_transient(
                            "Ladder locked: complete the objective first.",
                            (255, 180, 80),
                            duration_ms=1600,
                        )
                    elif terminal.network_mode:
                        net_client.send_descend()
                    else:
                        advance_floor()
                else:
                    target_door = None
                    if dungeon.get(pos) == DOOR:
                        target_door = pos
                    elif dungeon.get(extended_pos) == DOOR:
                        target_door = extended_pos
                    elif (
                        last_door_target is not None
                        and now - last_door_target_time <= DOOR_COYOTE_TIME_MS
                        and dungeon.get(last_door_target) == DOOR
                    ):
                        target_door = last_door_target

                    if target_door is not None:
                        if terminal.network_mode:
                            net_client.send_interact(*target_door)
                        else:
                            door = materialize_door(dungeon, doors, *target_door)
                            begin_door_toggle(door, pygame.time.get_ticks())
            elif (
                event.type == pygame.MOUSEBUTTONDOWN
                and event.button == TOGGLE_LIGHT_KEY
                and terminal.state in ("PLAYING", "INVENTORY", "MAP")
            ):
                if terminal.network_mode and local_client_id in players:
                    if players[local_client_id].get("equipped_light"):
                        players[local_client_id]["light_on"] = not players[
                            local_client_id
                        ]["light_on"]
                        if net_client:
                            net_client.send_toggle_light()
                elif terminal.active_character and terminal.active_character.get(
                    "equipped_light"
                ):
                    terminal.active_character["light_on"] = (
                        not terminal.active_character["light_on"]
                    )
                    save_json(
                        terminal.active_character,
                        slot_path(terminal.active_character["slot"]),
                    )
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
            chosen_key = buffered_move
            buffered_move = None

            if chosen_key is None and candidates:
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

            if chosen_key is not None:
                (dx, dy), glyph = DIRECTION_KEYS[chosen_key]

                if terminal.network_mode:
                    net_client.send_input(dx, dy)
                else:
                    target_x, target_y = player_x + dx, player_y + dy
                    blocking_item = items.get((target_x, target_y), {}).get("item_id")
                    if blocking_item not in (
                        OBJECTIVE_TERMINAL,
                        SHOP_TERMINAL,
                    ) and is_walkable(dungeon, doors, target_x, target_y, dx, dy):
                        player_x, player_y = target_x, target_y

                time_since_last_move = 0

        # --- host-only: resolve pending interacts, door animation, and movement ---
        if net_server is not None:
            net_server.set_doors_snapshot(doors)
            net_server.set_items_snapshot(items)
            for cid, ix, iy in net_server.consume_pending_interacts():
                player_state = net_server.get_players_snapshot().get(cid)
                if not player_state:
                    continue
                if (
                    abs(player_state["x"] - ix) + abs(player_state["y"] - iy)
                    > DOOR_INTERACT_RANGE + 1
                ):
                    continue
                if dungeon.get((ix, iy)) == DOOR:
                    door = materialize_door(dungeon, doors, ix, iy)
                    begin_door_toggle(door, pygame.time.get_ticks())
                elif (ix, iy) in items:
                    item_id = items[(ix, iy)]["item_id"]
                    if item_id == OBJECTIVE_TERMINAL:
                        complete_objective()
                        if objective and objective.get("completed"):
                            items.pop((ix, iy), None)
                    elif item_id == ROAMING_SIGNAL:
                        complete_objective()
                        items.pop((ix, iy), None)
            for cid, ix, iy in net_server.consume_pending_pickups():
                pos = (ix, iy)
                if pos in items:
                    item = items.pop(pos)
                    item_id = item["item_id"]
                    if item_id == DATA_SCRAP:
                        net_server.add_loot_to_player(
                            cid,
                            {
                                "name": item.get("name", "Recovered data"),
                                "value": item.get("value", 10),
                            },
                        )
                        if objective and objective["type"] == "long":
                            objective["progress"] = min(
                                objective["required"], objective["progress"] + 1
                            )
                        net_server.set_world_state(objective=objective)
                    else:
                        net_server.add_item_to_player(cid, item_id)
                        if item_id in LIGHT_ITEM_IDS:
                            net_server.set_equipped_light(cid, item_id)
            for cid, item_id in net_server.consume_pending_purchases():
                player_state = net_server.get_players_snapshot().get(cid)
                if not player_state:
                    continue
                if not any(
                    item.get("item_id") == SHOP_TERMINAL for item in items.values()
                ):
                    net_server.send_purchase_result(cid, "Shop terminal unavailable.")
                    continue
                if item_id not in LIGHT_ITEM_IDS and item_id != "Map":
                    net_server.send_purchase_result(cid, "Unknown catalogue item.")
                    continue
                owned = player_state.get("items", [])
                if item_id in owned:
                    net_server.send_purchase_result(
                        cid, f"{ITEM_NAMES[item_id]} already owned."
                    )
                    continue
                free_light = item_id in LIGHT_ITEM_IDS and not player_state.get(
                    "shop_free_light_used", False
                )
                price = 0 if free_light else 100
                if net_server.shared_bytes < price:
                    net_server.send_purchase_result(cid, "Insufficient shared bytes.")
                    continue
                if price:
                    net_server.add_shared_bytes(-price)
                    shared_bytes = net_server.shared_bytes
                net_server.add_item_to_player(cid, item_id)
                if free_light:
                    with net_server._lock:
                        net_server.players[cid]["shop_free_light_used"] = True
                if item_id in LIGHT_ITEM_IDS:
                    net_server.set_equipped_light(cid, item_id)
                net_server.send_purchase_result(
                    cid, f"Purchased {ITEM_NAMES[item_id]}."
                )
            for cid in net_server.consume_pending_deposits():
                player_state = net_server.get_players_snapshot().get(cid)
                if not player_state:
                    continue
                loot_value = sum(
                    entry.get("value", 0) for entry in player_state.get("loot", [])
                )
                if loot_value <= 0:
                    net_server.send_purchase_result(cid, "No floor loot to deposit.")
                    continue
                with net_server._lock:
                    net_server.players[cid]["loot"] = []
                net_server.add_shared_bytes(loot_value)
                shared_bytes = net_server.shared_bytes
                net_server.send_purchase_result(
                    cid, f"Deposited loot for {loot_value} bytes."
                )
            descended = False
            for cid in net_server.consume_pending_descends():
                if descended:
                    continue
                if not objective or not objective.get("completed"):
                    continue
                pdata = net_server.get_players_snapshot().get(cid)
                ladder = next(
                    (pos for pos, tile in dungeon.items() if tile == LADDER), None
                )
                if (
                    pdata
                    and ladder
                    and abs(pdata["x"] - ladder[0]) + abs(pdata["y"] - ladder[1]) <= 1
                ):
                    advance_floor()
                    descended = True
            net_server.set_world_state(
                floor_number=floor_number,
                shared_bytes=shared_bytes,
                objective=objective,
            )
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
                blocking_item = items.get((target_x, target_y), {}).get("item_id")
                if blocking_item not in (
                    OBJECTIVE_TERMINAL,
                    SHOP_TERMINAL,
                ) and is_walkable(dungeon, doors, target_x, target_y, mdx, mdy):
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
                action = "Access" if item_id == SHOP_TERMINAL else "Pick up"
                interact_prompt = f"[E] {action} {name}"
            elif dungeon.get(face_pos) == DOOR:
                interact_prompt = "[E] Open/close door"
            elif dungeon.get(face_pos) == LADDER:
                if objective and objective.get("completed"):
                    interact_prompt = "[E] Descend to facility"
                else:
                    interact_prompt = "[E] Ladder locked: complete objective"

            if terminal.network_mode and local_client_id in players:
                equipped_light = players[local_client_id].get("equipped_light")
                light_on = players[local_client_id].get("light_on", False)
            elif terminal.active_character:
                equipped_light = terminal.active_character.get("equipped_light")
                light_on = terminal.active_character.get("light_on", False)
            else:
                equipped_light = None
                light_on = False
            if equipped_light:
                state = "ON" if light_on else "off"
                light_line = f"[Left Click] {ITEM_NAMES.get(equipped_light, equipped_light)}: {state}"
                interact_prompt = (
                    f"{interact_prompt}   {light_line}"
                    if interact_prompt
                    else light_line
                )

        if terminal.network_mode and local_client_id in players:
            equipped_light = players[local_client_id].get("equipped_light")
            light_on = players[local_client_id].get("light_on", False)
        elif terminal.active_character:
            equipped_light = terminal.active_character.get("equipped_light")
            light_on = terminal.active_character.get("light_on", False)
        else:
            equipped_light = None
            light_on = False

        shift_dx, shift_dy = DELTA_FOR_FACING.get(display_facing, (0, 1))
        target_light_x = px + shift_dx * NAKED_EYE_FORWARD_SHIFT
        target_light_y = py + shift_dy * NAKED_EYE_FORWARD_SHIFT

        light_visual_x += (target_light_x - light_visual_x) * min(
            1.0, dt * LIGHT_LERP_SPEED
        )
        light_visual_y += (target_light_y - light_visual_y) * min(
            1.0, dt * LIGHT_LERP_SPEED
        )

        # --- ambient (naked eye) vision: always active, private per-player ---
        ambient_visible = compute_visible_tiles(
            dungeon,
            doors,
            px,
            py,
            radius=10,
            light_cx=light_visual_x,
            light_cy=light_visual_y,
            ellipse_y_ratio=NAKED_EYE_ELLIPSE_Y_RATIO,
        )

        # --- physical light sources: shared, additive, visible to everyone ---
        light_sources = []
        if equipped_light == "Flashlight" and light_on:
            light_sources.append(
                {
                    "is_local": True,
                    "visible": compute_visible_tiles(
                        dungeon,
                        doors,
                        px,
                        py,
                        light_cx=px,
                        light_cy=py,
                        cone_angle=look_angle,
                        cone_half_angle=math.radians(FLASHLIGHT_HALF_ANGLE_DEG),
                        cone_range=FLASHLIGHT_RANGE,
                        close_radius=FLASHLIGHT_CLOSE_RADIUS,
                    ),
                    "brightness_fn": make_light_brightness_fn(
                        px,
                        py,
                        FLASHLIGHT_PLATEAU,
                        FLASHLIGHT_RANGE,
                        FLASHLIGHT_MIN_BRIGHTNESS,
                    ),
                }
            )
        elif equipped_light == "Lantern" and light_on:
            light_sources.append(
                {
                    "is_local": True,
                    "visible": compute_visible_tiles(
                        dungeon,
                        doors,
                        px,
                        py,
                        radius=LANTERN_FALLOFF_END,
                        light_cx=px,
                        light_cy=py,
                    ),
                    "brightness_fn": make_light_brightness_fn(
                        px,
                        py,
                        LANTERN_PLATEAU,
                        LANTERN_FALLOFF_END,
                        LANTERN_MIN_BRIGHTNESS,
                    ),
                }
            )

        if terminal.network_mode:
            for cid, p in players.items():
                if (
                    cid == local_client_id
                    or not p.get("connected", True)
                    or not p.get("alive", True)
                ):
                    continue
                if not (p.get("light_on") and p.get("equipped_light")):
                    continue
                ox, oy = p["x"], p["y"]
                if p["equipped_light"] == "Flashlight":
                    light_sources.append(
                        {
                            "is_local": False,
                            "visible": compute_visible_tiles(
                                dungeon,
                                doors,
                                ox,
                                oy,
                                light_cx=ox,
                                light_cy=oy,
                                cone_angle=p.get("look_angle", 0.0),
                                cone_half_angle=math.radians(FLASHLIGHT_HALF_ANGLE_DEG),
                                cone_range=FLASHLIGHT_RANGE,
                                close_radius=FLASHLIGHT_CLOSE_RADIUS,
                            ),
                            "brightness_fn": make_light_brightness_fn(
                                ox,
                                oy,
                                FLASHLIGHT_PLATEAU,
                                FLASHLIGHT_RANGE,
                                FLASHLIGHT_MIN_BRIGHTNESS,
                            ),
                        }
                    )
                elif p["equipped_light"] == "Lantern":
                    light_sources.append(
                        {
                            "is_local": False,
                            "visible": compute_visible_tiles(
                                dungeon,
                                doors,
                                ox,
                                oy,
                                radius=LANTERN_FALLOFF_END,
                                light_cx=ox,
                                light_cy=oy,
                            ),
                            "brightness_fn": make_light_brightness_fn(
                                ox,
                                oy,
                                LANTERN_PLATEAU,
                                LANTERN_FALLOFF_END,
                                LANTERN_MIN_BRIGHTNESS,
                            ),
                        }
                    )

        for source in light_sources:
            if not source["is_local"]:
                source["visible"] = {
                    (tx, ty)
                    for (tx, ty) in source["visible"]
                    if has_line_of_sight(dungeon, doors, px, py, tx, ty)
                }

        visible_tiles = ambient_visible
        for src in light_sources:
            visible_tiles = visible_tiles | src["visible"]
        visible_tiles = reveal_boundary_walls(dungeon, doors, visible_tiles)

        def tile_brightness(tx, ty):
            best = (
                get_fog_brightness(
                    light_visual_x,
                    light_visual_y,
                    tx,
                    ty,
                    ellipse_y_ratio=NAKED_EYE_ELLIPSE_Y_RATIO,
                )
                ** 2.5
            )
            for src in light_sources:
                if (tx, ty) in src["visible"]:
                    best = max(best, src["brightness_fn"](tx, ty))
            return best

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
                item_id = items[(wx, wy)]["item_id"]
                draw_char, is_wall_like, should_stretch = (
                    ITEM_GLYPHS.get(item_id, ITEM_GLYPH),
                    False,
                    False,
                )
            else:
                draw_char, is_wall_like = char, (char == WALL)
                should_stretch = is_wall_like

            stretch = (
                get_stretch_factor(px, py, wx, wy, max_range=10)
                if should_stretch
                else None
            )
            brightness = tile_brightness(wx, wy)

            dist = math.hypot(wx - px, wy - py)

            if stretch is not None:
                dx, dy = wx - px, wy - py
                dir_x, dir_y = dx / dist, dy / dist

                base_wall_color = (
                    WALL_COLOR if is_wall_like or char == DOOR else FLOOR_COLOR
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
                if char == LADDER:
                    base_tile_color = LADDER_COLOR
                elif (wx, wy) in items and items[(wx, wy)]["item_id"] == SHOP_TERMINAL:
                    base_tile_color = SHOP_COLOR
                elif active_seed == SHOP_SEED:
                    base_tile_color = (155, 55, 55) if is_wall_like else (72, 32, 32)
                else:
                    base_tile_color = (
                        ITEM_COLOR
                        if (wx, wy) in items
                        else WALL_COLOR if is_wall_like or char == DOOR else FLOOR_COLOR
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
                brightness = tile_brightness(p["x"], p["y"])
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
            local_loot_for_render = players.get(local_client_id, {}).get("loot", [])
        elif terminal.active_character:
            local_items_for_render = terminal.active_character["items"]
            local_loot_for_render = terminal.active_character.get("loot", [])
        else:
            local_items_for_render = []
            local_loot_for_render = []

        sync_world_ui()
        terminal.render(
            screen,
            terminal_rect,
            dungeon,
            discovered,
            (px, py),
            other_players=other_players_for_hud,
            local_items=local_items_for_render,
            local_loot=local_loot_for_render,
            interact_prompt=interact_prompt,
            objective=objective,
            floor_number=floor_number,
            shared_bytes=shared_bytes if terminal.network_mode else None,
        )
        pygame.display.flip()
        # print(f"seed={active_seed}")  # debug

    pygame.quit()


if __name__ == "__main__":
    main()
