class PlayerClass:
    def __init__(
        self,
        name: str,
        description: str,
        health_mod: float = 1.0,
        damage_mod: float = 1.0,
        gold_mod: float = 1.0,
        mana_mod: float = 0.0,
        stamina_mod: float = 0.0,
        str_stat: int = 5,
        dex_stat: int = 5,
        int_stat: int = 5,
        color: tuple[int, int, int] = (255, 255, 255),
    ):
        self.name = name
        self.description = description
        self.health_mod = health_mod
        self.damage_mod = damage_mod
        self.gold_mod = gold_mod
        self.mana_mod = mana_mod
        self.stamina_mod = stamina_mod
        self.str_stat = str_stat
        self.dex_stat = dex_stat
        self.int_stat = int_stat
        self.color = color  # (r, g, b) used for menu display only


def compute_stats(player_class: "PlayerClass") -> dict:
    return {
        "max_health": int(100 * player_class.health_mod),
        "max_mana": int(60 * player_class.mana_mod),
        "max_stamina": int(60 * player_class.stamina_mod),
        "str": player_class.str_stat,
        "dex": player_class.dex_stat,
        "int": player_class.int_stat,
    }


FIGHTER = PlayerClass(
    "Fighter",
    "Strong and capable",
    health_mod=1.5,
    damage_mod=1.5,
    gold_mod=1.2,
    stamina_mod=1.5,
    str_stat=9,
    dex_stat=5,
    int_stat=2,
    color=(220, 60, 60),
)
WARLOCK = PlayerClass(
    "Warlock",
    "Dark and mysterious",
    health_mod=0.9,
    damage_mod=1.1,
    gold_mod=1.2,
    mana_mod=1.5,
    str_stat=2,
    dex_stat=4,
    int_stat=10,
    color=(160, 60, 220),
)
ROGUE = PlayerClass(
    "Rogue",
    "Quick and lonesome",
    health_mod=1.0,
    damage_mod=1.2,
    gold_mod=1.5,
    stamina_mod=1.3,
    str_stat=4,
    dex_stat=10,
    int_stat=3,
    color=(60, 200, 100),
)
CLERIC = PlayerClass(
    "Cleric",
    "Divine healer and protector",
    health_mod=0.7,
    damage_mod=0.9,
    gold_mod=1.1,
    mana_mod=1.4,
    str_stat=3,
    dex_stat=3,
    int_stat=9,
    color=(230, 210, 80),
)

CLASSES = {
    "fighter": FIGHTER,
    "warlock": WARLOCK,
    "rogue": ROGUE,
    "cleric": CLERIC,
}
