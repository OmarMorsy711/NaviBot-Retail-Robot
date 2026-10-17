import os, json
import psycopg
from dotenv import load_dotenv

load_dotenv()
DB_URL = os.environ["DATABASE_URL"]

PLANOGRAM_PATH = "store_planogram.json"
LAYOUT_PATH = "store_layout.json"
PRODUCT_DETAILS_PATH = "products.json"

def main():
    # Load JSON
    with open(PLANOGRAM_PATH, "r", encoding="utf-8") as f:
        planogram = json.load(f)
    with open(LAYOUT_PATH, "r", encoding="utf-8") as f:
        layout = json.load(f)
    with open(PRODUCT_DETAILS_PATH, "r", encoding="utf-8") as f:
        catalog_source = json.load(f)


    store_id = planogram["store_meta"]["store_id"]
    version = planogram["store_meta"]["version"]

    if isinstance(catalog_source, dict):
        catalog_items = (
            catalog_source.get("products")
            or catalog_source.get("products")
            or catalog_source.get("items")
        )
    else:
        catalog_items = catalog_source

    if not catalog_items:
        raise ValueError("Product catalog JSON is empty or missing expected list")

    catalog_rows = []
    catalog_by_sku = {}
    catalog_by_name = {}
    for item in catalog_items:
        sku = str(item["sku"]).strip()
        name = item["name"].strip()
        price = item["price"]
        category = item["category"]
        image_path = item.get("image_path") or f"products/{sku}.png"
        catalog_rows.append((sku, name, float(price), category, image_path))
        catalog_by_sku[sku] = item
        catalog_by_name[name.lower()] = item

    # Planogram rows
    products_rows = []
    for p in planogram["products"]:
        sku = str(p["sku"]).strip()
        name = p["name"].strip()
        if sku not in catalog_by_sku and name.lower() not in catalog_by_name:
            raise ValueError(f"Missing products entry for '{name}' (sku={sku})")
        loc = p["location"]
        products_rows.append((
            store_id, version,
            p["sku"], p["name"], p["aisle"], p["shelf"],
            float(loc["x"]), float(loc["y"]), float(loc["z"])
        ))

    # Layout rows (static obstacles)
    obstacles_rows = []
    for o in layout.get("static_obstacles", []):
        obstacles_rows.append((
            o["id"],
            o.get("description"),
            "static_obstacles",
            json.dumps(o["corners"]),
        ))


    # Robot base row (single)
    robot_base = layout.get("robot_base")

    # Insert / upsert
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:

            # Upsert products FIRST (required for FK from store_planogram.sku)
            cur.executemany(
                """
                insert into products (sku, name, price, category, image_path)
                values (%s, %s, %s, %s, %s)
                on conflict (sku) do update set
                    name       = excluded.name,
                    price      = excluded.price,
                    category   = excluded.category,
                    image_path = excluded.image_path
                """,
                catalog_rows
            )

            # Upsert planogram
            cur.executemany(
                """
                insert into store_planogram
                (store_id, version, sku, name, aisle, shelf, x, y, z)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (sku) do update set
                    store_id = excluded.store_id,
                    version  = excluded.version,
                    name     = excluded.name,
                    aisle    = excluded.aisle,
                    shelf    = excluded.shelf,
                    x        = excluded.x,
                    y        = excluded.y,
                    z        = excluded.z
                """,
                products_rows
            )

            # Upsert static obstacles (Route B table store_layout)
            cur.executemany(
                """
                insert into store_layout (obstacle_id, description, type, corners)
                values (%s, %s, %s, %s::jsonb)
                on conflict (obstacle_id) do update set
                    description = excluded.description,
                    type        = excluded.type,
                    corners     = excluded.corners
                """,
                obstacles_rows
            )

            # Upsert robot base
            if robot_base is not None:
                cur.execute(
                    """
                    insert into store_layout (obstacle_id, description, type, position)
                    values (%s, %s, %s, %s::jsonb)
                    on conflict (obstacle_id) do update set
                        description = excluded.description,
                        type        = excluded.type,
                        position    = excluded.position
                    """,
                    (
                        "robot_base",
                        robot_base.get("description", "Robot base"),
                        "robot_base",
                        json.dumps({"x": float(robot_base["x"]), "y": float(robot_base["y"])}),
                    )
                )

        conn.commit()

    print(f"Seeded store_planogram: {len(products_rows)} rows")
    print(f"Seeded store_layout (static_obstacles): {len(obstacles_rows)} rows")
    print(f"Seeded products: {len(catalog_rows)} rows")
    print("Seeded store_layout (robot_base): 1 row" if robot_base is not None else "No robot_base found in layout JSON")


if __name__ == "__main__":
    main()
