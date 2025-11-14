import os
import random
import sqlite3
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
PROFICIENCY_BONUS = 2  # SRD level 1 characters

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
        "weapon": {"name": "Longsword", "dice": (1, 8), "ability": "str", "damage_type": "slashing"},
    },
    "Rogue": {
        "hit_die": 8,
        "primary_ability": "dex",
        "armor_bonus": 1,
        "weapon": {"name": "Shortsword", "dice": (1, 6), "ability": "dex", "damage_type": "piercing"},
    },
    "Wizard": {
        "hit_die": 6,
        "primary_ability": "int",
        "armor_bonus": 0,
        "weapon": {"name": "Arcane Bolt", "dice": (1, 8), "ability": "int", "damage_type": "force"},
    },
    "Cleric": {
        "hit_die": 8,
        "primary_ability": "wis",
        "armor_bonus": 1,
        "weapon": {"name": "Mace", "dice": (1, 6), "ability": "str", "damage_type": "bludgeoning"},
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
    weapon_tpl = class_data["weapon"]
    attack_ability = weapon_tpl.get("ability") or class_data["primary_ability"]
    proficiency = PROFICIENCY_BONUS
    max_hp = max(class_data["hit_die"] + ability_mods["con"], 1)
    ac = max(10 + ability_mods["dex"] + class_data.get("armor_bonus", 0), 10)
    attack_bonus = ability_mods[attack_ability] + proficiency
    weapon = {
        "name": weapon_tpl["name"],
        "dice": weapon_tpl["dice"],
        "dice_label": format_dice(weapon_tpl["dice"]),
        "ability": attack_ability,
        "damage_type": weapon_tpl.get("damage_type", "physical"),
    }
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
        "weapon": weapon,
        "attack_bonus": attack_bonus,
        "attack_ability": attack_ability,
    }


def derive_character_from_record(record):
    race = normalize_choice(record.get("race"), RACES, DEFAULT_RACE)
    char_class = normalize_choice(record.get("char_class"), CLASSES, DEFAULT_CLASS)
    class_data = CLASSES[char_class]
    abilities = {ability: record.get(f"{ability}_score") or 10 for ability in ABILITY_KEYS}
    ability_mods = {ability: ability_modifier(score) for ability, score in abilities.items()}
    proficiency = PROFICIENCY_BONUS
    ac = max(10 + ability_mods["dex"] + class_data.get("armor_bonus", 0), 10)
    weapon_tpl = class_data["weapon"]
    attack_ability = weapon_tpl.get("ability") or class_data["primary_ability"]
    attack_bonus = ability_mods[attack_ability] + proficiency
    max_hp = record.get("hp") or max(class_data["hit_die"] + ability_mods["con"], 1)
    weapon = {
        "name": weapon_tpl["name"],
        "dice": weapon_tpl["dice"],
        "dice_label": format_dice(weapon_tpl["dice"]),
        "ability": attack_ability,
        "damage_type": weapon_tpl.get("damage_type", "physical"),
    }
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
    }


def clamp_hp(value, max_hp):
    if value is None:
        return max_hp
    return max(0, min(int(value), max_hp))


