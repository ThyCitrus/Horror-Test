import math
import random

WALL = "#"
FLOOR = "."
LADDER = "‡"

DIRECTIONS = {
    "N": (0, -1),
    "S": (0, 1),
    "E": (1, 0),
    "W": (-1, 0),
}

seed_rng = random.Random(min(0, 2**32 - 1))  # For reproducible dungeon generation


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
    """Carves a solid rectangular room/hallway with walls on its outer perimeter."""
    for x in range(x1, x1 + w):
        for y in range(y1, y1 + h):
            is_border = x == x1 or x == x1 + w - 1 or y == y1 or y == y1 + h - 1
            if is_border:
                if tiles.get((x, y)) != FLOOR:
                    tiles[(x, y)] = WALL
            else:
                tiles[(x, y)] = FLOOR


def carve_circle(tiles, cx, cy, radius):
    """Carves a solid circular room with walls on its outer perimeter."""
    for x in range(cx - radius, cx + radius + 1):
        for y in range(cy - radius, cy + radius + 1):
            dist = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            if dist <= radius:
                if dist >= radius - 1:
                    if tiles.get((x, y)) != FLOOR:
                        tiles[(x, y)] = WALL
                else:
                    tiles[(x, y)] = FLOOR


def carve_ring(tiles, cx, cy, outer_radius, inner_radius):
    """Carves a ring room with walls on both its inner and outer boundaries."""
    for x in range(cx - outer_radius, cx + outer_radius + 1):
        for y in range(cy - outer_radius, cy + outer_radius + 1):
            dist = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            if dist > outer_radius or dist < inner_radius - 1:
                continue
            if dist >= outer_radius - 1 or dist <= inner_radius:
                if tiles.get((x, y)) != FLOOR:
                    tiles[(x, y)] = WALL
            else:
                tiles[(x, y)] = FLOOR


# =====================================================================
# PASS 1: STRUCTURE GEOMETRY PLACEMENT
# =====================================================================


def try_build_room(attach_point, tiles, placed_rects, structures):
    dx, dy = attach_point["dir"]
    max_dim, min_dim = 16, 6

    w = random.randint(min_dim, max_dim)
    h = random.randint(min_dim, max_dim)

    if dx == 1:
        x1, y1 = attach_point["x"] + 1, attach_point["y"] - h // 2
    elif dx == -1:
        x1, y1 = attach_point["x"] - w, attach_point["y"] - h // 2
    elif dy == 1:
        x1, y1 = attach_point["x"] - w // 2, attach_point["y"] + 1
    else:
        x1, y1 = attach_point["x"] - w // 2, attach_point["y"] - h

    new_rect = Rect(x1, y1, x1 + w - 1, y1 + h - 1)
    if any(new_rect.overlaps(r) for r in placed_rects):
        return False

    carve_rect(tiles, x1, y1, w, h)
    placed_rects.append(new_rect)

    info = {"type": "room", "x1": x1, "y1": y1, "w": w, "h": h, "rect": new_rect}
    structures.append(info)
    return info


def try_build_hallway(attach_point, tiles, placed_rects, structures):
    dx, dy = attach_point["dir"]
    length = random.randint(8, 16)
    total_width = 5

    if dx != 0:
        x1 = attach_point["x"] + 1 if dx == 1 else attach_point["x"] - length
        y1 = attach_point["y"] - total_width // 2
        w, h = length, total_width
    else:
        y1 = attach_point["y"] + 1 if dy == 1 else attach_point["y"] - length
        x1 = attach_point["x"] - total_width // 2
        w, h = total_width, length

    new_rect = Rect(x1, y1, x1 + w - 1, y1 + h - 1)
    if any(new_rect.overlaps(r) for r in placed_rects):
        return False

    carve_rect(tiles, x1, y1, w, h)
    placed_rects.append(new_rect)

    far_x = x1 + w - 1 if dx == 1 else (x1 if dx == -1 else attach_point["x"])
    far_y = y1 + h - 1 if dy == 1 else (y1 if dy == -1 else attach_point["y"])

    info = {
        "type": "hallway",
        "far_x": far_x,
        "far_y": far_y,
        "dir": (dx, dy),
        "rect": new_rect,
    }
    structures.append(info)
    return info


