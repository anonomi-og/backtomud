import json
import math
import os
import random
import sqlite3
import time
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_socketio import SocketIO, join_room, leave_room, emit, disconnect
from werkzeug.security import generate_password_hash, check_password_hash

# --- Basic Flask setup ---
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-in-prod")

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

DB_PATH = "game.db"

# --- Simple world definition (fixed map) ---
WORLD_WIDTH = 5
WORLD_HEIGHT = 5

WORLD_MAP = [
    [
        {"name": "Grassy clearing", "description": "A small patch of grass under a grey sky."},
        {"name": "Old oak tree", "description": "A huge oak dominates the area, branches creaking."},
        {"name": "Rocky outcrop", "description": "Jagged rocks jut out of the ground."},
        {"name": "Shallow stream", "description": "A trickling stream cuts through the dirt."},
        {"name": "Ruined wall", "description": "Remnants of a stone wall, long collapsed."},
    ],
    [
        {"name": "Dirt path", "description": "A worn path runs north to south."},
        {"name": "Fork in the path", "description": "The path splits in several directions."},
        {"name": "Lonely signpost", "description": "A signpost with faded, unreadable markings."},
        {"name": "Barren patch", "description": "The soil here is dry and cracked."},
        {"name": "Abandoned camp", "description": "Cold ashes and torn canvas flutter in the wind."},
    ],
    [
        {"name": "Shallow pit", "description": "A shallow pit filled with loose stones."},
        {"name": "Tall grass", "description": "Grass up to your waist rustles around you."},
        {"name": "Central crossroads", "description": "Paths lead in every direction."},
        {"name": "Fallen log", "description": "A mossy log lies across the ground."},
        {"name": "Quiet hollow", "description": "The world feels muffled and still here."},
    ],
    [
        {"name": "Muddy track", "description": "Your boots squelch in thick mud."},
        {"name": "Low hill", "description": "You can see a little further from here."},
        {"name": "Thicket", "description": "Tangled branches make movement awkward."},
        {"name": "Stone circle", "description": "Weathered stones form a crude circle."},
        {"name": "Old well", "description": "An ancient well, its rope long gone."},
    ],
    [
        {"name": "Edge of forest", "description": "Dark trees loom to the south."},
        {"name": "Broken cart", "description": "A shattered cart lies in pieces."},
        {"name": "Overgrown track", "description": "Nature is reclaiming this path."},
        {"name": "Quiet glade", "description": "A peaceful glade with soft moss."},
        {"name": "Crumbling tower base", "description": "The base of a long-fallen tower."},
    ],
]

START_X = 2
START_Y = 2  # central tile

ABILITY_KEYS = ("str", "dex", "con", "int", "wis", "cha")
DEFAULT_RACE = "Human"
DEFAULT_CLASS = "Fighter"
DEFAULT_WEAPON_KEY = "unarmed"
PROFICIENCY_BONUS = 2  # SRD level 1 characters

WEAPONS = {
    "unarmed": {"name": "Unarmed Strike", "dice": (1, 1), "ability": "str", "damage_type": "bludgeoning"},
    "longsword": {"name": "Longsword", "dice": (1, 8), "ability": "str", "damage_type": "slashing"},
    "battleaxe": {"name": "Battleaxe", "dice": (1, 8), "ability": "str", "damage_type": "slashing"},
    "spear": {"name": "Spear", "dice": (1, 6), "ability": "str", "damage_type": "piercing"},
    "shortsword": {"name": "Shortsword", "dice": (1, 6), "ability": "dex", "damage_type": "piercing"},
    "dagger": {"name": "Dagger", "dice": (1, 4), "ability": "dex", "damage_type": "piercing"},
    "shortbow": {"name": "Shortbow", "dice": (1, 6), "ability": "dex", "damage_type": "piercing"},
    "mace": {"name": "Mace", "dice": (1, 6), "ability": "str", "damage_type": "bludgeoning"},
    "warhammer": {"name": "Warhammer", "dice": (1, 8), "ability": "str", "damage_type": "bludgeoning"},
    "arcane_bolt": {"name": "Arcane Bolt", "dice": (1, 8), "ability": "int", "damage_type": "force"},
    "sacred_flame": {"name": "Sacred Flame", "dice": (1, 8), "ability": "wis", "damage_type": "radiant"},
}

GENERAL_ITEMS = {
    "rat_tail": {
        "name": "Rat Tail Token",
        "description": "A grisly token proving your victory over a giant rat.",
        "rarity": "common",
    },
    "goblin_bugle": {
        "name": "Goblin Bugle",
        "description": "A dented horn used to rally goblins. It no longer sounds quite right.",
        "rarity": "common",
    },
    "kobold_sling": {
        "name": "Kobold Sling",
        "description": "A worn leather sling sized for small hands. Still functional.",
        "rarity": "common",
    },
}

MOB_TEMPLATES = {
    "giant_rat": {
        "name": "Giant Rat",
        "ac": 12,
        "hp": 7,
        "hp_dice": "2d6",
        "speed": 30,
        "abilities": {"str": 7, "dex": 15, "con": 11, "int": 2, "wis": 10, "cha": 4},
        "attack_bonus": 4,
        "damage": {"dice": (1, 4), "bonus": 2, "type": "piercing"},
        "xp": 25,
        "initial_spawns": 2,
        "gold_range": (1, 6),
        "loot": [("rat_tail", 0.6)],
        "description": "A sewer-dwelling rat the size of a hound, eyes gleaming with hunger.",
    },
    "goblin": {
        "name": "Goblin",
        "ac": 15,
        "hp": 7,
        "hp_dice": "2d6",
        "speed": 30,
        "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
        "attack_bonus": 4,
        "damage": {"dice": (1, 6), "bonus": 2, "type": "slashing"},
        "xp": 50,
        "initial_spawns": 2,
        "gold_range": (2, 12),
        "loot": [("goblin_bugle", 0.4), ("dagger", 0.2)],
        "description": "A wiry goblin clutching rusted blades and muttering in guttural tones.",
    },
    "kobold": {
        "name": "Kobold",
        "ac": 12,
        "hp": 5,
        "hp_dice": "2d6-2",
        "speed": 30,
        "abilities": {"str": 7, "dex": 15, "con": 9, "int": 8, "wis": 7, "cha": 8},
        "attack_bonus": 4,
        "damage": {"dice": (1, 4), "bonus": 2, "type": "piercing"},
        "xp": 25,
        "initial_spawns": 2,
        "gold_range": (1, 8),
        "loot": [("kobold_sling", 0.5)],
        "description": "A scaly kobold scouting the area with wary, darting eyes.",
    },
}

SPELLS = {
    "magic_missile": {
        "name": "Magic Missile",
        "classes": ["Wizard"],
        "type": "attack",
        "description": "Launch three darts of force that automatically strike a creature for 3d4 + 3 force damage.",
        "ability": "int",
        "target": "enemy",
        "damage": {"dice": (3, 4), "bonus": 3, "damage_type": "force", "auto_hit": True},
        "cooldown": 8,
    },
    "burning_hands": {
        "name": "Burning Hands",
        "classes": ["Wizard"],
        "type": "attack",
        "description": "A sheet of flame erupts for 3d6 fire damage to a creature in front of you.",
        "ability": "int",
        "target": "enemy",
        "damage": {"dice": (3, 6), "damage_type": "fire"},
        "cooldown": 10,
    },
    "enhance_agility": {
        "name": "Enhance Ability (Cat's Grace)",
        "classes": ["Wizard", "Cleric"],
        "type": "buff",
        "description": "Bestow feline agility, granting +2 DEX modifier for 2 minutes.",
        "ability": "int",
        "target": "ally",
        "effect": {
            "key": "enhance_agility",
            "modifiers": {"ability_mods": {"dex": 2}},
            "duration": 120,
            "description": "+2 to Dexterity-based checks and defenses.",
        },
        "cooldown": 30,
    },
    "cure_wounds": {
        "name": "Cure Wounds",
        "classes": ["Cleric"],
        "type": "heal",
        "description": "Channel healing energy to restore 1d8 + WIS modifier hit points.",
        "ability": "wis",
        "target": "ally",
        "heal": {"dice": (1, 8), "add_ability_mod": True},
        "cooldown": 10,
    },
    "shield_of_faith": {
        "name": "Shield of Faith",
        "classes": ["Cleric"],
        "type": "buff",
        "description": "A shimmering field surrounds a creature, granting +2 AC for 2 minutes.",
        "ability": "wis",
        "target": "ally",
        "effect": {
            "key": "shield_of_faith",
            "modifiers": {"ac": 2},
            "duration": 120,
            "description": "+2 AC from radiant warding.",
        },
        "cooldown": 30,
    },
    "bless": {
        "name": "Bless",
        "classes": ["Cleric"],
        "type": "buff",
        "description": "You bless a creature, adding 1d4 to its attack rolls for 2 minutes.",
        "ability": "wis",
        "target": "ally",
        "effect": {
            "key": "bless",
            "modifiers": {
                "attack_roll_bonus": {"dice": (1, 4), "label": "Bless"}
            },
            "duration": 120,
            "description": "+1d4 on attack rolls.",
        },
        "cooldown": 30,
    },
    "second_wind": {
        "name": "Second Wind",
        "classes": ["Fighter"],
        "type": "heal",
        "description": "Draw on stamina to heal 1d10 + your level hit points.",
        "ability": "con",
        "target": "self",
        "heal": {"dice": (1, 10), "add_level": True},
        "cooldown": 60,
    },
    "shadow_veil": {
        "name": "Shadow Veil",
        "classes": ["Rogue"],
        "type": "buff",
        "description": "Wrap yourself in shadows, gaining +1 AC and +1 DEX modifier for 1 minute.",
        "ability": "dex",
        "target": "self",
        "effect": {
            "key": "shadow_veil",
            "modifiers": {"ac": 1, "ability_mods": {"dex": 1}},
            "duration": 60,
            "description": "Shrouded in shadow, harder to hit and quicker.",
        },
        "cooldown": 45,
    },
    "keen_eye": {
        "name": "Keen Eye",
        "classes": ["Rogue"],
        "type": "utility",
        "description": "Survey nearby paths to learn who lurks just beyond sight.",
        "ability": "wis",
        "target": "none",
        "cooldown": 30,
    },
}

