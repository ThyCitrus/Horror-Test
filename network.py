"""
network.py — authoritative host / connecting client, TCP, newline-delimited JSON.

GameServer:  runs on the host's machine. Owns connection lifecycle, name/color
             reservation, and per-client input intent + position. Does NOT
             resolve movement/collision — that's main.py's job, since it's the
             one with access to the dungeon.

GameClient:  runs on every machine, including the host (which connects to its
             own GameServer over a real socket — no shortcuts). Sends local
             input, receives broadcasts, hands everything to an inbox queue
             for main.py to dispatch.
"""

import json
import socket
import threading
import queue
import uuid
from pathlib import Path

DEFAULT_PORT = 5555
BROADCAST_HZ = 10
CLIENT_ID_PATH = Path("client_id.txt")


# --- Local persistent client identity (not a security boundary, just continuity) ---


def get_client_id() -> str:
    if CLIENT_ID_PATH.exists():
        return CLIENT_ID_PATH.read_text(encoding="utf-8").strip()
    token = str(uuid.uuid4())
    CLIENT_ID_PATH.write_text(token, encoding="utf-8")
    return token


# --- Shared framing helper: newline-delimited JSON over a TCP socket ---


class _JsonStream:
    def __init__(self, sock):
        self.sock = sock
        self._buf = b""

    def send(self, obj: dict) -> None:
        payload = (json.dumps(obj) + "\n").encode("utf-8")
        self.sock.sendall(payload)

    def read_messages(self):
        """Blocking-ish: reads whatever's available, yields any complete
        newline-delimited JSON messages. Raises OSError/ConnectionError on
        a dead socket, same as the underlying recv."""
        chunk = self.sock.recv(4096)
        if not chunk:
            raise ConnectionError("peer closed connection")
        self._buf += chunk
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            if line.strip():
                yield json.loads(line.decode("utf-8"))


# --- Server ---


