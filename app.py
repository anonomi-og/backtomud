import json
import math
import os
import random
import time
from typing import Optional

from dotenv import load_dotenv

from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_socketio import SocketIO, join_room, leave_room, emit, disconnect
from werkzeug.security import generate_password_hash, check_password_hash

import db_utils

# Database bootstrap:
#   1. Execute schema_and_seed.sql against your MariaDB instance (e.g. `mysql < schema_and_seed.sql`).
#   2. Provide DB_HOST, DB_PORT, DB_NAME, DB_USER, and DB_PASSWORD in your .env file.
#   3. Start the Flask app; all world, mob, and item data will now be read from MariaDB.

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency path
    OpenAI = None

try:
    import openai as openai_module
except ImportError:  # pragma: no cover - optional dependency path
    openai_module = None

# --- Basic Flask setup ---
load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-in-prod")

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# --- Multi-zone world definition (loaded from MariaDB) ---
DEFAULT_ZONE = "village"
MAX_CHARACTERS_PER_ACCOUNT = 3

DIRECTION_VECTORS = {
    "north": (0, -1),
    "south": (0, 1),
    "west": (-1, 0),
    "east": (1, 0),
}

DOOR_DEFINITIONS = {
    "village_town_hall_service": {
        "name": "Town Hall Service Door",
        "description": "Thick oak panels banded with iron link the town hall to the storage barn.",
        "initial_state": "closed",
        "endpoints": [
            {"zone": "village", "coords": (2, 1), "direction": "east"},
            {"zone": "village", "coords": (3, 1), "direction": "west"},
        ],
    },
    "crystal_gate_lattice": {
        "name": "Crystal Gate Lattice",
        "description": "A ribbed lattice of crystal bars can seal the passage between the gate and the watchpost.",
        "initial_state": "closed",
        "endpoints": [
            {"zone": "dungeon_2", "coords": (3, 0), "direction": "south"},
            {"zone": "dungeon_2", "coords": (3, 1), "direction": "north"},
        ],
    },
}

DOORS = {}
DOOR_ENDPOINT_LOOKUP = {}


def initialize_doors():
    for door_id, spec in DOOR_DEFINITIONS.items():
        endpoints = []
        for endpoint in spec.get("endpoints", []):
            coords = tuple(endpoint["coords"])
            record = {
                "zone": endpoint["zone"],
                "coords": coords,
                "direction": endpoint["direction"],
            }
            endpoints.append(record)
            DOOR_ENDPOINT_LOOKUP[(record["zone"], coords[0], coords[1], record["direction"])] = door_id
        DOORS[door_id] = {
            "id": door_id,
            "name": spec["name"],
            "description": spec["description"],
            "state": spec.get("initial_state", "closed"),
            "endpoints": endpoints,
        }


initialize_doors()

ABILITY_KEYS = ("str", "dex", "con", "int", "wis", "cha")
DEFAULT_RACE = "Human"
DEFAULT_CLASS = "Fighter"
DEFAULT_WEAPON_KEY = "unarmed"
PROFICIENCY_BONUS = 2  # SRD level 1 characters

# --- Global action timing ---
BASE_ACTION_COOLDOWN = 1.0  # baseline delay between rate-limited actions
MIN_ACTION_MULTIPLIER = 0.75
MAX_ACTION_MULTIPLIER = 1.25

NPC_SECRET_THRESHOLD = 5
NPC_MODEL_NAME = os.environ.get("OPENAI_NPC_MODEL", "gpt-4o-mini")

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


def _weapon_template_map():
    return db_utils.get_weapon_templates()


def _general_item_template_map():
    return db_utils.get_general_item_templates()


def parse_damage_dice(notation):
    if not notation:
        return (1, 1)
    try:
        count_str, size_str = notation.lower().split("d", 1)
        return (int(count_str or 1), int(size_str))
    except (ValueError, AttributeError):
        return (1, 1)


def get_weapon(key):
    templates = _weapon_template_map()
    record = templates.get(key) or templates.get(DEFAULT_WEAPON_KEY)
    if not record:
        raise RuntimeError("Default weapon template is missing from the database")
    ability = "str"
    metadata = record.get("consumable_effect_json")
    if metadata:
        try:
            ability = json.loads(metadata).get("ability", "str")
        except (TypeError, json.JSONDecodeError):
            ability = "str"
    dice = parse_damage_dice(record.get("damage_dice"))
    return {
        "name": record.get("name", key or DEFAULT_WEAPON_KEY),
        "dice": dice,
        "ability": ability,
        "damage_type": record.get("damage_type", "physical"),
    }


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
            templates = _weapon_template_map()
            return [item for item in data if item in templates]
    except (json.JSONDecodeError, TypeError):
        pass
    templates = _weapon_template_map()
    return [part.strip() for part in str(payload).split(",") if part.strip() in templates]


def serialize_items(items):
    return json.dumps(items or [])


def deserialize_items(payload):
    if not payload:
        return []
    if isinstance(payload, list):
        templates = _general_item_template_map()
        return [item for item in payload if item in templates]
    try:
        data = json.loads(payload)
        if isinstance(data, list):
            templates = _general_item_template_map()
            return [item for item in data if item in templates]
    except (json.JSONDecodeError, TypeError):
        pass
    templates = _general_item_template_map()
    return [part.strip() for part in str(payload).split(",") if part.strip() in templates]


def format_item_payload(key):
    item = db_utils.get_item_template(key)
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


def build_character_sheet(race_choice, class_choice, base_scores=None):
    race = normalize_choice(race_choice, RACES, DEFAULT_RACE)
    char_class = normalize_choice(class_choice, CLASSES, DEFAULT_CLASS)
    if base_scores:
        base_scores = {ability: int(base_scores.get(ability, 10)) for ability in ABILITY_KEYS}
    else:
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
    race = normalize_choice(record.get("species") or record.get("race"), RACES, DEFAULT_RACE)
    char_class = normalize_choice(record.get("class") or record.get("char_class"), CLASSES, DEFAULT_CLASS)
    class_data = CLASSES[char_class]
    abilities = {ability: record.get(f"{ability}_score") or 10 for ability in ABILITY_KEYS}
    ability_mods = {ability: ability_modifier(score) for ability, score in abilities.items()}
    proficiency = record.get("proficiency_bonus") or PROFICIENCY_BONUS
    ac = max(10 + ability_mods["dex"] + class_data.get("armor_bonus", 0), 10)
    max_hp = record.get("max_hp") or record.get("hp") or max(class_data["hit_die"] + ability_mods["con"], 1)
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
        "gold": record.get("coin_gp") or record.get("gold") or 0,
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

# --- DB helpers ---



def init_db():
    """Ensure the MariaDB connection is available and seed mobs on startup."""

    try:
        db_utils.fetch_one("SELECT 1 AS ok")
    except Exception as exc:
        raise RuntimeError("Unable to connect to the game database") from exc
    if not mobs:
        spawn_initial_mobs()


def get_account(username):
    return db_utils.fetch_one("SELECT * FROM accounts WHERE username = :username", username=username)


def get_account_by_id(account_id):
    return db_utils.fetch_one("SELECT * FROM accounts WHERE account_id = :account_id", account_id=account_id)


def create_account(username, password):
    password_hash = generate_password_hash(password)
    return db_utils.insert_and_return_id(
        "INSERT INTO accounts (username, password_hash) VALUES (:username, :password_hash)",
        username=username,
        password_hash=password_hash,
    )


def count_account_characters(account_id):
    row = db_utils.fetch_one(
        "SELECT COUNT(*) AS total FROM characters WHERE account_id = :account_id",
        account_id=account_id,
    )
    return int(row.get("total", 0)) if row else 0


def get_account_characters(account_id):
    rows = db_utils.fetch_all(
        "SELECT * FROM characters WHERE account_id = :account_id ORDER BY created_at",
        account_id=account_id,
    )
    return [normalize_character_record(row) for row in rows]


def get_character_by_id(character_id):
    record = db_utils.fetch_one(
        "SELECT * FROM characters WHERE character_id = :character_id",
        character_id=character_id,
    )
    return normalize_character_record(record)


def get_character_by_name(name):
    record = db_utils.fetch_one(
        "SELECT * FROM characters WHERE name = :name",
        name=name,
    )
    return normalize_character_record(record)