def try_build_elbow_hallway(attach_point, tiles, placed_rects, structures):
    dx, dy = attach_point["dir"]
    leg1 = random.randint(6, 12)
    total_width = 5

    if dx != 0:
        x1 = attach_point["x"] + 1 if dx == 1 else attach_point["x"] - leg1
        y1 = attach_point["y"] - total_width // 2
        w1, h1 = leg1, total_width
        elbow_x = x1 + w1 - 1 if dx == 1 else x1
        elbow_y = attach_point["y"]
        turn_dy = random.choice([-1, 1])
        turn_dir = (0, turn_dy)
    else:
        y1 = attach_point["y"] + 1 if dy == 1 else attach_point["y"] - leg1
        x1 = attach_point["x"] - total_width // 2
        w1, h1 = total_width, leg1
        elbow_x = attach_point["x"]
        elbow_y = y1 + h1 - 1 if dy == 1 else y1
        turn_dx = random.choice([-1, 1])
        turn_dir = (turn_dx, 0)

    leg1_rect = Rect(x1, y1, x1 + w1 - 1, y1 + h1 - 1)
    if any(leg1_rect.overlaps(r) for r in placed_rects):
        return False

    leg2 = random.randint(6, 12)
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

    for jx in range(elbow_x - 1, elbow_x + 2):
        for jy in range(elbow_y - 1, elbow_y + 2):
            tiles[(jx, jy)] = FLOOR

    placed_rects.append(leg1_rect)
    placed_rects.append(leg2_rect)

    far_x = x2 + w2 - 1 if tdx == 1 else (x2 if tdx == -1 else elbow_x)
    far_y = y2 + h2 - 1 if tdy == 1 else (y2 if tdy == -1 else elbow_y)

    info = {
        "type": "elbow",
        "far_x": far_x,
        "far_y": far_y,
        "dir": turn_dir,
        "rect": leg2_rect,
        "rects": [leg1_rect, leg2_rect],
    }
    structures.append(info)
    return info


def get_rect_for_adjacency(struct):
    if struct["type"] == "elbow":
        rects = struct.get("rects", [struct["rect"]])
        xs = []
        ys = []
        for r in rects:
            xs.extend([r.x1, r.x2])
            ys.extend([r.y1, r.y2])
        return Rect(min(xs), min(ys), max(xs), max(ys))
    return struct["rect"]


def try_build_circular_room(attach_point, tiles, placed_rects, structures, radius=7):
    dx, dy = attach_point["dir"]
    cx = attach_point["x"] + dx * (radius + 1)
    cy = attach_point["y"] + dy * (radius + 1)

    new_rect = Rect(cx - radius, cy - radius, cx + radius, cy + radius)
    if any(new_rect.overlaps(r) for r in placed_rects):
        return False

    carve_circle(tiles, cx, cy, radius)
    placed_rects.append(new_rect)

    info = {
        "type": "circle",
        "cx": cx,
        "cy": cy,
        "radius": radius,
        "rect": new_rect,
    }
    structures.append(info)
    return info


def try_build_ring_room(
    attach_point,
    tiles,
    placed_rects,
    structures,
    outer_radius=15,
    inner_radius=8,
):
    dx, dy = attach_point["dir"]
    cx = attach_point["x"] + dx * (outer_radius + 1)
    cy = attach_point["y"] + dy * (outer_radius + 1)

    new_rect = Rect(
        cx - outer_radius, cy - outer_radius, cx + outer_radius, cy + outer_radius
    )
    if any(new_rect.overlaps(r) for r in placed_rects):
        return False

    carve_ring(tiles, cx, cy, outer_radius, inner_radius)
    placed_rects.append(new_rect)

    info = {
        "type": "ring",
        "cx": cx,
        "cy": cy,
        "outer_radius": outer_radius,
        "inner_radius": inner_radius,
        "rect": new_rect,
    }
    structures.append(info)
    return info


