import os
import json
from typing import Any, Dict, List, Optional, Tuple
import psycopg
from psycopg import sql
import httpx
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
SUPABASE_URL = os.getenv("SUPABASE_URL")  #
PRODUCT_BUCKET = os.getenv("PRODUCT_BUCKET", "images")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
ALERT_TABLE = os.getenv("ALERT_TABLE", "alerts")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Put it in .env or your environment variables.")


def get_conn() -> psycopg.Connection:
    # psycopg commits on successful context manager exit, rolls back on exception.
    return psycopg.connect(DATABASE_URL)


def _as_obj(value: Any) -> Any:
    """
    psycopg may return jsonb as a Python object (list/dict) or as a string.
    Normalize to Python object.
    """
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        return json.loads(value)
    return value


# ----------------------------
# Images
# ----------------------------
def build_public_image_url(image_path: Optional[str]) -> Optional[str]:
    if not image_path:
        return None
    if not SUPABASE_URL:
        return None
    return f"{SUPABASE_URL}/storage/v1/object/public/{PRODUCT_BUCKET}/{image_path}"


def _get_supabase_key() -> Optional[str]:
    return SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY


def _storage_headers(content_type: Optional[str] = None) -> Dict[str, str]:
    key = _get_supabase_key()
    if not SUPABASE_URL or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_ANON_KEY) are required for storage operations.")

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def storage_list_files(prefix: str) -> List[str]:
    """
    Returns file paths under PRODUCT_BUCKET for a given prefix.
    Example output: ["inventory/references/shelf_view_G0_P0.png", ...]
    """
    if not SUPABASE_URL:
        return []

    normalized_prefix = (prefix or "").strip("/")

    url = f"{SUPABASE_URL}/storage/v1/object/list/{PRODUCT_BUCKET}"
    payload = {
        "prefix": normalized_prefix,
        "limit": 1000,
        "offset": 0,
        "sortBy": {"column": "name", "order": "asc"},
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(url, headers=_storage_headers("application/json"), json=payload)
            if response.status_code >= 400:
                return []
            entries = response.json() if response.content else []
    except Exception:
        return []

    results: List[str] = []
    for entry in entries:
        name = entry.get("name")
        if not name:
            continue
        if name.startswith(normalized_prefix):
            results.append(name)
        elif normalized_prefix:
            results.append(f"{normalized_prefix}/{name}")
        else:
            results.append(name)
    return results


def storage_upload_bytes(path: str, content: bytes, content_type: str = "image/png", upsert: bool = True) -> bool:
    normalized_path = (path or "").strip("/")
    if not normalized_path:
        return False

    url = f"{SUPABASE_URL}/storage/v1/object/{PRODUCT_BUCKET}/{normalized_path}"
    headers = _storage_headers(content_type)
    headers["x-upsert"] = "true" if upsert else "false"

    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.post(url, headers=headers, content=content)
            if response.status_code < 400:
                return True

            fallback_response = client.put(url, headers=headers, content=content)
            if fallback_response.status_code < 400:
                return True

            print(
                f"Supabase upload failed for '{normalized_path}' "
                f"(POST {response.status_code}, PUT {fallback_response.status_code}). "
                f"POST body: {response.text[:300]}"
            )
            return False
    except Exception as e:
        print(f"Supabase upload exception for '{normalized_path}': {e}")
        return False


def storage_download_bytes(path: str) -> Optional[bytes]:
    normalized_path = (path or "").strip("/")
    if not normalized_path:
        return None

    url = f"{SUPABASE_URL}/storage/v1/object/{PRODUCT_BUCKET}/{normalized_path}"
    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.get(url, headers=_storage_headers())
            if response.status_code >= 400:
                return None
            return response.content
    except Exception:
        return None


def storage_delete_files(paths: List[str]) -> bool:
    normalized_paths = [p.strip("/") for p in (paths or []) if p and p.strip("/")]
    if not normalized_paths:
        return True

    headers = _storage_headers()
    all_ok = True
    try:
        with httpx.Client(timeout=20.0) as client:
            for path in normalized_paths:
                url = f"{SUPABASE_URL}/storage/v1/object/{PRODUCT_BUCKET}/{path}"
                response = client.delete(url, headers=headers)
                if response.status_code >= 400 and response.status_code != 404:
                    all_ok = False
    except Exception:
        return False

    return all_ok

# ----------------------------
# PRODUCTS / PLANOGRAM
# ----------------------------

def get_all_planogram_products():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            select sku, name, x, y
            from store_planogram
        """)
        rows = cur.fetchall()
        return [{"sku": r[0], "name": r[1], "x": r[2], "y": r[3]} for r in rows]


def get_planogram_products_for_vision() -> List[Dict[str, Any]]:
    """
    Returns products in the shape used by OCR/patrol controller logic:
    [
      {
        "sku": "...",
        "name": "...",
        "price": 12.34,
        "location": {"x": ..., "y": ..., "z": ...}
      },
      ...
    ]

    Data source:
    - store_planogram for x/y/z placement
    - product_catalog for price (left join by sku)
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select p.sku, p.name, p.x, p.y, p.z, c.price
            from store_planogram p
            left join product_catalog c on c.sku = p.sku
            """
        )
        rows = cur.fetchall()

    out: List[Dict[str, Any]] = []
    for sku, name, x, y, z, price in rows:
        out.append(
            {
                "sku": sku,
                "name": name,
                "price": float(price) if price is not None else 0.0,
                "location": {
                    "x": float(x),
                    "y": float(y),
                    "z": float(z) if z is not None else 0.0,
                },
            }
        )
    return out

def search_products(keyword: str, limit: int = 30) -> list[dict]:
    q = (keyword or "").strip()
    tokens = [t.strip() for t in q.split() if t.strip()]
    if not tokens:
        return []

    with get_conn() as conn, conn.cursor() as cur:

        # ----------------------------
        # SINGLE TOKEN: favor category
        # ----------------------------
        if len(tokens) == 1:
            pattern = f"%{tokens[0]}%"

            cur.execute(
                """
                SELECT sku, name, price, category, image_path,
                CASE WHEN category ILIKE %s THEN 0 WHEN name ILIKE %s THEN 1 ELSE 2 END AS rank
                FROM product_catalog
                WHERE category ILIKE %s
                OR name ILIKE %s
                ORDER BY rank, name
                LIMIT %s
                """,
                (pattern, pattern, pattern, pattern, limit),
            )
            rows = cur.fetchall()

        # -----------------------------------------
        # MULTI TOKEN: phrase, then all-tokens-in-name
        # -----------------------------------------
        else:
            patterns = [f"%{t}%" for t in tokens]
            phrase = f"%{q}%"

            cur.execute(
                """
                SELECT sku, name, price, category, image_path,
                       CASE
                         WHEN name ILIKE %s THEN 0
                         WHEN NOT EXISTS (
                           SELECT 1 FROM unnest(%s::text[]) p
                           WHERE name NOT ILIKE p
                         ) THEN 1
                         ELSE 2
                       END AS rank
                FROM product_catalog
                WHERE name ILIKE %s
                   OR NOT EXISTS (
                       SELECT 1 FROM unnest(%s::text[]) p
                       WHERE name NOT ILIKE p
                   )
                ORDER BY rank, name
                LIMIT %s
                """,
                (phrase, patterns, phrase, patterns, limit),
            )
            rows = cur.fetchall()

    return [
        {
            "sku": r[0],
            "name": r[1],
            "price": float(r[2]),
            "category": r[3],
            "image_url": build_public_image_url(r[4]),
        }
        for r in rows
    ]



def get_product_details_by_sku(sku: str) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select c.sku, c.name, c.price, c.image_path, c.inventory, p.aisle, p.shelf
            from product_catalog c
            join store_planogram p on p.sku = c.sku
            where c.sku = %s
            limit 1
            """,
            (str(sku),),
        )
        r = cur.fetchone()

    if not r:
        return None

    return {
        "sku": r[0],
        "name": r[1],
        "price": float(r[2]),
        "image_url": build_public_image_url(r[3]),
        "inventory": int(r[4]) if r[4] is not None else 0,
        "aisle": r[5],
        "shelf": r[6],
    }