def create_character(account_id, name, race_choice, class_choice, ability_scores, bio="", description=""):
    sheet = build_character_sheet(race_choice, class_choice, base_scores=ability_scores)
    ability_values = sheet["abilities"]
    start_zone = DEFAULT_ZONE
    start_x, start_y = get_world_start(start_zone)
    room_record = db_utils.get_room_by_coords(start_zone, start_x, start_y)
    current_room_id = room_record.get("room_id") if room_record else None
    inventory_payload = serialize_inventory(sheet["inventory"])
    character_id = db_utils.insert_and_return_id(
        """
        INSERT INTO characters (
            account_id, name, species, `class`, level, xp, current_room_id, last_zone_id,
            str_score, dex_score, con_score, int_score, wis_score, cha_score,
            proficiency_bonus, max_hp, current_hp, armor_class, initiative_mod, speed_walk,
            equipped_weapon, weapon_inventory, item_inventory, bio, description, coin_gp
        ) VALUES (
            :account_id, :name, :species, :char_class, :level, 0, :current_room_id, :last_zone_id,
            :str_score, :dex_score, :con_score, :int_score, :wis_score, :cha_score,
            :proficiency_bonus, :max_hp, :current_hp, :armor_class, :initiative_mod, :speed_walk,
            :equipped_weapon, :weapon_inventory, :item_inventory, :bio, :description, 0
        )
        """,
        account_id=account_id,
        name=name,
        species=sheet["race"],
        char_class=sheet["char_class"],
        level=sheet["level"],
        current_room_id=current_room_id,
        last_zone_id=start_zone,
        str_score=ability_values["str"],
        dex_score=ability_values["dex"],
        con_score=ability_values["con"],
        int_score=ability_values["int"],
        wis_score=ability_values["wis"],
        cha_score=ability_values["cha"],
        proficiency_bonus=sheet["proficiency"],
        max_hp=sheet["max_hp"],
        current_hp=sheet["current_hp"],
        armor_class=sheet["ac"],
        initiative_mod=sheet["ability_mods"]["dex"],
        speed_walk=30,
        equipped_weapon=sheet["equipped_weapon"],
        weapon_inventory=inventory_payload,
        item_inventory=serialize_items([]),
        bio=bio or "",
        description=description or "",
    )
    return character_id


def delete_character(account_id, character_id):
    affected = db_utils.execute(
        "DELETE FROM characters WHERE character_id = :character_id AND account_id = :account_id",
        character_id=character_id,
        account_id=account_id,
    )
    return affected > 0


def update_character_current_hp(character_id, hp):
    db_utils.execute(
        "UPDATE characters SET current_hp = :hp, last_saved_at = CURRENT_TIMESTAMP WHERE character_id = :character_id",
        hp=int(hp),
        character_id=character_id,
    )


def update_character_equipped_weapon(character_id, weapon_key):
    db_utils.execute(
        "UPDATE characters SET equipped_weapon = :weapon_key, last_saved_at = CURRENT_TIMESTAMP WHERE character_id = :character_id",
        weapon_key=weapon_key,
        character_id=character_id,
    )


def update_character_weapon_inventory(character_id, inventory):
    db_utils.execute(
        "UPDATE characters SET weapon_inventory = :inventory, last_saved_at = CURRENT_TIMESTAMP WHERE character_id = :character_id",
        inventory=serialize_inventory(inventory),
        character_id=character_id,
    )


def update_character_gold(character_id, gold):
    db_utils.execute(
        "UPDATE characters SET coin_gp = :gold, last_saved_at = CURRENT_TIMESTAMP WHERE character_id = :character_id",
        gold=int(gold),
        character_id=character_id,
    )


def update_character_xp(character_id, xp):
    db_utils.execute(
        "UPDATE characters SET xp = :xp, last_saved_at = CURRENT_TIMESTAMP WHERE character_id = :character_id",
        xp=int(xp),
        character_id=character_id,
    )


def update_character_items(character_id, items):
    db_utils.execute(
        "UPDATE characters SET item_inventory = :items, last_saved_at = CURRENT_TIMESTAMP WHERE character_id = :character_id",
        items=serialize_items(items),
        character_id=character_id,
    )



# Normalization helpers ----------------------------------------------------

def normalize_character_record(record):
    if not record:
        return None
    normalized = dict(record)
    if "character_id" in normalized:
        normalized.setdefault("id", normalized["character_id"])
    if "species" in normalized:
        normalized.setdefault("race", normalized["species"])
    if "class" in normalized:
        normalized.setdefault("char_class", normalized["class"])
    if "max_hp" in normalized:
        normalized.setdefault("hp", normalized["max_hp"])
    if "coin_gp" in normalized:
        normalized.setdefault("gold", normalized["coin_gp"])
    return normalized
# --- In-memory game state (per container, MVP only) ---
# players[character_name] = {
#     "sid": socket_id,
#     "character_id": int,
#     "account_id": int,
#     "name": str,
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
npcs = {}
npc_lookup_by_id = {}
npc_conversations = {}
npc_conversation_history = {}
_openai_client: Optional[object] = None
_openai_mode: Optional[str] = None


def compute_action_multiplier(initiative):
    """Convert initiative into a small speed boost/penalty."""
    try:
        value = float(initiative)
    except (TypeError, ValueError):
        value = 10.0
    # Clamp initiative influence to keep multipliers within the desired range.
    delta = max(-5.0, min(5.0, value - 10.0))
    # Each 5 points away from 10 shifts the multiplier by ~0.25.
    adjustment = (delta / 5.0) * 0.25
    multiplier = 1.0 + adjustment
    return max(MIN_ACTION_MULTIPLIER, min(MAX_ACTION_MULTIPLIER, multiplier))


def update_player_action_timing(player):
    """Refresh derived initiative and cooldown values for the player."""
    base_initiative = player.get("base_initiative", 10)
    dex_bonus = player.get("ability_mods", {}).get("dex", 0)
    total_initiative = max(1.0, base_initiative + dex_bonus)
    multiplier = compute_action_multiplier(total_initiative)
    player["initiative"] = total_initiative
    player["action_cooldown"] = BASE_ACTION_COOLDOWN / multiplier
    player.setdefault("last_action_ts", 0)


def get_player_action_cooldown_remaining(player):
    cooldown = player.get("action_cooldown", BASE_ACTION_COOLDOWN)
    last_ts = player.get("last_action_ts", 0)
    remaining = cooldown - (time.time() - last_ts)
    return max(0.0, remaining)


def send_action_denied(username, player, remaining):
    payload = {"reason": "cooldown", "remaining": round(remaining, 2)}
    socketio.emit("action_denied", payload, to=player["sid"])
    notify_player(username, f"You must wait {remaining:.1f}s before acting again.")


def check_player_action_gate(username):
    """Server-side guard that enforces the global action cooldown per player."""
    player = players.get(username)
    if not player:
        return False
    recalculate_player_stats(player)
    remaining = get_player_action_cooldown_remaining(player)
    if remaining > 0:
        send_action_denied(username, player, remaining)
        return False
    return True


def mark_player_action(player):
    player["last_action_ts"] = time.time()
room_loot = {}
_mob_counter = 0
_loot_counter = 0


def get_world(zone):
    world = db_utils.get_world(zone)
    if world:
        return world
    fallback = db_utils.get_world(DEFAULT_ZONE)
    if fallback:
        return fallback
    return {"zone_id": DEFAULT_ZONE, "map": [], "width": 0, "height": 0, "start": (0, 0)}


def get_world_dimensions(zone):
    world = get_world(zone)
    return world.get("width", 0), world.get("height", 0)


def get_world_map(zone):
    return get_world(zone).get("map", [])


def get_world_start(zone):
    start = get_world(zone).get("start")
    if isinstance(start, (list, tuple)) and len(start) == 2:
        return tuple(start)
    return (0, 0)


def get_door_id(zone, x, y, direction):
    return DOOR_ENDPOINT_LOOKUP.get((zone, x, y, direction))


def is_door_open(door_id):
    door = DOORS.get(door_id)
    if not door:
        return True
    return door.get("state") == "open"


def format_door_payload(door_id, facing_direction, zone, x, y):
    door = DOORS.get(door_id)
    if not door:
        return None
    other_side = None
    for endpoint in door.get("endpoints", []):
        coords = endpoint["coords"]
        if endpoint["zone"] == zone and coords == (x, y) and endpoint["direction"] == facing_direction:
            continue
        other_room = get_room_info(endpoint["zone"], coords[0], coords[1])
        other_side = {
            "zone": endpoint["zone"],
            "direction": endpoint["direction"],
            "coords": {"x": coords[0], "y": coords[1]},
            "room_name": other_room.get("name") if other_room else None,
        }
        break
    return {
        "id": door_id,
        "name": door["name"],
        "description": door["description"],
        "state": door.get("state", "closed"),
        "is_open": door.get("state") == "open",
        "facing": facing_direction,
        "other_side": other_side,
    }


