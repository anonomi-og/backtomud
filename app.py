import os
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


# --- DB helpers: very simple users table ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


def get_user(username):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, username, password_hash FROM users WHERE username = ?", (username,))
    row = c.fetchone()
    conn.close()
    if row:
        return {"id": row[0], "username": row[1], "password_hash": row[2]}
    return None


def create_user(username, password):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    password_hash = generate_password_hash(password)
    c.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        (username, password_hash),
    )
    conn.commit()
    conn.close()


# --- In-memory game state (per container, MVP only) ---
# players[username] = {"sid": socket_id, "x": int, "y": int}
players = {}


def room_name(x, y):
    return f"room_{x}_{y}"


def get_room_info(x, y):
    if 0 <= x < WORLD_WIDTH and 0 <= y < WORLD_HEIGHT:
        return WORLD_MAP[y][x]
    return {"name": "Unknown void", "description": "You should not be here."}


def get_players_in_room(x, y):
    return [u for u, p in players.items() if p["x"] == x and p["y"] == y]


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
            create_user(username, password)
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
    return render_template("login.html")


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
        disconnect()
        return

    # If player already exists, keep their position; otherwise spawn at start
    if username not in players:
        players[username] = {
            "sid": request.sid,
            "x": START_X,
            "y": START_Y,
        }
    else:
        players[username]["sid"] = request.sid  # update sid if reconnect

    x = players[username]["x"]
    y = players[username]["y"]
    rname = room_name(x, y)

    join_room(rname)

    # Notify others in the room
    emit("system_message", {"text": f"{username} has entered the room."}, room=rname, include_self=False)

    # Send room state to this player
    room = get_room_info(x, y)
    occupants = get_players_in_room(x, y)
    emit(
        "room_state",
        {
            "x": x,
            "y": y,
            "room_name": room["name"],
            "description": room["description"],
            "players": occupants,
        },
    )


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
    room = get_room_info(new_x, new_y)
    occupants = get_players_in_room(new_x, new_y)
    emit(
        "room_state",
        {
            "x": new_x,
            "y": new_y,
            "room_name": room["name"],
            "description": room["description"],
            "players": occupants,
        },
    )


@socketio.on("chat")
def on_chat(data):
    username = session.get("username")
    if not username or username not in players:
        return

    text = (data.get("text") or "").strip()
    if not text:
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
        # Remove from players (MVP: no persistent positions)
        players.pop(username, None)


if __name__ == "__main__":
    init_db()
    # Bind to 0.0.0.0 for container use
    socketio.run(app, host="0.0.0.0", port=5000)