def roll_weapon_damage(weapon, ability_mod, crit=False):
    dice_count, dice_size = weapon["dice"]
    total_dice = dice_count * (2 if crit else 1)
    total = sum(random.randint(1, dice_size) for _ in range(total_dice)) + ability_mod
    return max(1, total)

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
            level INTEGER
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
    c.execute("UPDATE users SET hp = COALESCE(hp, 10)")
    c.execute("UPDATE users SET atk = COALESCE(atk, 2)")

    # Backfill missing character sheets
    c.execute(
        """
        SELECT id, username, race, char_class, hp, current_hp, level,
               str_score, dex_score, con_score, int_score, wis_score, cha_score
        FROM users
        """
    )
    for row in c.fetchall():
        needs_sheet = (
            row["race"] is None
            or row["char_class"] is None
            or any(row[f"{ability}_score"] is None for ability in ABILITY_KEYS)
        )
        if needs_sheet:
            sheet = build_character_sheet(row["race"] or DEFAULT_RACE, row["char_class"] or DEFAULT_CLASS)
            ability_values = sheet["abilities"]
            c.execute(
                """
                UPDATE users
                SET race = ?, char_class = ?, level = ?, hp = ?, current_hp = ?,
                    atk = ?, str_score = ?, dex_score = ?, con_score = ?,
                    int_score = ?, wis_score = ?, cha_score = ?
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
                    row["id"],
                ),
            )
        elif row["current_hp"] is None:
            c.execute("UPDATE users SET current_hp = hp WHERE id = ?", (row["id"],))

    conn.commit()
    conn.close()


def get_user(username):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = c.fetchone()
    conn.commit()
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
            hp, current_hp, atk
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
# }
players = {}


def room_name(x, y):
    return f"room_{x}_{y}"


def get_room_info(x, y):
    if 0 <= x < WORLD_WIDTH and 0 <= y < WORLD_HEIGHT:
        return WORLD_MAP[y][x]
    return {"name": "Unknown void", "description": "You should not be here."}


def get_players_in_room(x, y):
    return [u for u, p in players.items() if p["x"] == x and p["y"] == y]


def build_player_state(user_record, sid):
    derived = derive_character_from_record(user_record)
    state = {
        "sid": sid,
        "x": START_X,
        "y": START_Y,
    }
    state.update(derived)
    state["hp"] = clamp_hp(user_record.get("current_hp"), derived["max_hp"])
    return state


def send_room_state(username):
    player = players.get(username)
    if not player:
        return
    x, y = player["x"], player["y"]
    room = get_room_info(x, y)
    occupants = get_players_in_room(x, y)
    weapon = player.get("weapon", {})
    payload = {
        "x": x,
        "y": y,
        "room_name": room["name"],
        "description": room["description"],
        "players": occupants,
        "character": {
            "race": player["race"],
            "char_class": player["char_class"],
            "level": player.get("level", 1),
            "hp": player["hp"],
            "max_hp": player["max_hp"],
            "ac": player["ac"],
            "proficiency": player["proficiency"],
            "weapon": {
                "name": weapon.get("name", "Unarmed"),
                "dice": weapon.get("dice_label", "-"),
                "damage_type": weapon.get("damage_type", ""),
            },
            "attack_bonus": player["attack_bonus"],
            "attack_ability": player["attack_ability"],
            "abilities": player["abilities"],
            "ability_mods": player["ability_mods"],
        },
    }
    socketio.emit("room_state", payload, to=player["sid"])


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
            notify_player(username, "Usage: /attack <player_name>")
            return True
        target_name = parts[1]
        resolve_attack(username, target_name)
        return True

    notify_player(username, f"Unknown command: {cmd}")
    return True

def attack_roll_success(roll, attack_bonus, target_ac):
    if roll == 1:
        return False
    if roll == 20:
        return True
    return (roll + attack_bonus) >= target_ac


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
        notify_player(attacker_name, f"{target_name} is nowhere to be found.")
        return

    if attacker["x"] != target["x"] or attacker["y"] != target["y"]:
        notify_player(attacker_name, f"{target_name} is not in the same room.")
        return

    roll = random.randint(1, 20)
    crit = roll == 20
    attack_bonus = attacker["attack_bonus"]
    total_attack = roll + attack_bonus
    target_ac = target["ac"]
    room = room_name(attacker["x"], attacker["y"])

    if not attack_roll_success(roll, attack_bonus, target_ac):
        socketio.emit(
            "system_message",
            {
                "text": f"{attacker_name} attacks {target_name} but misses "
                f"(roll {roll} + {attack_bonus} = {total_attack} vs AC {target_ac})."
            },
            room=room,
        )
        return

    ability_key = attacker["weapon"].get("ability") or attacker["attack_ability"]
    ability_mod = attacker["ability_mods"].get(ability_key, 0)
    damage = roll_weapon_damage(attacker["weapon"], ability_mod, crit=crit)
    target["hp"] = clamp_hp(target["hp"] - damage, target["max_hp"])
    update_user_current_hp(target_name, target["hp"])

    socketio.emit(
        "system_message",
        {
            "text": f"{attacker_name} hits {target_name} with {attacker['weapon']['name']} "
            f"for {damage} damage (roll {roll}{' - critical!' if crit else ''}, AC {target_ac})."
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

    # If player already exists, keep their position; otherwise spawn at start
    if username not in players:
        players[username] = build_player_state(user_record, request.sid)
    else:
        existing = players[username]
        existing["sid"] = request.sid  # update sid if reconnect
        refreshed = build_player_state(user_record, request.sid)
        for key in [
            "race",
            "char_class",
            "level",
            "abilities",
            "ability_mods",
            "max_hp",
            "ac",
            "proficiency",
            "weapon",
            "attack_bonus",
            "attack_ability",
        ]:
            existing[key] = refreshed[key]
        existing["hp"] = clamp_hp(user_record.get("current_hp"), existing["max_hp"])

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


if __name__ == "__main__":
    init_db()
    # Bind to 0.0.0.0 for container use
    socketio.run(app, host="0.0.0.0", port=5000)
