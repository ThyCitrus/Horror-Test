"""Host-side state machine for Agnate, the false teammate."""

import math
from collections import deque

from dungeon_gen import DOOR, FLOOR, WALL, has_line_of_sight, is_walkable

MIN_FLOOR = 1
TUTORIAL_FLOORS = 3

FIRST_APPEARANCE_S = (35.0, 55.0)
HIDE_COOLDOWN_S = (14.0, 26.0)
CATCH_COOLDOWN_S = (20.0, 32.0)
MIMIC_S = (18.0, 32.0)
MIMIC_GRACE_MS = 10000
APPROACH_TIMEOUT_MS = 30000
APPROACH_STEP_MS = 190
WINDUP_MS_TUTORIAL = 2200
WINDUP_MS = 1400
RETREAT_MS = 3500
STATIC_FADE_MS = 500
CONTAINMENT_STEP_MS = 650

CHASE_STEP_MS = 130
CHASE_BASE_S = 10.0
CHASE_PER_FLOOR_S = 1.0
CHASE_MAX_S = 25.0

PROXIMITY = 3.0
SEEN_RANGE = 14.0
ISOLATION_DIST = 12.0
SPAWN_MIN_DEPTH = 10
SPAWN_MAX_DEPTH = 22
STATIC_START_DISTANCE = 16.0
CONTAINMENT_COMPONENT_MIN = 3

FLASH_BASE_CHANCE = 0.05
FLASH_MAX_CHANCE = 0.40
FLASH_ROLL_MS = 750
FLASH_DECAY_MS = 4000
STUN_MS = 700
TRANSFER_MIN_CHASE_MS = 6000

FLANK_REROLL_MS = 3000
LEAD_MAX = 6
BFS_NODE_CAP = 9000

DEBUG_LOG = False
DEBUG_FAST_TIMERS = False

_DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def _passable(dungeon, blocked, pos):
    return dungeon.get(pos, WALL) != WALL and pos not in blocked