def try_build_pillar_room(attach_point, tiles, placed_rects, structures):
    interior_w = 3 * 2 + 2 * 4 + 6
    interior_h = 2 * 2 + 1 * 4 + 6
    w, h = interior_w + 2, interior_h + 2

    dx, dy = attach_point["dir"]
    if dx == 1:
        x1, y1 = attach_point["x"] + 1, attach_point["y"] - h // 2
    elif dx == -1:
        x1, y1 = attach_point["x"] - w, attach_point["y"] - h // 2
    elif dy == 1:
        x1, y1 = attach_point["x"] - w // 2, attach_point["y"] + 1
    else:
        x1, y1 = attach_point["x"] - w // 2, attach_point["y"] - h

    new_rect = Rect(x1, y1, x1 + w - 1, y1 + h - 1)
    if any(new_rect.overlaps(r) for r in placed_rects):
        return False

    carve_rect(tiles, x1, y1, w, h)

    pillars = []
    start_x = x1 + 4
    start_y = y1 + 4
    for row in range(2):
        for col in range(3):
            px = start_x + col * (2 + 4)
            py = start_y + row * (2 + 4)
            for ox in range(2):
                for oy in range(2):
                    tiles[(px + ox, py + oy)] = WALL
                    pillars.append((px + ox, py + oy))

    placed_rects.append(new_rect)

    info = {
        "type": "pillar",
        "center_x": x1 + w // 2,
        "center_y": y1 + h // 2,
        "pillars": set(pillars),
        "rect": new_rect,
    }
    structures.append(info)
    return info


def queue_attachment_points(structure, attach_queue):
    rect = structure["rect"]
    x1, y1, x2, y2 = rect.x1, rect.y1, rect.x2, rect.y2

    if structure["type"] == "ring":
        cx, cy = structure["cx"], structure["cy"]
        r = structure["outer_radius"]
        for side, (dx, dy) in DIRECTIONS.items():
            attach_queue.append({"x": cx + dx * r, "y": cy + dy * r, "dir": (dx, dy)})
        return

    if structure["type"] in ("hallway", "elbow"):
        attach_queue.append(
            {
                "x": structure["far_x"],
                "y": structure["far_y"],
                "dir": structure["dir"],
            }
        )
        return

    sides = [("N", (0, -1)), ("S", (0, 1)), ("E", (1, 0)), ("W", (-1, 0))]
    for side_name, (dx, dy) in sides:
        num_points = random.randint(1, 2)
        for _ in range(num_points):
            if side_name in ("N", "S"):
                ax = random.randint(x1 + 2, x2 - 2)
                ay = y1 if side_name == "N" else y2
            else:
                ay = random.randint(y1 + 2, y2 - 2)
                ax = x1 if side_name == "W" else x2
            attach_queue.append({"x": ax, "y": ay, "dir": (dx, dy)})


# =====================================================================
# PASS 2: DYNAMIC CONNECTIVITY & ORTHOGONAL DOORWAY CARVING
# =====================================================================


def carve_matched_doors_pass(tiles, structures):
    """Evaluates adjacent structures and cuts matched grid connections."""
    for i in range(len(structures)):
        s1 = structures[i]
        r1 = get_rect_for_adjacency(s1)

        for j in range(i + 1, len(structures)):
            s2 = structures[j]
            r2 = get_rect_for_adjacency(s2)

            if not r1.overlaps(r2, buffer=1):
                continue

            connect_adjacent_structures(tiles, s1, s2)