CLASS_SPELLS = {
    "Wizard": ["magic_missile", "burning_hands", "enhance_agility"],
    "Cleric": ["cure_wounds", "shield_of_faith", "bless", "enhance_agility"],
    "Fighter": ["second_wind"],
    "Rogue": ["shadow_veil", "keen_eye"],
}

RACES = {
    "Human": {"modifiers": {ability: 1 for ability in ABILITY_KEYS}},
    "Elf": {"modifiers": {"dex": 2}},
    "Dwarf": {"modifiers": {"con": 2}},
    "Halfling": {"modifiers": {"dex": 2}},
}

CLASSES = {
    "Fighter": {
        "hit_die": 10,
        "primary_ability": "str",
        "armor_bonus": 2,
        "starting_weapons": ["longsword", "battleaxe", "spear", "dagger"],
    },
    "Rogue": {
        "hit_die": 8,
        "primary_ability": "dex",
        "armor_bonus": 1,
        "starting_weapons": ["shortsword", "dagger", "shortbow"],
    },
    "Wizard": {
        "hit_die": 6,
        "primary_ability": "int",
        "armor_bonus": 0,
        "starting_weapons": ["arcane_bolt", "dagger"],
    },
    "Cleric": {
        "hit_die": 8,
        "primary_ability": "wis",
        "armor_bonus": 1,
        "starting_weapons": ["mace", "warhammer", "sacred_flame"],
    },
}

RACE_OPTIONS = list(RACES.keys())
CLASS_OPTIONS = list(CLASSES.keys())


def normalize_choice(value, valid, default_value):
    if not value:
        return default_value
    value = value.strip()
    for key in valid.keys():
        if key.lower() == value.lower():
            return key
    return default_value


def get_weapon(key):
    if not key:
        return WEAPONS[DEFAULT_WEAPON_KEY]
    return WEAPONS.get(key, WEAPONS[DEFAULT_WEAPON_KEY])


def format_weapon_payload(key):
    weapon = get_weapon(key)
    dice = weapon.get("dice") or (1, 1)
    return {
        "key": key or DEFAULT_WEAPON_KEY,
        "name": weapon["name"],
        "dice": dice,
        "dice_label": format_dice(dice),
        "ability": weapon.get("ability", "str"),
        "damage_type": weapon.get("damage_type", "physical"),
    }


def get_spell(key):
    if not key:
        return None
    return SPELLS.get(key)


def get_spells_for_class(class_name):
    canonical = normalize_choice(class_name, CLASSES, DEFAULT_CLASS)
    return list(dict.fromkeys(CLASS_SPELLS.get(canonical, [])))


def default_inventory_for_class(class_name):
    char_class = normalize_choice(class_name, CLASSES, DEFAULT_CLASS)
    return list(dict.fromkeys(CLASSES[char_class].get("starting_weapons", []) + [DEFAULT_WEAPON_KEY]))


def serialize_inventory(inventory):
    return json.dumps(inventory or [])


def deserialize_inventory(payload):
    if not payload:
        return []
    if isinstance(payload, list):
        return payload
    try:
        data = json.loads(payload)
        if isinstance(data, list):
            return [item for item in data if item in WEAPONS]
    except (json.JSONDecodeError, TypeError):
        pass
    return [part.strip() for part in str(payload).split(",") if part.strip() in WEAPONS]


def serialize_items(items):
    return json.dumps(items or [])


def deserialize_items(payload):
    if not payload:
        return []
    if isinstance(payload, list):
        return [item for item in payload if item in GENERAL_ITEMS]
    try:
        data = json.loads(payload)
        if isinstance(data, list):
            return [item for item in data if item in GENERAL_ITEMS]
    except (json.JSONDecodeError, TypeError):
        pass
    return [part.strip() for part in str(payload).split(",") if part.strip() in GENERAL_ITEMS]


def format_item_payload(key):
    item = GENERAL_ITEMS.get(key)
    if not item:
        return None
    return {
        "key": key,
        "name": item.get("name", key.title()),
        "description": item.get("description", ""),
        "rarity": item.get("rarity", "common"),
    }


def ensure_equipped_weapon(equipped_key, inventory):
    if equipped_key in inventory:
        return equipped_key
    if inventory:
        return inventory[0]
    return DEFAULT_WEAPON_KEY


def roll_4d6_drop_lowest():
    rolls = sorted([random.randint(1, 6) for _ in range(4)], reverse=True)
    return sum(rolls[:3])


def generate_base_scores():
    return {ability: roll_4d6_drop_lowest() for ability in ABILITY_KEYS}


def apply_race_modifiers(scores, race_name):
    race = RACES.get(race_name, RACES[DEFAULT_RACE])
    mods = race.get("modifiers", {})
    modified = dict(scores)
    for ability, bonus in mods.items():
        modified[ability] = modified.get(ability, 10) + bonus
    return modified


def ability_modifier(score):
    return (score - 10) // 2


def format_dice(dice):
    return f"{dice[0]}d{dice[1]}"


def build_character_sheet(race_choice, class_choice):
    race = normalize_choice(race_choice, RACES, DEFAULT_RACE)
    char_class = normalize_choice(class_choice, CLASSES, DEFAULT_CLASS)
    base_scores = generate_base_scores()
    ability_scores = apply_race_modifiers(base_scores, race)
    ability_mods = {ability: ability_modifier(score) for ability, score in ability_scores.items()}
    class_data = CLASSES[char_class]
    inventory = default_inventory_for_class(char_class)
    equipped_weapon = inventory[0] if inventory else DEFAULT_WEAPON_KEY
    weapon_payload = format_weapon_payload(equipped_weapon)
    attack_ability = weapon_payload["ability"] or class_data["primary_ability"]
    proficiency = PROFICIENCY_BONUS
    max_hp = max(class_data["hit_die"] + ability_mods["con"], 1)
    ac = max(10 + ability_mods["dex"] + class_data.get("armor_bonus", 0), 10)
    attack_bonus = ability_mods[attack_ability] + proficiency
    return {
        "race": race,
        "char_class": char_class,
        "level": 1,
        "abilities": ability_scores,
        "ability_mods": ability_mods,
        "max_hp": max_hp,
        "current_hp": max_hp,
        "ac": ac,
        "proficiency": proficiency,
        "weapon": weapon_payload,
        "attack_bonus": attack_bonus,
        "attack_ability": attack_ability,
        "inventory": inventory,
        "equipped_weapon": equipped_weapon,
    }