def get_product_basic_by_name(name: str) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select name, price
            from product_catalog
            where lower(name) = lower(%s)
            limit 1
            """,
            (name,),
        )
        r = cur.fetchone()

    if not r:
        return None

    return {"name": r[0], "price": float(r[1])}


def get_product_by_name(name: str) -> Optional[Dict[str, Any]]:
    """
    Fetch full product row by name (case-insensitive).
    Table: store_planogram
    Expected columns: sku, name, aisle, shelf, x, y, z
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select sku, name, aisle, shelf, x, y, z
            from store_planogram
            where lower(name) = lower(%s)
            limit 1
            """,
            (name,),
        )
        row = cur.fetchone()

    if not row:
        return None

    return {
        "sku": row[0],
        "name": row[1],
        "aisle": row[2],
        "shelf": row[3],
        "x": float(row[4]),
        "y": float(row[5]),
        "z": float(row[6]) if row[6] is not None else None,
    }


def get_product_coords(product_name: str) -> Optional[Tuple[float, float]]:
    """
    Route B: Return (x, y) for the robot, by product name (case-insensitive).
    Table: store_planogram
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select x, y
            from store_planogram
            where lower(name) = lower(%s)
            limit 1
            """,
            (product_name,),
        )
        row = cur.fetchone()

    if not row:
        return None

    return float(row[0]), float(row[1])


# ----------------------------
# STORE LAYOUT / OBSTACLES
# ---------------------------

def get_robot_base() -> Optional[Dict[str, Any]]:
    """
    Reads the robot base from store_layout table.
    Row format (based on your table):
      type = 'robot_base'
      position = {"x": ..., "y": ...}
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            select position
            from store_layout
            where type = 'robot_base'
            limit 1
        """)
        row = cur.fetchone()

    if not row or not row[0]:
        return None

    pos = _as_obj(row[0]) or {}
    if "x" not in pos or "y" not in pos:
        return None

    return {"x": float(pos["x"]), "y": float(pos["y"])}


def get_zone_corners(zone_name: str) -> Optional[List[Dict[str, float]]]:
    """
    Gets the corners of a zone/aisle shelf from the store_layout table.
    Zone names like "A1a", "A1b" map to "Aisle_A1_Shelf" etc.
    Returns list of corner coordinates: [{"x": ..., "y": ...}, ...]
    """
    # Extract base aisle name (e.g., "A1a" -> "A1", "B2b" -> "B2")
    # Remove trailing 'a' or 'b' to get shelf ID
    base_zone = zone_name.rstrip('ab')
    search_pattern = f"%{base_zone}%"
    
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            select corners
            from store_layout
            where obstacle_id ilike %s or description ilike %s
            limit 1
        """, (search_pattern, search_pattern))
        row = cur.fetchone()

    if row and row[0]:
        corners = _as_obj(row[0]) or []
        if corners and isinstance(corners, list):
            return [{"x": float(c["x"]), "y": float(c["y"])} for c in corners if "x" in c and "y" in c]

    return None