class Agnate:
    def __init__(self, rng):
        self.rng = rng
        self.enabled = False
        self.floor = 0
        self.mode = "hidden"
        self.x = self.y = 0
        self._reset_runtime()

    def _reset_runtime(self):
        self.timer_at = 0
        self.approach_deadline = 0
        self.mimic_end = 0
        self.windup_end = 0
        self._pending_chase_ms = 0
        self.retreat_end = 0
        self.retreat_goal = None
        self.chase_started_at = 0
        self.containment_started_at = 0
        self.retreat_started_at = 0
        self.hidden_goal = None
        self.hidden_target = None
        self.hidden_source = None
        self.hidden_next_step = 0
        self.position_initialized = False
        self.mimic_sprinting = False
        self.chase_origin = None
        self.source = None
        self.target = None
        self.static_target = None
        self.static_level = 0.0
        self.static_fade_until = 0
        self.static_fade_level = 0.0
        self.target_dir = (0, 0)
        self.color = "255 255 255"
        self.flash_range = 13
        self.flash_half_angle = math.radians(30)
        self.facing = "v"
        self.chase_end = 0
        self.next_step = 0
        self.stunned_until = 0
        self.path = []
        self.path_age = 0
        self.path_goal = None
        self.flank = False
        self.flank_reroll_at = 0
        self.flash_chance = FLASH_BASE_CHANCE
        self.flash_last_exposure = 0
        self.flash_last_roll = {}
        self.flashed_until = 0

    def reset(self, floor_number, enabled, now):
        """Reset the entity when a floor loads."""
        self._reset_runtime()
        self.floor = floor_number
        self.enabled = enabled
        self.mode = "hidden"
        if enabled:
            self.timer_at = now + self._ms(FIRST_APPEARANCE_S, self._scale())

    def _scale(self):
        return max(0.45, 1.0 - 0.07 * max(0, self.floor - MIN_FLOOR))

    def _ms(self, span, scale=1.0):
        lo, hi = span
        value = self.rng.uniform(lo, hi) * 1000 * scale
        return int(value * (0.1 if DEBUG_FAST_TIMERS else 1.0))

    def _set_mode(self, mode):
        if DEBUG_LOG and mode != self.mode:
            print(
                f"[agnate] {self.mode} -> {mode} at ({self.x},{self.y}) "
                f"floor={self.floor}"
            )
        self.mode = mode
        if mode != "mimic":
            self.color = "255 255 255"

    def _hide(self, now, span):
        if self.static_target is None:
            self.static_target = self.target
        self.static_fade_level = self.static_level
        self.static_fade_until = now + STATIC_FADE_MS
        self._set_mode("hidden")
        self.timer_at = now + self._ms(span, self._scale())
        self.target = None
        self.path = []
        self.flash_chance = FLASH_BASE_CHANCE

    def update(
        self,
        now,
        dungeon,
        doors,
        blocked,
        players,
        moves,
        floor_number,
        flash_range,
        flash_half_angle,
    ):
        """Advance a frame and return gameplay events for the host."""
        events = []
        if not self.enabled:
            return events
        self.floor = floor_number
        self.flash_range = flash_range
        self.flash_half_angle = flash_half_angle
        alive = {
            cid: player
            for cid, player in players.items()
            if player.get("connected", True) and player.get("alive", True)
        }
        if not alive:
            if self.mode != "hidden":
                self._hide(now, HIDE_COOLDOWN_S)
            return events

        if not self.position_initialized:
            floor_tiles = [
                pos for pos, tile in dungeon.items() if tile in (FLOOR, DOOR)
            ]
            if floor_tiles:
                self.x, self.y = self.rng.choice(floor_tiles)
                self.position_initialized = True

        if self.target in moves:
            target_move = moves[self.target]
            if isinstance(target_move, dict):
                self.target_dir = (
                    target_move.get("dx", 0),
                    target_move.get("dy", 0),
                )
            else:
                self.target_dir = target_move
        if self.mode in ("mimic", "windup", "chase", "containment"):
            self._flash_check(
                now, dungeon, doors, alive, flash_range, flash_half_angle, events
            )

        if self.mode == "hidden":
            self._tick_hidden(now, dungeon, doors, blocked, alive)
        elif self.mode == "approach":
            self._tick_approach(now, dungeon, doors, blocked, alive, events)
        elif self.mode == "mimic":
            self._tick_mimic(now, dungeon, doors, blocked, alive, moves, events)
        elif self.mode == "windup":
            self._tick_windup(now, alive, events)
        elif self.mode == "chase":
            self._tick_chase(now, dungeon, doors, blocked, alive, events)
        elif self.mode == "containment":
            self._tick_containment(now, dungeon, doors, blocked, alive, events)
        elif self.mode == "retreat":
            self._tick_retreat(now, dungeon, doors, blocked, alive, events)
        self._update_static(now, alive)
        return events

    def snapshot(self, now):
        if not self.enabled:
            return None
        static = self.static_level
        static_target = self.static_target
        if self.mode == "hidden":
            remaining = self.static_fade_until - now
            static = (
                self.static_fade_level * max(0.0, remaining / STATIC_FADE_MS)
                if remaining > 0
                else 0.0
            )
        if self.mode == "hidden" and static <= 0:
            return None
        return {
            "x": self.x,
            "y": self.y,
            "mode": self.mode,
            "visible": self.mode in (
                "mimic",
                "windup",
                "chase",
                "containment",
                "retreat",
            ),
            "color": self.color,
            "facing": self.facing,
            "target": self.target,
            "static_target": static_target,
            "static": max(0.0, min(1.0, static)),
            "flashed": now < self.flashed_until,
            "contained": self.mode == "containment",
            "chase_progress": self._chase_progress(now),
            "sprinting": self.mimic_sprinting,
        }

    def _chase_progress(self, now):
        if self.mode not in ("chase", "containment"):
            return 0.0
        duration = max(1, self.chase_end - self.chase_started_at)
        return max(0.0, min(1.0, (now - self.chase_started_at) / duration))

    def _update_static(self, now, alive):
        if self.mode in (
            "approach",
            "mimic",
            "windup",
            "chase",
            "containment",
            "retreat",
        ):
            self.static_target = self.target
            player = alive.get(self.target)
            distance = (
                math.hypot(player["x"] - self.x, player["y"] - self.y)
                if player
                else STATIC_START_DISTANCE
            )
            if self.mode == "approach":
                progress = max(
                    0.0,
                    min(
                        1.0,
                        (STATIC_START_DISTANCE - distance) / STATIC_START_DISTANCE,
                    ),
                )
                self.static_level = (
                    0.08 + 0.22 * progress
                    if distance <= STATIC_START_DISTANCE
                    else 0.0
                )
            elif self.mode == "mimic":
                self.static_level = max(self.static_level, 0.42)
            elif self.mode == "windup":
                self.static_level = 0.82
            elif self.mode == "retreat":
                fade = max(
                    0.0,
                    1.0 - (now - self.retreat_started_at) / STATIC_FADE_MS,
                )
                self.static_level = 0.15 + 0.55 * fade
            else:
                elapsed = max(
                    0.0, min(1.0, (now - self.chase_started_at) / 8000.0)
                )
                proximity = max(0.0, min(1.0, 1.0 - distance / 14.0))
                self.static_level = min(1.0, 0.25 + 0.55 * elapsed + 0.4 * proximity)
            self.static_fade_until = 0
        elif self.mode == "hidden" and now >= self.static_fade_until:
            self.static_target = None
            self.static_level = 0.0

    def _in_cone(self, player, flash_range, half_angle):
        dx, dy = self.x - player["x"], self.y - player["y"]
        distance = math.hypot(dx, dy)
        if player.get("equipped_light") == "Lantern":
            return distance <= min(flash_range, 7.0)
        if distance > flash_range:
            return False
        diff = abs(
            (math.atan2(dy, dx) - player.get("look_angle", 0.0) + math.pi)
            % math.tau
            - math.pi
        )
        return diff <= half_angle

    def _flash_check(self, now, dungeon, doors, alive, flash_range, half_angle, events):
        exposed = False
        for cid, player in alive.items():
            if player.get("equipped_light") not in (
                "Flashlight",
                "Lantern",
            ) or not player.get("light_on"):
                continue
            if not self._in_cone(player, flash_range, half_angle):
                continue
            if not has_line_of_sight(
                dungeon, doors, player["x"], player["y"], self.x, self.y
            ):
                continue
            exposed = True
            self.flashed_until = now + 400
            if self.mode in ("windup", "chase", "containment") and cid == self.target:
                continue
            if now - self.flash_last_roll.get(cid, -(10**9)) < FLASH_ROLL_MS:
                continue
            self.flash_last_roll[cid] = now
            if self.rng.random() < self.flash_chance:
                self._aggro(cid, now, events)
                break
            self.flash_chance = min(FLASH_MAX_CHANCE, self.flash_chance * 2)
        if exposed:
            self.flash_last_exposure = now
        elif now - self.flash_last_exposure > FLASH_DECAY_MS:
            self.flash_chance = FLASH_BASE_CHANCE

    def _aggro(self, cid, now, events):
        self.flash_chance = FLASH_BASE_CHANCE
        if self.mode in ("mimic", "windup"):
            self._start_windup(cid, now)
        elif self.mode in ("chase", "containment"):
            self.target = cid
            self.static_target = cid
            self.path = []
            self.target_dir = (0, 0)
            self.stunned_until = now + STUN_MS
            self.chase_end = max(self.chase_end, now + TRANSFER_MIN_CHASE_MS)
            self._set_mode("chase")
            events.append(("aggro", cid))

    def _tick_hidden(self, now, dungeon, doors, blocked, alive):
        if now < self.timer_at:
            return
        if self.hidden_goal is None:
            target = self._pick_target(alive)
            self.hidden_goal = self._pick_spawn(
                dungeon, doors, blocked, alive, target
            )
            if self.hidden_goal is None:
                self.timer_at = now + 5000
                return
            self.hidden_target = target
            self.hidden_source = self._pick_source(alive, target)
            self.hidden_next_step = now
            self.approach_deadline = now + APPROACH_TIMEOUT_MS
        if now < self.hidden_next_step:
            return
        self.hidden_next_step = now + APPROACH_STEP_MS
        if now >= self.approach_deadline:
            self.hidden_goal = None
            self.timer_at = now + 1000
            return
        dx = self.hidden_goal[0] - self.x
        dy = self.hidden_goal[1] - self.y
        if dx:
            self.x += 1 if dx > 0 else -1
        elif dy:
            self.y += 1 if dy > 0 else -1
        if (self.x, self.y) == self.hidden_goal:
            self.target = self.hidden_target
            self.static_target = self.target
            self.static_level = 0.0
            self.source = self.hidden_source
            self.path = []
            self.path_age = 0
            self.path_goal = None
            self.next_step = now + APPROACH_STEP_MS
            self.approach_deadline = now + APPROACH_TIMEOUT_MS
            self.hidden_goal = None
            self._set_mode("approach")

    def _tick_approach(self, now, dungeon, doors, blocked, alive, events):
        can_reveal = (
            dungeon.get((self.x, self.y), WALL) != WALL
            and (self.x, self.y) not in blocked
        )
        if can_reveal and self._proximity_chase(now, dungeon, doors, alive, events):
            return
        if can_reveal and self._seen_by(dungeon, doors, alive) is not None:
            self._enter_mimic(now, alive)
            return
        target_player = alive.get(self.target)
        if target_player is None or now >= self.approach_deadline:
            self._hide(now, HIDE_COOLDOWN_S)
            return
        if now < self.next_step:
            return
        self.next_step = now + APPROACH_STEP_MS
        dx = target_player["x"] - self.x
        dy = target_player["y"] - self.y
        if abs(dx) >= abs(dy) and dx:
            step = (1 if dx > 0 else -1, 0)
        elif dy:
            step = (0, 1 if dy > 0 else -1)
        else:
            return
        next_pos = (self.x + step[0], self.y + step[1])
        self.x, self.y = next_pos

    def _enter_mimic(self, now, alive):
        extra_mimic = min(12.0, max(0, self.floor - TUTORIAL_FLOORS) * 1.5)
        self.mimic_end = now + self._ms((MIMIC_S[0] + extra_mimic, MIMIC_S[1] + extra_mimic))
        source_player = alive.get(self.source)
        if source_player:
            self.color = source_player["color"]
            self.facing = source_player.get("facing", "v")
        self._set_mode("mimic")

    def _tick_mimic(self, now, dungeon, doors, blocked, alive, moves, events):
        if self.source not in alive:
            others = [cid for cid in alive if cid != self.target] or list(alive)
            self.source = others[0]
        source_player = alive[self.source]
        self.color = source_player["color"]
        self.facing = source_player.get("facing", "v")

        move = moves.get(self.source)
        if move:
            if isinstance(move, dict):
                dx, dy = move.get("dx", 0), move.get("dy", 0)
                self.mimic_sprinting = move.get("sprinting", False)
                self.facing = move.get("facing", self.facing)
            else:
                dx, dy = move
            next_pos = (self.x + dx, self.y + dy)
            if next_pos not in blocked and is_walkable(
                dungeon, doors, next_pos[0], next_pos[1], dx, dy
            ):
                self.x, self.y = next_pos
        else:
            self.mimic_sprinting = False

        if self._proximity_chase(now, dungeon, doors, alive, events):
            return
        if now < self.mimic_end:
            return
        target = self.target if self.target in alive else self._pick_target(alive)
        if self._isolated(target, alive):
            self._start_windup(target, now)
        elif (
            self._seen_by(dungeon, doors, alive) is not None
            and now < self.mimic_end + MIMIC_GRACE_MS
        ):
            return
        else:
            self._hide(now, HIDE_COOLDOWN_S)

    def _start_windup(self, cid, now):
        chase_seconds = min(
            CHASE_MAX_S,
            CHASE_BASE_S
            + max(0, self.floor - TUTORIAL_FLOORS) * CHASE_PER_FLOOR_S,
        )
        self.target = cid
        self.static_target = cid
        self.static_level = max(self.static_level, 0.42)
        self.windup_end = now + (
            WINDUP_MS_TUTORIAL if self.floor <= TUTORIAL_FLOORS else WINDUP_MS
        )
        self._pending_chase_ms = int(chase_seconds * 1000)
        self.chase_origin = (self.x, self.y)
        self._set_mode("windup")
        self.path = []
        self.path_goal = None

    def _tick_windup(self, now, alive, events):
        if self.target not in alive:
            self._hide(now, HIDE_COOLDOWN_S)
            return
        if now < self.windup_end:
            return
        cid = self.target
        self._set_mode("chase")
        self.chase_end = now + self._pending_chase_ms
        self.chase_started_at = now
        self.next_step = now + CHASE_STEP_MS
        self.path = []
        self.path_goal = None
        self.target_dir = (0, 0)
        self.flank = False
        self.flank_reroll_at = now + FLANK_REROLL_MS
        self.stunned_until = 0
        events.append(("chase_start", cid))

    def _tick_chase(self, now, dungeon, doors, blocked, alive, events):
        target_player = alive.get(self.target)
        if target_player is None or now >= self.chase_end:
            self._end_chase(now, events, HIDE_COOLDOWN_S)
            return
        if now < self.stunned_until:
            return
        goal = (target_player["x"], target_player["y"])
        if self._is_contained(
            (target_player["x"], target_player["y"]),
            dungeon,
            doors,
            blocked,
        ):
            self.containment_started_at = now
            self._set_mode("containment")
            self.path = []
            self.path_goal = None
            return
        if self._touching(target_player):
            self._catch(now, events, target_player, dungeon, doors, blocked)
            return
        if now >= self.flank_reroll_at:
            self.flank_reroll_at = now + FLANK_REROLL_MS
            self.flank = self.rng.random() < self._flank_chance()
        if self.flank:
            lead = self._lead_point(target_player, dungeon, blocked)
            if lead:
                goal = lead
        self._advance(
            now,
            CHASE_STEP_MS,
            goal,
            dungeon,
            doors,
            blocked,
            events,
            straighten=True,
        )
        if self._touching(target_player):
            self._catch(now, events, target_player, dungeon, doors, blocked)

    def _tick_containment(self, now, dungeon, doors, blocked, alive, events):
        target_player = alive.get(self.target)
        if target_player is None:
            self._end_chase(now, events, HIDE_COOLDOWN_S)
            return
        if not self._is_contained(
            (target_player["x"], target_player["y"]), dungeon, doors, blocked
        ):
            self._set_mode("chase")
            self.path = []
            self.path_goal = None
            self.next_step = now + CHASE_STEP_MS
            return
        if self._touching(target_player):
            self._catch(now, events, target_player, dungeon, doors, blocked)
            return
        self._advance(
            now,
            CONTAINMENT_STEP_MS,
            (target_player["x"], target_player["y"]),
            dungeon,
            doors,
            blocked,
            events,
            straighten=True,
        )
        if self._touching(target_player):
            self._catch(now, events, target_player, dungeon, doors, blocked)

    def _tick_retreat(self, now, dungeon, doors, blocked, alive, events):
        if now >= self.retreat_end:
            self._end_chase(now, events, CATCH_COOLDOWN_S)
            return
        target_player = alive.get(self.target)
        if target_player is None:
            self._end_chase(now, events, CATCH_COOLDOWN_S)
            return
        if not any(
            self._player_can_see(player, (self.x, self.y), dungeon, doors)
            for player in alive.values()
        ):
            return
        if now < self.next_step:
            return
        self.retreat_goal = self._least_seen_goal(
            (self.x, self.y), dungeon, doors, blocked, alive
        )
        if self.retreat_goal is not None:
            self._advance(
                now,
                CHASE_STEP_MS,
                self.retreat_goal,
                dungeon,
                doors,
                blocked,
                events,
            )

    def _begin_retreat(self, now, target_player, dungeon, doors, blocked, events):
        self._set_mode("retreat")
        self.retreat_started_at = now
        self.retreat_end = max(self.chase_end, now + RETREAT_MS)
        self.retreat_goal = None
        self.next_step = now + CHASE_STEP_MS
        self.path = []
        self.path_goal = None
        events.append(("yield", self.target))

    def _touching(self, player):
        return abs(self.x - player["x"]) + abs(self.y - player["y"]) <= 1

    def _catch(self, now, events, target_player, dungeon, doors, blocked):
        cid = self.target
        if self.floor <= TUTORIAL_FLOORS:
            self._begin_retreat(now, target_player, dungeon, doors, blocked, events)
        else:
            events.append(("kill", cid))
            self._end_chase(now, events, CATCH_COOLDOWN_S)

    def _component(self, start, dungeon, doors, blocked):
        if start in blocked or dungeon.get(start, WALL) == WALL:
            return set()
        seen = {start}
        queue = deque([start])
        while queue:
            x, y = queue.popleft()
            for dx, dy in _DIRS:
                pos = (x + dx, y + dy)
                if pos in seen or pos in blocked or dungeon.get(pos, WALL) == WALL:
                    continue
                if not is_walkable(dungeon, doors, pos[0], pos[1], dx, dy):
                    continue
                seen.add(pos)
                queue.append(pos)
        return seen

    def _is_contained(self, target, dungeon, doors, blocked):
        agnate_pos = (self.x, self.y)
        if target == agnate_pos:
            return False
        open_component = self._component(target, dungeon, doors, blocked)
        if agnate_pos not in open_component:
            return False
        closed = set(blocked) | {agnate_pos}
        target_component = self._component(target, dungeon, doors, closed)
        if len(target_component) < CONTAINMENT_COMPONENT_MIN:
            return False
        other_components = []
        for dx, dy in _DIRS:
            neighbor = (agnate_pos[0] + dx, agnate_pos[1] + dy)
            if neighbor in blocked or dungeon.get(neighbor, WALL) == WALL:
                continue
            if not is_walkable(dungeon, doors, neighbor[0], neighbor[1], -dx, -dy):
                continue
            component = self._component(neighbor, dungeon, doors, closed)
            if component and component != target_component:
                other_components.append(component)
        return bool(other_components) and max(map(len, other_components)) > len(
            target_component
        )

    def _player_can_see(self, player, pos, dungeon, doors):
        px, py = player["x"], player["y"]
        dx, dy = pos[0] - px, pos[1] - py
        distance = math.hypot(dx, dy)
        if distance > SEEN_RANGE or not has_line_of_sight(
            dungeon, doors, px, py, pos[0], pos[1]
        ):
            ambient_visible = False
        else:
            ambient_visible = True
        if ambient_visible:
            return True
        if not player.get("light_on"):
            return False
        light = player.get("equipped_light")
        if light == "Lantern":
            return distance <= 7.0 and has_line_of_sight(
                dungeon, doors, px, py, pos[0], pos[1]
            )
        if light != "Flashlight" or distance > self.flash_range:
            return False
        angle = math.atan2(dy, dx)
        diff = abs(
            (angle - player.get("look_angle", 0.0) + math.pi) % math.tau - math.pi
        )
        return diff <= self.flash_half_angle and has_line_of_sight(
            dungeon, doors, px, py, pos[0], pos[1]
        )

    def _least_seen_goal(self, start, dungeon, doors, blocked, alive):
        depths = {start: 0}
        queue = deque([start])
        best_score = None
        best_pos = start
        while queue and len(depths) < BFS_NODE_CAP:
            current = queue.popleft()
            seen_count = sum(
                self._player_can_see(player, current, dungeon, doors)
                for player in alive.values()
            )
            nearest = min(
                (
                    math.hypot(current[0] - player["x"], current[1] - player["y"])
                    for player in alive.values()
                ),
                default=0.0,
            )
            score = (seen_count, depths[current], -nearest)
            if best_score is None or score < best_score:
                best_score, best_pos = score, current
            for dx, dy in _DIRS:
                pos = (current[0] + dx, current[1] + dy)
                if pos in depths or pos in blocked:
                    continue
                if not is_walkable(dungeon, doors, pos[0], pos[1], dx, dy):
                    continue
                depths[pos] = depths[current] + 1
                queue.append(pos)
        return best_pos

    def _end_chase(self, now, events, span):
        events.append(("chase_end", None))
        self._hide(now, span)

    def _flank_chance(self):
        return min(0.7, 0.3 + 0.05 * max(0, self.floor - MIN_FLOOR))

    def _lead_point(self, target_player, dungeon, blocked):
        dx, dy = self.target_dir
        if (dx, dy) == (0, 0):
            return None
        distance = abs(self.x - target_player["x"]) + abs(self.y - target_player["y"])
        steps = min(LEAD_MAX, distance // 3)
        for count in range(steps, 1, -1):
            candidate = (
                target_player["x"] + dx * count,
                target_player["y"] + dy * count,
            )
            if _passable(dungeon, blocked, candidate):
                return candidate
        return None

    def _isolated(self, cid, alive):
        player = alive.get(cid)
        if player is None:
            return False
        others = [other for other_cid, other in alive.items() if other_cid != cid]
        if not others:
            return True
        nearest = min(
            math.hypot(player["x"] - other["x"], player["y"] - other["y"])
            for other in others
        )
        return nearest > ISOLATION_DIST

    def _pick_target(self, alive):
        cids = list(alive)
        if len(cids) == 1:
            return cids[0]
        best, best_score = cids[0], -1.0
        for cid in cids:
            player = alive[cid]
            nearest = min(
                math.hypot(player["x"] - other["x"], player["y"] - other["y"])
                for other_cid, other in alive.items()
                if other_cid != cid
            )
            score = nearest + self.rng.uniform(0, 4)
            if score > best_score:
                best, best_score = cid, score
        return best

    def _pick_source(self, alive, target):
        others = [cid for cid in alive if cid != target]
        if not others:
            return target
        target_player = alive[target]
        return max(
            others,
            key=lambda cid: math.hypot(
                alive[cid]["x"] - target_player["x"],
                alive[cid]["y"] - target_player["y"],
            )
            + self.rng.uniform(0, 3),
        )

    def _seen_by(self, dungeon, doors, alive):
        for cid, player in alive.items():
            if math.hypot(player["x"] - self.x, player["y"] - self.y) <= SEEN_RANGE:
                if has_line_of_sight(
                    dungeon, doors, player["x"], player["y"], self.x, self.y
                ):
                    return cid
        return None

    def _proximity_chase(self, now, dungeon, doors, alive, events):
        best, best_distance = None, PROXIMITY + 1
        for cid, player in alive.items():
            distance = math.hypot(player["x"] - self.x, player["y"] - self.y)
            if distance <= PROXIMITY and distance < best_distance:
                if has_line_of_sight(
                    dungeon, doors, player["x"], player["y"], self.x, self.y
                ):
                    best, best_distance = cid, distance
        if best is None:
            return False
        self._start_windup(best, now)
        return True

    def _pick_spawn(self, dungeon, doors, blocked, alive, target):
        target_player = alive[target]
        start = (target_player["x"], target_player["y"])
        depths = {start: 0}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            depth = depths[current]
            if depth >= SPAWN_MAX_DEPTH:
                continue
            for dx, dy in _DIRS:
                next_pos = (current[0] + dx, current[1] + dy)
                if next_pos in depths or not _passable(dungeon, blocked, next_pos):
                    continue
                depths[next_pos] = depth + 1
                queue.append(next_pos)
        candidates = [
            pos
            for pos, depth in depths.items()
            if depth >= SPAWN_MIN_DEPTH and dungeon.get(pos) == FLOOR
        ]
        self.rng.shuffle(candidates)
        for pos in candidates[:60]:
            if all(
                not self._player_can_see(player, pos, dungeon, doors)
                for player in alive.values()
            ):
                return pos
        return None

    def _find_path(self, start, goal, dungeon, blocked, straighten=False):
        if not _passable(dungeon, blocked, goal):
            return []
        if start == goal:
            return []
        distances = {goal: 0}
        queue = deque([goal])
        while queue and len(distances) < BFS_NODE_CAP:
            current = queue.popleft()
            for dx, dy in _DIRS:
                next_pos = (current[0] + dx, current[1] + dy)
                if next_pos in distances or not _passable(dungeon, blocked, next_pos):
                    continue
                distances[next_pos] = distances[current] + 1
                queue.append(next_pos)
        if start not in distances:
            return []
        path = []
        current = start
        origin = self.chase_origin if straighten and self.chase_origin else start
        line_dx, line_dy = goal[0] - origin[0], goal[1] - origin[1]
        line_length = max(1.0, math.hypot(line_dx, line_dy))
        while current != goal:
            next_steps = [
                (current[0] + dx, current[1] + dy)
                for dx, dy in _DIRS
                if distances.get((current[0] + dx, current[1] + dy))
                == distances[current] - 1
            ]
            if not next_steps:
                return []
            if straighten:
                def line_score(pos):
                    rel_x, rel_y = pos[0] - origin[0], pos[1] - origin[1]
                    deviation = abs(rel_x * line_dy - rel_y * line_dx) / line_length
                    horizontal_first = 0 if line_dx and pos[0] != current[0] else 1
                    return deviation, horizontal_first

                next_pos = min(next_steps, key=line_score)
            else:
                next_pos = next_steps[0]
            path.append(next_pos)
            current = next_pos
        return path

    def _advance(
        self, now, interval, goal, dungeon, doors, blocked, events, straighten=False
    ):
        """Take due steps; ask the host to open any blocking doors."""
        if now - self.next_step > 600:
            self.next_step = now
        steps = 0
        while now >= self.next_step and steps < 2:
            steps += 1
            self.next_step += interval
            drifted = (
                self.path_goal is None
                or abs(goal[0] - self.path_goal[0])
                + abs(goal[1] - self.path_goal[1])
                > 2
            )
            if not self.path or self.path_age >= 4 or drifted:
                self.path = self._find_path(
                    (self.x, self.y), goal, dungeon, blocked, straighten
                )
                self.path_goal = goal
                self.path_age = 0
            if not self.path:
                return
            next_pos = self.path[0]
            if dungeon.get(next_pos) == DOOR:
                dx, dy = next_pos[0] - self.x, next_pos[1] - self.y
                if not is_walkable(
                    dungeon, doors, next_pos[0], next_pos[1], dx, dy
                ):
                    events.append(("door", next_pos))
                    continue
            self.path.pop(0)
            if self.mode == "retreat":
                dx, dy = next_pos[0] - self.x, next_pos[1] - self.y
                self.facing = {
                    (1, 0): ">",
                    (-1, 0): "<",
                    (0, 1): "v",
                    (0, -1): "^",
                }[(dx, dy)]
            self.x, self.y = next_pos
            self.path_age += 1