def connect_adjacent_structures(tiles, s1, s2):
    """Punches door/corridor openings where two structures meet."""
    r1, r2 = s1["rect"], s2["rect"]
    shared_walls = []

    min_x, max_x = max(r1.x1, r2.x1), min(r1.x2, r2.x2)
    min_y, max_y = max(r1.y1, r2.y1), min(r1.y2, r2.y2)

    # Vertical shared boundary
    if min_y + 1 <= max_y - 1:
        for x in range(min_x, max_x + 1):
            for y in range(min_y + 1, max_y):
                if is_invalid_carve_target(s1, s2, x, y):
                    continue
                left_floor = tiles.get((x - 1, y)) == FLOOR
                right_floor = tiles.get((x + 1, y)) == FLOOR
                if left_floor and right_floor:
                    shared_walls.append((x, y))

    # Horizontal shared boundary
    if min_x + 1 <= max_x - 1:
        for y in range(min_y, max_y + 1):
            for x in range(min_x + 1, max_x):
                if is_invalid_carve_target(s1, s2, x, y):
                    continue
                top_floor = tiles.get((x, y - 1)) == FLOOR
                bottom_floor = tiles.get((x, y + 1)) == FLOOR
                if top_floor and bottom_floor:
                    shared_walls.append((x, y))

    if shared_walls:
        shared_walls.sort()
        mid_idx = len(shared_walls) // 2
        wx, wy = shared_walls[mid_idx]
        tiles[(wx, wy)] = FLOOR

        if len(shared_walls) >= 3:
            alt_x, alt_y = shared_walls[mid_idx - 1]
            tiles[(alt_x, alt_y)] = FLOOR
    else:
        c1 = get_structure_center(s1)
        c2 = get_structure_center(s2)
        carve_orthogonal_connector(tiles, s1, s2, c1[0], c1[1], c2[0], c2[1])


def is_invalid_carve_target(s1, s2, x, y):
    """Prevents connectors from carving into ring holes or interior room pillars."""
    for s in (s1, s2):
        if s["type"] == "ring":
            cx, cy = s["cx"], s["cy"]
            dist = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            if dist < s["inner_radius"]:
                return True
        elif s["type"] == "pillar":
            if (x, y) in s.get("pillars", set()):
                return True
    return False


def get_structure_center(struct):
    if struct["type"] in ("circle", "ring"):
        return struct["cx"], struct["cy"]
    r = struct["rect"]
    return (r.x1 + r.x2) // 2, (r.y1 + r.y2) // 2


def carve_orthogonal_connector(tiles, s1, s2, x0, y0, x1, y1):
    """Carves a strictly grid-aligned pathway without destroying pillars or inner ring spaces."""
    curr_x, curr_y = x0, y0

    # Step horizontally first
    step_x = 1 if x1 > x0 else -1
    while curr_x != x1:
        curr_x += step_x
        if not is_invalid_carve_target(s1, s2, curr_x, curr_y):
            tiles[(curr_x, curr_y)] = FLOOR

    # Step vertically second
    step_y = 1 if y1 > y0 else -1
    while curr_y != y1:
        curr_y += step_y
        if not is_invalid_carve_target(s1, s2, curr_x, curr_y):
            tiles[(curr_x, curr_y)] = FLOOR


def enclose_dungeon_walls(tiles):
    """Ensures every single floor tile is surrounded by solid wall boundaries."""
    neighbors = [
        (-1, -1),
        (0, -1),
        (1, -1),
        (-1, 0),
        (1, 0),
        (-1, 1),
        (0, 1),
        (1, 1),
    ]
    floors = [pos for pos, tile in tiles.items() if tile == FLOOR]

    for x, y in floors:
        for dx, dy in neighbors:
            nx, ny = x + dx, y + dy
            if (nx, ny) not in tiles:
                tiles[(nx, ny)] = WALL


# =====================================================================
# MAIN GENERATOR & AUXILIARY UTILITIES
# =====================================================================


