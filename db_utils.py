"""Database utility helpers for the MariaDB-backed game data layer."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Tuple

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, Result

load_dotenv()

_ENGINE: Optional[Engine] = None


def get_engine() -> Engine:
    """Create (or return the cached) SQLAlchemy engine for MariaDB access."""

    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE

    host = os.environ.get("DB_HOST")
    port = os.environ.get("DB_PORT", "3306")
    name = os.environ.get("DB_NAME")
    user = os.environ.get("DB_USER")
    password = os.environ.get("DB_PASSWORD")

    if not all([host, name, user, password]):
        raise RuntimeError(
            "Database credentials are not fully configured. "
            "Ensure DB_HOST, DB_PORT, DB_NAME, DB_USER, and DB_PASSWORD are set in the environment/.env file."
        )

    connection_url = f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}"
    _ENGINE = create_engine(connection_url, pool_pre_ping=True, future=True)
    return _ENGINE


def _execute(query: str, **params: Any) -> Result:
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text(query), params)
        conn.commit()
        return result


def fetch_one(query: str, **params: Any) -> Optional[Dict[str, Any]]:
    result = _execute(query, **params)
    row = result.mappings().fetchone()
    return dict(row) if row else None


def fetch_all(query: str, **params: Any) -> List[Dict[str, Any]]:
    result = _execute(query, **params)
    return [dict(row) for row in result.mappings().all()]


def insert_and_return_id(query: str, **params: Any) -> int:
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(text(query), params)
        inserted = result.lastrowid
    return int(inserted or 0)


def execute(query: str, **params: Any) -> int:
    result = _execute(query, **params)
    return int(getattr(result, "rowcount", 0))


# --- Core lookup helpers -------------------------------------------------


def get_zone(zone_id: str) -> Optional[Dict[str, Any]]:
    return fetch_one("SELECT * FROM zones WHERE zone_id = :zone_id", zone_id=zone_id)


def list_zone_ids() -> List[str]:
    records = fetch_all("SELECT zone_id FROM zones ORDER BY zone_id")
    return [record["zone_id"] for record in records]


def get_rooms_by_zone(zone_id: str) -> List[Dict[str, Any]]:
    return fetch_all(
        "SELECT * FROM rooms WHERE zone_id = :zone_id",
        zone_id=zone_id,
    )


def get_room_by_coords(zone_id: str, x: int, y: int) -> Optional[Dict[str, Any]]:
    return fetch_one(
        "SELECT * FROM rooms WHERE zone_id = :zone_id AND x_coord = :x AND y_coord = :y",
        zone_id=zone_id,
        x=x,
        y=y,
    )


def get_current_room_by_character(character_id: int) -> Optional[Dict[str, Any]]:
    return fetch_one(
        """
        SELECT rooms.*
        FROM characters
        LEFT JOIN rooms ON rooms.room_id = characters.current_room_id
        WHERE characters.character_id = :character_id
        """,
        character_id=character_id,
    )


def _parse_notes(notes: Optional[str]) -> Dict[str, Any]:
    if not notes:
        return {}
    try:
        return json.loads(notes)
    except (TypeError, json.JSONDecodeError):
        return {}


def _room_search_payload(room: Dict[str, Any], loot_items: Iterable[str]) -> Optional[Dict[str, Any]]:
    meta = _parse_notes(room.get("notes_gm"))
    search_meta = meta.get("search") if isinstance(meta, dict) else None
    if not search_meta:
        if room.get("search_dc"):
            return {
                "dc": room.get("search_dc"),
                "ability": "wis",
                "success_text": room.get("description_searched"),
                "failure_text": None,
                "loot": list(loot_items),
            }
        return None
    payload = {
        "dc": room.get("search_dc") or search_meta.get("dc"),
        "ability": search_meta.get("ability", "wis"),
        "success_text": room.get("description_searched") or search_meta.get("success_text"),
        "failure_text": search_meta.get("failure_text"),
        "loot": list(loot_items) or list(search_meta.get("loot") or []),
    }
    return payload


def _room_warp_payload(room: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    meta = _parse_notes(room.get("notes_gm"))
    warp = meta.get("warp") if isinstance(meta, dict) else None
    if not warp:
        return None
    travel_to = warp.get("destination") or {}
    return {
        "warp_description": warp.get("description"),
        "travel_to": travel_to if isinstance(travel_to, dict) else None,
    }


def get_room_loot_templates(room_id: int) -> List[str]:
    records = fetch_all(
        "SELECT item_template_id FROM room_loot_tables WHERE room_id = :room_id",
        room_id=room_id,
    )
    return [row["item_template_id"] for row in records]


def get_room_mob_spawn_records(room_id: int) -> List[Dict[str, Any]]:
    return fetch_all(
        "SELECT * FROM room_mob_spawns WHERE room_id = :room_id",
        room_id=room_id,
    )


def build_room_payload(room: Dict[str, Any]) -> Dict[str, Any]:
    room_payload: Dict[str, Any] = {
        "room_id": room["room_id"],
        "zone_id": room["zone_id"],
        "name": room["name"],
        "description": room.get("description_base", ""),
        "description_searched": room.get("description_searched"),
        "x": room.get("x_coord"),
        "y": room.get("y_coord"),
        "is_starting": bool(room.get("is_starting_room")),
        "is_safe": bool(room.get("is_safe_room")),
    }

    loot_items = get_room_loot_templates(room["room_id"])
    search_payload = _room_search_payload(room, loot_items)
    if search_payload:
        room_payload["search"] = search_payload

    warp_payload = _room_warp_payload(room)
    if warp_payload:
        room_payload.update(warp_payload)

    spawn_records = get_room_mob_spawn_records(room["room_id"])
    if spawn_records:
        room_payload["mobs"] = [record["mob_template_id"] for record in spawn_records]

    return room_payload


@lru_cache(maxsize=32)
def load_world(zone_id: str) -> Optional[Dict[str, Any]]:
    zone = get_zone(zone_id)
    if not zone:
        return None

    rooms = get_rooms_by_zone(zone_id)
    if not rooms:
        return {
            "zone_id": zone_id,
            "name": zone.get("name"),
            "map": [],
            "width": 0,
            "height": 0,
            "start": (0, 0),
        }

    max_x = max(room["x_coord"] for room in rooms)
    max_y = max(room["y_coord"] for room in rooms)
    width = max_x + 1
    height = max_y + 1
    grid: List[List[Dict[str, Any]]] = [[{} for _ in range(width)] for _ in range(height)]
    start = (0, 0)

    for room in rooms:
        payload = build_room_payload(room)
        x, y = payload["x"], payload["y"]
        if 0 <= y < height and 0 <= x < width:
            grid[y][x] = payload
        if payload.get("is_starting"):
            start = (x, y)

    return {
        "zone_id": zone_id,
        "name": zone.get("name"),
        "map": grid,
        "width": width,
        "height": height,
        "start": start,
    }


def get_world(zone_id: str) -> Optional[Dict[str, Any]]:
    return load_world(zone_id)


def get_world_dimensions(zone_id: str) -> Tuple[int, int]:
    world = load_world(zone_id)
    if not world:
        return 0, 0
    return world["width"], world["height"]


def get_world_start(zone_id: str) -> Tuple[int, int]:
    world = load_world(zone_id)
    if not world:
        return 0, 0
    return tuple(world.get("start", (0, 0)))


def get_room_payload(zone_id: str, x: int, y: int) -> Optional[Dict[str, Any]]:
    world = load_world(zone_id)
    if not world:
        return None
    grid = world.get("map", [])
    if 0 <= y < len(grid):
        row = grid[y]
        if 0 <= x < len(row):
            payload = row[x]
            return payload if payload else None
    room = get_room_by_coords(zone_id, x, y)
    if room:
        return build_room_payload(room)
    return None


# --- Mob helpers --------------------------------------------------------


def get_mob_template(template_id: str) -> Optional[Dict[str, Any]]:
    record = fetch_one(
        "SELECT * FROM mob_templates WHERE mob_template_id = :template_id",
        template_id=template_id,
    )
    if not record:
        return None
    notes = _parse_notes(record.get("notes_gm"))
    return {
        **record,
        "notes": notes,
    }


def find_mob_templates(name: Optional[str] = None, max_cr: Optional[float] = None) -> List[Dict[str, Any]]:
    clauses = []
    params: Dict[str, Any] = {}
    if name:
        clauses.append("name LIKE :name")
        params["name"] = f"%{name}%"
    if max_cr is not None:
        clauses.append("cr <= :max_cr")
        params["max_cr"] = max_cr
    where_clause = " WHERE " + " AND ".join(clauses) if clauses else ""
    return fetch_all(f"SELECT * FROM mob_templates{where_clause}", **params)


def create_mob_instance_record(
    template_id: str,
    room_id: Optional[int],
    current_hp: Optional[int],
    status: str = "alive",
) -> int:
    return insert_and_return_id(
        """
        INSERT INTO mob_instances (
            mob_template_id, room_id, current_hp, status
        ) VALUES (:template_id, :room_id, :current_hp, :status)
        """,
        template_id=template_id,
        room_id=room_id,
        current_hp=current_hp,
        status=status,
    )


# --- Item helpers -------------------------------------------------------


def get_item_template(item_id: str) -> Optional[Dict[str, Any]]:
    return fetch_one(
        "SELECT * FROM item_templates WHERE item_template_id = :item_id",
        item_id=item_id,
    )


@lru_cache(maxsize=1)
def get_weapon_templates() -> Dict[str, Dict[str, Any]]:
    records = fetch_all("SELECT * FROM item_templates WHERE item_type = 'weapon'")
    return {record["item_template_id"]: record for record in records}


@lru_cache(maxsize=1)
def get_general_item_templates() -> Dict[str, Dict[str, Any]]:
    records = fetch_all("SELECT * FROM item_templates WHERE item_type <> 'weapon'")
    return {record["item_template_id"]: record for record in records}


def list_item_instances_for_room(room_id: int) -> List[Dict[str, Any]]:
    return fetch_all(
        "SELECT * FROM item_instances WHERE room_id = :room_id",
        room_id=room_id,
    )


def create_item_instance(
    item_id: str,
    room_id: Optional[int] = None,
    owner_character_id: Optional[int] = None,
    stack_size: int = 1,
) -> int:
    return insert_and_return_id(
        """
        INSERT INTO item_instances (
            item_template_id, room_id, owner_character_id, stack_size
        ) VALUES (:item_id, :room_id, :owner_character_id, :stack_size)
        """,
        item_id=item_id,
        room_id=room_id,
        owner_character_id=owner_character_id,
        stack_size=stack_size,
    )


# --- NPC helpers --------------------------------------------------------


def get_npc_template(npc_id: str) -> Optional[Dict[str, Any]]:
    return fetch_one(
        "SELECT * FROM npc_templates WHERE npc_template_id = :npc_id",
        npc_id=npc_id,
    )


def get_room_npc_spawns(zone_id: str) -> List[Dict[str, Any]]:
    return fetch_all(
        """
        SELECT room_npc_spawns.*, rooms.zone_id, rooms.x_coord, rooms.y_coord
        FROM room_npc_spawns
        JOIN rooms ON rooms.room_id = room_npc_spawns.room_id
        WHERE rooms.zone_id = :zone_id
        """,
        zone_id=zone_id,
    )


def get_npc_spawn(npc_id: str) -> Optional[Dict[str, Any]]:
    return fetch_one(
        """
        SELECT room_npc_spawns.*, rooms.zone_id, rooms.x_coord, rooms.y_coord
        FROM room_npc_spawns
        JOIN rooms ON rooms.room_id = room_npc_spawns.room_id
        WHERE room_npc_spawns.npc_template_id = :npc_id
        LIMIT 1
        """,
        npc_id=npc_id,
    )


# --- Utility ------------------------------------------------------------


def refresh_world_cache(zone_id: str) -> None:
    """Clear the cached world payload for a zone."""

    try:
        load_world.cache_clear()
    except AttributeError:
        pass
    load_world(zone_id)