def get_static_obstacles() -> List[Dict[str, Any]]:
    """
    Returns obstacles in the shape the controller expects:
    [
      {"obstacle_id": "...", "description": "...", "corners": [{"x":..,"y":..}, ...]},
      ...
    ]

    Table: store_layout
    Columns: obstacle_id (text), description (text), corners (jsonb)
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select obstacle_id, description, corners
            from store_layout
            """
        )
        rows = cur.fetchall()

    out: List[Dict[str, Any]] = []
    for obstacle_id, description, corners in rows:
        out.append(
            {
                "obstacle_id": obstacle_id,
                "description": description,
                "corners": _as_obj(corners) or [],
            }
        )

    return out


def _compute_alert_status(pd_disc: str, pc_disc: str) -> str:
    pd_value = (pd_disc or "OK").strip()
    pc_value = (pc_disc or "OK").strip()

    if pd_value.upper() == "OK" and pc_value.upper() == "OK":
        return "OK"

    parts: List[str] = []
    if pd_value.upper() != "OK":
        parts.append(pd_value)
    if pc_value.upper() != "OK":
        parts.append(pc_value)
    return " | ".join(parts)


def get_planogram_location_text(product_name: str) -> Optional[str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select aisle, shelf
            from store_planogram
            where lower(name) = lower(%s)
            limit 1
            """,
            (product_name,),
        )
        row = cur.fetchone()

    if not row:
        return None

    aisle = str(row[0]).strip() if row[0] is not None else "UNKNOWN"
    shelf = str(row[1]).strip() if row[1] is not None else "UNKNOWN"
    return f"Aisle {aisle} - Shelf {shelf}"


def upsert_inventory_alert(product_name: str, pd_disc: str, pc_disc: str) -> None:
    """
    Persists/updates one patrol alert row for a product.
    Expected table columns: product_name, status, pd_disc, pc_disc, location.
    """
    status = _compute_alert_status(pd_disc, pc_disc)

    with get_conn() as conn, conn.cursor() as cur:
        table_ident = sql.Identifier(ALERT_TABLE)

        if status.upper() == "OK":
            cur.execute(
                sql.SQL(
                    """
                    delete from {table}
                    where lower(product_name) = lower(%s)
                    """
                ).format(table=table_ident),
                (product_name,),
            )
            return

        location = get_planogram_location_text(product_name)

        cur.execute(
            sql.SQL(
                """
                update {table}
                set status = %s,
                    pd_disc = %s,
                    pc_disc = %s,
                    location = %s
                where lower(product_name) = lower(%s)
                returning id
                """
            ).format(table=table_ident),
            (status, pd_disc, pc_disc, location, product_name),
        )
        updated = cur.fetchone()

        if not updated:
            cur.execute(
                sql.SQL(
                    """
                    insert into {table} (product_name, status, pd_disc, pc_disc, location)
                    values (%s, %s, %s, %s, %s)
                    """
                ).format(table=table_ident),
                (product_name, status, pd_disc, pc_disc, location),
            )


def get_inventory_alerts(limit: int = 100) -> List[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
                select id, product_name, status, location, pd_disc, pc_disc
                from {table}
                where upper(coalesce(status, 'OK')) <> 'OK'
                order by id desc
                limit %s
                """
            ).format(table=sql.Identifier(ALERT_TABLE)),
            (limit,),
        )
        rows = cur.fetchall()

    return [
        {
            "id": int(r[0]),
            "product_name": r[1],
            "status": r[2],
            "location": r[3],
            "pd_disc": r[4],
            "pc_disc": r[5],
        }
        for r in rows
    ]