def generate_dungeon(max_structures=15, seed=None):
    if seed is not None:
        random.seed(seed)

    tiles, placed_rects, attach_queue, structures = {}, [], [], []

    # Initial anchor room
    w, h = random.randint(6, 12), random.randint(6, 12)
    x1, y1 = -w // 2, -h // 2
    carve_rect(tiles, x1, y1, w, h)
    anchor_rect = Rect(x1, y1, x1 + w - 1, y1 + h - 1)
    placed_rects.append(anchor_rect)

    anchor_info = {
        "type": "room",
        "x1": x1,
        "y1": y1,
        "w": w,
        "h": h,
        "rect": anchor_rect,
    }
    structures.append(anchor_info)
    queue_attachment_points(anchor_info, attach_queue)

    placed_count = 1
    attempts = 0
    max_attempts = max_structures * 10
    last_info = anchor_info

    # PASS 1: Generate dungeon room & hallway layout geometry
    while attach_queue and placed_count < max_structures and attempts < max_attempts:
        attempts += 1
        attach_point = attach_queue.pop(0)

        roll = random.random()
        info = False

        if roll < 0.35:
            info = try_build_room(attach_point, tiles, placed_rects, structures)
        elif roll < 0.55:
            info = try_build_hallway(attach_point, tiles, placed_rects, structures)
        elif roll < 0.65:
            info = try_build_elbow_hallway(
                attach_point, tiles, placed_rects, structures
            )
        elif roll < 0.75:
            info = try_build_circular_room(
                attach_point,
                tiles,
                placed_rects,
                structures,
                radius=random.choice([7, 15]),
            )
        elif roll < 0.85:
            info = try_build_ring_room(attach_point, tiles, placed_rects, structures)
        else:
            info = try_build_pillar_room(attach_point, tiles, placed_rects, structures)

        if info:
            placed_count += 1
            last_info = info
            queue_attachment_points(info, attach_queue)

    # PASS 2: Carve matched doors and connections between adjacent layout geometry
    carve_matched_doors_pass(tiles, structures)

    # PASS 3: Enclose exposed outer floor edges
    enclose_dungeon_walls(tiles)

    place_ladders(tiles, last_info)
    return tiles


def place_ladders(tiles, info):
    if not info:
        return

    t = info["type"]

    # Pick an initial candidate coordinate based on structure type
    if t == "room":
        cx = info["x1"] + info["w"] // 2
        cy = info["y1"] + info["h"] // 2
    elif t in ("hallway", "elbow"):
        cx, cy = info["far_x"], info["far_y"]
    elif t == "circle":
        cx, cy = info["cx"], info["cy"]
    elif t == "ring":
        # Start from a point definitely in the ring band
        cx = info["cx"] + info["outer_radius"] - 1
        cy = info["cy"]
    elif t == "pillar":
        cx, cy = info["center_x"], info["center_y"]
    else:
        return

    # If the exact center is floor, use it
    if tiles.get((cx, cy)) == FLOOR:
        tiles[(cx, cy)] = LADDER
        return

    # Otherwise, search in expanding squares until we hit a floor tile
    max_radius = max(info.get("w", 10), info.get("h", 10), info.get("radius", 10), 20)
    for r in range(1, max_radius + 1):
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if abs(dx) != r and abs(dy) != r:
                    continue  # only the perimeter of the square
                x, y = cx + dx, cy + dy
                if tiles.get((x, y)) == FLOOR:
                    tiles[(x, y)] = LADDER
                    return


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
    for x, y in line[1:-1]:
        if dungeon.get((x, y), WALL) == WALL:
            return False
    return True


FOG_START_RADIUS = 1
FOG_END_RADIUS = 7
FOG_MIN_BRIGHTNESS = 0.05


def get_fog_brightness(px, py, tx, ty):
    dist = math.hypot(tx - px, ty - py)

    if dist <= FOG_START_RADIUS:
        return 1.0
    if dist >= FOG_END_RADIUS:
        return FOG_MIN_BRIGHTNESS

    # Normalize distance to [0, 1] over [FOG_START_RADIUS, FOG_END_RADIUS]
    span = FOG_END_RADIUS - FOG_START_RADIUS
    t = (dist - FOG_START_RADIUS) / span  # t in [0, 1]

    # Logarithmic-style falloff:
    # Use log(1 + k*t) / log(1 + k) so that:
    #   t = 0 -> factor = 0
    #   t = 1 -> factor = 1
    # Larger k => sharper drop near the start.
    k = 6.0  # tweak this to adjust "sharpness"
    factor = math.log(1 + k * t) / math.log(1 + k)

    # Map factor from [0,1] to brightness from 1.0 down to FOG_MIN_BRIGHTNESS
    brightness = 1.0 - factor * (1.0 - FOG_MIN_BRIGHTNESS)
    return max(FOG_MIN_BRIGHTNESS, min(1.0, brightness))


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
            continue
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
    return px, py


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
    seed = random.randint(0, 2**32 - 1)
    dungeon = generate_dungeon(max_structures=15, seed=seed)
    print(f"Seed: {seed}")
    print_dungeon(dungeon)