def derive_character_from_record(record):
    race = normalize_choice(record.get("race"), RACES, DEFAULT_RACE)
    char_class = normalize_choice(record.get("char_class"), CLASSES, DEFAULT_CLASS)
    class_data = CLASSES[char_class]
    abilities = {ability: record.get(f"{ability}_score") or 10 for ability in ABILITY_KEYS}
    ability_mods = {ability: ability_modifier(score) for ability, score in abilities.items()}
    proficiency = PROFICIENCY_BONUS
    ac = max(10 + ability_mods["dex"] + class_data.get("armor_bonus", 0), 10)
    max_hp = record.get("hp") or max(class_data["hit_die"] + ability_mods["con"], 1)
    inventory = deserialize_inventory(record.get("weapon_inventory"))
    if not inventory:
        inventory = default_inventory_for_class(char_class)
    equipped_key = ensure_equipped_weapon(record.get("equipped_weapon"), inventory)
    weapon = format_weapon_payload(equipped_key)
    attack_ability = weapon["ability"] or class_data["primary_ability"]
    attack_bonus = ability_mods[attack_ability] + proficiency
    items = deserialize_items(record.get("item_inventory"))
    return {
        "race": race,
        "char_class": char_class,
        "level": record.get("level") or 1,
        "abilities": abilities,
        "ability_mods": ability_mods,
        "max_hp": max_hp,
        "ac": ac,
        "proficiency": proficiency,
        "weapon": weapon,
        "attack_bonus": attack_bonus,
        "attack_ability": attack_ability,
        "inventory": inventory,
        "equipped_weapon": weapon["key"],
        "xp": record.get("xp") or 0,
        "gold": record.get("gold") or 0,
        "items": items,
    }


def clamp_hp(value, max_hp):
    if value is None:
        return max_hp
    return max(0, min(int(value), max_hp))


def roll_weapon_damage(weapon, ability_mod, crit=False, bonus_damage=0):
    dice_count, dice_size = weapon["dice"]
    total_dice = dice_count * (2 if crit else 1)
    total = sum(random.randint(1, dice_size) for _ in range(total_dice)) + ability_mod + bonus_damage
    return max(1, total)


def roll_dice(dice):
    if not dice:
        return 0
    count, size = dice
    return sum(random.randint(1, size) for _ in range(max(0, count)))

# --- DB helpers: very simple users table ---
def _column_exists(cursor, table, column):
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            hp INTEGER NOT NULL DEFAULT 10,
            atk INTEGER NOT NULL DEFAULT 2,
            race TEXT,
            char_class TEXT,
            str_score INTEGER,
            dex_score INTEGER,
            con_score INTEGER,
            int_score INTEGER,
            wis_score INTEGER,
            cha_score INTEGER,
            current_hp INTEGER,
            level INTEGER,
            equipped_weapon TEXT,
            weapon_inventory TEXT,
            xp INTEGER DEFAULT 0,
            gold INTEGER DEFAULT 0,
            item_inventory TEXT
        );
        """
    )
    if not _column_exists(c, "users", "hp"):
        c.execute("ALTER TABLE users ADD COLUMN hp INTEGER NOT NULL DEFAULT 10")
    if not _column_exists(c, "users", "atk"):
        c.execute("ALTER TABLE users ADD COLUMN atk INTEGER NOT NULL DEFAULT 2")
    if not _column_exists(c, "users", "race"):
        c.execute(f"ALTER TABLE users ADD COLUMN race TEXT DEFAULT '{DEFAULT_RACE}'")
    if not _column_exists(c, "users", "char_class"):
        c.execute(f"ALTER TABLE users ADD COLUMN char_class TEXT DEFAULT '{DEFAULT_CLASS}'")
    for ability in ABILITY_KEYS:
        column = f"{ability}_score"
        if not _column_exists(c, "users", column):
            c.execute(f"ALTER TABLE users ADD COLUMN {column} INTEGER DEFAULT 10")
    if not _column_exists(c, "users", "current_hp"):
        c.execute("ALTER TABLE users ADD COLUMN current_hp INTEGER")
    if not _column_exists(c, "users", "level"):
        c.execute("ALTER TABLE users ADD COLUMN level INTEGER DEFAULT 1")
    if not _column_exists(c, "users", "equipped_weapon"):
        c.execute(f"ALTER TABLE users ADD COLUMN equipped_weapon TEXT DEFAULT '{DEFAULT_WEAPON_KEY}'")
    if not _column_exists(c, "users", "weapon_inventory"):
        c.execute("ALTER TABLE users ADD COLUMN weapon_inventory TEXT")
    if not _column_exists(c, "users", "xp"):
        c.execute("ALTER TABLE users ADD COLUMN xp INTEGER DEFAULT 0")
    if not _column_exists(c, "users", "gold"):
        c.execute("ALTER TABLE users ADD COLUMN gold INTEGER DEFAULT 0")
    if not _column_exists(c, "users", "item_inventory"):
        c.execute("ALTER TABLE users ADD COLUMN item_inventory TEXT")
    c.execute("UPDATE users SET hp = COALESCE(hp, 10)")
    c.execute("UPDATE users SET atk = COALESCE(atk, 2)")
    c.execute("UPDATE users SET xp = COALESCE(xp, 0)")
    c.execute("UPDATE users SET gold = COALESCE(gold, 0)")

    # Backfill missing character sheets
    c.execute(
        """
        SELECT id, username, race, char_class, hp, current_hp, level,
               str_score, dex_score, con_score, int_score, wis_score, cha_score,
               equipped_weapon, weapon_inventory, xp, gold, item_inventory
        FROM users
        """
    )
    for row in c.fetchall():
        needs_sheet = (
            row["race"] is None
            or row["char_class"] is None
            or any(row[f"{ability}_score"] is None for ability in ABILITY_KEYS)
        )
        inventory = deserialize_inventory(row["weapon_inventory"])
        items = deserialize_items(row["item_inventory"])
        char_class = row["char_class"] or DEFAULT_CLASS
        if not inventory:
            inventory = default_inventory_for_class(char_class)
        equipped_weapon = ensure_equipped_weapon(row["equipped_weapon"], inventory)

        if needs_sheet:
            sheet = build_character_sheet(row["race"] or DEFAULT_RACE, row["char_class"] or DEFAULT_CLASS)
            ability_values = sheet["abilities"]
            c.execute(
                """
                UPDATE users
                SET race = ?, char_class = ?, level = ?, hp = ?, current_hp = ?,
                    atk = ?, str_score = ?, dex_score = ?, con_score = ?,
                    int_score = ?, wis_score = ?, cha_score = ?,
                    equipped_weapon = ?, weapon_inventory = ?,
                    xp = ?, gold = ?, item_inventory = ?
                WHERE id = ?
                """,
                (
                    sheet["race"],
                    sheet["char_class"],
                    sheet["level"],
                    sheet["max_hp"],
                    sheet["current_hp"],
                    sheet["attack_bonus"],
                    ability_values["str"],
                    ability_values["dex"],
                    ability_values["con"],
                    ability_values["int"],
                    ability_values["wis"],
                    ability_values["cha"],
                    sheet["equipped_weapon"],
                    serialize_inventory(sheet["inventory"]),
                    row["xp"] if row["xp"] is not None else 0,
                    row["gold"] if row["gold"] is not None else 0,
                    serialize_items([]),
                    row["id"],
                ),
            )
        else:
            if row["current_hp"] is None:
                c.execute("UPDATE users SET current_hp = hp WHERE id = ?", (row["id"],))
            c.execute(
                """
                UPDATE users
                SET equipped_weapon = ?, weapon_inventory = ?,
                    xp = COALESCE(xp, 0), gold = COALESCE(gold, 0),
                    item_inventory = ?
                WHERE id = ?
                """,
                (
                    equipped_weapon,
                    serialize_inventory(inventory),
                    serialize_items(items),
                    row["id"],
                ),
            )

    conn.commit()
    conn.close()
    if not mobs:
        spawn_initial_mobs()


def get_user(username):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = c.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def create_user(username, password, race_choice, class_choice):
    sheet = build_character_sheet(race_choice, class_choice)
    ability_values = sheet["abilities"]
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    password_hash = generate_password_hash(password)
    c.execute(
        """
        INSERT INTO users (
            username, password_hash, race, char_class, level,
            str_score, dex_score, con_score, int_score, wis_score, cha_score,
            hp, current_hp, atk, equipped_weapon, weapon_inventory,
            xp, gold, item_inventory
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            username,
            password_hash,
            sheet["race"],
            sheet["char_class"],
            sheet["level"],
            ability_values["str"],
            ability_values["dex"],
            ability_values["con"],
            ability_values["int"],
            ability_values["wis"],
            ability_values["cha"],
            sheet["max_hp"],
            sheet["current_hp"],
            sheet["attack_bonus"],
            sheet["equipped_weapon"],
            serialize_inventory(sheet["inventory"]),
            0,
            0,
            serialize_items([]),
        ),
    )
    conn.commit()
    conn.close()


