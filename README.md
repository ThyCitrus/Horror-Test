# Dungeon Crawler

A terminal-UI styled horror dungeon crawler built with **Pygame**, featuring procedural level generation, dynamic field-of-view, fog-of-war visual rendering, persistent character saves, and host/client networking.

---

## Key Features

* **Procedural Dungeon Generation**: Generates complex dungeons with connected rooms, hallways, elbows, rings, and pillar structures.
* **Dynamic FOV & Fog of War**: Real-time line-of-sight calculation with dynamic brightness falloff and perspective stretching on visible wall tiles.
* **Character Saves**: Persistent save slots storing player location, character attributes, seed data, and inventory.
* **Terminal Interface**: ASCII-inspired UI rendering local logs, minimap, inventory, and host connection information.
* **Multiplayer (In Development)**: Built-in TCP host/client networking framework for multi-player dungeon crawling.

---

## Networking & Port Forwarding

* **Current Status**: Multiplayer connection features are currently under active development and may be blocked by Windows Firewall or local router settings.
* **Workaround**: If hosting or joining across different networks fails, use tunneling services like [playit.gg](https://playit.gg/) or Ngrok to route the default TCP port (`5555`).

---

## File Overview

* **`main.py`**: Primary entry point. Handles rendering loops, camera viewport tracking, Pygame event processing, and multiplayer message dispatching.
* **`dungeon_gen.py`**: Core algorithm for procedural dungeon layout generation, line-of-sight checks, FOV computation, and fog brightness calculations.
* **`terminal_ui.py`**: Manages interactive UI states, save slot navigation, multiplayer setup screens, and HUD/minimap rendering.
* **`network.py`**: Implements `GameServer` and `GameClient` using TCP sockets with newline-delimited JSON message streams.
* **`save_utils.py`**: File I/O utilities for loading, writing, listing, and deleting character saves in JSON format.
* **`enemies.py`**: Defines base enemy types and behavior structures.

---

## Getting Started

### Prerequisites

* Python 3.8 or higher

### Installation

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/your-username/horror-test.git](https://github.com/your-username/horror-test.git)
   cd horror-test


Install dependencies:

```Bash
pip install -r requirements.txt
Run the game:
```
```Bash
python main.py
```
## Controls
W / A / S / D: Move character / Change facing direction

Arrow Keys: Navigate menu and color selection screens

Enter / Left Click: Confirm menu selection

Esc: Open menu / Save & quit singleplayer / Disconnect from multiplayer