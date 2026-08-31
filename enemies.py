class EnemyType:
    def __init__(
        self, name, glyph, color, move_interval_ms=300, movement_pattern="chase"
    ):
        self.name = name
        self.glyph = glyph
        self.color = color
        self.move_interval_ms = move_interval_ms
        self.movement_pattern = movement_pattern  # "chase" for now; room for "wander", "stationary" etc later


ENEMY_TYPES = {}


class Enemy:
    def __init__(self, enemy_type: EnemyType, x, y):
        self.enemy_type = enemy_type
        self.x = x
        self.y = y
        self.time_since_last_move = 0

    def update(self, dt, dungeon, player_x, player_y, wall_char):
        if self.enemy_type.movement_pattern == "stationary":
            return

        self.time_since_last_move += dt
        if self.time_since_last_move < self.enemy_type.move_interval_ms:
            return

        self.time_since_last_move = 0

        if self.enemy_type.movement_pattern == "chase":
            self._chase(dungeon, player_x, player_y, wall_char)

    def _chase(self, dungeon, player_x, player_y, wall_char):
        dx = player_x - self.x
        dy = player_y - self.y

        candidates = []
        if dx != 0:
            candidates.append((1 if dx > 0 else -1, 0))
        if dy != 0:
            candidates.append((0, 1 if dy > 0 else -1))

        candidates.sort(key=lambda d: abs(dx) if d[1] == 0 else abs(dy), reverse=True)

        for step_dx, step_dy in candidates:
            nx, ny = self.x + step_dx, self.y + step_dy
            if (nx, ny) == (player_x, player_y):
                continue
            if dungeon.get((nx, ny), wall_char) == wall_char:
                continue
            self.x, self.y = nx, ny
            return