def update_user_current_hp(username, hp):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET current_hp = ? WHERE username = ?", (hp, username))
    conn.commit()
    conn.close()


def update_user_equipped_weapon(username, weapon_key):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET equipped_weapon = ? WHERE username = ?", (weapon_key, username))
    conn.commit()
    conn.close()


def update_user_weapon_inventory(username, inventory):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "UPDATE users SET weapon_inventory = ? WHERE username = ?",
        (serialize_inventory(inventory), username),
    )
    conn.commit()
    conn.close()


def update_user_gold(username, gold):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET gold = ? WHERE username = ?", (gold, username))
    conn.commit()
    conn.close()


def update_user_xp(username, xp):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET xp = ? WHERE username = ?", (xp, username))
    conn.commit()
    conn.close()


def update_user_items(username, items):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "UPDATE users SET item_inventory = ? WHERE username = ?",
        (serialize_items(items), username),
    )
    conn.commit()
    conn.close()


# --- In-memory game state (per container, MVP only) ---
# players[username] = {
#     "sid": socket_id,
#     "x": int,
#     "y": int,
#     "hp": int,
#     "max_hp": int,
#     "ac": int,
#     "race": str,
#     "char_class": str,
#     "level": int,
#     "abilities": dict,
#     "ability_mods": dict,
#     "weapon": dict,
#     "attack_bonus": int,
#     "attack_ability": str,
#     "proficiency": int,
#     "inventory": list[str],
#     "equipped_weapon": str,
# }
players = {}
mobs = {}
room_loot = {}
_mob_counter = 0
_loot_counter = 0


def room_name(x, y):
    return f"room_{x}_{y}"


def get_room_info(x, y):
    if 0 <= x < WORLD_WIDTH and 0 <= y < WORLD_HEIGHT:
        return WORLD_MAP[y][x]
    return {"name": "Unknown void", "description": "You should not be here."}


def get_players_in_room(x, y):
    return [u for u, p in players.items() if p["x"] == x and p["y"] == y]


def random_world_position(exclude=None):
    exclude = set(exclude or [])
    attempts = 0
    while attempts < 50:
        x = random.randrange(WORLD_WIDTH)
        y = random.randrange(WORLD_HEIGHT)
        if (x, y) not in exclude:
            return x, y
        attempts += 1
    return random.randrange(WORLD_WIDTH), random.randrange(WORLD_HEIGHT)


def roll_hit_points_from_notation(notation, fallback):
    if not notation:
        return max(1, int(fallback or 1))
    cleaned = notation.lower().replace(" ", "")
    if "d" not in cleaned:
        try:
            return max(1, int(cleaned))
        except ValueError:
            return max(1, int(fallback or 1))
    num_part, rest = cleaned.split("d", 1)
    try:
        count = int(num_part) if num_part else 1
    except ValueError:
        count = 1
    modifier = 0
    size_part = rest
    if "+" in rest:
        size_part, mod_part = rest.split("+", 1)
        try:
            modifier = int(mod_part)
        except ValueError:
            modifier = 0
    elif "-" in rest:
        size_part, mod_part = rest.split("-", 1)
        try:
            modifier = -int(mod_part)
        except ValueError:
            modifier = 0
    try:
        size = int(size_part)
    except ValueError:
        size = max(1, int(fallback or 1))
    total = sum(random.randint(1, max(1, size)) for _ in range(max(1, count))) + modifier
    return max(1, total)


def spawn_mob(template_key, x=None, y=None):
    template = MOB_TEMPLATES.get(template_key)
    if not template:
        return None
    global _mob_counter
    if x is None or y is None:
        x, y = random_world_position(exclude={(START_X, START_Y)})
    _mob_counter += 1
    hp = roll_hit_points_from_notation(template.get("hp_dice"), template.get("hp", 1))
    mob_id = f"{template_key}-{_mob_counter}"
    mob = {
        "id": mob_id,
        "template": template_key,
        "name": template.get("name", template_key.title()),
        "x": x,
        "y": y,
        "ac": template.get("ac", 10),
        "hp": hp,
        "max_hp": hp,
        "xp": template.get("xp", 0),
        "description": template.get("description", ""),
        "abilities": template.get("abilities", {}),
        "gold_range": template.get("gold_range", (0, 0)),
        "loot": list(template.get("loot", [])),
        "contributions": {},
        "alive": True,
    }
    mobs[mob_id] = mob
    return mob


def spawn_initial_mobs():
    occupied = {(START_X, START_Y)}
    for key, template in MOB_TEMPLATES.items():
        count = max(1, int(template.get("initial_spawns", 1)))
        for _ in range(count):
            x, y = random_world_position(exclude=occupied)
            occupied.add((x, y))
            spawn_mob(key, x, y)


def get_mobs_in_room(x, y):
    return [mob for mob in mobs.values() if mob["alive"] and mob["x"] == x and mob["y"] == y]


def format_mob_payload(mob):
    return {
        "id": mob["id"],
        "name": mob["name"],
        "hp": mob["hp"],
        "max_hp": mob["max_hp"],
        "ac": mob["ac"],
        "xp": mob.get("xp", 0),
        "description": mob.get("description", ""),
    }


def find_mob_in_room(identifier, x, y):
    if not identifier:
        return None
    lookup = identifier.strip().lower()
    for mob in get_mobs_in_room(x, y):
        if mob["id"].lower() == lookup or mob["name"].lower() == lookup:
            return mob
    return None


def get_loot_in_room(x, y):
    return list(room_loot.get((x, y), []))


def add_loot_to_room(x, y, loot_entry):
    room_loot.setdefault((x, y), []).append(loot_entry)


def generate_loot_entry_gold(amount):
    global _loot_counter
    _loot_counter += 1
    return {
        "id": f"loot-{_loot_counter}",
        "type": "gold",
        "amount": amount,
        "name": f"{amount} gold coins",
        "description": "A small pile of coins dropped by a defeated foe.",
    }


def generate_loot_entry_item(item_key):
    global _loot_counter
    _loot_counter += 1
    item = GENERAL_ITEMS.get(item_key) or WEAPONS.get(item_key)
    name = item.get("name", item_key.title()) if item else item_key.title()
    description = item.get("description", "") if item else "An unidentified item."
    return {
        "id": f"loot-{_loot_counter}",
        "type": "item",
        "item_key": item_key,
        "name": name,
        "description": description,
    }


def format_loot_payload(entries):
    payload = []
    for entry in entries:
        payload.append(
            {
                "id": entry["id"],
                "type": entry.get("type", "item"),
                "name": entry.get("name", "Mysterious loot"),
                "amount": entry.get("amount"),
                "description": entry.get("description", ""),
            }
        )
    return payload


def resolve_spell_key_from_input(player, identifier):
    if not player or not identifier:
        return None
    lookup = identifier.strip().lower()
    for key in player.get("spells", []):
        spell = get_spell(key)
        if not spell:
            continue
        if key.lower() == lookup or spell["name"].lower() == lookup:
            return key
    return None


def get_spell_cooldown_remaining(player, spell_key):
    if not player:
        return 0
    ready_at = (player.get("cooldowns") or {}).get(spell_key)
    if not ready_at:
        return 0
    remaining = ready_at - time.time()
    if remaining <= 0:
        return 0
    return int(math.ceil(remaining))


def recalculate_player_stats(player):
    if not player:
        return
    base_mods = dict(player.get("base_ability_mods") or player.get("ability_mods") or {})
    if "base_ability_mods" not in player:
        player["base_ability_mods"] = dict(base_mods)
    ability_mods = dict(base_mods)
    base_ac = player.get("base_ac", player.get("ac", 10))
    if "base_ac" not in player:
        player["base_ac"] = base_ac
    proficiency = player.get("proficiency", 0)
    attack_ability = player.get("attack_ability")
    extra_attack_bonus = 0
    ac_bonus = 0
    attack_roll_bonus = []
    damage_bonus = 0
    now = time.time()
    active_effects = []
    for effect in player.get("active_effects", []) or []:
        expires_at = effect.get("expires_at")
        if expires_at and expires_at <= now:
            continue
        active_effects.append(effect)
        modifiers = effect.get("modifiers") or {}
        for ability, delta in (modifiers.get("ability_mods") or {}).items():
            ability_mods[ability] = ability_mods.get(ability, 0) + delta
        ac_bonus += modifiers.get("ac", 0)
        extra_attack_bonus += modifiers.get("attack_bonus", 0)
        attack_bonus_mod = modifiers.get("attack_roll_bonus")
        if attack_bonus_mod:
            attack_roll_bonus.append(dict(attack_bonus_mod))
        damage_bonus += modifiers.get("damage_bonus", 0)
    player["active_effects"] = active_effects
    player["ability_mods"] = ability_mods
    dex_delta = ability_mods.get("dex", 0) - base_mods.get("dex", 0)
    player["ac"] = base_ac + dex_delta + ac_bonus
    attack_mod = ability_mods.get(attack_ability, 0) if attack_ability else 0
    player["attack_bonus"] = proficiency + attack_mod + extra_attack_bonus
    player["attack_roll_bonus_dice"] = attack_roll_bonus
    player["damage_bonus"] = damage_bonus
    player.setdefault("cooldowns", {})
    player.setdefault("active_effects", [])


