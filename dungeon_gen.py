import random
import math

WALL = "#"
FLOOR = "."
STAIRS = ">"

DIRECTIONS = {
    "N": (0, -1),
    "S": (0, 1),
    "E": (1, 0),
    "W": (-1, 0),
}

seed_rng = random.Random()


class Rect:
    def __init__(self, x1, y1, x2, y2):
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2

    def overlaps(self, other, buffer=0):
        return not (
            self.x2 + buffer < other.x1
            or other.x2 + buffer < self.x1
            or self.y2 + buffer < other.y1
            or other.y2 + buffer < self.y1
        )


def carve_rect(tiles, x1, y1, w, h):
    for x in range(x1, x1 + w):
        for y in range(y1, y1 + h):
            if x == x1 or x == x1 + w - 1 or y == y1 or y == y1 + h - 1:
                tiles[(x, y)] = WALL
            else:
                tiles.setdefault((x, y), FLOOR)


import math


def carve_circle(tiles, cx, cy, radius):
    for x in range(cx - radius, cx + radius + 1):
        for y in range(cy - radius, cy + radius + 1):
            dist = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            if dist <= radius:
                if dist >= radius - 1:
                    tiles[(x, y)] = WALL
                else:
                    tiles.setdefault((x, y), FLOOR)


def try_build_circular_room(door, tiles, placed_rects, pending_doors, radius=7):
    dx, dy = door["dir"]
    cx = door["x"] + dx * (radius + 1)
    cy = door["y"] + dy * (radius + 1)

    new_rect = Rect(cx - radius, cy - radius, cx + radius, cy + radius)
    if any(new_rect.overlaps(r) for r in placed_rects):
        return False

    carve_circle(tiles, cx, cy, radius)

    # Carve a straight connector from the door to the circle's edge
    steps = radius + 1
    for i in range(steps + 1):
        px, py = door["x"] + dx * i, door["y"] + dy * i
        tiles[(px, py)] = FLOOR

    placed_rects.append(new_rect)

    # Queue a few new doors around the circle's rim, in open compass directions
    for side in ("N", "S", "E", "W"):
        sdx, sdy = DIRECTIONS[side]
        if (sdx, sdy) == (-dx, -dy):
            continue  # skip the side we entered from
        if random.random() < 0.6:
            edge_x = cx + sdx * radius
            edge_y = cy + sdy * radius
            pending_doors.append(
                {"x": edge_x, "y": edge_y, "dir": (sdx, sdy), "source": "room"}
            )
    return {"type": "circle", "cx": cx, "cy": cy, "radius": radius, "door": door}


def try_build_ring_room(
    door, tiles, placed_rects, pending_doors, outer_radius=15, inner_radius=8
):
    dx, dy = door["dir"]
    cx = door["x"] + dx * (outer_radius + 1)
    cy = door["y"] + dy * (outer_radius + 1)

    new_rect = Rect(
        cx - outer_radius, cy - outer_radius, cx + outer_radius, cy + outer_radius
    )
    if any(new_rect.overlaps(r) for r in placed_rects):
        return False

    for x in range(cx - outer_radius, cx + outer_radius + 1):
        for y in range(cy - outer_radius, cy + outer_radius + 1):
            dist = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            if dist > outer_radius or dist < inner_radius - 1:
                continue
            if dist >= outer_radius - 1 or dist <= inner_radius:
                tiles[(x, y)] = WALL
            else:
                tiles.setdefault((x, y), FLOOR)

    steps = outer_radius + 1
    for i in range(steps + 1):
        px, py = door["x"] + dx * i, door["y"] + dy * i
        tiles[(px, py)] = FLOOR

    placed_rects.append(new_rect)

    for side in ("N", "S", "E", "W"):
        sdx, sdy = DIRECTIONS[side]
        if (sdx, sdy) == (-dx, -dy):
            continue
        if random.random() < 0.6:
            edge_x = cx + sdx * outer_radius
            edge_y = cy + sdy * outer_radius
            pending_doors.append(
                {"x": edge_x, "y": edge_y, "dir": (sdx, sdy), "source": "room"}
            )
    return {
        "type": "ring",
        "cx": cx,
        "cy": cy,
        "outer_radius": outer_radius,
        "door": door,
    }