def get_room_door_payload(zone, x, y):
    seen = set()
    doors_here = []
    for direction in DIRECTION_VECTORS:
        door_id = get_door_id(zone, x, y, direction)
        if not door_id or door_id in seen:
            continue
        seen.add(door_id)
        payload = format_door_payload(door_id, direction, zone, x, y)
        if payload:
            doors_here.append(payload)
    return doors_here


def build_exit_payload(zone, x, y):
    width, height = get_world_dimensions(zone)
    exits = {}
    for direction, (dx, dy) in DIRECTION_VECTORS.items():
        nx, ny = x + dx, y + dy
        in_bounds = 0 <= nx < width and 0 <= ny < height
        reason = None
        door_id = get_door_id(zone, x, y, direction)
        door_payload = format_door_payload(door_id, direction, zone, x, y) if door_id else None
        can_travel = in_bounds
        if not in_bounds:
            reason = "No path in that direction."
            can_travel = False
        elif door_payload and not door_payload["is_open"]:
            reason = f"{door_payload['name']} is closed."
            can_travel = False
        exits[direction] = {
            "available": can_travel,
            "reason": reason,
            "door": door_payload,
            "target": {"zone": zone, "x": nx, "y": ny} if in_bounds else None,
        }
    return exits


def room_name(zone, x, y):
    return f"room_{zone}_{x}_{y}"


def get_room_info(zone, x, y):
    payload = db_utils.get_room_payload(zone, x, y)
    if payload:
        return payload
    return {"name": "Unknown void", "description": "You should not be here."}


def get_players_in_room(zone, x, y):
    return [u for u, p in players.items() if p.get("zone", DEFAULT_ZONE) == zone and p["x"] == x and p["y"] == y]


def random_world_position(zone, exclude=None):
    exclude = set(exclude or [])
    width, height = get_world_dimensions(zone)
    if width == 0 or height == 0:
        return 0, 0
    attempts = 0
    while attempts < 50:
        x = random.randrange(width)
        y = random.randrange(height)
        if (x, y) not in exclude:
            return x, y
        attempts += 1
    return random.randrange(width), random.randrange(height)


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



def spawn_mob(template_key, x=None, y=None, zone=None):
    record = db_utils.get_mob_template(template_key)
    if not record:
        return None
    global _mob_counter
    zone = zone or DEFAULT_ZONE
    if x is None or y is None:
        x, y = random_world_position(zone)
    room_record = db_utils.get_room_by_coords(zone, x, y)
    room_id = room_record.get("room_id") if room_record else None
    _mob_counter += 1
    notes = record.get("notes") or {}
    hp = roll_hit_points_from_notation(record.get("hp_dice"), record.get("hp_average") or 1)
    mob_id = f"{template_key}-{_mob_counter}"
    abilities = {
        "str": record.get("str_score", 10) or 10,
        "dex": record.get("dex_score", 10) or 10,
        "con": record.get("con_score", 10) or 10,
        "int": record.get("int_score", 10) or 10,
        "wis": record.get("wis_score", 10) or 10,
        "cha": record.get("cha_score", 10) or 10,
    }
    damage_info = notes.get("damage") if isinstance(notes, dict) else None
    if damage_info and isinstance(damage_info.get("dice"), list):
        damage_info = {
            **damage_info,
            "dice": tuple(damage_info.get("dice") or (1, 4)),
        }
    loot_table = notes.get("loot") if isinstance(notes, dict) else []
    description = ""
    traits_json = record.get("traits_json")
    if traits_json:
        try:
            traits = json.loads(traits_json)
            if isinstance(traits, list) and traits:
                description = traits[0].get("description", "")
        except (TypeError, json.JSONDecodeError):
            description = str(traits_json)
    mob = {
        "id": mob_id,
        "template": template_key,
        "name": record.get("name", template_key.title()),
        "zone": zone,
        "x": x,
        "y": y,
        "room_id": room_id,
        "ac": record.get("armor_class", 10),
        "hp": hp,
        "max_hp": hp,
        "attack_interval": record.get("attack_interval", 3.0),
        "last_attack_ts": 0,
        "initiative": 10 + (record.get("initiative_mod") or 0),
        "behaviour_type": record.get("aggro_type", "defensive"),
        "xp": record.get("xp_value", 0),
        "description": description,
        "abilities": abilities,
        "gold_range": tuple(notes.get("gold_range", (0, 0))) if isinstance(notes, dict) else (0, 0),
        "loot": list(loot_table),
        "damage": None,
        "attack_bonus": notes.get("attack_bonus", 0) if isinstance(notes, dict) else 0,
        "alive": True,
    }
    if damage_info:
        mob["damage"] = {
            "dice": damage_info.get("dice", (1, 4)),
            "bonus": damage_info.get("bonus", 0),
            "type": damage_info.get("type", "physical"),
        }
    mobs[mob_id] = mob
    db_utils.create_mob_instance_record(template_key, room_id, mob.get("hp"))
    return mob




def spawn_npc_instance(npc_key, spawn_record=None):
    record = db_utils.get_npc_template(npc_key)
    if not record:
        return None
    existing_id = npcs.get(npc_key)
    if existing_id:
        existing = mobs.get(existing_id)
        if existing and existing.get("alive"):
            return existing
    if spawn_record is None:
        spawn_record = db_utils.get_npc_spawn(npc_key)
    if not spawn_record:
        return None
    coords = (spawn_record.get("x_coord", 0), spawn_record.get("y_coord", 0))
    zone = spawn_record.get("zone_id", DEFAULT_ZONE)
    traits_json = record.get("traits_json")
    traits = {}
    if traits_json:
        try:
            traits = json.loads(traits_json)
        except (TypeError, json.JSONDecodeError):
            traits = {}
    template_key = traits.get("mob_template") or f"npc_{npc_key}"
    mob = spawn_mob(template_key, coords[0], coords[1], zone)
    if not mob:
        return None
    mob["is_npc"] = True
    mob["npc_key"] = npc_key
    mob["npc_character_description"] = traits.get("character_description", "")
    mob["npc_bio"] = traits.get("bio", "")
    mob["npc_personality"] = traits.get("personality", "")
    mob["npc_fixed_memory"] = list(traits.get("fixed_memory", []))
    mob["npc_facts"] = list(traits.get("facts", []))
    mob["npc_secret_fact"] = traits.get("secret_fact")
    mob["npc_aliases"] = list(traits.get("aliases", []))
    npcs[npc_key] = mob["id"]
    npc_lookup_by_id[mob["id"]] = npc_key
    return mob


def spawn_initial_npcs():
    npcs.clear()
    npc_lookup_by_id.clear()
    npc_conversation_history.clear()
    spawn_map = {}
    for zone in db_utils.list_zone_ids():
        for record in db_utils.get_room_npc_spawns(zone):
            spawn_map[record["npc_template_id"]] = record
    for npc_key, spawn in spawn_map.items():
        npc_conversations.setdefault(npc_key, {})
        npc_conversation_history.setdefault(npc_key, [])
        mob = spawn_npc_instance(npc_key, spawn)
        if not mob:
            continue
def spawn_initial_mobs():
    mobs.clear()
    for zone in db_utils.list_zone_ids():
        world = get_world(zone)
        tile_map = world.get("map", [])
        for y, row in enumerate(tile_map):
            for x, tile in enumerate(row):
                if not tile:
                    continue
                for template_key in tile.get("mobs", []):
                    spawn_mob(template_key, x, y, zone)
    spawn_initial_npcs()


def get_mobs_in_room(zone, x, y):
    return [
        mob
        for mob in mobs.values()
        if mob["alive"] and mob.get("zone", DEFAULT_ZONE) == zone and mob["x"] == x and mob["y"] == y
    ]


def get_npcs_in_room(zone, x, y):
    return [mob for mob in get_mobs_in_room(zone, x, y) if mob.get("is_npc")]


def format_mob_payload(mob):
    return {
        "id": mob["id"],
        "name": mob["name"],
        "hp": mob["hp"],
        "max_hp": mob["max_hp"],
        "ac": mob["ac"],
        "xp": mob.get("xp", 0),
        "description": mob.get("description", ""),
        "behaviour": mob.get("behaviour_type", "defensive"),
        "is_npc": mob.get("is_npc", False),
    }