def apply_effect_to_player(target, effect_template):
    if not target or not effect_template:
        return None
    effect = {
        "key": effect_template.get("key") or effect_template.get("name"),
        "name": effect_template.get("name"),
        "description": effect_template.get("description", ""),
        "modifiers": effect_template.get("modifiers", {}),
        "expires_at": None,
    }
    duration = effect_template.get("duration")
    if duration:
        effect["expires_at"] = time.time() + duration
    stackable = effect_template.get("stackable", False)
    effects = target.setdefault("active_effects", [])
    replaced = False
    if not stackable and effect["key"]:
        for idx, existing in enumerate(effects):
            if existing.get("key") == effect["key"]:
                effects[idx] = effect
                replaced = True
                break
    if not replaced:
        effects.append(effect)
    recalculate_player_stats(target)
    return effect


def format_spell_list(player):
    payload = []
    if not player:
        return payload
    for key in sorted(player.get("spells", []), key=lambda k: get_spell(k)["name"] if get_spell(k) else k):
        spell = get_spell(key)
        if not spell:
            continue
        payload.append(
            {
                "key": key,
                "name": spell["name"],
                "type": spell.get("type", "").title(),
                "description": spell.get("description", ""),
                "cooldown": spell.get("cooldown", 0),
                "cooldown_remaining": get_spell_cooldown_remaining(player, key),
                "target": spell.get("target", "self"),
            }
        )
    return payload


def format_effect_list(player):
    payload = []
    if not player:
        return payload
    now = time.time()
    for effect in player.get("active_effects", []):
        expires_at = effect.get("expires_at")
        remaining = None
        if expires_at:
            remaining = max(0, int(math.ceil(expires_at - now)))
        payload.append(
            {
                "key": effect.get("key"),
                "name": effect.get("name"),
                "description": effect.get("description", ""),
                "expires_in": remaining,
            }
        )
    return payload


def build_player_state(user_record, sid):
    derived = derive_character_from_record(user_record)
    state = {
        "sid": sid,
        "x": START_X,
        "y": START_Y,
    }
    state.update(derived)
    state["inventory"] = list(state.get("inventory", []))
    state["items"] = list(state.get("items", []))
    state["gold"] = int(derived.get("gold", 0))
    state["xp"] = int(derived.get("xp", 0))
    state["hp"] = clamp_hp(user_record.get("current_hp"), derived["max_hp"])
    state["base_ability_mods"] = dict(state.get("ability_mods", {}))
    state["base_ac"] = state.get("ac", 10)
    state["active_effects"] = []
    state["cooldowns"] = {}
    state["spells"] = get_spells_for_class(state.get("char_class"))
    state["attack_roll_bonus_dice"] = []
    state["damage_bonus"] = 0
    apply_weapon_to_player_state(state, state.get("equipped_weapon"))
    recalculate_player_stats(state)
    return state


def apply_weapon_to_player_state(player, weapon_key=None):
    inventory = player.get("inventory") or [DEFAULT_WEAPON_KEY]
    weapon_key = ensure_equipped_weapon(weapon_key, inventory)
    player["equipped_weapon"] = weapon_key
    weapon_payload = format_weapon_payload(weapon_key)
    player["weapon"] = weapon_payload
    class_name = normalize_choice(player.get("char_class"), CLASSES, DEFAULT_CLASS)
    class_data = CLASSES[class_name]
    attack_ability = weapon_payload.get("ability") or class_data["primary_ability"]
    player["attack_ability"] = attack_ability
    recalculate_player_stats(player)
    return weapon_payload


def resolve_weapon_key_from_input(player, identifier):
    if not identifier:
        return None
    target = identifier.strip().lower()
    for key in player.get("inventory", []):
        weapon = get_weapon(key)
        if key.lower() == target or weapon["name"].lower() == target:
            return key
    return None


def equip_weapon_for_player(username, weapon_identifier):
    player = players.get(username)
    if not player:
        return False, "You are not in the game."
    if not weapon_identifier:
        return False, "Select a weapon to equip."

    weapon_key = resolve_weapon_key_from_input(player, weapon_identifier)
    if not weapon_key:
        return False, "You do not possess that weapon."
    if weapon_key == player.get("equipped_weapon"):
        return False, f"{get_weapon(weapon_key)['name']} is already equipped."

    apply_weapon_to_player_state(player, weapon_key)
    update_user_equipped_weapon(username, weapon_key)
    send_room_state(username)

    room = room_name(player["x"], player["y"])
    message = f"{username} equips {player['weapon']['name']}."
    socketio.emit("system_message", {"text": message}, room=room)
    return True, message


def send_room_state(username):
    player = players.get(username)
    if not player:
        return
    recalculate_player_stats(player)
    x, y = player["x"], player["y"]
    room = get_room_info(x, y)
    occupants = get_players_in_room(x, y)
    weapon = player.get("weapon", {})
    inventory_payload = []
    for key in player.get("inventory", []):
        info = format_weapon_payload(key)
        inventory_payload.append(
            {
                "key": info["key"],
                "name": info["name"],
                "dice": info["dice_label"],
                "damage_type": info["damage_type"],
                "equipped": info["key"] == player.get("equipped_weapon"),
            }
        )
    item_payload = []
    for key in player.get("items", []):
        info = format_item_payload(key)
        if not info:
            continue
        item_payload.append(info)
    mobs_here = [format_mob_payload(mob) for mob in get_mobs_in_room(x, y)]
    loot_here = format_loot_payload(get_loot_in_room(x, y))
    payload = {
        "x": x,
        "y": y,
        "room_name": room["name"],
        "description": room["description"],
        "players": occupants,
        "mobs": mobs_here,
        "loot": loot_here,
        "character": {
            "race": player["race"],
            "char_class": player["char_class"],
            "level": player.get("level", 1),
            "hp": player["hp"],
            "max_hp": player["max_hp"],
            "ac": player["ac"],
            "proficiency": player["proficiency"],
            "weapon": {
                "key": weapon.get("key", DEFAULT_WEAPON_KEY),
                "name": weapon.get("name", "Unarmed"),
                "dice": weapon.get("dice_label", "-"),
                "damage_type": weapon.get("damage_type", ""),
            },
            "attack_bonus": player["attack_bonus"],
            "attack_ability": player["attack_ability"],
            "abilities": player["abilities"],
            "ability_mods": player["ability_mods"],
            "weapon_inventory": inventory_payload,
            "items": item_payload,
            "gold": player.get("gold", 0),
            "xp": player.get("xp", 0),
            "spells": format_spell_list(player),
            "effects": format_effect_list(player),
        },
    }
    socketio.emit("room_state", payload, to=player["sid"])


def broadcast_room_state(x, y):
    for occupant in get_players_in_room(x, y):
        send_room_state(occupant)


def describe_adjacent_players(player):
    directions = [
        ("north", (0, -1)),
        ("south", (0, 1)),
        ("west", (-1, 0)),
        ("east", (1, 0)),
    ]
    lines = []
    for label, (dx, dy) in directions:
        nx, ny = player["x"] + dx, player["y"] + dy
        if not (0 <= nx < WORLD_WIDTH and 0 <= ny < WORLD_HEIGHT):
            continue
        occupants = get_players_in_room(nx, ny)
        room = get_room_info(nx, ny)
        if occupants:
            lines.append(f"{label.title()} ({room['name']}): {', '.join(occupants)}")
        else:
            lines.append(f"{label.title()} ({room['name']}): No one in sight.")
    if not lines:
        return "You sense nothing nearby."
    return "Nearby presences:\n" + "\n".join(lines)