def dismiss_inventory_alert(alert_id: int) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
                delete from {table}
                where id = %s
                returning id
                """
            ).format(table=sql.Identifier(ALERT_TABLE)),
            (alert_id,),
        )
        deleted = cur.fetchone()

    return deleted is not None


# ----------------------------
# COMMAND LOGGING
# ----------------------------
def get_login_lockout_until(username: str) -> Optional[float]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select payload
            from command_log
            where command_type = 'admin_login_lockout'
              and payload->>'username' = %s
            order by created_at desc
            limit 1
            """,
            (username,),
        )
        row = cur.fetchone()

    if not row:
        return None

    payload = _as_obj(row[0]) or {}
    lockout_until = payload.get("lockout_until")
    try:
        return float(lockout_until)
    except (TypeError, ValueError):
        return None


def get_recent_login_results(username: str, limit: int = 3) -> List[str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select result
            from command_log
            where command_type in ('admin_login_success', 'admin_login_failure')
              and payload->>'username' = %s
            order by created_at desc
            limit %s
            """,
            (username, limit),
        )
        rows = cur.fetchall()

    return [r[0] for r in rows if r and r[0]]


def log_command(source: str, command_type: str, payload: Dict[str, Any], status: str = "sent", result: Optional[str] = None, error_message: Optional[str] = None) -> int:
    """
    Inserts into command_log and returns inserted row id.
    payload is stored as jsonb.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into command_log (source, command_type, payload, status, result, error_message)
            values (%s, %s, %s::jsonb, %s, %s, %s)
            returning id
            """,
            (source, command_type, json.dumps(payload), status, result, error_message),
        )
        return int(cur.fetchone()[0])
    

def update_command_result(
    command_id: int,
    result: str,
    error_message: Optional[str] = None,
    status: Optional[str] = None,
) -> None:
    """
    Updates result/error_message (and optionally status) for a command_log row.
    """
    with get_conn() as conn, conn.cursor() as cur:
        if status is None:
            cur.execute(
                """
                update command_log
                set result = %s,
                    error_message = %s
                where id = %s
                """,
                (result, error_message, command_id),
            )
        else:
            cur.execute(
                """
                update command_log
                set result = %s,
                    error_message = %s,
                    status = %s
                where id = %s
                """,
                (result, error_message, status, command_id),
            )

def get_command_logs(limit: int = 50, command_type: Optional[str] = None):
    with get_conn() as conn, conn.cursor() as cur:
        if command_type:
            cur.execute(
                """
                select id, command_type, status, created_at, result
                from command_log
                where command_type = %s
                order by created_at desc
                limit %s
                """,
                (command_type, limit),
            )
        else:
            cur.execute(
                """
                select id, command_type, status, created_at, result
                from command_log
                order by created_at desc
                limit %s
                """,
                (limit,),
            )
        rows = cur.fetchall()

    return [
        {
            "id": r[0],
            "command": r[1],
            "status": r[2],
            "timestamp": r[3].isoformat() if r[3] else None,
            "result": r[4],
        }
        for r in rows
    ]


def get_all_command_types() -> List[str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select distinct command_type
            from command_log
            where command_type is not null and command_type <> ''
            order by command_type asc
            """
        )
        rows = cur.fetchall()

    return [r[0] for r in rows if r and r[0]]