def format_npc_payload(mob, viewer=None):
    npc_key = mob.get("npc_key")
    counts = npc_conversations.get(npc_key, {}) if npc_key else {}
    handle = npc_key or mob.get("id")
    return {
        "id": mob["id"],
        "name": mob.get("name"),
        "ac": mob.get("ac"),
        "hp": mob.get("hp"),
        "max_hp": mob.get("max_hp"),
        "description": mob.get("description", ""),
        "bio": mob.get("npc_bio", ""),
        "handle": handle,
        "conversation_count": counts.get(viewer, 0) if viewer else 0,
    }


def ensure_openai_client():
    global _openai_client, _openai_mode
    if _openai_mode == "disabled":
        return None, "disabled"
    if _openai_client is not None and _openai_mode:
        return _openai_client, _openai_mode
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        _openai_mode = "disabled"
        return None, "disabled"
    if OpenAI is not None:
        try:
            _openai_client = OpenAI(api_key=api_key)
            _openai_mode = "client"
            return _openai_client, _openai_mode
        except Exception:
            _openai_client = None
            _openai_mode = None
    if openai_module is not None:
        openai_module.api_key = api_key
        _openai_client = openai_module
        _openai_mode = "legacy"
        return _openai_client, _openai_mode
    _openai_mode = "disabled"
    return None, "disabled"


def build_npc_knowledge(mob, conversation_count):
    knowledge = list(mob.get("npc_fixed_memory", []))
    knowledge.extend(mob.get("npc_facts", []))
    secret = mob.get("npc_secret_fact")
    if secret and conversation_count > NPC_SECRET_THRESHOLD:
        knowledge.append(secret)
    return knowledge