def extract_spell_and_target(player, text):
    cleaned = (text or "").strip()
    if not cleaned:
        return None, None
    lower = cleaned.lower()
    for key in player.get("spells", []):
        spell = get_spell(key)
        if not spell:
            continue
        for candidate in (key.lower(), spell["name"].lower()):
            if lower.startswith(candidate):
                remainder = cleaned[len(candidate) :].strip()
                return key, (remainder or None)
    parts = cleaned.split(None, 1)
    if not parts:
        return None, None
    key_guess = resolve_spell_key_from_input(player, parts[0])
    if key_guess:
        remainder = parts[1].strip() if len(parts) > 1 else None
        return key_guess, (remainder or None)
    return None, None


def cast_spell_for_player(username, spell_identifier, target_identifier=None):
    player = players.get(username)
    if not player:
        return False, "You are not in the game."
    if not spell_identifier:
        return False, "Choose a spell or ability to use."

    recalculate_player_stats(player)
    spells_known = player.get("spells", [])
    if spell_identifier in spells_known:
        spell_key = spell_identifier
    else:
        spell_key = resolve_spell_key_from_input(player, spell_identifier)
    if not spell_key:
        return False, "You do not know that spell or ability."

    spell = get_spell(spell_key)
    if not spell:
        return False, "That magic is unknown to this realm."

    remaining = get_spell_cooldown_remaining(player, spell_key)
    if remaining > 0:
        return False, f"{spell['name']} will be ready in {remaining} seconds."

    target_requirement = spell.get("target", "self")
    identifier = (target_identifier or "").strip()
    target_name = None
    if target_requirement == "none":
        target_name = None
    elif target_requirement == "self":
        target_name = username
    elif target_requirement in ("ally", "self_or_ally", "ally_or_self"):
        target_name = identifier or username
    elif target_requirement == "enemy":
        if not identifier:
            return False, f"Choose a target for {spell['name']}."
        target_name = identifier
    else:
        target_name = identifier or username

    target_player = None
    if target_name:
        if target_name == username:
            target_player = player
        else:
            target_player = players.get(target_name)
        if not target_player:
            return False, f"{target_name} is not present."
        if target_requirement == "enemy" and target_name == username:
            return False, "You cannot target yourself with that."
        if target_requirement != "none" and (player["x"], player["y"]) != (target_player["x"], target_player["y"]):
            return False, f"{target_name} is not in the same room."
        recalculate_player_stats(target_player)

    success, feedback = execute_spell(username, player, spell_key, spell, target_player, target_name)
    if not success:
        return False, feedback

    cooldown = spell.get("cooldown", 0)
    if cooldown:
        player.setdefault("cooldowns", {})[spell_key] = time.time() + cooldown

    send_room_state(username)
    if target_player and target_name and target_name != username:
        send_room_state(target_name)

    return True, feedback


def execute_spell(caster_name, caster, spell_key, spell, target_player, target_name):
    room = room_name(caster["x"], caster["y"])
    ability_mod = caster.get("ability_mods", {}).get(spell.get("ability"), 0)
    spell_type = spell.get("type")

    if spell_type == "attack":
        if not target_player or not target_name:
            return False, "No valid target."
        damage_info = spell.get("damage", {})
        damage = roll_dice(damage_info.get("dice")) + damage_info.get("bonus", 0)
        if damage_info.get("add_ability_mod"):
            damage += ability_mod
        damage += caster.get("damage_bonus", 0)
        damage = max(1, damage)
        target_player["hp"] = clamp_hp(target_player["hp"] - damage, target_player["max_hp"])
        update_user_current_hp(target_name, target_player["hp"])
        damage_type = damage_info.get("damage_type")
        dmg_suffix = f" {damage_type} damage" if damage_type else " damage"
        message = f"{caster_name} casts {spell['name']} at {target_name}, dealing {damage}{dmg_suffix}!"
        socketio.emit("system_message", {"text": message}, room=room)
        if target_player["hp"] == 0:
            socketio.emit(
                "system_message",
                {"text": f"{target_name} collapses under the assault!"},
                room=room,
            )
            respawn_player(target_name)
        return True, message

    if spell_type == "heal":
        target = target_player or caster
        target_label = target_name or caster_name
        heal_info = spell.get("heal", {})
        amount = 0
        if heal_info.get("dice"):
            amount += roll_dice(heal_info.get("dice"))
        if heal_info.get("add_ability_mod"):
            amount += ability_mod
        if heal_info.get("add_level"):
            amount += caster.get("level", 1)
        amount += heal_info.get("bonus", 0)
        amount = max(1, amount)
        before = target["hp"]
        target["hp"] = clamp_hp(target["hp"] + amount, target["max_hp"])
        restored = target["hp"] - before
        update_user_current_hp(target_label, target["hp"])
        if restored <= 0:
            message = f"{spell['name']} has no effect on {target_label}."
        else:
            message = f"{caster_name} casts {spell['name']} and restores {restored} HP to {target_label}."
        socketio.emit("system_message", {"text": message}, room=room)
        return True, message

    if spell_type == "buff":
        target = target_player or caster
        target_label = target_name or caster_name
        effect_template = dict(spell.get("effect") or {})
        if not effect_template:
            return False, "No effect defined for this magic."
        effect_template.setdefault("name", spell.get("name"))
        apply_effect_to_player(target, effect_template)
        description = effect_template.get("description")
        if target_label == caster_name:
            message = f"{caster_name} is wreathed in {spell['name']}."
        else:
            message = f"{caster_name} casts {spell['name']} on {target_label}."
        if description:
            message += f" ({description})"
        socketio.emit("system_message", {"text": message}, room=room)
        return True, message

    if spell_type == "utility":
        if spell_key == "keen_eye":
            socketio.emit(
                "system_message",
                {"text": f"{caster_name} narrows their eyes, surveying the surrounding paths."},
                room=room,
            )
            report = describe_adjacent_players(caster)
            notify_player(caster_name, report)
            return True, report
        message = f"{caster_name} invokes {spell['name']}, but its effect is subtle."
        socketio.emit("system_message", {"text": message}, room=room)
        return True, message

    message = f"{caster_name} channels {spell['name']}, but nothing notable happens."
    socketio.emit("system_message", {"text": message}, room=room)
    return True, message


def notify_player(username, text):
    player = players.get(username)
    if not player:
        return
    socketio.emit("system_message", {"text": text}, to=player["sid"])


def respawn_player(username):
    player = players.get(username)
    if not player:
        return

    old_room = room_name(player["x"], player["y"])
    leave_room(old_room, sid=player["sid"])
    socketio.emit(
        "system_message",
        {"text": f"{username} collapses and vanishes in a swirl of grey mist."},
        room=old_room,
    )

    player["x"], player["y"] = START_X, START_Y
    player["hp"] = player["max_hp"]
    update_user_current_hp(username, player["hp"])
    player["active_effects"] = []
    recalculate_player_stats(player)

    new_room = room_name(player["x"], player["y"])
    join_room(new_room, sid=player["sid"])
    socketio.emit(
        "system_message",
        {"text": f"{username} staggers back into the area, looking dazed."},
        room=new_room,
        include_self=False,
    )
    notify_player(username, "You have been defeated and respawn at the crossroads.")
    send_room_state(username)


def handle_command(username, command_text):
    command_text = (command_text or "").strip()
    if not command_text:
        return False

    parts = command_text.split()
    cmd = parts[0].lower()
    if cmd in ("attack", "fight"):
        if len(parts) < 2:
            notify_player(username, "Usage: /attack <target>")
            return True
        target_name = parts[1]
        resolve_attack(username, target_name)
        return True
    if cmd in ("equip", "wield"):
        if len(parts) < 2:
            notify_player(username, "Usage: /equip <weapon_name>")
            return True
        weapon_name = " ".join(parts[1:])
        success, message = equip_weapon_for_player(username, weapon_name)
        if not success:
            notify_player(username, message)
        return True
    if cmd == "cast":
        player = players.get(username)
        if not player:
            notify_player(username, "You are not in the game.")
            return True
        remainder = command_text[len(parts[0]):].strip()
        if not remainder:
            notify_player(username, "Usage: /cast <spell_name> [target]")
            return True
        spell_key, target_text = extract_spell_and_target(player, remainder)
        if not spell_key:
            notify_player(username, "You do not know that spell or ability.")
            return True
        success, message = cast_spell_for_player(username, spell_key, target_text)
        if not success and message:
            notify_player(username, message)
        return True
    if cmd in ("spells", "abilities"):
        player = players.get(username)
        if not player:
            notify_player(username, "You are not in the game.")
            return True
        recalculate_player_stats(player)
        known = format_spell_list(player)
        if not known:
            notify_player(username, "You have no spells or class abilities.")
            return True
        lines = []
        for spell in known:
            cooldown = spell.get("cooldown_remaining", 0)
            base_cd = spell.get("cooldown", 0)
            if cooldown:
                cooldown_text = f" (recharges in {cooldown}s)"
            elif base_cd:
                cooldown_text = f" ({base_cd}s cooldown)"
            else:
                cooldown_text = ""
            spell_type = spell.get("type") or ""
            type_label = f"[{spell_type}] " if spell_type else ""
            lines.append(f"- {type_label}{spell['name']}: {spell['description']}{cooldown_text}")
        notify_player(username, "Known spells & abilities:\n" + "\n".join(lines))
        return True
    if cmd in ("loot", "take", "pickup"):
        if len(parts) < 2:
            notify_player(username, "Usage: /loot <loot-id>")
            return True
        loot_id = parts[1]
        success, message = pickup_loot(username, loot_id)
        if not success and message:
            notify_player(username, message)
        return True

    notify_player(username, f"Unknown command: {cmd}")
    return True