def get_latest_command_by_type(command_type: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select id, command_type, status, created_at, result, error_message
            from command_log
            where command_type = %s
            order by created_at desc
            limit 1
            """,
            (command_type,),
        )
        row = cur.fetchone()

    if not row:
        return None

    return {
        "id": row[0],
        "command": row[1],
        "status": row[2],
        "timestamp": row[3].isoformat() if row[3] else None,
        "result": row[4],
        "error_message": row[5],
    }


def get_latest_customer_activity(window_seconds: int = 45) -> Optional[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select command_type, created_at
            from command_log
            where (
                source = 'user_ui'
                or source like 'user_ui:%'
            )
              and created_at >= (now() - (%s * interval '1 second'))
            order by created_at desc
            limit 1
            """,
            (window_seconds,),
        )
        row = cur.fetchone()

    if not row:
        return None

    return {
        "command": row[0],
        "timestamp": row[1].isoformat() if row[1] else None,
    }


def get_latest_customer_navigation() -> Optional[Dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select id, command_type, status, created_at, result, error_message
            from command_log
            where command_type = 'navigate'
              and (
                    source = 'user_ui'
                    or source like 'user_ui:%'
              )
            order by created_at desc
            limit 1
            """
        )
        row = cur.fetchone()

    if not row:
        return None

    return {
        "id": row[0],
        "command": row[1],
        "status": row[2],
        "timestamp": row[3].isoformat() if row[3] else None,
        "result": row[4],
        "error_message": row[5],
    }


def update_latest_command_by_type(
    command_type: str,
    result: str,
    error_message: Optional[str] = None,
    status: Optional[str] = None,
) -> bool:
    latest = get_latest_command_by_type(command_type)
    if not latest:
        return False

    update_command_result(
        command_id=int(latest["id"]),
        result=result,
        error_message=error_message,
        status=status,
    )
    return True


'''
# ----------------------------
# HAZARDS
# ----------------------------
def log_hazard(hazard_type: str, details: Dict[str, Any], status: str = "new") -> None:
    """
    Inserts into hazard_events.
    Table columns: hazard_type (text), details (jsonb), status (text)
    created_at should be handled by DB default.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into hazard_events (hazard_type, details, status)
            values (%s, %s::jsonb, %s)
            """,
            (hazard_type, json.dumps(details), status),
        )

        '''
