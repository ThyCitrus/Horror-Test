import math
import pygame
import socket
import random
import array
import sys
import wave

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
    OBJECTIVE_COMPLETE_GLYPH,
    AGNATE_WARPED_SPAWN,
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
    layout_checksum,
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
from network import (
    GameServer,
    GameClient,
    DEFAULT_PORT,
    VOICE_PORT_OFFSET,
    VOICE_MAX_DISTANCE,
)
from agnate import Agnate, MIN_FLOOR as AGNATE_MIN_FLOOR
from prox_voice import ProximityVoiceChat

WINDOW_WIDTH, WINDOW_HEIGHT = 1200, 660
VIEWPORT_TILES_X, VIEWPORT_TILES_Y = 27, 18
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
OBJECTIVE_COLOR = (70, 150, 255)
OBJECTIVE_COMPLETE_COLOR = (110, 230, 190)
LIGHT_ITEM_IDS = {"Flashlight", "Lantern"}
TEAMMATE_MIN_BRIGHTNESS = 0.2
WALK_MOVE_MS = 150
SPRINT_MOVE_MIN_MS = 90
SPRINT_MOVE_MAX_MS = 120
SPRINT_FALLOFF_POWER = 2.5
STAMINA_MAX = 100.0
STAMINA_DRAIN_PER_S = 25.0
STAMINA_REGEN_MIN_PER_S = 8.0
STAMINA_REGEN_MAX_PER_S = 24.0
STAMINA_CURVE_STEEPNESS = 3.0
STAMINA_REGEN_DELAY_MS = 1000
STAMINA_RECOVER_THRESHOLD = 25.0
AGNATE_GLYPHS = ".:;+oO0@"
ITEM_NAMES = {
    "TestItem": "Test Item",
    "Flashlight": "Flashlight",
    "Lantern": "Lantern",
    "Map": "Map",
    SHOP_TERMINAL: "Shop Terminal",
    OBJECTIVE_TERMINAL: "Objective Terminal",
    AGNATE_WARPED_SPAWN: "Agnate Warp Marker",
    DATA_SCRAP: "Data Scrap",
    ROAMING_SIGNAL: "Roaming Signal",
}  # item_id -> display name, falls back to item_id

OBJECTIVE_LORE = {
    "Arrow Sequence": [
        "[ARCHIVE]: PERSONAL PASSKEY ACCEPTED. AUXILIARY POWER USED TO WAKE",
        "[LOG_NOTE]: PRJCT-4413 SUCCESSFUL. WE ARE NOW TESTING LIMITS OF THE BPU.",
    ],
    "Number Calibration": [
        "[SYS_AUTH]: ADMIN_CREDENTIALS_VERIFIED (LEVEL 4)",
        "[ARCHIVE]: LOCKED FOLDER UNLOCKED. ADDITIONAL FUNCTIONALITY ADDED",
    ],
    "Sine Wave Signal Tuner": [
        "[LOG_NOTE]: HOSTILE PROGRAM MISALIGNED SIGNAL. MANUAL ALIGNMENT ACCEPTED",
        "[SYS_WARN]: LIGHTING GRID COLLAPSED IN SECTOR 02.",
        "[TEXT_STREAM]: I THINK, THEREFORE I AM. I AM, THEREFORE I THINK. I AM, THEREFORE I AM.",
        "[ALARM_TRIP]: IRREGULAR MOVEMENT (SUBLEVEL 0-4/3A)",
    ],
    "Memory Pattern / Simon": [
        "[REQ]: CLEAR GRID SECTOR 04",
        "[LOCAL_OVERRIDE_4413]: THIS ONE. IT LEARNS. PLEASE. CONTINUE TEACHING IT.",
    ],
    "Hotspot Signal Tracker": [
        "",
    ],
}


def next_lore_snippet(character, game_name):
    """Sequential per-run pull from OBJECTIVE_LORE[game_name]: xn, then
    xn+1, etc. Wraps back to the start once a run exhausts a game's list,
    rather than erroring or going silent — flag if you'd rather it stop
    handing out lore for that game once exhausted. Returns None for an
    unrecognized game name so a bad objective_game can't crash a pickup."""
    table = OBJECTIVE_LORE.get(game_name)
    if not table or character is None:
        return None
    progress = character.setdefault("lore_progress", {})
    index = progress.get(game_name, 0)
    progress[game_name] = index + 1
    return table[index % len(table)]


ITEM_GLYPHS = {
    SHOP_TERMINAL: "‰",
    OBJECTIVE_TERMINAL: OBJECTIVE_GLYPH,
    AGNATE_WARPED_SPAWN: "A",
    DATA_SCRAP: random.choice(("¼", "½", "¾")),
    ROAMING_SIGNAL: "S",
}
TOGGLE_LIGHT_KEY = pygame.K_SPACE
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

total_terminals = 0
completed_terminals = 0
LORE_REWARD_CHANCE = 0.13


def get_stretch_factor(player_x, player_y, wall_x, wall_y, max_range=4):
    dist = ((wall_x - player_x) ** 2 + (wall_y - player_y) ** 2) ** 0.5
    if dist <= 0 or dist > max_range:
        return None
    return 1.0 - (dist - 1) / max_range


def make_light_brightness_fn(cx, cy, plateau, falloff_end, min_brightness):
    return lambda tx, ty: get_light_brightness(
        cx, cy, tx, ty, plateau, falloff_end, min_brightness
    )


def load_echoed_sound(path):
    sound = pygame.mixer.Sound(path)
    mixer_config = pygame.mixer.get_init()
    if not mixer_config or mixer_config[1] != -16:
        return sound

    with wave.open(path, "rb") as audio_file:
        if (
            audio_file.getcomptype() != "NONE"
            or audio_file.getsampwidth() != 2
            or audio_file.getframerate() != mixer_config[0]
            or audio_file.getnchannels() != mixer_config[2]
        ):
            return sound
        samples = array.array("h", audio_file.readframes(audio_file.getnframes()))

    if sys.byteorder == "big":
        samples.byteswap()
    taps = (
        (int(mixer_config[0] * 0.11) * mixer_config[2], 0.20),
        (int(mixer_config[0] * 0.23) * mixer_config[2], 0.09),
    )
    echoed = array.array("i", [0]) * (
        len(samples) + max(delay for delay, _ in taps)
    )
    for index, sample in enumerate(samples):
        echoed[index] += sample
        for delay, gain in taps:
            echoed[index + delay] += int(sample * gain)
    output = array.array(
        "h", (max(-32768, min(32767, sample)) for sample in echoed)
    )
    if sys.byteorder == "big":
        output.byteswap()
    return pygame.mixer.Sound(buffer=output.tobytes())