def attack_roll_success(roll, total_attack, target_ac):
    if roll == 1:
        return False
    if roll == 20:
        return True
    return total_attack >= target_ac


def distribute_xp(contributions, total_xp):
    awards = {}
    if not total_xp or total_xp <= 0:
        return awards
    filtered = {player: max(0, int(damage)) for player, damage in contributions.items() if damage > 0}
    if not filtered:
        return awards
    total_damage = sum(filtered.values())
    if total_damage <= 0:
        return awards
    remaining = total_xp
    ordered = sorted(filtered.items(), key=lambda item: item[1], reverse=True)
    for username, damage in ordered:
        share = int(total_xp * damage / total_damage)
        if share > remaining:
            share = remaining
        awards[username] = share
        remaining -= share
    idx = 0
    while remaining > 0 and ordered:
        username = ordered[idx % len(ordered)][0]
        awards[username] = awards.get(username, 0) + 1
        remaining -= 1
        idx += 1
    return {user: amount for user, amount in awards.items() if amount > 0}


def award_xp(username, amount):
    if not amount or amount <= 0:
        return
    player = players.get(username)
    if player:
        player["xp"] = player.get("xp", 0) + amount
        update_user_xp(username, player["xp"])
        notify_player(username, f"You gain {amount} XP.")
    else:
        record = get_user(username)
        if record is None:
            return
        new_total = (record.get("xp") or 0) + amount
        update_user_xp(username, new_total)


def handle_mob_defeat(mob, killer_name=None):
    if not mob or not mob.get("alive"):
        return
    mob["alive"] = False
    x, y = mob["x"], mob["y"]
    room = room_name(x, y)
    socketio.emit(
        "system_message",
        {"text": f"{mob['name']} is slain!"},
        room=room,
    )
    contributions = mob.get("contributions", {})
    xp_total = mob.get("xp", 0)
    awards = distribute_xp(contributions, xp_total)
    if awards:
        for username, amount in awards.items():
            award_xp(username, amount)
    gold_min, gold_max = mob.get("gold_range", (0, 0))
    drops = []
    if gold_max and gold_max >= gold_min and gold_max > 0:
        gold_amount = random.randint(gold_min, gold_max)
        if gold_amount > 0:
            gold_entry = generate_loot_entry_gold(gold_amount)
            add_loot_to_room(x, y, gold_entry)
            drops.append(gold_entry)
    for entry in mob.get("loot", []):
        if isinstance(entry, (list, tuple)) and entry:
            item_key = entry[0]
            chance = entry[1] if len(entry) > 1 else 1.0
        else:
            item_key = entry
            chance = 1.0
        if random.random() <= chance:
            loot_entry = generate_loot_entry_item(item_key)
            add_loot_to_room(x, y, loot_entry)
            drops.append(loot_entry)
    if drops:
        names = ", ".join(drop["name"] for drop in drops)
        socketio.emit(
            "system_message",
            {"text": f"Treasure spills onto the ground: {names}."},
            room=room,
        )
    mobs.pop(mob["id"], None)
    broadcast_room_state(x, y)


def resolve_attack_against_mob(attacker_name, attacker, mob):
    recalculate_player_stats(attacker)
    roll = random.randint(1, 20)
    crit = roll == 20
    attack_bonus = attacker["attack_bonus"]
    bonus_rolls = []
    bonus_total = 0
    for bonus in attacker.get("attack_roll_bonus_dice", []):
        extra = roll_dice(bonus.get("dice"))
        bonus_total += extra
        label = bonus.get("label") or format_dice(bonus.get("dice"))
        bonus_rolls.append((label, extra))
    total_attack = roll + attack_bonus + bonus_total
    room = room_name(attacker["x"], attacker["y"])
    if not attack_roll_success(roll, total_attack, mob["ac"]):
        bonus_text = "".join(f" + {label} {value}" for label, value in bonus_rolls)
        socketio.emit(
            "system_message",
            {
                "text": f"{attacker_name} strikes at {mob['name']} but misses (roll {roll} + {attack_bonus}{bonus_text} = {total_attack} vs AC {mob['ac']}).",
            },
            room=room,
        )
        return
    ability_key = attacker.get("attack_ability", "str")
    ability_mod = attacker["ability_mods"].get(ability_key, 0)
    damage = roll_weapon_damage(
        attacker["weapon"], ability_mod, crit=crit, bonus_damage=attacker.get("damage_bonus", 0)
    )
    mob["hp"] = max(0, mob["hp"] - damage)
    contributions = mob.setdefault("contributions", {})
    contributions[attacker_name] = contributions.get(attacker_name, 0) + damage
    bonus_text = "".join(f" + {label} {value}" for label, value in bonus_rolls)
    attack_detail = f"roll {roll}{' - critical!' if crit else ''} + {attack_bonus}{bonus_text} = {total_attack}"
    socketio.emit(
        "system_message",
        {
            "text": f"{attacker_name} hits {mob['name']} with {attacker['weapon']['name']} for {damage} damage ({attack_detail}, AC {mob['ac']}).",
        },
        room=room,
    )
    if mob["hp"] <= 0:
        handle_mob_defeat(mob, killer_name=attacker_name)
    else:
        broadcast_room_state(attacker["x"], attacker["y"])


def pickup_loot(username, loot_identifier):
    player = players.get(username)
    if not player:
        return False, "You are not in the game."
    if not loot_identifier:
        return False, "Specify which loot to take."
    loot_identifier = loot_identifier.strip().lower()
    x, y = player["x"], player["y"]
    entries = room_loot.get((x, y), [])
    match = None
    for entry in entries:
        if entry["id"].lower() == loot_identifier:
            match = entry
            break
    if not match:
        return False, "No such loot lies here."
    room_loot[(x, y)].remove(match)
    if not room_loot[(x, y)]:
        room_loot.pop((x, y), None)
    room = room_name(x, y)
    if match.get("type") == "gold":
        amount = int(match.get("amount") or 0)
        player["gold"] = player.get("gold", 0) + amount
        update_user_gold(username, player["gold"])
        message = f"{username} scoops up {amount} gold coins."
    else:
        item_key = match.get("item_key")
        if item_key in GENERAL_ITEMS:
            items = player.setdefault("items", [])
            items.append(item_key)
            update_user_items(username, items)
            item_name = GENERAL_ITEMS[item_key]["name"]
            message = f"{username} picks up {item_name}."
        elif item_key in WEAPONS:
            inventory = player.setdefault("inventory", [])
            if item_key not in inventory:
                inventory.append(item_key)
                update_user_weapon_inventory(username, inventory)
            item_name = WEAPONS[item_key]["name"]
            message = f"{username} claims {item_name}."
        else:
            items = player.setdefault("items", [])
            items.append(item_key)
            update_user_items(username, items)
            item_name = match.get("name", "an item")
            message = f"{username} picks up {item_name}."
    socketio.emit("system_message", {"text": message}, room=room)
    broadcast_room_state(x, y)
    return True, message