def try_build_pillar_room(door, tiles, placed_rects, pending_doors):
    # Fixed layout: 3x2 pillars, each 2x2, four tiles of floor between them.
    # Interior span: 3 pillars * 2 wide + 2 gaps * 4 + 2 edge margins of 3 each
    interior_w = 3 * 2 + 2 * 4 + 6
    interior_h = 2 * 2 + 1 * 4 + 6
    w, h = interior_w + 2, interior_h + 2  # + wall border

    if dx_dy_room_pos := _room_pos_from_door(door, w, h):
        x1, y1 = dx_dy_room_pos
    else:
        return False

    dx, dy = door["dir"]
    new_rect = Rect(x1, y1, x1 + w - 1, y1 + h - 1)
    if any(new_rect.overlaps(r) for r in placed_rects):
        return False

    two_wide = door.get("source") == "room"
    if not _carve_rect_room_with_door(tiles, x1, y1, w, h, door, dx, dy, two_wide):
        return False

    # Place the 3x2 grid of 2x2 pillars, centered in the interior
    start_x = x1 + 4
    start_y = y1 + 4
    for row in range(2):
        for col in range(3):
            px = start_x + col * (2 + 4)
            py = start_y + row * (2 + 4)
            for ox in range(2):
                for oy in range(2):
                    tiles[(px + ox, py + oy)] = WALL

    placed_rects.append(new_rect)

    back_side = {"N": "S", "S": "N", "E": "W", "W": "E"}[
        [k for k, v in DIRECTIONS.items() if v == (dx, dy)][0]
    ]
    queue_doors_for_room(
        x1, y1, w, h, pending_doors, skip_side=back_side, source="room"
    )
    return {"type": "pillar", "center_x": x1 + w // 2, "center_y": y1 + h // 2}


def queue_doors_for_room(x1, y1, w, h, pending_doors, skip_side=None, source="room"):
    sides = [s for s in ("N", "S", "E", "W") if s != skip_side]
    for side in sides:
        num_doors = random.randint(1, 2)
        for _ in range(num_doors):
            dx, dy = DIRECTIONS[side]
            if side in ("N", "S"):
                door_x = random.randint(x1 + 1, x1 + w - 2)
                door_y = y1 if side == "N" else y1 + h - 1
            else:
                door_y = random.randint(y1 + 1, y1 + h - 2)
                door_x = x1 if side == "W" else x1 + w - 1
            pending_doors.append(
                {"x": door_x, "y": door_y, "dir": (dx, dy), "source": source}
            )


def _room_pos_from_door(door, w, h):
    dx, dy = door["dir"]
    if dx == 1:
        return door["x"] + 1, door["y"] - h // 2
    elif dx == -1:
        return door["x"] - w, door["y"] - h // 2
    elif dy == 1:
        return door["x"] - w // 2, door["y"] + 1
    else:
        return door["x"] - w // 2, door["y"] - h


def _carve_rect_room_with_door(tiles, x1, y1, w, h, door, dx, dy, two_wide):
    carve_rect(tiles, x1, y1, w, h)

    if dx == 1:
        wall_x, wall_y = x1, door["y"]
        second = (wall_x, wall_y + 1)
        interior_ok = y1 + 1 <= wall_y + 1 <= y1 + h - 2
    elif dx == -1:
        wall_x, wall_y = x1 + w - 1, door["y"]
        second = (wall_x, wall_y + 1)
        interior_ok = y1 + 1 <= wall_y + 1 <= y1 + h - 2
    elif dy == 1:
        wall_x, wall_y = door["x"], y1
        second = (wall_x + 1, wall_y)
        interior_ok = x1 + 1 <= wall_x + 1 <= x1 + w - 2
    else:
        wall_x, wall_y = door["x"], y1 + h - 1
        second = (wall_x + 1, wall_y)
        interior_ok = x1 + 1 <= wall_x + 1 <= x1 + w - 2

    tiles[(door["x"], door["y"])] = FLOOR
    tiles[(wall_x, wall_y)] = FLOOR

    if two_wide and interior_ok:
        tiles[second] = FLOOR
        parent_second = (
            (door["x"], door["y"] + 1) if dx != 0 else (door["x"] + 1, door["y"])
        )
        tiles[parent_second] = FLOOR

    return True


def try_build_room(door, tiles, placed_rects, pending_doors):
    dx, dy = door["dir"]
    max_dim, min_dim = 20, 5

    w = random.randint(min_dim, max_dim)
    h = random.randint(min_dim, min(max_dim, int(w * 1.5)))
    if h / w > 1.5:
        h = int(w * 1.5)
    if w / h > 1.5:
        w = int(h * 1.5)

    if dx == 1:
        x1, y1 = door["x"] + 1, door["y"] - h // 2
    elif dx == -1:
        x1, y1 = door["x"] - w, door["y"] - h // 2
    elif dy == 1:
        x1, y1 = door["x"] - w // 2, door["y"] + 1
    else:
        x1, y1 = door["x"] - w // 2, door["y"] - h

    new_rect = Rect(x1, y1, x1 + w - 1, y1 + h - 1)
    if any(new_rect.overlaps(r) for r in placed_rects):
        return False

    two_wide = door.get("source") == "room"

    if dx == 1:
        wall_x, wall_y = x1, door["y"]
        second = (wall_x, wall_y + 1)
        interior_ok = y1 + 1 <= wall_y + 1 <= y1 + h - 2
    elif dx == -1:
        wall_x, wall_y = x1 + w - 1, door["y"]
        second = (wall_x, wall_y + 1)
        interior_ok = y1 + 1 <= wall_y + 1 <= y1 + h - 2
    elif dy == 1:
        wall_x, wall_y = door["x"], y1
        second = (wall_x + 1, wall_y)
        interior_ok = x1 + 1 <= wall_x + 1 <= x1 + w - 2
    else:
        wall_x, wall_y = door["x"], y1 + h - 1
        second = (wall_x + 1, wall_y)
        interior_ok = x1 + 1 <= wall_x + 1 <= x1 + w - 2

    if two_wide and not interior_ok:
        two_wide = False

    carve_rect(tiles, x1, y1, w, h)
    tiles[(door["x"], door["y"])] = FLOOR
    tiles[(wall_x, wall_y)] = FLOOR

    if two_wide:
        tiles[second] = FLOOR
        parent_second = (
            (door["x"], door["y"] + 1) if dx != 0 else (door["x"] + 1, door["y"])
        )
        tiles[parent_second] = FLOOR

    placed_rects.append(new_rect)

    back_side = {"N": "S", "S": "N", "E": "W", "W": "E"}[
        [k for k, v in DIRECTIONS.items() if v == (dx, dy)][0]
    ]
    queue_doors_for_room(
        x1, y1, w, h, pending_doors, skip_side=back_side, source="room"
    )
    return {"type": "room", "x1": x1, "y1": y1, "w": w, "h": h, "door": door}


def try_build_hallway(door, tiles, placed_rects, pending_doors):
    dx, dy = door["dir"]
    length = random.randint(10, 25)
    total_width = 5

    if dx != 0:
        x1 = door["x"] + 1 if dx == 1 else door["x"] - length
        y1 = door["y"] - total_width // 2
        w, h = length, total_width
    else:
        y1 = door["y"] + 1 if dy == 1 else door["y"] - length
        x1 = door["x"] - total_width // 2
        w, h = total_width, length

    new_rect = Rect(x1, y1, x1 + w - 1, y1 + h - 1)
    if any(new_rect.overlaps(r) for r in placed_rects):
        return False

    carve_rect(tiles, x1, y1, w, h)
    tiles[(door["x"], door["y"])] = FLOOR

    if dx == 1:
        near_wall = (x1, door["y"])
    elif dx == -1:
        near_wall = (x1 + w - 1, door["y"])
    elif dy == 1:
        near_wall = (door["x"], y1)
    else:
        near_wall = (door["x"], y1 + h - 1)
    tiles[near_wall] = FLOOR

    placed_rects.append(new_rect)

    if dx == 1:
        far_x, far_y = x1 + w - 1, door["y"]
    elif dx == -1:
        far_x, far_y = x1, door["y"]
    elif dy == 1:
        far_x, far_y = door["x"], y1 + h - 1
    else:
        far_x, far_y = door["x"], y1

    pending_doors.append({"x": far_x, "y": far_y, "dir": (dx, dy), "source": "hallway"})
    return {"type": "hallway", "far_x": far_x, "far_y": far_y}


def try_build_elbow_hallway(door, tiles, placed_rects, pending_doors):
    dx, dy = door["dir"]
    leg1 = random.randint(6, 15)
    total_width = 5

    # First leg: straight from the door, same as a normal hallway
    if dx != 0:
        x1 = door["x"] + 1 if dx == 1 else door["x"] - leg1
        y1 = door["y"] - total_width // 2
        w1, h1 = leg1, total_width
        elbow_x = x1 + w1 - 1 if dx == 1 else x1
        elbow_y = door["y"]
        turn_dy = random.choice([-1, 1])
        turn_dir = (0, turn_dy)
    else:
        y1 = door["y"] + 1 if dy == 1 else door["y"] - leg1
        x1 = door["x"] - total_width // 2
        w1, h1 = total_width, leg1
        elbow_x = door["x"]
        elbow_y = y1 + h1 - 1 if dy == 1 else y1
        turn_dx = random.choice([-1, 1])
        turn_dir = (turn_dx, 0)

    leg1_rect = Rect(x1, y1, x1 + w1 - 1, y1 + h1 - 1)
    if any(leg1_rect.overlaps(r) for r in placed_rects):
        return False

    leg2 = random.randint(6, 15)
    tdx, tdy = turn_dir
    if tdx != 0:
        x2 = elbow_x + 1 if tdx == 1 else elbow_x - leg2
        y2 = elbow_y - total_width // 2
        w2, h2 = leg2, total_width
    else:
        y2 = elbow_y + 1 if tdy == 1 else elbow_y - leg2
        x2 = elbow_x - total_width // 2
        w2, h2 = total_width, leg2

    leg2_rect = Rect(x2, y2, x2 + w2 - 1, y2 + h2 - 1)
    if any(leg2_rect.overlaps(r) for r in placed_rects) or leg2_rect.overlaps(
        leg1_rect, buffer=-2
    ):
        return False

    carve_rect(tiles, x1, y1, w1, h1)
    carve_rect(tiles, x2, y2, w2, h2)
    tiles[(door["x"], door["y"])] = FLOOR

    if tdx != 0:
        connector = (x2 if tdx == 1 else x2 + w2 - 1, elbow_y)
    else:
        connector = (elbow_x, y2 if tdy == 1 else y2 + h2 - 1)
    tiles[connector] = FLOOR
    tiles[(elbow_x, elbow_y)] = FLOOR

    if dx == 1:
        tiles[(x1, door["y"])] = FLOOR
    elif dx == -1:
        tiles[(x1 + w1 - 1, door["y"])] = FLOOR
    elif dy == 1:
        tiles[(door["x"], y1)] = FLOOR
    else:
        tiles[(door["x"], y1 + h1 - 1)] = FLOOR

    placed_rects.append(leg1_rect)
    placed_rects.append(leg2_rect)

    if tdx == 1:
        far_x, far_y = x2 + w2 - 1, elbow_y
    elif tdx == -1:
        far_x, far_y = x2, elbow_y
    elif tdy == 1:
        far_x, far_y = elbow_x, y2 + h2 - 1
    else:
        far_x, far_y = elbow_x, y2

    pending_doors.append({"x": far_x, "y": far_y, "dir": turn_dir, "source": "hallway"})
    return {"type": "elbow", "end_x": far_x, "end_y": far_y}


def generate_dungeon(max_structures=15, seed=None):
    if seed is not None:
        random.seed(seed)

    tiles, placed_rects, pending_doors = {}, [], []
    w, h = random.randint(5, 12), random.randint(5, 12)
    x1, y1 = -w // 2, -h // 2
    carve_rect(tiles, x1, y1, w, h)
    placed_rects.append(Rect(x1, y1, x1 + w - 1, y1 + h - 1))
    queue_doors_for_room(x1, y1, w, h, pending_doors)

    structures = 1
    attempts = 0
    max_attempts = max_structures * 10
    last_info = None

    while pending_doors and structures < max_structures and attempts < max_attempts:
        attempts += 1
        door = pending_doors.pop(0)

        roll = random.random()
        if roll < 0.35:
            success = try_build_room(door, tiles, placed_rects, pending_doors)
        elif roll < 0.55:
            success = try_build_hallway(door, tiles, placed_rects, pending_doors)
        elif roll < 0.65:
            success = try_build_elbow_hallway(door, tiles, placed_rects, pending_doors)
        elif roll < 0.75:
            success = try_build_circular_room(
                door, tiles, placed_rects, pending_doors, radius=random.choice([7, 15])
            )
        elif roll < 0.85:
            success = try_build_ring_room(door, tiles, placed_rects, pending_doors)
        else:
            success = try_build_pillar_room(door, tiles, placed_rects, pending_doors)

        if success:
            structures += 1
            last_info = success

    place_stairs(tiles, last_info)
    return tiles


def place_stairs(tiles, info):
    if info is None:
        return

    t = info["type"]

    if t == "room":
        x1, y1, w, h, door = info["x1"], info["y1"], info["w"], info["h"], info["door"]
        dx, dy = door["dir"]
        cx, cy = x1 + w // 2, y1 + h // 2
        if dx == 1:
            sx, sy = x1 + w - 2, cy
        elif dx == -1:
            sx, sy = x1 + 1, cy
        elif dy == 1:
            sx, sy = cx, y1 + h - 2
        else:
            sx, sy = cx, y1 + 1

    elif t in ("hallway", "elbow"):
        sx, sy = info["far_x"] if t == "hallway" else info["end_x"], (
            info["far_y"] if t == "hallway" else info["end_y"]
        )

    elif t in ("circle", "ring"):
        cx, cy, door = info["cx"], info["cy"], info["door"]
        radius = info.get("radius") or info.get("outer_radius")
        dx, dy = door["dir"]
        sx, sy = cx - dx * (radius - 2), cy - dy * (radius - 2)

    elif t == "pillar":
        sx, sy = info["center_x"], info["center_y"]

    else:
        return

    if tiles.get((sx, sy)) == FLOOR:
        tiles[(sx, sy)] = STAIRS


def bresenham_line(x0, y0, x1, y1):
    points = []
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    x, y = x0, y0
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1

    if dx > dy:
        err = dx / 2.0
        while x != x1:
            points.append((x, y))
            err -= dy
            if err < 0:
                y += sy
                err += dx
            x += sx
    else:
        err = dy / 2.0
        while y != y1:
            points.append((x, y))
            err -= dx
            if err < 0:
                x += sx
                err += dy
            y += sy
    points.append((x, y))
    return points


def has_line_of_sight(dungeon, x0, y0, x1, y1):
    line = bresenham_line(x0, y0, x1, y1)
    for x, y in line[1:-1]:  # skip the player's own tile and the target tile itself
        if dungeon.get((x, y), WALL) == WALL:
            return False
    return True


# Tunables — adjust to taste
FOG_START_RADIUS = (
    1  # distance at which dimming begins (tiles closer than this are full brightness)
)
FOG_END_RADIUS = 7  # distance at which tiles are fully black
FOG_MIN_BRIGHTNESS = (
    0.08  # floor brightness so far tiles are a dim silhouette, not pure invisible black
)


def get_fog_brightness(px, py, tx, ty):
    dist = ((tx - px) ** 2 + (ty - py) ** 2) ** 0.5

    if dist <= FOG_START_RADIUS:
        return 1.0
    if dist >= FOG_END_RADIUS:
        return FOG_MIN_BRIGHTNESS

    span = FOG_END_RADIUS - FOG_START_RADIUS
    progress = (dist - FOG_START_RADIUS) / span
    return 1.0 - progress * (1.0 - FOG_MIN_BRIGHTNESS)


def compute_visible_tiles(dungeon, px, py, radius=10):
    visible = set()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx * dx + dy * dy > radius * radius:
                continue
            tx, ty = px + dx, py + dy
            if has_line_of_sight(dungeon, px, py, tx, ty):
                visible.add((tx, ty))
    visible.add((px, py))
    return visible


def reveal_boundary_walls(dungeon, visible):
    revealed = set(visible)
    for x, y in visible:
        if dungeon.get((x, y)) == WALL:
            continue  # only expand outward from floor tiles you can already see
        for dx, dy in (
            (1, 0),
            (-1, 0),
            (0, 1),
            (0, -1),
            (1, 1),
            (1, -1),
            (-1, 1),
            (-1, -1),
        ):
            nx, ny = x + dx, y + dy
            if dungeon.get((nx, ny)) == WALL:
                revealed.add((nx, ny))
    return revealed


def find_adjacent_spawn(dungeon, px, py):
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, ny = px + dx, py + dy
        if dungeon.get((nx, ny)) == FLOOR:
            return nx, ny
    return px, py  # fallback, shouldn't normally happen


def print_dungeon(tiles):
    if not tiles:
        print("No tiles generated.")
        return

    xs = [x for x, y in tiles]
    ys = [y for x, y in tiles]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    for y in range(min_y, max_y + 1):
        row = ""
        for x in range(min_x, max_x + 1):
            row += tiles.get((x, y), " ")
        print(row)


if __name__ == "__main__":
    dungeon = generate_dungeon(max_structures=15)
    print_dungeon(dungeon)