def generate_npc_response(mob, player, message, conversation_count):
    client, mode = ensure_openai_client()
    if not client or mode == "disabled":
        return None, "AI conversations are not configured. Set OPENAI_API_KEY on the server."

    npc_name = mob.get("name", "The villager")
    persona = mob.get("npc_personality", "friendly")
    character_description = mob.get("npc_character_description", "")
    bio = mob.get("npc_bio", "a notable resident of Dawnfell Village")
    npc_key = mob.get("npc_key")
    knowledge = build_npc_knowledge(mob, conversation_count)
    knowledge_text = "\n".join(f"- {fact}" for fact in knowledge) if knowledge else "- (No stored facts.)"
    player_name = player.get("name") or "An adventurer"
    race = player.get("race") or ""
    char_class = player.get("char_class") or ""
    level = player.get("level", 1)
    lineage = " ".join(part for part in [race, char_class] if part)
    summary_bits = [player_name]
    if lineage:
        summary_bits.append(lineage)
    summary_bits.append(f"Level {level}")
    player_summary = " • ".join(summary_bits)
    player_bio = player.get("bio") or "No personal biography provided."
    player_description = player.get("description") or ""
    conversation_line = f"You have spoken with this adventurer {conversation_count} times."
    history_entries = npc_conversation_history.get(npc_key, [])[-15:] if npc_key else []
    history_lines = [f"{entry.get('speaker', 'Someone')}: {entry.get('text', '')}" for entry in history_entries]
    history_text = "\n".join(history_lines) if history_lines else "(No recent conversation. Begin warmly.)"
    instructions = (
        f"You are {npc_name}. {character_description} {bio}. Speak in a {persona} tone. "
        "Stay in character and use the knowledge provided. Offer guidance about Dawnfell Village and warp stones when it fits the conversation. "
        "If a question exceeds your knowledge, admit uncertainty."
    )
    player_context_lines = [
        f"Adventurer summary: {player_summary}.",
        f"Adventurer bio: {player_bio}",
    ]
    if player_description:
        player_context_lines.append(f"Adventurer appearance: {player_description}")
    player_context = "\n".join(player_context_lines)
    messages = [
        {"role": "system", "content": instructions},
        {"role": "system", "content": conversation_line},
        {"role": "system", "content": "Knowledge available to you:\n" + knowledge_text},
        {"role": "system", "content": "Recent short-term memory (most recent last):\n" + history_text},
        {"role": "system", "content": "Respond in 1-3 short paragraphs."},
        {"role": "user", "content": f"{player_context}\nPlayer says: {message}"},
    ]
    try:
        if mode == "client" and hasattr(client, "chat"):
            response = client.chat.completions.create(
                model=NPC_MODEL_NAME,
                messages=messages,
                temperature=0.6,
                max_tokens=220,
            )
            reply = response.choices[0].message.content.strip()
        else:
            response = client.ChatCompletion.create(
                model=NPC_MODEL_NAME,
                messages=messages,
                temperature=0.6,
                max_tokens=220,
            )
            reply = response["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # pragma: no cover - network dependency
        return None, str(exc)
    return reply, None


def find_mob_in_room(identifier, zone, x, y):
    if not identifier:
        return None
    lookup = identifier.strip().lower()
    for mob in get_mobs_in_room(zone, x, y):
        if mob["id"].lower() == lookup or mob["name"].lower() == lookup:
            return mob
    return None


def find_npc_in_room(identifier, zone, x, y):
    if not identifier:
        return None
    lookup = identifier.strip().lower()
    for npc in get_npcs_in_room(zone, x, y):
        if npc["id"].lower() == lookup:
            return npc
        key = npc.get("npc_key")
        if key and key.lower() == lookup:
            return npc
        name = npc.get("name")
        if name and name.lower() == lookup:
            return npc
        if key and key.replace("_", " ").lower() == lookup:
            return npc
        for alias in npc.get("npc_aliases", []):
            if alias.lower() == lookup:
                return npc
    return None


def parse_talk_target(player, raw_args):
    if not player:
        return None, None
    text = (raw_args or "").strip()
    if not text:
        return None, None
    zone = player.get("zone", DEFAULT_ZONE)
    x, y = player["x"], player["y"]
    if text[0] in ('"', "'"):
        quote = text[0]
        closing = text.find(quote, 1)
        if closing != -1:
            identifier = text[1:closing].strip()
            remainder = text[closing + 1 :].strip()
            npc = find_npc_in_room(identifier, zone, x, y)
            if npc and remainder:
                return npc, remainder
    parts = text.split(None, 1)
    identifier = parts[0]
    remainder = parts[1].strip() if len(parts) > 1 else ""
    npc = find_npc_in_room(identifier, zone, x, y)
    if npc and remainder:
        return npc, remainder
    for candidate in get_npcs_in_room(zone, x, y):
        lowered = text.lower()
        aliases = [candidate.get("name", ""), candidate.get("npc_key", "")]
        aliases.extend(candidate.get("npc_aliases", []))
        for alias in aliases:
            alias = (alias or "").strip()
            if not alias:
                continue
            alias_lower = alias.lower()
            if lowered.startswith(alias_lower):
                remainder = text[len(alias) :].strip()
                if remainder:
                    return candidate, remainder
            compact = alias_lower.replace(" ", "_")
            if compact != alias_lower and lowered.startswith(compact):
                remainder = text[len(compact) :].strip()
                if remainder:
                    return candidate, remainder
    return None, None


def handle_talk_command(username, raw_args):
    player = players.get(username)
    if not player:
        return False, "You are not in the game."
    npc, message = parse_talk_target(player, raw_args)
    if not npc or not message:
        return False, "Usage: /talk <npc> <message>"
    if not npc.get("alive") or npc.get("hp", 0) <= 0:
        return False, f"{npc.get('name', 'That NPC')} cannot respond right now."
    npc_key = npc.get("npc_key")
    if not npc_key:
        return False, "That creature does not respond to conversation."
    counts = npc_conversations.setdefault(npc_key, {}) if npc_key else {}
    previous = counts.get(username, 0)
    counts[username] = previous + 1
    conversation_total = counts[username]
    reply, error = generate_npc_response(npc, player, message, conversation_total)
    if error:
        counts[username] = previous
        return False, f"{npc.get('name', 'The NPC')} hesitates: {error}"

    zone = player.get("zone", DEFAULT_ZONE)
    room = room_name(zone, player["x"], player["y"])
    socketio.emit(
        "chat_message",
        {"from": username, "text": f"(to {npc['name']}) {message}"},
        room=room,
    )
    socketio.emit(
        "chat_message",
        {"from": npc["name"], "text": reply},
        room=room,
    )
    history = npc_conversation_history.setdefault(npc_key, [])
    history.append({"speaker": username, "text": message, "role": "player"})
    history.append({"speaker": npc.get("name", "The NPC"), "text": reply, "role": "npc"})
    if len(history) > 15:
        npc_conversation_history[npc_key] = history[-15:]
    send_room_state(username)
    return True, reply


def stop_mob_combat(mob):
    mob["in_combat"] = False
    mob["combat_targets"] = set()


def mob_combat_loop(mob_id):
    """Background loop that lets mobs retaliate on their own timer."""
    while True:
        socketio.sleep(0.25)
        mob = mobs.get(mob_id)
        if not mob or not mob.get("alive"):
            break
        if not mob.get("in_combat"):
            break

        targets = mob.setdefault("combat_targets", set())
        engaged = []
        for username in list(targets):
            player = players.get(username)
            if not player or player["hp"] <= 0:
                targets.discard(username)
                continue
            if (
                player.get("zone", DEFAULT_ZONE) != mob.get("zone", DEFAULT_ZONE)
                or (player["x"], player["y"]) != (mob["x"], mob["y"])
            ):
                targets.discard(username)
                continue
            engaged.append((username, player))

        if not engaged:
            stop_mob_combat(mob)
            break

        now = time.time()
        interval = mob.get("attack_interval", 3.0)
        if now - mob.get("last_attack_ts", 0) < interval:
            continue

        username, target = random.choice(engaged)
        damage_info = mob.get("damage", {})
        damage = roll_dice(damage_info.get("dice")) + damage_info.get("bonus", 0)
        damage = max(1, damage)
        mob["last_attack_ts"] = now

        target["hp"] = clamp_hp(target["hp"] - damage, target["max_hp"])
        update_character_current_hp(target["character_id"], target["hp"])
        room = room_name(mob.get("zone", DEFAULT_ZONE), mob["x"], mob["y"])
        dmg_type = damage_info.get("type")
        suffix = f" {dmg_type} damage" if dmg_type else " damage"
        socketio.emit(
            "system_message",
            {"text": f"{mob['name']} strikes {username} for {damage}{suffix}!"},
            room=room,
        )
        send_room_state(username)
        broadcast_room_state(mob.get("zone", DEFAULT_ZONE), mob["x"], mob["y"])

        if target["hp"] == 0:
            socketio.emit(
                "system_message",
                {"text": f"{username} is felled by {mob['name']}!"},
                room=room,
            )
            targets.discard(username)
            respawn_player(username)

    mob = mobs.get(mob_id)
    if mob:
        mob["combat_task"] = None


def engage_mob_with_player(mob, username, auto=False):
    """Ensure the mob is locked in combat with a player, starting timers if needed."""
    if not mob or not mob.get("alive"):
        return
    player = players.get(username)
    if not player or player["hp"] <= 0:
        return
    if (
        player.get("zone", DEFAULT_ZONE) != mob.get("zone", DEFAULT_ZONE)
        or (player["x"], player["y"]) != (mob["x"], mob["y"])
    ):
        return

    targets = mob.setdefault("combat_targets", set())
    if username not in targets:
        targets.add(username)
        room = room_name(mob.get("zone", DEFAULT_ZONE), mob["x"], mob["y"])
        if auto:
            socketio.emit(
                "system_message",
                {"text": f"{mob['name']} lunges at {username}!"},
                room=room,
            )
        else:
            socketio.emit(
                "system_message",
                {"text": f"{mob['name']} turns to fight {username}!"},
                room=room,
            )

    if not mob.get("in_combat"):
        mob["in_combat"] = True
        mob["last_attack_ts"] = time.time()
        if not mob.get("combat_task"):
            mob["combat_task"] = socketio.start_background_task(mob_combat_loop, mob["id"])
    elif not mob.get("combat_task"):
        mob["combat_task"] = socketio.start_background_task(mob_combat_loop, mob["id"])


def disengage_player_from_room_mobs(username, x, y):
    player = players.get(username)
    zone = player.get("zone", DEFAULT_ZONE) if player else DEFAULT_ZONE
    for mob in get_mobs_in_room(zone, x, y):
        targets = mob.setdefault("combat_targets", set())
        if username in targets:
            targets.discard(username)
            if not targets:
                stop_mob_combat(mob)


def trigger_aggressive_mobs_for_player(username, x, y):
    """Aggressive mobs attack as soon as a fresh player enters their room."""
    player = players.get(username)
    zone = player.get("zone", DEFAULT_ZONE) if player else DEFAULT_ZONE
    for mob in get_mobs_in_room(zone, x, y):
        if mob.get("behaviour_type") == "aggressive" and mob.get("alive"):
            engage_mob_with_player(mob, username, auto=True)


def get_loot_in_room(zone, x, y):
    return list(room_loot.get((zone, x, y), []))


def add_loot_to_room(zone, x, y, loot_entry):
    room_loot.setdefault((zone, x, y), []).append(loot_entry)


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
    item = db_utils.get_item_template(item_key) or _weapon_template_map().get(item_key)
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
    update_player_action_timing(player)


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
    start_x, start_y = get_world_start(DEFAULT_ZONE)
    state = {
        "sid": sid,
        "zone": DEFAULT_ZONE,
        "x": start_x,
        "y": start_y,
        "character_id": user_record.get("id"),
        "account_id": user_record.get("account_id"),
        "name": user_record.get("name"),
        "bio": user_record.get("bio") or "",
        "description": user_record.get("description") or "",
    }
    state.update(derived)
    state["inventory"] = list(state.get("inventory", []))
    state["items"] = list(state.get("items", []))
    state["gold"] = int(derived.get("gold", 0))
    state["xp"] = int(derived.get("xp", 0))
    state["hp"] = clamp_hp(user_record.get("current_hp"), derived["max_hp"])
    state["base_ability_mods"] = dict(state.get("ability_mods", {}))
    state["base_ac"] = state.get("ac", 10)
    state["base_initiative"] = 10
    state["initiative"] = 10
    state["action_cooldown"] = BASE_ACTION_COOLDOWN
    state["last_action_ts"] = 0
    state["active_effects"] = []
    state["cooldowns"] = {}
    state["spells"] = get_spells_for_class(state.get("char_class"))
    state["attack_roll_bonus_dice"] = []
    state["damage_bonus"] = 0
    state["searched_rooms"] = set()
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
    update_character_equipped_weapon(player["character_id"], weapon_key)
    send_room_state(username)

    zone = player.get("zone", DEFAULT_ZONE)
    room = room_name(zone, player["x"], player["y"])
    message = f"{username} equips {player['weapon']['name']}."
    socketio.emit("system_message", {"text": message}, room=room)
    return True, message


def send_room_state(username):
    player = players.get(username)
    if not player:
        return
    recalculate_player_stats(player)
    x, y = player["x"], player["y"]
    zone = player.get("zone", DEFAULT_ZONE)
    room = get_room_info(zone, x, y)
    occupants = get_players_in_room(zone, x, y)
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
    mobs_here = [format_mob_payload(mob) for mob in get_mobs_in_room(zone, x, y)]
    npcs_here = [format_npc_payload(npc, viewer=username) for npc in get_npcs_in_room(zone, x, y)]
    loot_here = format_loot_payload(get_loot_in_room(zone, x, y))
    doors_here = get_room_door_payload(zone, x, y)
    exits = build_exit_payload(zone, x, y)
    warp_info = None
    if room.get("travel_to"):
        warp_info = {
            "label": room.get("warp_label", "Warp Stone"),
            "description": room.get("warp_description")
            or "A rune-carved warp stone hums softly, awaiting activation.",
        }
    payload = {
        "zone": zone,
        "world_name": get_world(zone)["name"],
        "x": x,
        "y": y,
        "room_name": room["name"],
        "description": room["description"],
        "players": occupants,
        "mobs": mobs_here,
        "npcs": npcs_here,
        "loot": loot_here,
        "doors": doors_here,
        "exits": exits,
        "warp_stone": warp_info,
        "character": {
            "id": player.get("character_id"),
            "name": player.get("name"),
            "bio": player.get("bio", ""),
            "description": player.get("description", ""),
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


def broadcast_room_state(zone, x, y):
    for occupant in get_players_in_room(zone, x, y):
        send_room_state(occupant)


def describe_adjacent_players(player):
    directions = [
        ("north", (0, -1)),
        ("south", (0, 1)),
        ("west", (-1, 0)),
        ("east", (1, 0)),
    ]
    lines = []
    zone = player.get("zone", DEFAULT_ZONE)
    width, height = get_world_dimensions(zone)
    for label, (dx, dy) in directions:
        nx, ny = player["x"] + dx, player["y"] + dy
        if not (0 <= nx < width and 0 <= ny < height):
            continue
        occupants = get_players_in_room(zone, nx, ny)
        room = get_room_info(zone, nx, ny)
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
    if not check_player_action_gate(username):
        return False, None
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
        if target_requirement != "none":
            if player.get("zone", DEFAULT_ZONE) != target_player.get("zone", DEFAULT_ZONE):
                return False, f"{target_name} is not in the same room."
            if (player["x"], player["y"]) != (target_player["x"], target_player["y"]):
                return False, f"{target_name} is not in the same room."
        recalculate_player_stats(target_player)

    success, feedback = execute_spell(username, player, spell_key, spell, target_player, target_name)
    if not success:
        return False, feedback

    mark_player_action(player)
    cooldown = spell.get("cooldown", 0)
    if cooldown:
        player.setdefault("cooldowns", {})[spell_key] = time.time() + cooldown

    send_room_state(username)
    if target_player and target_name and target_name != username:
        send_room_state(target_name)

    return True, feedback


def execute_spell(caster_name, caster, spell_key, spell, target_player, target_name):
    zone = caster.get("zone", DEFAULT_ZONE)
    room = room_name(zone, caster["x"], caster["y"])
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
        update_character_current_hp(target_player["character_id"], target_player["hp"])
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
        update_character_current_hp(target["character_id"], target["hp"])
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

    zone = player.get("zone", DEFAULT_ZONE)
    old_room = room_name(zone, player["x"], player["y"])
    disengage_player_from_room_mobs(username, player["x"], player["y"])
    leave_room(old_room, sid=player["sid"])
    socketio.emit(
        "system_message",
        {"text": f"{username} collapses and vanishes in a swirl of grey mist."},
        room=old_room,
    )

    player["zone"] = DEFAULT_ZONE
    start_x, start_y = get_world_start(DEFAULT_ZONE)
    player["x"], player["y"] = start_x, start_y
    player["hp"] = player["max_hp"]
    update_character_current_hp(player["character_id"], player["hp"])
    player["active_effects"] = []
    recalculate_player_stats(player)

    new_room = room_name(player["zone"], player["x"], player["y"])
    join_room(new_room, sid=player["sid"])
    socketio.emit(
        "system_message",
        {"text": f"{username} staggers back into the area, looking dazed."},
        room=new_room,
        include_self=False,
    )
    notify_player(username, "You have been defeated and return to the village square.")
    send_room_state(username)
    trigger_aggressive_mobs_for_player(username, player["x"], player["y"])


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
    if cmd in ("search", "investigate"):
        success, message = perform_search_action(username)
        if not success and message:
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
    if cmd == "talk":
        remainder = command_text[len(parts[0]) :].strip()
        success, message = handle_talk_command(username, remainder)
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
        update_character_xp(player["character_id"], player["xp"])
        notify_player(username, f"You gain {amount} XP.")
    else:
        record = get_character_by_name(username)
        if record is None:
            return
        new_total = (record.get("xp") or 0) + amount
        update_character_xp(record["character_id"], new_total)


def collect_item_for_player(player, username, item_key):
    if not item_key:
        return None
    item_template = db_utils.get_item_template(item_key)
    if item_template:
        items = player.setdefault("items", [])
        items.append(item_key)
        update_character_items(player["character_id"], items)
        return item_template.get("name", item_key.replace("_", " ").title())
    weapon_template = _weapon_template_map().get(item_key)
    if weapon_template:
        inventory = player.setdefault("inventory", [])
        if item_key not in inventory:
            inventory.append(item_key)
            update_character_weapon_inventory(player["character_id"], inventory)
        return weapon_template.get("name", item_key.replace("_", " ").title())
    items = player.setdefault("items", [])
    items.append(item_key)
    update_character_items(player["character_id"], items)
    return item_key.replace("_", " ").title()


def handle_mob_defeat(mob, killer_name=None):
    if not mob or not mob.get("alive"):
        return
    mob["alive"] = False
    stop_mob_combat(mob)
    x, y = mob["x"], mob["y"]
    zone = mob.get("zone", DEFAULT_ZONE)
    room = room_name(zone, x, y)
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
            add_loot_to_room(zone, x, y, gold_entry)
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
            add_loot_to_room(zone, x, y, loot_entry)
            drops.append(loot_entry)
    if drops:
        names = ", ".join(drop["name"] for drop in drops)
        socketio.emit(
            "system_message",
            {"text": f"Treasure spills onto the ground: {names}."},
            room=room,
        )
    mobs.pop(mob["id"], None)
    if mob.get("is_npc"):
        npc_key = npc_lookup_by_id.pop(mob["id"], None)
        if npc_key:
            npcs.pop(npc_key, None)
            socketio.start_background_task(respawn_npc_after_delay, npc_key)
    broadcast_room_state(zone, x, y)


def resolve_attack_against_mob(attacker_name, attacker, mob):
    engage_mob_with_player(mob, attacker_name)
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
    zone = attacker.get("zone", DEFAULT_ZONE)
    room = room_name(zone, attacker["x"], attacker["y"])
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
    zone = player.get("zone", DEFAULT_ZONE)
    entries = room_loot.get((zone, x, y), [])
    match = None
    for entry in entries:
        if entry["id"].lower() == loot_identifier:
            match = entry
            break
    if not match:
        return False, "No such loot lies here."
    room_loot[(zone, x, y)].remove(match)
    if not room_loot[(zone, x, y)]:
        room_loot.pop((zone, x, y), None)
    room = room_name(zone, x, y)
    if match.get("type") == "gold":
        amount = int(match.get("amount") or 0)
        player["gold"] = player.get("gold", 0) + amount
        update_character_gold(player["character_id"], player["gold"])
        message = f"{username} scoops up {amount} gold coins."
    else:
        item_key = match.get("item_key")
        template = db_utils.get_item_template(item_key)
        weapon_template = _weapon_template_map().get(item_key)
        if template:
            items = player.setdefault("items", [])
            items.append(item_key)
            update_character_items(player["character_id"], items)
            item_name = template.get("name", match.get("name", item_key.replace("_", " ").title()))
            message = f"{username} picks up {item_name}."
        elif weapon_template:
            inventory = player.setdefault("inventory", [])
            if item_key not in inventory:
                inventory.append(item_key)
                update_character_weapon_inventory(player["character_id"], inventory)
            item_name = weapon_template.get("name", match.get("name", item_key.replace("_", " ").title()))
            message = f"{username} claims {item_name}."
        else:
            items = player.setdefault("items", [])
            items.append(item_key)
            update_character_items(player["character_id"], items)
            item_name = match.get("name", "an item")
            message = f"{username} picks up {item_name}."
    socketio.emit("system_message", {"text": message}, room=room)
    broadcast_room_state(zone, x, y)
    return True, message


def perform_search_action(username):
    player = players.get(username)
    if not player:
        return False, "You are not in the game."
    if not check_player_action_gate(username):
        return False, None

    zone = player.get("zone", DEFAULT_ZONE)
    x, y = player["x"], player["y"]
    room = get_room_info(zone, x, y)
    search_meta = room.get("search")

    mark_player_action(player)
    searched_rooms = player.setdefault("searched_rooms", set())
    location_key = (zone, x, y)

    if not search_meta:
        notify_player(username, "You search around but find nothing unusual.")
        return True, None

    ability_key = (search_meta.get("ability") or "wis").lower()
    ability_mod = player.get("ability_mods", {}).get(ability_key, 0)
    try:
        dc = int(search_meta.get("dc", 10))
    except (TypeError, ValueError):
        dc = 10
    roll = random.randint(1, 20)
    total = roll + ability_mod
    detail = f" (Roll {total} vs DC {dc})"

    if total >= dc:
        success_text = search_meta.get("success_text") or "You uncover something hidden."
        notify_player(username, success_text + detail)
        already_cleared = location_key in searched_rooms
        if already_cleared:
            loot_keys = search_meta.get("loot") or []
            if loot_keys:
                notify_player(username, "You have already recovered the valuables hidden here.")
            return True, None
        searched_rooms.add(location_key)
        loot_keys = search_meta.get("loot") or []
        if loot_keys:
            awarded = []
            for item_key in loot_keys:
                item_name = collect_item_for_player(player, username, item_key)
                if item_name:
                    awarded.append(item_name)
            if awarded:
                notify_player(username, "You obtain " + ", ".join(awarded) + ".")
        return True, None

    failure_text = search_meta.get("failure_text") or "You search around but find nothing unusual."
    notify_player(username, failure_text + detail)
    return True, None


def resolve_attack(attacker_name, target_name):
    attacker = players.get(attacker_name)
    if not attacker:
        return
    if not check_player_action_gate(attacker_name):
        return
    if not target_name:
        notify_player(attacker_name, "Choose a target to attack.")
        return

    target_name = target_name.strip()
    if attacker_name == target_name:
        notify_player(attacker_name, "You cannot attack yourself.")
        return

    target = players.get(target_name)
    attacker_zone = attacker.get("zone", DEFAULT_ZONE)
    if not target:
        mob = find_mob_in_room(target_name, attacker_zone, attacker["x"], attacker["y"])
        if mob:
            mark_player_action(attacker)
            resolve_attack_against_mob(attacker_name, attacker, mob)
            return
        notify_player(attacker_name, f"{target_name} is nowhere to be found.")
        return

    if attacker_zone != target.get("zone", DEFAULT_ZONE) or attacker["x"] != target["x"] or attacker["y"] != target["y"]:
        notify_player(attacker_name, f"{target_name} is not in the same room.")
        return

    recalculate_player_stats(attacker)
    recalculate_player_stats(target)
    mark_player_action(attacker)

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
    room = room_name(attacker_zone, attacker["x"], attacker["y"])

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
    update_character_current_hp(target["character_id"], target["hp"])

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
            if get_account(username):
                flash("Username already taken.")
                return redirect(url_for("login"))
            create_account(username, password)
            flash("Account created. You can now log in.")
            return redirect(url_for("login"))
        elif action == "login":
            account = get_account(username)
            if not account or not check_password_hash(account["password_hash"], password):
                flash("Invalid username or password.")
                return redirect(url_for("login"))
            session.clear()
            session["account_id"] = account["account_id"]
            session["account_username"] = account["username"]
            return redirect(url_for("character_select"))

        flash("Invalid action.")
        return redirect(url_for("login"))

    if session.get("account_id"):
        return redirect(url_for("character_select"))
    return render_template("login.html")


@app.route("/characters")
def character_select():
    if "account_id" not in session:
        return redirect(url_for("login"))
    account_id = session["account_id"]
    characters = get_account_characters(account_id)
    return render_template(
        "characters.html",
        account_username=session.get("account_username"),
        characters=characters,
        max_characters=MAX_CHARACTERS_PER_ACCOUNT,
    )


@app.route("/characters/new", methods=["GET", "POST"])
def new_character():
    if "account_id" not in session:
        return redirect(url_for("login"))
    account_id = session["account_id"]
    character_count = count_account_characters(account_id)
    if character_count >= MAX_CHARACTERS_PER_ACCOUNT and request.method == "GET":
        flash("You already have the maximum number of characters.")
        return redirect(url_for("character_select"))

    rolls = session.get("rolled_scores")
    if not rolls:
        rolls = generate_base_scores()
    rolls = {ability: int((rolls or {}).get(ability, 10)) for ability in ABILITY_KEYS}
    session["rolled_scores"] = rolls

    if request.method == "POST":
        action = request.form.get("action")
        if action == "roll":
            session["rolled_scores"] = generate_base_scores()
            return redirect(url_for("new_character"))
        elif action == "create":
            if count_account_characters(account_id) >= MAX_CHARACTERS_PER_ACCOUNT:
                flash("You already have the maximum number of characters.")
                return redirect(url_for("character_select"))
            name = request.form.get("name", "").strip()
            race_choice = request.form.get("race")
            class_choice = request.form.get("char_class")
            bio = (request.form.get("bio") or "").strip()
            description = (request.form.get("description") or "").strip()
            if not name:
                flash("Character name is required.")
                return redirect(url_for("new_character"))
            if len(name) > 40:
                flash("Character names must be 40 characters or fewer.")
                return redirect(url_for("new_character"))
            if get_character_by_name(name):
                flash("Character name already taken.")
                return redirect(url_for("new_character"))
            race_choice = normalize_choice(race_choice, RACES, None)
            class_choice = normalize_choice(class_choice, CLASSES, None)
            if not race_choice or not class_choice:
                flash("Select a valid race and class.")
                return redirect(url_for("new_character"))
            ability_scores = {}
            try:
                for ability in ABILITY_KEYS:
                    raw = request.form.get(f"ability_{ability}")
                    if raw is None or raw.strip() == "":
                        raw = rolls.get(ability)
                    value = int(raw)
                    ability_scores[ability] = max(1, min(value, 30))
            except (TypeError, ValueError):
                flash("Ability scores must be numbers.")
                return redirect(url_for("new_character"))
            if len(bio) > 500 or len(description) > 1000:
                flash("Bio or description is too long.")
                return redirect(url_for("new_character"))
            create_character(account_id, name, race_choice, class_choice, ability_scores, bio, description)
            session.pop("rolled_scores", None)
            flash(f"{name} has been created.")
            return redirect(url_for("character_select"))

    return render_template(
        "new_character.html",
        account_username=session.get("account_username"),
        rolls=rolls,
        race_options=RACE_OPTIONS,
        class_options=CLASS_OPTIONS,
        ability_keys=ABILITY_KEYS,
        max_characters=MAX_CHARACTERS_PER_ACCOUNT,
    )


@app.route("/characters/play/<int:character_id>", methods=["POST"])
def play_character(character_id):
    if "account_id" not in session:
        return redirect(url_for("login"))
    record = get_character_by_id(character_id)
    if not record or record["account_id"] != session["account_id"]:
        flash("Character not found.")
        return redirect(url_for("character_select"))
    session["character_id"] = record["character_id"]
    session["character_name"] = record["name"]
    session.pop("rolled_scores", None)
    existing = players.get(record["name"])
    if existing:
        update_character_current_hp(existing["character_id"], existing["hp"])
        players.pop(record["name"], None)
    return redirect(url_for("game"))


@app.route("/characters/delete/<int:character_id>", methods=["POST"])
def delete_character_route(character_id):
    if "account_id" not in session:
        return redirect(url_for("login"))
    record = get_character_by_id(character_id)
    if not record or record["account_id"] != session["account_id"]:
        flash("Character not found.")
        return redirect(url_for("character_select"))
    players.pop(record["name"], None)
    if session.get("character_id") == character_id:
        session.pop("character_id", None)
        session.pop("character_name", None)
    if delete_character(session["account_id"], character_id):
        flash(f"{record['name']} was deleted.")
    else:
        flash("Unable to delete character.")
    return redirect(url_for("character_select"))


@app.route("/game")
def game():
    if "account_id" not in session:
        return redirect(url_for("login"))
    if "character_id" not in session:
        return redirect(url_for("character_select"))
    return render_template(
        "game.html",
        account_username=session.get("account_username"),
        character_name=session.get("character_name"),
    )


@app.route("/logout")
def logout():
    character_name = session.get("character_name")
    if character_name and character_name in players:
        update_character_current_hp(players[character_name]["character_id"], players[character_name]["hp"])
        players.pop(character_name, None)
    session.clear()
    return redirect(url_for("login"))


# --- Socket.IO events ---

@socketio.on("connect")
def on_connect():
    if "account_id" not in session or "character_id" not in session or "character_name" not in session:
        disconnect()
        return
    emit("connected", {"message": "Connected to game server."})


@socketio.on("join_game")
def on_join_game():
    account_id = session.get("account_id")
    character_id = session.get("character_id")
    character_name = session.get("character_name")
    if not account_id or not character_id or not character_name:
        emit("system_message", {"text": "You are not logged in. Please reconnect."})
        disconnect()
        return

    record = get_character_by_id(character_id)
    if not record or record.get("account_id") != account_id or record.get("name") != character_name:
        emit("system_message", {"text": "Unable to load your character. Please log in again."})
        disconnect()
        return

    if character_name not in players:
        state = build_player_state(record, request.sid)
    else:
        existing = players[character_name]
        preserved_zone = existing.get("zone", DEFAULT_ZONE)
        start_x, start_y = get_world_start(preserved_zone)
        preserved_position = (existing.get("x", start_x), existing.get("y", start_y))
        preserved_hp = clamp_hp(existing.get("hp"), existing.get("max_hp", 1))
        preserved_effects = list(existing.get("active_effects", []))
        preserved_cooldowns = dict(existing.get("cooldowns", {}))
        state = build_player_state(record, request.sid)
        state["zone"] = preserved_zone
        state["x"], state["y"] = preserved_position
        state["hp"] = preserved_hp
        state["active_effects"] = preserved_effects
        state["cooldowns"] = preserved_cooldowns
        state["last_action_ts"] = existing.get("last_action_ts", 0)
        state["searched_rooms"] = set(existing.get("searched_rooms", set()))
        recalculate_player_stats(state)

    state["character_id"] = record["character_id"]
    state["account_id"] = account_id
    state["name"] = record["name"]
    players[character_name] = state

    x = state["x"]
    y = state["y"]
    zone = state.get("zone", DEFAULT_ZONE)
    rname = room_name(zone, x, y)

    join_room(rname)

    emit("system_message", {"text": f"{character_name} has entered the room."}, room=rname, include_self=False)

    send_room_state(character_name)
    trigger_aggressive_mobs_for_player(character_name, x, y)


def handle_travel_portal(username):
    player = players.get(username)
    if not player:
        return False
    zone = player.get("zone", DEFAULT_ZONE)
    x, y = player["x"], player["y"]
    room = get_room_info(zone, x, y)
    travel = room.get("travel_to")
    if not travel:
        return False
    target_zone = travel.get("zone")
    if not target_zone:
        return False
    target_world = get_world(target_zone)
    if not target_world or not target_world.get("map"):
        return False
    destination = travel.get("start")
    if isinstance(destination, (list, tuple)) and len(destination) == 2:
        tx, ty = destination
    else:
        tx, ty = target_world["start"]
    width, height = target_world["width"], target_world["height"]
    if not (0 <= tx < width and 0 <= ty < height):
        tx, ty = target_world["start"]

    origin_zone = zone
    source_room = room_name(origin_zone, x, y)
    leave_room(source_room)
    socketio.emit(
        "system_message",
        {"text": f"{username} presses the warp stone and vanishes in a burst of light."},
        room=source_room,
    )
    broadcast_room_state(origin_zone, x, y)

    player["zone"] = target_zone
    player["x"], player["y"] = tx, ty
    destination_room = room_name(target_zone, tx, ty)
    join_room(destination_room)
    socketio.emit(
        "system_message",
        {"text": f"{username} coalesces beside the warp stone in a shimmer of light."},
        room=destination_room,
        include_self=False,
    )
    world_name = target_world.get("name", target_zone.title())
    dest_info = get_room_info(target_zone, tx, ty)
    notify_player(username, f"The warp stone pulls you to {world_name}: {dest_info['name']}.")
    send_room_state(username)
    trigger_aggressive_mobs_for_player(username, tx, ty)
    broadcast_room_state(target_zone, tx, ty)
    return True


@socketio.on("move")
def on_move(data):
    username = session.get("character_name")
    if not username or username not in players:
        return
    if not check_player_action_gate(username):
        return

    direction = (data.get("direction") or "").lower()
    player = players[username]
    old_x, old_y = player["x"], player["y"]
    zone = player.get("zone", DEFAULT_ZONE)
    width, height = get_world_dimensions(zone)
    if direction not in DIRECTION_VECTORS:
        return
    dx, dy = DIRECTION_VECTORS[direction]
    new_x, new_y = old_x + dx, old_y + dy

    # Bounds check
    if not (0 <= new_x < width and 0 <= new_y < height):
        emit("system_message", {"text": "You cannot go that way."})
        return

    door_id = get_door_id(zone, old_x, old_y, direction)
    if door_id and not is_door_open(door_id):
        door = DOORS.get(door_id)
        door_name = door.get("name") if door else "The door"
        notify_player(username, f"{door_name} is closed.")
        send_room_state(username)
        return

    old_room = room_name(zone, old_x, old_y)
    new_room = room_name(zone, new_x, new_y)

    if (new_x, new_y) == (old_x, old_y):
        # no move
        return

    # Update player position
    disengage_player_from_room_mobs(username, old_x, old_y)
    player["x"], player["y"] = new_x, new_y

    # Leave old room, notify others
    leave_room(old_room)
    emit("system_message", {"text": f"{username} has left the room."}, room=old_room)

    # Join new room, notify others
    join_room(new_room)
    emit("system_message", {"text": f"{username} has entered the room."}, room=new_room, include_self=False)

    # Send new room state to moving player
    mark_player_action(player)
    send_room_state(username)
    trigger_aggressive_mobs_for_player(username, player["x"], player["y"])


@socketio.on("activate_warp")
def on_activate_warp():
    username = session.get("character_name")
    if not username or username not in players:
        return
    if not check_player_action_gate(username):
        return

    player = players[username]
    zone = player.get("zone", DEFAULT_ZONE)
    x, y = player["x"], player["y"]
    room = get_room_info(zone, x, y)
    if not room.get("travel_to"):
        notify_player(username, "No warp stone responds in this room.")
        send_room_state(username)
        return

    mark_player_action(player)
    if not handle_travel_portal(username):
        notify_player(username, "The warp stone flickers but does not take hold.")
        send_room_state(username)


@socketio.on("door_action")
def on_door_action(data):
    username = session.get("character_name")
    if not username or username not in players:
        return
    if not check_player_action_gate(username):
        return

    payload = data or {}
    door_id = payload.get("door_id")
    action = (payload.get("action") or "").lower()
    door = DOORS.get(door_id)
    if not door:
        notify_player(username, "That door does not seem to exist.")
        return

    player = players[username]
    zone = player.get("zone", DEFAULT_ZONE)
    coords = (player["x"], player["y"])
    facing = None
    for endpoint in door.get("endpoints", []):
        if endpoint["zone"] == zone and endpoint["coords"] == coords:
            facing = endpoint["direction"]
            break
    if not facing:
        notify_player(username, "You are not close enough to that door.")
        send_room_state(username)
        return

    if action == "open":
        if door.get("state") == "open":
            notify_player(username, f"The {door['name']} is already open.")
            return
        door["state"] = "open"
        verb = "opens"
        feedback = f"You swing the {door['name']} open."
    elif action == "close":
        if door.get("state") == "closed":
            notify_player(username, f"The {door['name']} is already closed.")
            return
        door["state"] = "closed"
        verb = "closes"
        feedback = f"You pull the {door['name']} closed."
    else:
        notify_player(username, "You must choose to open or close the door.")
        return

    mark_player_action(player)
    notify_player(username, feedback)

    touched_rooms = set()
    for endpoint in door.get("endpoints", []):
        z = endpoint["zone"]
        ex, ey = endpoint["coords"]
        room_key = (z, ex, ey)
        if room_key in touched_rooms:
            continue
        touched_rooms.add(room_key)
        room_channel = room_name(z, ex, ey)
        include_self = not (z == zone and (ex, ey) == coords)
        socketio.emit(
            "system_message",
            {"text": f"{username} {verb} the {door['name']}."},
            room=room_channel,
            include_self=include_self,
        )
        broadcast_room_state(z, ex, ey)


@socketio.on("equip_weapon")
def on_equip_weapon(data):
    username = session.get("character_name")
    if not username or username not in players:
        return
    weapon_key = (data or {}).get("weapon") or (data or {}).get("weapon_key")
    success, message = equip_weapon_for_player(username, weapon_key)
    if not success:
        notify_player(username, message)


@socketio.on("cast_spell")
def on_cast_spell(data):
    username = session.get("character_name")
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
    username = session.get("character_name")
    if not username or username not in players:
        return
    payload = data or {}
    loot_id = payload.get("loot_id") or payload.get("id") or payload.get("loot")
    success, message = pickup_loot(username, loot_id)
    if not success and message:
        notify_player(username, message)


@socketio.on("search")
def on_search_event(data):
    username = session.get("character_name")
    if not username or username not in players:
        return
    success, message = perform_search_action(username)
    if not success and message:
        notify_player(username, message)


@socketio.on("chat")
def on_chat(data):
    username = session.get("character_name")
    if not username or username not in players:
        return

    text = (data.get("text") or "").strip()
    if not text:
        return

    if text.startswith("/"):
        handled = handle_command(username, text[1:])
        if handled:
            return

    player = players[username]
    x, y = player["x"], player["y"]
    zone = player.get("zone", DEFAULT_ZONE)
    rname = room_name(zone, x, y)

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
        player = players.get(username)
        if player:
            x, y = player["x"], player["y"]
            zone = player.get("zone", DEFAULT_ZONE)
            rname = room_name(zone, x, y)
        else:
            rname = None
            x = y = 0
        disengage_player_from_room_mobs(username, x, y)
        # Notify others
        if rname:
            emit("system_message", {"text": f"{username} has disconnected."}, room=rname)
        update_character_current_hp(players[username]["character_id"], players[username]["hp"])
        # Remove from players (MVP: no persistent positions)
        players.pop(username, None)


if not mobs:
    spawn_initial_mobs()


if __name__ == "__main__":
    init_db()
    # Bind to 0.0.0.0 for container use
    socketio.run(app, host="0.0.0.0", port=5000)