class GameServer:
    def __init__(self, seed: int, port: int = DEFAULT_PORT, spawn_fn=None):
        self.seed = seed
        self.port = port
        self.players = {}  # client_id -> player record dict
        self.spawn_fn = spawn_fn or (lambda: (0, 0))
        self._lock = threading.RLock()
        self._sock = None
        self._running = False

    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("0.0.0.0", self.port))
        self._sock.listen()
        self._running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()
        threading.Thread(target=self._broadcast_loop, daemon=True).start()

    def stop(self):
        self._running = False
        if self._sock:
            self._sock.close()

    # --- accept / per-client handling ---

    def _accept_loop(self):
        while self._running:
            try:
                conn, _addr = self._sock.accept()
            except OSError:
                return
            threading.Thread(
                target=self._client_loop, args=(conn,), daemon=True
            ).start()

    def _client_loop(self, conn):
        stream = _JsonStream(conn)
        client_id = None
        try:
            while self._running:
                for msg in stream.read_messages():
                    mtype = msg.get("type")

                    if mtype == "hello":
                        client_id = msg.get("client_id")
                        self._handle_hello(client_id, stream)

                    elif mtype == "join" and client_id:
                        self._handle_join(
                            client_id, msg.get("name", ""), msg.get("color", ""), stream
                        )

                    elif mtype == "input" and client_id:
                        with self._lock:
                            if client_id in self.players:
                                self.players[client_id]["dx"] = msg.get("dx", 0)
                                self.players[client_id]["dy"] = msg.get("dy", 0)
        except (ConnectionError, OSError):
            pass
        finally:
            if client_id:
                with self._lock:
                    if client_id in self.players:
                        self.players[client_id]["connected"] = False
                        self.players[client_id]["socket"] = None

    def _handle_hello(self, client_id, stream):
        with self._lock:
            existing = self.players.get(client_id)
            if existing and not existing["connected"]:
                # reconnect: same identity, no re-pick needed
                existing["connected"] = True
                existing["socket"] = stream
                stream.send(
                    {
                        "type": "roster",
                        "reconnect": True,
                        "you": {"name": existing["name"], "color": existing["color"]},
                        "seed": self.seed,
                    }
                )
                return

            taken_names, taken_colors = self._taken(exclude=client_id)
            stream.send(
                {
                    "type": "roster",
                    "reconnect": False,
                    "taken_names": taken_names,
                    "taken_colors": taken_colors,
                    "seed": self.seed,
                }
            )

    def _handle_join(self, client_id, name, color, stream):
        with self._lock:
            taken_names, taken_colors = self._taken(exclude=client_id)
            if not name.strip():
                stream.send({"type": "join_reject", "reason": "name_empty"})
                return
            if name in taken_names:
                stream.send(
                    {
                        "type": "join_reject",
                        "reason": "name_taken",
                        "taken_names": taken_names,
                        "taken_colors": taken_colors,
                    }
                )
                return
            if color in taken_colors:
                stream.send(
                    {
                        "type": "join_reject",
                        "reason": "color_taken",
                        "taken_names": taken_names,
                        "taken_colors": taken_colors,
                    }
                )
                return

            spawn_x, spawn_y = self.spawn_fn()

            self.players[client_id] = {
                "name": name,
                "color": color,
                "x": spawn_x,
                "y": spawn_y,
                "facing": "v",
                "alive": True,
                "connected": True,
                "dx": 0,
                "dy": 0,
                "socket": stream,
            }
            stream.send(
                {
                    "type": "join_ack",
                    "client_id": client_id,
                    "name": name,
                    "color": color,
                }
            )

    def _taken(self, exclude=None):
        names, colors = [], []
        for cid, p in self.players.items():
            if cid == exclude:
                continue
            if p["connected"]:
                names.append(p["name"])
                colors.append(p["color"])
        return names, colors

    # --- called by main.py's authoritative loop ---

    def get_players_snapshot(self) -> dict:
        with self._lock:
            return {
                cid: {k: v for k, v in p.items() if k != "socket"}
                for cid, p in self.players.items()
            }

    def consume_and_clear_input(self, client_id):
        """Reads and zeroes a player's pending move intent in one atomic step,
        so a single move-tick from a client is applied exactly once."""
        with self._lock:
            p = self.players.get(client_id)
            if not p:
                return 0, 0
            dx, dy = p["dx"], p["dy"]
            p["dx"], p["dy"] = 0, 0
            return dx, dy

    def update_player_position(self, client_id, x, y, facing):
        with self._lock:
            if client_id in self.players:
                self.players[client_id]["x"] = x
                self.players[client_id]["y"] = y
                self.players[client_id]["facing"] = facing

    def set_player_alive(self, client_id, alive: bool):
        with self._lock:
            if client_id in self.players:
                self.players[client_id]["alive"] = alive

    # --- broadcast thread ---

    def _broadcast_loop(self):
        import time

        while self._running:
            time.sleep(1.0 / BROADCAST_HZ)
            with self._lock:
                state = {
                    "type": "state",
                    "players": {
                        cid: {k: v for k, v in p.items() if k != "socket"}
                        for cid, p in self.players.items()
                    },
                }
                dead_sockets = []
                for cid, p in self.players.items():
                    if p["connected"] and p["socket"]:
                        try:
                            p["socket"].send(state)
                        except (ConnectionError, OSError):
                            dead_sockets.append(cid)
                for cid in dead_sockets:
                    self.players[cid]["connected"] = False
                    self.players[cid]["socket"] = None


# --- Client ---


class GameClient:
    def __init__(self):
        self.client_id = get_client_id()
        self._sock = None
        self._stream = None
        self.inbox = queue.Queue()
        self._running = False

    def connect(self, host: str, port: int = DEFAULT_PORT, timeout: float = 5.0):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(timeout)
        self._sock.connect((host, port))
        self._sock.settimeout(None)  # back to blocking for the recv loop
        self._stream = _JsonStream(self._sock)
        self._running = True
        threading.Thread(target=self._recv_loop, daemon=True).start()
        self._stream.send({"type": "hello", "client_id": self.client_id})

    def disconnect(self):
        self._running = False
        if self._sock:
            self._sock.close()

    def send_join(self, name: str, color: str):
        self._stream.send({"type": "join", "name": name, "color": color})

    def send_input(self, dx: int, dy: int):
        self._stream.send({"type": "input", "dx": dx, "dy": dy})

    def poll_messages(self):
        """Non-blocking drain. Call once per frame; dispatch by msg['type']."""
        msgs = []
        while True:
            try:
                msgs.append(self.inbox.get_nowait())
            except queue.Empty:
                break
        return msgs

    def _recv_loop(self):
        try:
            while self._running:
                for msg in self._stream.read_messages():
                    self.inbox.put(msg)
        except (ConnectionError, OSError):
            self.inbox.put({"type": "disconnected"})