def resolve_attack(attacker_name, target_name):
    attacker = players.get(attacker_name)
    if not attacker:
        return
    if not target_name:
        notify_player(attacker_name, "Choose a target to attack.")
        return

    target_name = target_name.strip()
    if attacker_name == target_name:
        notify_player(attacker_name, "You cannot attack yourself.")
        return

    target = players.get(target_name)
    if not target:
        mob = find_mob_in_room(target_name, attacker["x"], attacker["y"])
        if mob:
            resolve_attack_against_mob(attacker_name, attacker, mob)
            return
        notify_player(attacker_name, f"{target_name} is nowhere to be found.")
        return

    if attacker["x"] != target["x"] or attacker["y"] != target["y"]:
        notify_player(attacker_name, f"{target_name} is not in the same room.")
        return

    recalculate_player_stats(attacker)
    recalculate_player_stats(target)

    roll = random.randint(1, 20)
    crit = roll == 20
    attack_bonus = attacker["attack_bonus"]
    bonus_rolls = []
    bonus_total = 0
    for bonus in attacker.get("attack_roll_bonus_dice", []):
        extra = roll_dice(bonus.get("dice"))
        bonus_total += extra
        label = bonus.get("label") or format_dice(bonus.get("dice"))
        bonus_rolls.append((label, extra))
    total_attack = roll + attack_bonus + bonus_total
    target_ac = target["ac"]
    room = room_name(attacker["x"], attacker["y"])

    if not attack_roll_success(roll, total_attack, target_ac):
        bonus_text = "".join(f" + {label} {value}" for label, value in bonus_rolls)
        socketio.emit(
            "system_message",
            {
                "text": f"{attacker_name} attacks {target_name} but misses "
                f"(roll {roll} + {attack_bonus}{bonus_text} = {total_attack} vs AC {target_ac})."
            },
            room=room,
        )
        return

    ability_key = attacker["weapon"].get("ability") or attacker["attack_ability"]
    ability_mod = attacker["ability_mods"].get(ability_key, 0)
    damage = roll_weapon_damage(
        attacker["weapon"], ability_mod, crit=crit, bonus_damage=attacker.get("damage_bonus", 0)
    )
    target["hp"] = clamp_hp(target["hp"] - damage, target["max_hp"])
    update_user_current_hp(target_name, target["hp"])

    bonus_text = "".join(f" + {label} {value}" for label, value in bonus_rolls)
    attack_detail = (
        f"roll {roll}{' - critical!' if crit else ''} + {attack_bonus}{bonus_text} = {total_attack}"
    )

    socketio.emit(
        "system_message",
        {
            "text": f"{attacker_name} hits {target_name} with {attacker['weapon']['name']} "
            f"for {damage} damage ({attack_detail}, AC {target_ac})."
        },
        room=room,
    )

    send_room_state(attacker_name)
    send_room_state(target_name)

    if target["hp"] == 0:
        socketio.emit(
            "system_message",
            {"text": f"{target_name} collapses from their wounds!"},
            room=room,
        )
        respawn_player(target_name)


# --- Routes ---
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        action = request.form.get("action")
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Username and password required.")
            return redirect(url_for("login"))

        if action == "register":
            if get_user(username):
                flash("Username already taken.")
                return redirect(url_for("login"))
            race_choice = normalize_choice(request.form.get("race"), RACES, None)
            class_choice = normalize_choice(request.form.get("char_class"), CLASSES, None)
            if not race_choice or not class_choice:
                flash("Select a valid race and class to register.")
                return redirect(url_for("login"))
            create_user(username, password, race_choice, class_choice)
            flash("Account created. You can now log in.")
            return redirect(url_for("login"))

        elif action == "login":
            user = get_user(username)
            if not user or not check_password_hash(user["password_hash"], password):
                flash("Invalid username or password.")
                return redirect(url_for("login"))

            session["username"] = username
            return redirect(url_for("game"))

    if "username" in session:
        return redirect(url_for("game"))
    return render_template("login.html", race_options=RACE_OPTIONS, class_options=CLASS_OPTIONS)


@app.route("/game")
def game():
    if "username" not in session:
        return redirect(url_for("login"))
    return render_template("game.html", username=session["username"])


@app.route("/logout")
def logout():
    username = session.pop("username", None)
    # also clean up in-memory state if they had one
    if username and username in players:
        update_user_current_hp(username, players[username]["hp"])
        # We can't easily emit here without a socket, so leave it to disconnect handler if active
        players.pop(username, None)
    return redirect(url_for("login"))


# --- Socket.IO events ---

@socketio.on("connect")
def on_connect():
    if "username" not in session:
        # Not logged in, refuse connection
        disconnect()
        return
    # connection accepted; actual game join will be done in "join_game"
    emit("connected", {"message": "Connected to game server."})


@socketio.on("join_game")
def on_join_game():
    username = session.get("username")
    if not username:
        emit("system_message", {"text": "You are not logged in. Please reconnect."})
        disconnect()
        return

    user_record = get_user(username)
    if not user_record:
        emit("system_message", {"text": "Unable to load your character. Please log in again."})
        disconnect()
        return

    if username not in players:
        players[username] = build_player_state(user_record, request.sid)
    else:
        existing = players[username]
        preserved_position = (existing.get("x", START_X), existing.get("y", START_Y))
        preserved_hp = clamp_hp(existing.get("hp"), existing.get("max_hp", 1))
        preserved_effects = list(existing.get("active_effects", []))
        preserved_cooldowns = dict(existing.get("cooldowns", {}))

        state = build_player_state(user_record, request.sid)
        state["x"], state["y"] = preserved_position
        state["hp"] = preserved_hp
        state["active_effects"] = preserved_effects
        state["cooldowns"] = preserved_cooldowns
        recalculate_player_stats(state)
        players[username] = state

    x = players[username]["x"]
    y = players[username]["y"]
    rname = room_name(x, y)

    join_room(rname)

    # Notify others in the room
    emit("system_message", {"text": f"{username} has entered the room."}, room=rname, include_self=False)

    # Send room state to this player
    send_room_state(username)


@socketio.on("move")
def on_move(data):
    username = session.get("username")
    if not username or username not in players:
        return

    direction = data.get("direction")
    player = players[username]
    old_x, old_y = player["x"], player["y"]
    new_x, new_y = old_x, old_y

    if direction == "north":
        new_y -= 1
    elif direction == "south":
        new_y += 1
    elif direction == "west":
        new_x -= 1
    elif direction == "east":
        new_x += 1

    # Bounds check
    if not (0 <= new_x < WORLD_WIDTH and 0 <= new_y < WORLD_HEIGHT):
        emit("system_message", {"text": "You cannot go that way."})
        return

    old_room = room_name(old_x, old_y)
    new_room = room_name(new_x, new_y)

    if (new_x, new_y) == (old_x, old_y):
        # no move
        return

    # Update player position
    player["x"], player["y"] = new_x, new_y

    # Leave old room, notify others
    leave_room(old_room)
    emit("system_message", {"text": f"{username} has left the room."}, room=old_room)

    # Join new room, notify others
    join_room(new_room)
    emit("system_message", {"text": f"{username} has entered the room."}, room=new_room, include_self=False)

    # Send new room state to moving player
    send_room_state(username)


@socketio.on("equip_weapon")
def on_equip_weapon(data):
    username = session.get("username")
    if not username or username not in players:
        return
    weapon_key = (data or {}).get("weapon") or (data or {}).get("weapon_key")
    success, message = equip_weapon_for_player(username, weapon_key)
    if not success:
        notify_player(username, message)


@socketio.on("cast_spell")
def on_cast_spell(data):
    username = session.get("username")
    if not username or username not in players:
        return
    payload = data or {}
    spell_identifier = payload.get("spell") or payload.get("spell_key") or payload.get("name")
    target = payload.get("target") or payload.get("target_name")
    success, message = cast_spell_for_player(username, spell_identifier, target)
    if not success and message:
        notify_player(username, message)


@socketio.on("pickup_loot")
def on_pickup_loot(data):
    username = session.get("username")
    if not username or username not in players:
        return
    payload = data or {}
    loot_id = payload.get("loot_id") or payload.get("id") or payload.get("loot")
    success, message = pickup_loot(username, loot_id)
    if not success and message:
        notify_player(username, message)


@socketio.on("chat")
def on_chat(data):
    username = session.get("username")
    if not username or username not in players:
        return

    text = (data.get("text") or "").strip()
    if not text:
        return

    if text.startswith("/"):
        handled = handle_command(username, text[1:])
        if handled:
            return

    x, y = players[username]["x"], players[username]["y"]
    rname = room_name(x, y)

    emit("chat_message", {"from": username, "text": text}, room=rname)


@socketio.on("disconnect")
def on_disconnect():
    # We can try to identify the user by sid
    username = None
    for u, p in list(players.items()):
        if p["sid"] == request.sid:
            username = u
            break

    if username:
        x, y = players[username]["x"], players[username]["y"]
        rname = room_name(x, y)
        # Notify others
        emit("system_message", {"text": f"{username} has disconnected."}, room=rname)
        update_user_current_hp(username, players[username]["hp"])
        # Remove from players (MVP: no persistent positions)
        players.pop(username, None)


if not mobs:
    spawn_initial_mobs()


if __name__ == "__main__":
    init_db()
    # Bind to 0.0.0.0 for container use
    socketio.run(app, host="0.0.0.0", port=5000)