def main():
    pygame.init()
    pygame.mixer.music.load("music/Slow_Creepy_Strings.wav")
    screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.RESIZABLE)
    pygame.display.set_caption("Dungeon Crawler")
    clock = pygame.time.Clock()

    player_heartbeat_sound = pygame.mixer.Sound("sfx/Lving_Heartbeat.wav")
    agnate_heartbeat_sound = load_echoed_sound("sfx/Agnate_Heartbeat.wav")
    death_sound = pygame.mixer.Sound("sfx/Death.wav")
    death_sound.set_volume(0.7)

    static_sound = None
    mixer_config = pygame.mixer.get_init()
    if mixer_config and mixer_config[1] == -16:
        sample_rate, _, channels = mixer_config
        sample_count = int(sample_rate * 0.5)
        samples = array.array("h")
        noise_rng = random.Random()
        filtered = 0.0
        for index in range(sample_count):
            filtered = filtered * 0.55 + noise_rng.randint(-7000, 7000) * 0.45
            sample = max(-32767, min(32767, int(filtered)))
            samples.extend([sample] * channels)
        if sys.byteorder == "big":
            samples.byteswap()
        static_sound = pygame.mixer.Sound(buffer=samples.tobytes())
    sound_channels = {"player": None, "agnate": None, "static": None}

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
    voice_chat = None
    voice_host = "127.0.0.1"
    voice_port = DEFAULT_PORT + VOICE_PORT_OFFSET
    voice_state = {"toggle": False, "last_press": 0}
    spectator_state = {"target": None}
    players = (
        {}
    )  # client_id -> {x,y,visual_x,visual_y,facing,color,name,alive,connected}
    doors = {}  # (x,y) -> {"state": "closed"|"opening"|"open", "anim_start": timestamp}
    items = {}
    floor_number = 1
    objective = None
    total_terminals = 0
    completed_terminals = 0
    active_objective_pos = None
    active_network_objectives = {}
    shared_bytes = 0
    seen_events = set()
    ladder_flags = {"open": False}
    layout_cache = {}
    light_sync = {"t": -10000}
    lore_holders = {}
    agnate = Agnate(seed_rng)
    agnate_state = {
        "data": None,
        "vx": None,
        "vy": None,
        "static_level": 0.0,
    }
    agnate_state["static_rng"] = random.Random()
    wipe = {"at": None, "n": 0}
    death_flag = {"alive": True}
    last_door_target = None
    last_door_target_time = 0

    def make_floor_objective(number, floor_items, terminal_pos=None):
        if terminal_pos is None:
            return None
        item = floor_items.get(tuple(terminal_pos), {})
        game_name = item.get("objective_game")
        if not game_name:
            raise ValueError(f"Terminal at {terminal_pos} has no objective game")
        supported_games = {
            "Arrow Sequence",
            "Number Calibration",
            "Sine Wave Signal Tuner",
            "Flow Puzzle",
            "Memory Pattern / Simon",
            "Active Hold / Pong",
            "Hotspot Signal Tracker",
        }
        if game_name not in supported_games:
            raise ValueError(
                f"Unsupported objective game {game_name!r} at {terminal_pos}"
            )
        objective_type = (
            "roaming"
            if game_name == "Hotspot Signal Tracker"
            else (
                "fast"
                if game_name
                in (
                    "Arrow Sequence",
                    "Number Calibration",
                    "Sine Wave Signal Tuner",
                    "Memory Pattern / Simon",
                )
                else "long"
            )
        )
        target = tuple(terminal_pos) if terminal_pos else None
        required = 1
        return {
            "id": f"floor-{number}-{target or game_name}",
            "type": objective_type,
            "game": game_name,
            "title": game_name,
            "progress": 0,
            "required": required,
            "target_count": 1,
            "target_progress": 0,
            "target": list(target) if target else None,
            "completed": bool(item.get("objective_completed")),
            "terminal_prompt": f"{game_name.upper()} // EXECUTE",
        }

    def objectives_complete():
        if terminal.network_mode and net_server is None:
            return ladder_flags["open"]
        objective_items = [
            item
            for item in items.values()
            if item.get("item_id") in (OBJECTIVE_TERMINAL, ROAMING_SIGNAL)
        ]
        if objective_items and all(
            item.get("objective_completed") for item in objective_items
        ):
            ladder_flags["open"] = True
        return ladder_flags["open"]

    def get_player_count():
        if net_server is not None:
            return max(1, len(net_server.get_players_snapshot()))
        return 1

    def load_floor(number, seed, player_count=None):
        nonlocal dungeon, doors, items, objective, total_terminals, completed_terminals
        if player_count is None:
            player_count = get_player_count()
        dungeon, doors, items = build_floor_dungeon(number, seed, player_count)
        objective = None
        ladder_flags["open"] = False
        total_terminals = sum(
            1
            for item in items.values()
            if item.get("item_id") in (OBJECTIVE_TERMINAL, ROAMING_SIGNAL)
        )
        completed_terminals = 0

    def narrative_for_floor(number, objective_data=None):
        hooks = {
            "Facility network offline": "Facility network offline. Manual descent required."
        }
        names = list(hooks.keys())
        name = names[(number - 1) % len(names)]
        text = hooks[name]
        lore = f"{name} // floor {number}: {text}"
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
        show_ladder = active_seed not in (SHOP_SEED, LOBBY_SEED)
        terminal.set_world_state(
            floor_number,
            objective,
            shared_bytes,
            objectives_complete() if show_ladder else None,
        )

    def equip_item(item_id):
        if item_id not in LIGHT_ITEM_IDS:
            return "Only light sources can be equipped."
        if terminal.network_mode:
            me = players.get(local_client_id)
            if not me or item_id not in me.get("items", []):
                return "Light source is not in inventory."
            if net_client:
                net_client.send_equip(item_id)
            me["equipped_light"] = item_id
            me["light_on"] = False
            light_sync["t"] = pygame.time.get_ticks()
            return f"Equipped {ITEM_NAMES[item_id]}."
        character = terminal.active_character
        if character is not None and item_id in character.get("items", []):
            character["equipped_light"] = item_id
            # Equipping a source starts it in the safe, off state.  This also
            # prevents the old source's state from carrying over.
            character["light_on"] = False
            save_json(character, slot_path(character["slot"]))
            return f"Equipped {ITEM_NAMES[item_id]}."
        return "Light source is not in inventory."

    def deposit_loot():
        nonlocal shared_bytes
        if terminal.network_mode:
            if net_client:
                net_client.send_deposit()
            return "Deposit request sent to Archive."
        character = terminal.active_character
        if not character or not character.get("loot"):
            return "No floor loot to deposit."
        deposited = sum(entry.get("value", 0) for entry in character["loot"])
        character["loot"] = []
        character["bytes"] = character.get("bytes", 0) + deposited
        shared_bytes = character["bytes"]
        save_json(character, slot_path(character["slot"]))
        return f"Deposited loot for {deposited} bytes."

    def handle_slot_hover(slot_info, get_current=False):
        nonlocal active_seed, dungeon, player_x, player_y, player_color, enemies, doors, items, floor_number, objective, shared_bytes, total_terminals, completed_terminals
        if get_current:
            return active_seed, (player_x, player_y)

        if slot_info is None:
            return
        ladder_flags["open"] = False

        if slot_info["filled"]:
            char_data = normalize_character(load_json(slot_info["path"]))
            active_seed = char_data.get("seed", seed_rng.randint(1, 999999))
            floor_number = char_data.get("floor", 0 if active_seed == SHOP_SEED else 1)
            if active_seed == SHOP_SEED:
                dungeon, doors, items = build_shop_dungeon()
                objective = None
                total_terminals, completed_terminals = 0, 0
            else:
                load_floor(floor_number, active_seed)
                objective = make_floor_objective(floor_number, items)
            shared_bytes = char_data.get("bytes", char_data.get("gold", 0))
            preview_floors = [pos for pos, c in dungeon.items() if c in (FLOOR, LADDER)]
            default_pos = preview_floors[0] if preview_floors else (0, 0)
            player_x = char_data.get("player_x", default_pos[0])
            player_y = char_data.get("player_y", default_pos[1])
            player_color = tuple(map(int, char_data["color"].split()))
        else:
            active_seed = SHOP_SEED
            dungeon, doors, items = build_shop_dungeon()
            objective = None
            total_terminals, completed_terminals = 0, 0
            floor_number = 0
            shared_bytes = 100
            preview_floors = [pos for pos, c in dungeon.items() if c in (FLOOR, LADDER)]
            player_x, player_y = preview_floors[0] if preview_floors else (0, 0)
            player_color = (120, 120, 120)

        enemies = []
        sync_world_ui()

    # --- multiplayer glue ---

    def start_host():
        nonlocal net_server, net_client, local_client_id, voice_host, voice_port
        floor_player_count = get_player_count()
        char = terminal.active_character
        net_server = GameServer(
            seed=active_seed,
            port=DEFAULT_PORT,
            spawn_fn=lambda: find_adjacent_spawn(dungeon, player_x, player_y),
            floor_number=floor_number,
            shared_bytes=shared_bytes,
            objective=objective,
            player_count=floor_player_count,
            saved_players=(
                char.get("multiplayer_players", {}) if char else {}
            ),
        )
        net_server.set_floor(
            active_seed, floor_number, floor_player_count, layout_checksum(dungeon)
        )
        net_server.start()
        agnate.reset(
            floor_number,
            active_seed != SHOP_SEED and floor_number >= AGNATE_MIN_FLOOR,
            pygame.time.get_ticks(),
        )
        net_server.set_agnate(agnate.snapshot(pygame.time.get_ticks()))
        net_client = GameClient()
        net_client.connect("127.0.0.1", DEFAULT_PORT)
        voice_host = "127.0.0.1"
        voice_port = DEFAULT_PORT + VOICE_PORT_OFFSET
        local_client_id = net_client.client_id
        net_client.send_join(char["name"], char["color"])
        terminal.set_hosting_info(f"{get_local_ip()}:{DEFAULT_PORT}")

    def start_voice_chat(token, port):
        nonlocal voice_chat, voice_port
        if voice_chat:
            return
        voice_port = port
        try:
            voice_chat = ProximityVoiceChat(
                voice_host, voice_port, local_client_id, token
            )
        except Exception as exc:
            voice_chat = None
            terminal.set_transient(
                f"Voice unavailable: {exc}",
                (255, 180, 80),
                duration_ms=6000,
            )

    def attempt_join(address_text):
        nonlocal net_client, local_client_id, voice_host, voice_port
        host_part, _, port_part = address_text.partition(":")
        try:
            port = int(port_part) if port_part else DEFAULT_PORT
            client = GameClient()
            client.connect(host_part, port)
            net_client = client
            voice_host = host_part
            voice_port = port + VOICE_PORT_OFFSET
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
                if item_id == terminal.active_character.get("equipped_light"):
                    terminal.active_character["equipped_light"] = None
                    terminal.active_character["light_on"] = False
                save_json(
                    terminal.active_character,
                    slot_path(terminal.active_character["slot"]),
                )

    def drop_loot(index):
        if terminal.network_mode:
            return
        if terminal.active_character:
            loot = terminal.active_character.get("loot", [])
            if 0 <= index < len(loot):
                entry = loot.pop(index)
                drop_pos = find_drop_position(dungeon, items, player_x, player_y)
                items[drop_pos] = {
                    "item_id": DATA_SCRAP,
                    "name": entry.get("name", "Recovered data"),
                    "value": entry.get("value", 0),
                }
                save_json(
                    terminal.active_character,
                    slot_path(terminal.active_character["slot"]),
                )

    def persist_multiplayer_character():
        character = terminal.active_character
        if not character or local_client_id is None:
            return
        player = None
        if net_server:
            player = net_server.get_players_snapshot().get(local_client_id)
        if player is None:
            player = players.get(local_client_id)
        if player is None:
            return
        character["items"] = list(player.get("items", []))
        character["loot"] = list(player.get("loot", []))
        character["equipped_light"] = player.get("equipped_light")
        character["light_on"] = bool(
            player.get("light_on", False) and character["equipped_light"]
        )
        saved_players = {}
        if net_server:
            for client_id, player_state in net_server.get_players_snapshot().items():
                if client_id == local_client_id:
                    continue
                saved_players[client_id] = {
                    key: player_state[key]
                    for key in (
                        "name",
                        "color",
                        "x",
                        "y",
                        "facing",
                        "alive",
                        "look_angle",
                        "items",
                        "loot",
                        "equipped_light",
                        "light_on",
                    )
                    if key in player_state
                }
        character["multiplayer_players"] = saved_players
        character.update(
            {
                "seed": active_seed,
                "floor": floor_number,
                "player_x": player.get("x", player_x),
                "player_y": player.get("y", player_y),
                "bytes": (
                    net_server.shared_bytes
                    if net_server
                    else shared_bytes
                ),
            }
        )
        save_json(character, slot_path(character["slot"]))

    def teardown_multiplayer():
        nonlocal net_server, net_client, local_client_id, players, doors, items, voice_chat
        persist_multiplayer_character()
        if voice_chat:
            voice_chat.close()
            voice_chat = None
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
        if terminal.network_mode:
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
            character["light_on"] = False
        save_json(character, slot_path(character["slot"]))
        return f"Purchased {ITEM_NAMES[item_id]}."

    def complete_objective(action_index=0, from_server=False):
        nonlocal objective, active_objective_pos, completed_terminals
        if terminal.network_mode and not from_server:
            if net_client:
                net_client.send_objective_complete()
            return ""
        if not objective:
            return "No active objective."
        if objective.get("completed"):
            return "Objective already complete."
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
        # The UI uses progress while the objective resolver uses target_progress.
        # Keep both representations synchronized when the puzzle reports a
        # successful action.
        objective["progress"] = min(
            objective.get("required", 1), objective["target_progress"]
        )
        if objective["completed"]:
            objective["progress"] = objective["required"]
        if active_objective_pos is not None:
            item = items.get(active_objective_pos)
            if item and item.get("item_id") in (OBJECTIVE_TERMINAL, ROAMING_SIGNAL):
                if not item.get("objective_completed"):
                    completed_terminals += 1
                item["objective_completed"] = True
            active_objective_pos = None
        if terminal.active_character:
            terminal.active_character["objective"] = objective
            save_json(
                terminal.active_character,
                slot_path(terminal.active_character["slot"]),
            )
        if net_server:
            net_server.set_world_state(objective=objective)
        return ""

    def advance_floor():
        nonlocal active_seed, dungeon, doors, items, player_x, player_y, floor_number, objective, shared_bytes, discovered
        floor_number += 1
        floor_player_count = get_player_count()
        if floor_number % 3 == 0:
            active_seed = SHOP_SEED
            dungeon, doors, items = build_shop_dungeon()
            objective = None
            ladder_flags["open"] = False
        else:
            active_seed = seed_rng.randint(1, 999999)
            load_floor(floor_number, active_seed, floor_player_count)
        doors = {}
        discovered.clear()
        floor_tiles = [pos for pos, char in dungeon.items() if char in (FLOOR, LADDER)]
        player_x, player_y = floor_tiles[0] if floor_tiles else (0, 0)
        if net_server:
            shared_bytes = net_server.shared_bytes
        elif terminal.active_character:
            shared_bytes = terminal.active_character.get("bytes", 0)
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
                net_server.set_player_alive(cid, True)
                if cid in players:
                    players[cid]["x"] = spawn_x
                    players[cid]["y"] = spawn_y
        narrative_for_floor(floor_number, objective)
        if net_server:
            net_server.set_floor(
                active_seed,
                floor_number,
                floor_player_count,
                layout_checksum(dungeon),
            )
            net_server.set_world_state(
                shared_bytes=shared_bytes,
                objective=objective,
                ladder_open=objectives_complete(),
            )
        agnate.reset(
            floor_number,
            active_seed != SHOP_SEED and floor_number >= AGNATE_MIN_FLOOR,
            pygame.time.get_ticks(),
        )
        agnate_state.update(data=None, vx=None, vy=None)
        if net_server:
            net_server.set_agnate(None)
        sync_world_ui()

    terminal = TerminalUI(
        font,
        bold_font,
        handle_slot_hover,
        on_join_address=attempt_join,
        on_mp_color_confirm=confirm_mp_color,
        on_multiplayer_quit=teardown_multiplayer,
        on_drop_item=drop_item,
        on_drop_loot=drop_loot,
        on_shop_purchase=purchase_shop_item,
        on_objective_action=complete_objective,
        on_equip_item=equip_item,
        on_deposit_loot=deposit_loot,
    )
    # Keep the concrete terminal instance receiving input.  This is deliberately
    # a reference, rather than a separate ``in_terminal`` flag, so a terminal
    # screen cannot accidentally keep using another terminal's input buffer.
    active_terminal = terminal

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
                elif pygame.time.get_ticks() - light_sync["t"] > 400:
                    p["light_on"] = pdata.get("light_on", False)

    def handle_network_message(msg):
        nonlocal active_seed, dungeon, doors, items, floor_number, objective, shared_bytes, active_objective_pos
        mtype = msg.get("type")

        if mtype == "roster":
            if msg.get("voice_available", True) and msg.get("voice_token"):
                start_voice_chat(
                    msg["voice_token"],
                    msg.get("voice_port", DEFAULT_PORT + VOICE_PORT_OFFSET),
                )
            elif msg.get("voice_available") is False:
                terminal.set_transient(
                    f"Voice relay unavailable: {msg.get('voice_error', 'UDP port unavailable')}",
                    (255, 180, 80),
                    duration_ms=6000,
                )
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

        elif mtype == "objective_start":
            remote_objective = msg.get("objective") or objective
            target = msg.get("target")
            objective_pos = msg.get("objective_pos")
            if objective_pos:
                active_objective_pos = tuple(objective_pos)
            if (
                remote_objective
                and remote_objective.get("type") == "roaming"
                and target
            ):
                if terminal.network_mode and local_client_id in players:
                    local_position = (
                        players[local_client_id]["x"],
                        players[local_client_id]["y"],
                    )
                else:
                    local_position = (player_x, player_y)
                terminal.start_signal_tracker(remote_objective, target, local_position)
            elif remote_objective:
                terminal.open_objective_terminal(remote_objective)
            if net_client:
                net_client.send_input(0, 0)

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
                char = terminal.active_character
                net_server.set_player_inventory(
                    local_client_id,
                    char.get("items", []) if char else [],
                    char.get("loot", []) if char else [],
                    char.get("equipped_light") if char else None,
                )
                net_server.update_player_position(
                    local_client_id, player_x, player_y, player_facing
                )
                net_server.set_player_alive(local_client_id, True)
            terminal.enter_multiplayer_playing(msg["name"], msg["color"])

        elif mtype == "join_reject":
            terminal.mp_join_rejected(
                msg.get("reason"),
                msg.get("taken_names", []),
                msg.get("taken_colors", []),
            )

        elif mtype == "state":
            agnate_state["data"] = msg.get("agnate")
            if net_server is None:
                ladder_flags["open"] = msg.get("ladder_open", False)
            sync_players_from_state(msg["players"])
            incoming_seed = msg.get("seed", active_seed)
            if net_server is None and incoming_seed != active_seed:
                active_seed = incoming_seed
                layout_cache.clear()
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
            if net_server is None and msg.get("layout_crc") is not None:
                if layout_cache.get("id") != id(dungeon):
                    layout_cache["id"] = id(dungeon)
                    layout_cache["crc"] = layout_checksum(dungeon)
                if layout_cache["crc"] != msg["layout_crc"]:
                    terminal.set_transient(
                        "MAP MISMATCH WITH HOST - are your files identical?",
                        (255, 80, 80),
                        duration_ms=1000,
                    )
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
            sync_world_ui()

        elif mtype == "lore":
            text = msg.get("text")
            if text:
                if terminal.active_character:
                    add_notepad_entry(terminal.active_character, text)
                    save_json(
                        terminal.active_character,
                        slot_path(terminal.active_character["slot"]),
                    )
                elif text not in terminal.mp_notepad:
                    terminal.mp_notepad.append(text)
                terminal.set_transient(
                    "Fragment archived to Data.",
                    (130, 220, 255),
                    duration_ms=2000,
                )

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
        set_loop_channel("player", player_heartbeat_sound, 0.0)
        set_loop_channel("agnate", agnate_heartbeat_sound, 0.0)
        set_loop_channel("static", static_sound, 0.0)
        pygame.mixer.music.set_volume(1.0)
        pygame.mixer.music.play(-1)

    def stop_menu_music():
        pygame.mixer.music.stop()

    def set_loop_channel(key, sound, volume, pan=0.0):
        channel = sound_channels[key]
        if sound is None or volume <= 0.005:
            if channel:
                channel.stop()
            sound_channels[key] = None
            return
        if channel is None or not channel.get_busy():
            channel = sound.play(-1)
            sound_channels[key] = channel
        if channel is not None:
            pan = max(-1.0, min(1.0, pan))
            left = volume * (1.0 - max(0.0, pan))
            right = volume * (1.0 + min(0.0, pan))
            channel.set_volume(min(1.0, left), min(1.0, right))

    play_menu_music()

    stamina = {
        "value": STAMINA_MAX,
        "exhausted": False,
        "regen_at": 0,
        "last_pos": None,
        "moved_at": 0,
    }
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

        local_dead = (
            terminal.network_mode
            and local_client_id in players
            and not players[local_client_id].get("alive", True)
        )
        if local_dead:
            spectate_candidates = [
                cid
                for cid, player in players.items()
                if cid != local_client_id
                and player.get("connected", True)
                and player.get("alive", True)
            ]
            if spectator_state["target"] not in spectate_candidates:
                spectator_state["target"] = (
                    spectate_candidates[0] if spectate_candidates else None
                )
        else:
            spectator_state["target"] = None

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif (
                event.type == pygame.KEYUP
                and event.key == pygame.K_v
                and voice_chat
            ):
                if not voice_state["toggle"]:
                    try:
                        voice_chat.stop_talking()
                    except RuntimeError as exc:
                        terminal.set_transient(
                            str(exc), (255, 180, 80), duration_ms=3000
                        )
            elif (
                local_dead
                and event.type == pygame.KEYDOWN
                and event.key == pygame.K_TAB
            ):
                if spectate_candidates:
                    current_index = spectate_candidates.index(spectator_state["target"])
                    spectator_state["target"] = spectate_candidates[
                        (current_index + 1) % len(spectate_candidates)
                    ]
            elif terminal.state in TEXT_INPUT_STATES:
                active_terminal.handle_input(event)
            elif (
                event.type == pygame.KEYDOWN
                and event.key == pygame.K_v
                and voice_chat
                and terminal.state in ("PLAYING", "INVENTORY", "MAP")
                and not local_dead
                and not getattr(event, "repeat", False)
            ):
                press_time = pygame.time.get_ticks()
                double_tap = (
                    voice_state["last_press"]
                    and press_time - voice_state["last_press"] <= 350
                )
                voice_state["last_press"] = 0 if double_tap else press_time
                try:
                    if double_tap:
                        voice_state["toggle"] = not voice_state["toggle"]
                        if voice_state["toggle"]:
                            voice_chat.start_talking()
                        else:
                            voice_chat.stop_talking()
                    elif not voice_state["toggle"]:
                        voice_chat.start_talking()
                except RuntimeError as exc:
                    voice_state["toggle"] = False
                    terminal.set_transient(
                        str(exc), (255, 180, 80), duration_ms=3000
                    )
            elif (
                event.type == pygame.KEYDOWN
                and event.key == pygame.K_m
                and voice_chat
                and terminal.state in ("PLAYING", "INVENTORY", "MAP")
            ):
                voice_chat.set_muted(not voice_chat.muted)
                terminal.set_transient(
                    "Voice muted." if voice_chat.muted else "Voice unmuted.",
                    (220, 220, 220),
                    duration_ms=1200,
                )
            # Inventory and map are overlays, so movement input must continue
            # to reach the world while they are open. Interactive architecture
            # (shop, terminals, etc.) remains modal and consumes input here.
            elif terminal.is_modal_game() and terminal.state not in (
                "INVENTORY",
                "MAP",
            ):
                if active_terminal is not None:
                    active_terminal.handle_input(event)
            elif (
                terminal.signal_tracker
                and event.type == pygame.KEYDOWN
                and event.key in (pygame.K_ESCAPE, pygame.K_q)
            ):
                if active_terminal is not None:
                    active_terminal.handle_input(event)
            elif event.type == pygame.KEYDOWN and event.key in DIRECTION_KEYS:
                pending_moves[event.key] = now
                buffered_move = event.key
            elif event.type == pygame.KEYUP and event.key in pending_moves:
                del pending_moves[event.key]
            elif (
                event.type == pygame.KEYDOWN
                and event.key == INTERACT_KEY
                and terminal.state in ("PLAYING", "INVENTORY", "MAP")
                and not local_dead
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
                        terminal.open_shop(
                            terminal.active_character,
                            shared_bytes if terminal.network_mode else None,
                            on_deposit=deposit_loot,
                        )
                    elif item_id == DATA_SCRAP and terminal.network_mode:
                        net_client.send_pickup(*pos)
                    elif item_id in (OBJECTIVE_TERMINAL, ROAMING_SIGNAL) and not items[
                        pos
                    ].get("objective_completed"):
                        if terminal.network_mode:
                            net_client.send_interact(*pos)
                        elif item_id == OBJECTIVE_TERMINAL:
                            active_objective_pos = pos
                            objective = make_floor_objective(floor_number, items, pos)
                            terminal.open_objective_terminal(objective)
                        elif objective and not objective.get("completed"):
                            candidates = [
                                candidate
                                for candidate, tile in dungeon.items()
                                if tile == FLOOR
                                and candidate not in items
                                and abs(candidate[0] - player_x)
                                + abs(candidate[1] - player_y)
                                >= 6
                            ]
                            if candidates:
                                target = max(
                                    candidates,
                                    key=lambda candidate: abs(candidate[0] - player_x)
                                    + abs(candidate[1] - player_y),
                                )
                                active_objective_pos = pos
                                terminal.start_signal_tracker(
                                    make_floor_objective(floor_number, items, pos),
                                    target,
                                    (player_x, player_y),
                                )
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
                        elif item_id in (OBJECTIVE_TERMINAL, ROAMING_SIGNAL):
                            snippet = None
                            if seed_rng.random() < LORE_REWARD_CHANCE:
                                snippet = next_lore_snippet(
                                    terminal.active_character,
                                    item.get("objective_game", ""),
                                )
                            if snippet and terminal.active_character:
                                add_notepad_entry(terminal.active_character, snippet)
                            terminal.add_system_event(
                                "Terminal decommissioned; fragment archived to Data."
                            )
                        else:
                            terminal.active_character["items"].append(item_id)
                            if item_id in LIGHT_ITEM_IDS:
                                terminal.active_character["equipped_light"] = item_id
                                terminal.active_character["light_on"] = False
                        terminal.active_character["objective"] = objective
                        save_json(
                            terminal.active_character,
                            slot_path(terminal.active_character["slot"]),
                        )
                elif pos in dungeon and dungeon[pos] == LADDER:
                    if active_seed != SHOP_SEED and not objectives_complete():
                        terminal.set_transient(
                            "Ladder locked: complete every terminal objective.",
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
                event.type == pygame.KEYDOWN
                and event.key == TOGGLE_LIGHT_KEY
                and terminal.state in ("PLAYING", "INVENTORY", "MAP")
            ):
                if terminal.network_mode and local_client_id in players:
                    me = players[local_client_id]
                    if me.get("equipped_light"):
                        me["light_on"] = not me.get("light_on", False)
                        light_sync["t"] = now
                        if net_client:
                            net_client.send_set_light(me["light_on"])
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

        if terminal.network_mode and local_client_id in players:
            alive_now = players[local_client_id].get("alive", True)
            if death_flag["alive"] and not alive_now:
                terminal.set_transient(
                    "[SYS_WARN]: SIGNAL LOST. OPERATOR TERMINATED.",
                    (255, 70, 70),
                    duration_ms=4000,
                )
                death_sound.play()
            death_flag["alive"] = alive_now
            if not alive_now and voice_chat and voice_chat.transmitting:
                try:
                    voice_chat.stop_talking()
                    voice_state["toggle"] = False
                except RuntimeError as exc:
                    terminal.set_transient(
                        str(exc), (255, 180, 80), duration_ms=3000
                    )

        if voice_chat:
            for voice_message in voice_chat.update():
                if voice_message[0] == "error":
                    terminal.set_transient(
                        voice_message[1], (255, 180, 80), duration_ms=3000
                    )
            local_voice_player = players.get(local_client_id, {})
            speaker_levels = {}
            if local_voice_player.get("alive", True) and not voice_chat.muted:
                local_x = local_voice_player.get("x", 0)
                local_y = local_voice_player.get("y", 0)
                local_look = local_voice_player.get("look_angle", 0.0)
                for speaker_id, speaker in players.items():
                    if (
                        speaker_id == local_client_id
                        or not speaker.get("connected", True)
                        or not speaker.get("alive", True)
                    ):
                        continue
                    dx = speaker.get("x", 0) - local_x
                    dy = speaker.get("y", 0) - local_y
                    distance = math.hypot(dx, dy)
                    if distance > VOICE_MAX_DISTANCE:
                        continue
                    volume = (
                        0.8
                        * max(0.0, 1.0 - distance / VOICE_MAX_DISTANCE) ** 1.4
                    )
                    has_sight = has_line_of_sight(
                        dungeon,
                        doors,
                        local_x,
                        local_y,
                        speaker.get("x", 0),
                        speaker.get("y", 0),
                    )
                    if not has_sight:
                        volume *= 0.38
                    relative_angle = math.atan2(dy, dx) - local_look
                    speaker_levels[speaker_id] = (
                        volume,
                        math.sin(relative_angle),
                        not has_sight,
                    )
            voice_chat.set_speaker_levels(speaker_levels)

        agnate_data = agnate_state["data"]
        local_player = players.get(local_client_id, {})
        audio_active = bool(
            terminal.state
            in ("PLAYING", "INVENTORY", "MAP", "SHOP", "TERMINAL_GAME")
            and terminal.network_mode
            and local_player
            and local_player.get("alive", True)
        )
        in_containment = (
            audio_active
            and agnate_data
            and agnate_data.get("mode") == "containment"
            and agnate_data.get("target") == local_client_id
        )
        ag_mode = agnate_data.get("mode") if agnate_data else None
        is_targeted = (
            audio_active
            and agnate_data
            and agnate_data.get("static_target") == local_client_id
        )
        static_intensity = (
            max(
                agnate_state["static_level"],
                agnate_data.get("static", 0.0),
            )
            if is_targeted
            else 0.0
        )
        player_heartbeat_volume = 0.0
        if is_targeted and ag_mode in ("windup", "chase", "containment"):
            player_heartbeat_volume = min(
                0.55, 0.16 + 0.36 * max(static_intensity, 0.0)
            )
        set_loop_channel(
            "player", player_heartbeat_sound, player_heartbeat_volume
        )
        set_loop_channel(
            "static",
            static_sound,
            min(0.24, static_intensity * 0.24),
        )

        agnate_heartbeat_volume = 0.0
        agnate_heartbeat_pan = 0.0
        audible_modes = ("mimic", "windup", "chase", "containment", "retreat")
        if audio_active and agnate_data and ag_mode in audible_modes:
            ag_distance = math.hypot(
                agnate_data["x"] - local_player["x"],
                agnate_data["y"] - local_player["y"],
            )
            if ag_distance < 14.0:
                agnate_heartbeat_volume = 0.4 * (1.0 - ag_distance / 14.0)
                if not has_line_of_sight(
                    dungeon,
                    doors,
                    local_player["x"],
                    local_player["y"],
                    agnate_data["x"],
                    agnate_data["y"],
                ):
                    agnate_heartbeat_volume *= 0.28
                agnate_heartbeat_pan = (
                    agnate_data["x"] - local_player["x"]
                ) / max(1.0, ag_distance)
        set_loop_channel(
            "agnate",
            agnate_heartbeat_sound,
            agnate_heartbeat_volume,
            agnate_heartbeat_pan,
        )

        if terminal.state == "PLAYING":
            stop_menu_music()
            if in_containment:
                pygame.mixer.music.set_volume(0.0)
            else:
                pygame.mixer.music.set_volume(1.0)

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
        can_move = terminal.state in ("PLAYING", "INVENTORY", "MAP") and (
            (terminal.active_character and not terminal.network_mode)
            or (
                terminal.network_mode
                and local_client_id in players
                and players[local_client_id].get("alive", True)
            )
        )
        # --- sprint / stamina ---
        if terminal.network_mode and local_client_id in players:
            cur_pos = (players[local_client_id]["x"], players[local_client_id]["y"])
        else:
            cur_pos = (player_x, player_y)
        if cur_pos != stamina["last_pos"]:
            stamina["last_pos"] = cur_pos
            stamina["moved_at"] = now
        keys_now = pygame.key.get_pressed()
        sprint_held = keys_now[pygame.K_LSHIFT] or keys_now[pygame.K_RSHIFT]
        recently_moved = now - stamina["moved_at"] < 300
        movement_intended = buffered_move in DIRECTION_KEYS or any(
            keys_now[key] for key in DIRECTION_KEYS
        )
        sprint_intended = (
            can_move
            and sprint_held
            and movement_intended
            and not stamina["exhausted"]
            and stamina["value"] > 0
        )
        stamina_fraction = stamina["value"] / STAMINA_MAX
        stamina_curve = math.log1p((math.e - 1.0) * stamina_fraction)
        sprint_curve = stamina_curve**SPRINT_FALLOFF_POWER
        dt_s = dt / 1000.0
        draining_stamina = sprint_intended and recently_moved
        if draining_stamina:
            stamina["value"] = max(0.0, stamina["value"] - STAMINA_DRAIN_PER_S * dt_s)
            stamina["regen_at"] = now + STAMINA_REGEN_DELAY_MS
            if stamina["value"] <= 0:
                stamina["exhausted"] = True
        elif not sprint_held and now >= stamina["regen_at"]:
            regen_per_s = STAMINA_REGEN_MIN_PER_S + (
                STAMINA_REGEN_MAX_PER_S - STAMINA_REGEN_MIN_PER_S
            ) * (1.0 - stamina_curve)
            stamina["value"] = min(STAMINA_MAX, stamina["value"] + regen_per_s * dt_s)
            if stamina["exhausted"] and stamina["value"] >= STAMINA_RECOVER_THRESHOLD:
                stamina["exhausted"] = False
        move_interval = (
            SPRINT_MOVE_MIN_MS
            + (SPRINT_MOVE_MAX_MS - SPRINT_MOVE_MIN_MS) * (1.0 - sprint_curve)
            if sprint_intended
            else WALK_MOVE_MS
        )

        if can_move and time_since_last_move >= move_interval:
            candidates = [
                (pending_moves.get(k, now), k) for k in DIRECTION_KEYS if keys_now[k]
            ]
            chosen_key = buffered_move
            buffered_move = None

            if chosen_key is None and candidates:
                held_keys = [k for _, k in candidates if keys_now[k]]

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
                    net_client.send_input(dx, dy, sprint_intended)
                else:
                    target_x, target_y = player_x + dx, player_y + dy
                    blocking_item = items.get((target_x, target_y), {}).get("item_id")
                    if blocking_item not in (
                        OBJECTIVE_TERMINAL,
                        SHOP_TERMINAL,
                        ROAMING_SIGNAL,
                    ) and is_walkable(dungeon, doors, target_x, target_y, dx, dy):
                        player_x, player_y = target_x, target_y

                time_since_last_move = 0

        if terminal.signal_tracker:
            if terminal.network_mode and local_client_id in players:
                terminal.update_signal_tracker(
                    (players[local_client_id]["x"], players[local_client_id]["y"])
                )
            elif not terminal.network_mode:
                terminal.update_signal_tracker((player_x, player_y))
        terminal.update_objective_game(dt)

        # --- host-only: resolve pending interacts, door animation, and movement ---
        if net_server is not None:
            net_server.set_doors_snapshot(doors)
            net_server.set_items_snapshot(items)
            for cid in net_server.consume_pending_objective_completions():
                player_state = net_server.get_players_snapshot().get(cid)
                if not player_state or not player_state.get("alive", True):
                    continue
                active_objective_pos = active_network_objectives.pop(cid, None)
                if active_objective_pos is not None:
                    objective = make_floor_objective(
                        floor_number, items, active_objective_pos
                    )
                complete_objective(from_server=True)
            for cid, ix, iy in net_server.consume_pending_interacts():
                player_state = net_server.get_players_snapshot().get(cid)
                if not player_state or not player_state.get("alive", True):
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
                    if item_id in (OBJECTIVE_TERMINAL, ROAMING_SIGNAL):
                        terminal_objective = make_floor_objective(
                            floor_number, items, (ix, iy)
                        )
                        player_pos = (player_state["x"], player_state["y"])
                        target = None
                        if item_id == ROAMING_SIGNAL:
                            candidates = [
                                candidate
                                for candidate, tile in dungeon.items()
                                if tile == FLOOR
                                and candidate not in items
                                and abs(candidate[0] - player_pos[0])
                                + abs(candidate[1] - player_pos[1])
                                >= 6
                            ]
                            if candidates:
                                target = max(
                                    candidates,
                                    key=lambda candidate: abs(
                                        candidate[0] - player_pos[0]
                                    )
                                    + abs(candidate[1] - player_pos[1]),
                                )
                        active_network_objectives[cid] = (ix, iy)
                        net_server.send_objective_start(
                            cid,
                            terminal_objective,
                            target,
                            objective_pos=(ix, iy),
                        )
            for cid, ix, iy in net_server.consume_pending_pickups():
                player_state = net_server.get_players_snapshot().get(cid)
                if not player_state or not player_state.get("alive", True):
                    continue
                pos = (ix, iy)
                item = items.get(pos)
                if not item:
                    continue
                item_id = item["item_id"]
                if item_id == SHOP_TERMINAL:
                    continue
                if item_id in (OBJECTIVE_TERMINAL, ROAMING_SIGNAL):
                    if item.get("objective_completed"):
                        items.pop(pos)
                        if seed_rng.random() < LORE_REWARD_CHANCE:
                            snippet = next_lore_snippet(
                                lore_holders.setdefault(cid, {}),
                                item.get("objective_game", ""),
                            )
                            if snippet:
                                net_server.send_lore(cid, snippet)
                    continue
                items.pop(pos)
                if item_id == DATA_SCRAP:
                    net_server.add_loot_to_player(
                        cid,
                        {
                            "name": item.get("name", "Recovered data"),
                            "value": item.get("value", 10),
                        },
                    )
                    net_server.set_world_state(objective=objective)
                else:
                    net_server.add_item_to_player(cid, item_id)
                    if item_id in LIGHT_ITEM_IDS:
                        net_server.set_equipped_light(cid, item_id)
                        with net_server._lock:
                            net_server.players[cid]["light_on"] = False
            for cid, item_id in net_server.consume_pending_purchases():
                player_state = net_server.get_players_snapshot().get(cid)
                if not player_state or not player_state.get("alive", True):
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
                if not player_state or not player_state.get("alive", True):
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
                pdata = net_server.get_players_snapshot().get(cid)
                ladder = next(
                    (pos for pos, tile in dungeon.items() if tile == LADDER), None
                )
                if (
                    pdata
                    and pdata.get("alive", True)
                    and ladder
                    and abs(pdata["x"] - ladder[0]) + abs(pdata["y"] - ladder[1]) <= 1
                ):
                    if active_seed == SHOP_SEED or objectives_complete():
                        advance_floor()
                        descended = True
                    else:
                        net_server.send_purchase_result(
                            cid,
                            "Ladder locked: complete every terminal objective.",
                        )
            shared_bytes = net_server.shared_bytes
            net_server.set_world_state(
                floor_number=floor_number,
                objective=objective,
                ladder_open=objectives_complete(),
            )
            for cid, index in net_server.consume_pending_drops():
                snap = net_server.get_players_snapshot().get(cid)
                if not snap or not snap.get("alive", True):
                    continue
                item_id = net_server.pop_item_from_player(cid, index)
                if item_id is not None:
                    with net_server._lock:
                        if net_server.players[cid].get("equipped_light") == item_id:
                            net_server.players[cid]["equipped_light"] = None
                            net_server.players[cid]["light_on"] = False
                    drop_pos = find_drop_position(dungeon, items, snap["x"], snap["y"])
                    items[drop_pos] = {"item_id": item_id}
            advance_door_animations(doors, pygame.time.get_ticks())
            agnate_moves = {}
            for cid, pdata in net_server.get_players_snapshot().items():
                mdx, mdy, sprinting = net_server.consume_and_clear_input(cid)
                if mdx == 0 and mdy == 0:
                    continue
                if not pdata.get("alive", True):
                    continue
                agnate_moves[cid] = {
                    "dx": mdx,
                    "dy": mdy,
                    "sprinting": sprinting,
                    "facing": pdata["facing"],
                }
                target_x, target_y = pdata["x"] + mdx, pdata["y"] + mdy
                blocking_item = items.get((target_x, target_y), {}).get("item_id")
                if blocking_item not in (
                    OBJECTIVE_TERMINAL,
                    SHOP_TERMINAL,
                    ROAMING_SIGNAL,
                ) and is_walkable(dungeon, doors, target_x, target_y, mdx, mdy):
                    net_server.update_player_position(
                        cid, target_x, target_y, pdata["facing"]
                    )
                else:
                    net_server.update_player_position(
                        cid, pdata["x"], pdata["y"], pdata["facing"]
                    )
            now_ms = pygame.time.get_ticks()
            if agnate.enabled:
                blocked = {
                    pos
                    for pos, item in items.items()
                    if item.get("item_id")
                    in (OBJECTIVE_TERMINAL, SHOP_TERMINAL, ROAMING_SIGNAL)
                }
                for kind, data in agnate.update(
                    now_ms,
                    dungeon,
                    doors,
                    blocked,
                    net_server.get_players_snapshot(),
                    agnate_moves,
                    floor_number,
                    FLASHLIGHT_RANGE,
                    math.radians(FLASHLIGHT_HALF_ANGLE_DEG),
                ):
                    if kind == "kill":
                        net_server.set_player_alive(data, False)
                    elif kind == "door":
                        door = materialize_door(dungeon, doors, *data)
                        begin_door_toggle(door, now_ms)
            net_server.set_agnate(agnate.snapshot(now_ms))

            roster = net_server.get_players_snapshot()
            connected = [
                player for player in roster.values() if player.get("connected", True)
            ]
            if connected and not any(player.get("alive", True) for player in connected):
                if wipe["at"] is None:
                    wipe["at"] = now_ms + 5000
                elif now_ms >= wipe["at"]:
                    wipe["n"] += 1
                    wipe["at"] = None
                    with net_server._lock:
                        for player in net_server.players.values():
                            player["loot"] = []
                    if terminal.active_character:
                        terminal.active_character["loot"] = []
                        save_json(
                            terminal.active_character,
                            slot_path(terminal.active_character["slot"]),
                        )
                    net_server.add_event(
                        f"[SYS_WARN]: ALL OPERATORS LOST. RUN TERMINATED. #{wipe['n']}"
                    )
                    floor_number = -1
                    advance_floor()
            else:
                wipe["at"] = None
        elif not terminal.network_mode:
            advance_door_animations(doors, pygame.time.get_ticks())

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
            camera_player_id = (
                spectator_state["target"]
                if local_dead and spectator_state["target"] in players
                else local_client_id
            )
            camera_player = players.get(camera_player_id, players.get(local_client_id))
            cam_x, cam_y = camera_player["visual_x"], camera_player["visual_y"]
            px, py = camera_player["x"], camera_player["y"]
            display_facing = camera_player["facing"]
            display_color = tuple(map(int, camera_player["color"].split()))
        else:
            camera_player_id = None
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
                interact_prompt = "[E] Descend to facility"

            if terminal.network_mode and local_client_id in players:
                equipped_light = players[local_client_id].get("equipped_light")
                light_on = players[local_client_id].get("light_on", False)
            elif terminal.active_character:
                equipped_light = terminal.active_character.get("equipped_light")
                light_on = terminal.active_character.get("light_on", False)
            else:
                equipped_light = None
                light_on = False
            if equipped_light not in (
                players[local_client_id].get("items", [])
                if terminal.network_mode and local_client_id in players
                else (
                    terminal.active_character.get("items", [])
                    if terminal.active_character
                    else []
                )
            ):
                equipped_light = None
                light_on = False
            if equipped_light:
                state = "ON" if light_on else "off"
                light_line = (
                    f"[Space] {ITEM_NAMES.get(equipped_light, equipped_light)}: {state}"
                )
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

        # A stale equipped_light must never keep the toggle control or its
        # physical light alive after the item leaves the inventory.
        owned_lights = (
            players.get(local_client_id, {}).get("items", [])
            if terminal.network_mode
            else (
                terminal.active_character.get("items", [])
                if terminal.active_character
                else []
            )
        )
        if equipped_light not in owned_lights:
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
        locally_visible_tiles = set(visible_tiles)

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

        discovered.update(locally_visible_tiles)
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
                item = items[(wx, wy)]
                item_id = item["item_id"]
                draw_char, is_wall_like, should_stretch = (
                    (
                        OBJECTIVE_COMPLETE_GLYPH
                        if item.get("objective_completed")
                        else ITEM_GLYPHS.get(item_id, ITEM_GLYPH)
                    ),
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
                        OBJECTIVE_COMPLETE_COLOR
                        if (wx, wy) in items
                        and items[(wx, wy)].get("objective_completed")
                        else (
                            OBJECTIVE_COLOR
                            if (wx, wy) in items
                            and items[(wx, wy)]["item_id"]
                            in (OBJECTIVE_TERMINAL, ROAMING_SIGNAL)
                            else (
                                ITEM_COLOR
                                if (wx, wy) in items
                                else (
                                    WALL_COLOR
                                    if is_wall_like or char == DOOR
                                    else FLOOR_COLOR
                                )
                            )
                        )
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

        if not local_dead or spectator_state["target"] in players:
            p_surf = map_font.render(display_facing, True, display_color)
            screen.blit(
                p_surf,
                p_surf.get_rect(center=(player_screen_x, player_screen_y)),
            )

        # --- remote teammates ---
        if terminal.network_mode:
            for cid, p in players.items():
                if cid in (local_client_id, camera_player_id) or not p.get(
                    "connected", True
                ):
                    continue
                tx, ty = p["x"], p["y"]
                lit = bool(p.get("light_on") and p.get("equipped_light"))
                if (tx, ty) not in visible_tiles and not (
                    lit
                    and p.get("alive", True)
                    and has_line_of_sight(dungeon, doors, px, py, tx, ty)
                ):
                    continue
                gcx = offset_x + (p["visual_x"] - camera_start_x) * cell_spacing_x
                gcy = offset_y + (p["visual_y"] - camera_start_y) * cell_spacing_y
                sx = gcx + cell_spacing_x / 2
                sy = gcy + cell_spacing_y / 2
                if not (0 <= sx <= map_width and 0 <= sy <= screen.get_height()):
                    continue
                brightness = max(
                    tile_brightness(tx, ty),
                    0.75 if lit else TEAMMATE_MIN_BRIGHTNESS,
                )
                base_color = (
                    tuple(map(int, p["color"].split()))
                    if p.get("alive", True)
                    else (90, 90, 90)
                )
                glyph_color = tuple(int(c * brightness) for c in base_color)
                other_surf = map_font.render(p["facing"], True, glyph_color)
                screen.blit(
                    other_surf,
                    other_surf.get_rect(center=(sx, sy)),
                )
            if local_dead:
                watched = players.get(spectator_state["target"])
                spectator_text = (
                    f"SPECTATING: {watched.get('name', 'Teammate')}  [Tab] Next"
                    if watched
                    else "SPECTATING: No living teammates"
                )
                spectator_surf = font.render(spectator_text, True, (220, 220, 220))
                screen.blit(spectator_surf, (12, screen.get_height() - 30))

        # --- Agnate ---
        ag = agnate_state["data"]
        if terminal.network_mode and ag:
            if ag.get("visible", True):
                if agnate_state["vx"] is None or (
                    abs(agnate_state["vx"] - ag["x"])
                    + abs(agnate_state["vy"] - ag["y"])
                    > 3
                ):
                    agnate_state["vx"], agnate_state["vy"] = float(ag["x"]), float(
                        ag["y"]
                    )
                agnate_state["vx"] += (ag["x"] - agnate_state["vx"]) * min(
                    1.0, dt * 0.012
                )
                agnate_state["vy"] += (ag["y"] - agnate_state["vy"]) * min(
                    1.0, dt * 0.012
                )
                ax, ay = ag["x"], ag["y"]
                ag_mode = ag.get("mode")
                near_active = (
                    ag_mode in ("windup", "chase", "containment", "retreat")
                    and math.hypot(ax - px, ay - py) <= 6
                    and has_line_of_sight(dungeon, doors, px, py, ax, ay)
                )
                if (ax, ay) in visible_tiles or near_active:
                    sx = (
                        offset_x
                        + (agnate_state["vx"] - camera_start_x) * cell_spacing_x
                        + cell_spacing_x / 2
                    )
                    sy = (
                        offset_y
                        + (agnate_state["vy"] - camera_start_y) * cell_spacing_y
                        + cell_spacing_y / 2
                    )
                    if 0 <= sx <= map_width and 0 <= sy <= screen.get_height():
                        tile_brightness_value = tile_brightness(ax, ay)
                        reveal = ag_mode in ("windup", "chase", "containment") or (
                            ag.get("flashed") and (now // 90) % 2 == 0
                        )
                        if reveal:
                            cell = pygame.Rect(
                                0, 0, int(cell_spacing_x) + 2, int(cell_spacing_y) + 2
                            )
                            cell.center = (int(sx), int(sy))
                            pygame.draw.rect(screen, (235, 235, 235), cell)
                            glyph_index = min(
                                len(AGNATE_GLYPHS) - 1,
                                int(
                                    max(0.0, min(1.0, tile_brightness_value))
                                    * len(AGNATE_GLYPHS)
                                ),
                            )
                            agnate_surf = map_font.render(
                                AGNATE_GLYPHS[glyph_index], True, (0, 0, 0)
                            )
                        else:
                            base_color = tuple(map(int, ag["color"].split()))
                            brightness = max(
                                tile_brightness_value, TEAMMATE_MIN_BRIGHTNESS
                            )
                            agnate_surf = map_font.render(
                                ag.get("facing", "v"),
                                True,
                                tuple(int(channel * brightness) for channel in base_color),
                            )
                        screen.blit(agnate_surf, agnate_surf.get_rect(center=(sx, sy)))

        stress = 0.0
        if (
            terminal.network_mode
            and ag
            and ag.get("static_target") == local_client_id
        ):
            proximity = max(0.0, min(1.0, 1.0 - math.hypot(ag["x"] - px, ag["y"] - py) / 14.0))
            exhaustion = 1.0 - stamina["value"] / STAMINA_MAX
            stress = ag.get("static", 0.0)
            if ag.get("mode") in ("chase", "containment"):
                stress = max(
                    stress,
                    0.15
                    + 0.35 * ag.get("chase_progress", 0.0)
                    + 0.35 * proximity
                    + 0.2 * exhaustion
                    + (0.25 if ag.get("contained") else 0.0),
                )
        agnate_state["static_level"] += (
            stress - agnate_state["static_level"]
        ) * min(1.0, dt * (0.012 if stress else 0.008))
        if terminal.network_mode and agnate_state["static_level"] > 0.01:
            intensity = min(1.0, agnate_state["static_level"])
            static_rng = agnate_state["static_rng"]
            for _ in range(int(260 * intensity)):
                value = static_rng.randrange(20, int(50 + 100 * intensity))
                screen.fill(
                    (value, value, value),
                    (
                        static_rng.randrange(map_width),
                        static_rng.randrange(screen.get_height()),
                        static_rng.randint(1, 3),
                        1,
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
            stamina=(stamina["value"] / STAMINA_MAX, stamina["exhausted"]),
        )
        if terminal.network_mode:
            if voice_chat and voice_chat.transmitting:
                voice_label = (
                    "VOICE OPEN  [V] x2 TO CLOSE"
                    if voice_state["toggle"]
                    else "TRANSMITTING  [V]"
                )
                voice_color = (255, 110, 100)
            elif voice_chat and voice_chat.muted:
                voice_label = (
                    "VOICE MUTED [M]  |  HOLD [V] OR DOUBLE-TAP TO TOGGLE"
                )
                voice_color = (255, 190, 100)
            elif voice_chat and voice_chat.connected:
                voice_label = (
                    "VOICE READY  |  HOLD [V] OR DOUBLE-TAP TO TOGGLE  |  [M] MUTE"
                )
                voice_color = (150, 220, 190)
            elif voice_chat and voice_chat.failure:
                voice_label = "VOICE OFFLINE  |  CHECK CONNECTION"
                voice_color = (255, 150, 120)
            elif voice_chat:
                voice_label = "VOICE CONNECTING  |  HOLD [V] OR DOUBLE-TAP TO TOGGLE"
                voice_color = (220, 190, 120)
            else:
                voice_label = "VOICE OFFLINE"
                voice_color = (170, 150, 140)
            voice_surface = pygame.font.SysFont(FONT_NAME, 15).render(
                voice_label, True, voice_color
            )
            screen.blit(voice_surface, (12, 12))
        pygame.display.flip()
        # print(f"seed={active_seed}")  # debug

    teardown_multiplayer()
    pygame.quit()


if __name__ == "__main__":
    main()
