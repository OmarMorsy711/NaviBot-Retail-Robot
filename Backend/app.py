from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
from auth import verify_login
import os
import time
import db

# Use the merged OCR+override controller implementation.
from Navibot_controller_new import NaviBotController

app = FastAPI(title="NaviBot Backend")
APP_LAUNCHED_AT = datetime.now(timezone.utc).replace(tzinfo=None)

# CORS: allow your frontend to call the API during dev.
# Tighten origins later (replace "*" with your frontend URL).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

controller = None

def get_controller():
    global controller
    if controller is None:
        try:
            controller = NaviBotController()
        except Exception as e:
            # keep controller as None if sim is not available
            controller = None
            raise HTTPException(status_code=503, detail=f"CoppeliaSim not available: {e}")
    return controller


def mark_latest_navigation_overridden(reason: str) -> None:
    latest_nav = db.get_latest_command_by_type("navigate")
    if not latest_nav:
        return

    if latest_nav.get("result") in {"success", "failed", "overridden"}:
        return

    db.update_latest_command_by_type(
        command_type="navigate",
        result="overridden",
        error_message=reason,
        status="received",
    )


def _cmd_time(command: Optional[dict]) -> Optional[datetime]:
    if not command:
        return None
    ts = command.get("timestamp")
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except Exception:
        return None


def _latest_by_type(command_types: list[str]) -> Optional[dict]:
    latest = None
    latest_time = None
    for command_type in command_types:
        candidate = db.get_latest_command_by_type(command_type)
        candidate_time = _cmd_time(candidate)
        if candidate and candidate_time and (latest_time is None or candidate_time > latest_time):
            latest = candidate
            latest_time = candidate_time
    return latest


def _is_runtime_command(command: Optional[dict]) -> bool:
    command_time = _cmd_time(command)
    return command_time is not None and command_time >= APP_LAUNCHED_AT


def _has_runtime_commands() -> bool:
    latest_logs = db.get_command_logs(limit=1)
    if not latest_logs:
        return False

    latest_time = _cmd_time(latest_logs[0])
    if latest_time is None:
        return False

    return latest_time >= APP_LAUNCHED_AT


class NavigateRequest(BaseModel):
    product_name: str
    source: str = "user_ui"  # user_ui or manager_dashboard

class HazardRequest(BaseModel):
    hazard_type: str
    details: dict
    source: str = "robot"
    status: str = "new"


class DismissAlertRequest(BaseModel):
    source: str = "employee_dashboard"

'''
@app.on_event("startup")
def startup():
    get_controller()
'''

@app.get("/api/health")
def health():
    return {"ok": True}

@app.get("/api/db-check")
def db_check():
    try:
        with db.get_conn():
            return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB connection failed: {e}")

@app.get("/api/config")
def get_config():
    url = os.getenv("SUPABASE_URL")
    anon = os.getenv("SUPABASE_ANON_KEY")  # add this to your .env

    if not url or not anon:
        raise HTTPException(status_code=500, detail="Missing SUPABASE_URL or SUPABASE_ANON_KEY")

    return {
        "supabase_url": url,
        "supabase_anon_key": anon,
    }

@app.get("/api/products/search")
def products_search(q: str):
    products = db.search_products(q)
    try:
        db.log_command(
            source="user_ui",
            command_type="user_product_search",
            payload={"query": q, "result_count": len(products)},
            status="received",
            result="success",
            error_message=None,
        )
    except Exception:
        pass
    return {"ok": True, "products": products}


@app.get("/api/products/{sku}/coordinates")
def product_coordinates(sku: str):
    """
    Returns x, y coordinates for a product.
    Used by cart navigation for TSP route optimization.
    Does NOT trigger any robot movement or logging spam.
    """
    try:
        # Query the store_planogram table directly for coordinates
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.x, p.y, p.aisle, p.shelf, c.name
                FROM store_planogram p
                JOIN product_catalog c ON c.sku = p.sku
                WHERE p.sku = %s
                LIMIT 1
                """,
                (str(sku),)
            )
            row = cur.fetchone()
            
            if not row:
                raise HTTPException(status_code=404, detail="Product coordinates not found")
            
            return {
                "ok": True,
                "sku": sku,
                "name": row[4],
                "aisle": row[2],
                "shelf": row[3],
                "x": float(row[0]),
                "y": float(row[1])
            }
            
    except HTTPException:
        raise
    except Exception as e:
        # Silent fail - don't spam terminal
        raise HTTPException(status_code=500, detail="Failed to fetch coordinates")


@app.get("/api/products/{sku}")
def product_details(sku: str):
    product = db.get_product_details_by_sku(sku)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    try:
        db.log_command(
            source="user_ui",
            command_type="user_view_product",
            payload={"sku": sku},
            status="received",
            result="success",
            error_message=None,
        )
    except Exception:
        pass
    return {"ok": True, "product": product}

@app.get("/api/scan")
def scan_result(name: str):
    product = db.get_product_basic_by_name(name)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    try:
        db.log_command(
            source="user_ui",
            command_type="user_scan_price",
            payload={"name": name},
            status="received",
            result="success",
            error_message=None,
        )
    except Exception:
        pass
    return {"ok": True, "product": product}


@app.get("/api/logs")
def command_logs(limit: int = 50, command_type: Optional[str] = None):
    logs = db.get_command_logs(limit=limit, command_type=command_type)
    return {"ok": True, "logs": logs, "count": len(logs)}


@app.get("/api/logs/command-types")
def command_log_types():
    command_types = db.get_all_command_types()
    return {"ok": True, "command_types": command_types, "count": len(command_types)}


@app.get("/api/alerts")
def get_alerts(limit: int = 100):
    alerts = db.get_inventory_alerts(limit=limit)
    return {"ok": True, "alerts": alerts, "count": len(alerts)}


@app.post("/api/alerts/{alert_id}/dismiss")
def dismiss_alert(alert_id: int, req: DismissAlertRequest):
    deleted = db.dismiss_inventory_alert(alert_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Alert not found")

    return {"ok": True, "dismissed": alert_id}


@app.get("/api/navigation/status")
def navigation_status():
    latest = db.get_latest_command_by_type("navigate")
    latest_user_cancel = db.get_latest_command_by_type("user_cancel_navigation_override")

    if not latest:
        return {"ok": True, "state": "idle", "navigation": None}

    navigate_time = _cmd_time(latest)
    user_cancel_time = _cmd_time(latest_user_cancel)
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    user_cancel_pending_or_success = (
        latest_user_cancel is not None
        and latest_user_cancel.get("status") in {"sent", "received"}
        and latest_user_cancel.get("result") in {None, "success"}
    )

    cancelled_by_user = (
        latest.get("result") == "overridden"
        and user_cancel_pending_or_success
        and (
            (
                latest_user_cancel.get("id") is not None
                and latest.get("id") is not None
                and int(latest_user_cancel.get("id")) > int(latest.get("id"))
            )
            or (
                navigate_time is not None
                and user_cancel_time is not None
                and user_cancel_time >= navigate_time
            )
            or (
                user_cancel_time is not None
                and (now_utc - user_cancel_time).total_seconds() <= 5
            )
        )
    )

    state = "in_progress"
    if latest.get("result") == "success":
        state = "success"
    elif cancelled_by_user:
        state = "cancelled_by_user"
    elif latest.get("result") == "overridden":
        state = "overridden"
    elif latest.get("result") == "failed":
        state = "failed"
    elif latest.get("status") == "received":
        state = "received"

    return {
        "ok": True,
        "state": state,
        "navigation": latest,
    }


@app.get("/api/admin/customer-in-use")
def admin_customer_in_use(window_seconds: int = 12):
    latest_nav = db.get_latest_customer_navigation()
    nav_state = "idle"
    nav_in_runtime = False

    if latest_nav:
        if latest_nav.get("result") == "success":
            nav_state = "success"
        elif latest_nav.get("result") == "overridden":
            nav_state = "overridden"
        elif latest_nav.get("result") == "failed":
            nav_state = "failed"
        elif latest_nav.get("status") == "received":
            nav_state = "received"
        else:
            nav_state = "in_progress"

        nav_in_runtime = _is_runtime_command(latest_nav)

    latest_customer_activity = None
    try:
        latest_customer_activity = db.get_latest_customer_activity(window_seconds=window_seconds)
    except Exception:
        latest_customer_activity = None

    in_use_by_navigation = nav_in_runtime and nav_state in {"in_progress", "received"}
    in_use_by_recent_activity = latest_customer_activity is not None
    in_use = in_use_by_navigation or in_use_by_recent_activity

    reason = None
    if in_use_by_navigation or in_use_by_recent_activity:
        reason = "The robot is already in use. Do you still wish to proceed?"

    return {
        "ok": True,
        "in_use": in_use,
        "reason": reason,
        "window_seconds": window_seconds,
    }


@app.get("/api/customer/screen-state")
def customer_screen_state():
    if not _has_runtime_commands():
        return {"ok": True, "mode": "normal", "reason": None}

    latest_power_off = db.get_latest_command_by_type("admin_override_power_off")
    latest_power_on = db.get_latest_command_by_type("power_on")

    power_off_time = _cmd_time(latest_power_off)
    power_on_time = _cmd_time(latest_power_on)

    latest_runtime_power_off = (
        latest_power_off
        if latest_power_off and _is_runtime_command(latest_power_off)
        else None
    )
    latest_runtime_power_on = (
        latest_power_on
        if (
            latest_power_on
            and _is_runtime_command(latest_power_on)
            and latest_power_on.get("status") in {"sent", "received"}
            and latest_power_on.get("result") in {None, "success"}
        )
        else None
    )

    power_off_pending_or_success = (
        latest_runtime_power_off
        and latest_runtime_power_off.get("status") in {"sent", "received"}
        and latest_runtime_power_off.get("result") in {None, "success"}
    )

    powered_off = False
    if power_off_pending_or_success:
        no_newer_power_on = (
            not latest_runtime_power_on
            or (
                power_off_time is not None
                and power_on_time is not None
                and power_off_time > power_on_time
            )
        )
        powered_off = no_newer_power_on

    power_on_reactivated = (
        latest_runtime_power_on
        and (
            not latest_runtime_power_off
            or (
                power_on_time is not None
                and power_off_time is not None
                and power_on_time > power_off_time
            )
        )
    )

    latest_base = _latest_by_type(["return_to_base", "admin_override_return_to_base"])
    latest_leave_base = _latest_by_type(["navigate", "deploy_zone", "user_cancel_navigation_override"])

    base_time = _cmd_time(latest_base)
    leave_time = _cmd_time(latest_leave_base)

    at_base = (
        latest_base
        and _is_runtime_command(latest_base)
        and latest_base.get("result") == "success"
        and (
            leave_time is None
            or leave_time < APP_LAUNCHED_AT
            or (base_time is not None and base_time > leave_time)
        )
    )

    heading_to_base = (
        latest_base
        and _is_runtime_command(latest_base)
        and latest_base.get("result") is None
        and (
            leave_time is None
            or leave_time < APP_LAUNCHED_AT
            or (base_time is not None and base_time >= leave_time)
        )
    )

    latest_admin_override = _latest_by_type([
        "admin_override_stop",
        "admin_override_patrol",
    ])
    latest_navigation = _latest_by_type(["navigate", "deploy_zone"])

    override_time = _cmd_time(latest_admin_override)
    navigate_time = _cmd_time(latest_navigation)

    overridden = False
    reason = "Robot is currently out of service."
    patrol_override_active = False

    if latest_admin_override and _is_runtime_command(latest_admin_override) and latest_admin_override.get("result") == "success":
        override_command = latest_admin_override.get("command_type") or latest_admin_override.get("command")

        navigation_recently_overridden = (
            latest_navigation is not None
            and latest_navigation.get("result") == "overridden"
            and (
                (
                    latest_admin_override.get("id") is not None
                    and latest_navigation.get("id") is not None
                    and int(latest_admin_override.get("id")) >= int(latest_navigation.get("id"))
                )
                or (
                    override_time is not None
                    and navigate_time is not None
                    and override_time >= navigate_time
                )
            )
        )

        if override_command == "admin_override_patrol":
            patrol_override_active = True
        elif navigation_recently_overridden:
            overridden = True
            reason = latest_admin_override.get("error_message") or reason

    if powered_off:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if power_off_time is not None and (now - power_off_time).total_seconds() < 0.5:
            return {"ok": True, "mode": "out_of_service", "reason": "Robot is powering off."}
        return {"ok": True, "mode": "black", "reason": "Robot is currently unavailable."}

    if heading_to_base:
        return {"ok": True, "mode": "out_of_service", "reason": "Robot is returning to base."}

    if patrol_override_active:
        return {"ok": True, "mode": "normal", "reason": None}

    if at_base:
        return {"ok": True, "mode": "charging", "reason": "Charging in progress."}

    if overridden:
        return {"ok": True, "mode": "out_of_service", "reason": reason}

    if power_on_reactivated:
        return {"ok": True, "mode": "normal", "reason": None}

    return {"ok": True, "mode": "normal", "reason": None}

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/api/login")
def login(req: LoginRequest):
    username = req.username.strip()
    lockout_seconds = 60
    max_attempts = 3

    lockout_until = db.get_login_lockout_until(username)
    now = time.time()
    if lockout_until and now < lockout_until:
        remaining = int(lockout_until - now)
        db.log_command(
            source="admin_portal",
            command_type="admin_login_blocked",
            payload={"username": username, "lockout_until": lockout_until},
            status="received",
            result="failed",
            error_message="Multiple Failed log in attempts",
        )
        raise HTTPException(
            status_code=429,
            detail={
                "message": "Please try again later",
                "lockout_until": lockout_until,
                "retry_after": remaining,
            },
        )

    ok = verify_login(username, req.password)
    if ok:
        db.log_command(
            source="admin_portal",
            command_type="admin_login_success",
            payload={"username": username},
            status="received",
            result="success",
            error_message=None,
        )
        return {"ok": True, "username": username}

    db.log_command(
        source="admin_portal",
        command_type="admin_login_failure",
        payload={"username": username},
        status="received",
        result="failed",
        error_message="invalid credentials",
    )

    recent = db.get_recent_login_results(username, limit=max_attempts)
    if len(recent) == max_attempts and all(result == "failed" for result in recent):
        lockout_until = now + lockout_seconds
        db.log_command(
            source="admin_portal",
            command_type="admin_login_lockout",
            payload={"username": username, "lockout_until": lockout_until},
            status="received",
            result="failed",
            error_message="Multiple Failed log in attempts",
        )
        raise HTTPException(
            status_code=429,
            detail={
                "message": "Please try again later",
                "lockout_until": lockout_until,
                "retry_after": lockout_seconds,
            },
        )

    raise HTTPException(status_code=401, detail="Invalid username or password")

class DeployZoneRequest(BaseModel):
    zone: str
    source: str = "manager_dashboard"

@app.post("/api/deploy_zone")
def deploy_zone(req: DeployZoneRequest):
    command_id = db.log_command(
        source=req.source,
        command_type="deploy_zone",
        payload={"zone": req.zone},
        status="sent",
        result=None,
        error_message=None,
    )

    try:
        ctrl = get_controller()
        target = ctrl.get_aisle_target(req.zone)
        if not target:
            raise HTTPException(status_code=404, detail="Zone not found")

        ctrl.stop_event.clear()
        nav_result = ctrl.navigate_to_target(target, f"Zone {req.zone}")

        if nav_result == "busy":
            db.update_command_result(
                command_id,
                "failed",
                "Another navigation command is already active.",
                status="received",
            )
            raise HTTPException(status_code=409, detail="Another navigation is already in progress")

        if nav_result == "aborted":
            db.update_command_result(
                command_id,
                "overridden",
                "Deploy-zone navigation was interrupted by an override action.",
                status="received",
            )
            raise HTTPException(status_code=409, detail="Deploy-zone navigation was overridden")

        if nav_result != "success":
            db.update_command_result(
                command_id,
                "failed",
                "Deploy-zone navigation failed to reach destination.",
                status="received",
            )
            raise HTTPException(status_code=500, detail="Deploy-zone navigation failed to reach destination")

        db.update_command_result(command_id, "success", None, status="received")
        return {"ok": True, "zone": req.zone}
    except HTTPException as he:
        if he.status_code != 409:
            db.update_command_result(command_id, "failed", str(he.detail), status="received")
        raise he
    except Exception as e:
        db.update_command_result(command_id, "failed", str(e), status="received")
        raise HTTPException(status_code=500, detail=str(e))

class AnalyzeVisionRequest(BaseModel):
    image: str  # Base64 encoded image

@app.post("/api/analyze-vision")
def analyze_vision(req: AnalyzeVisionRequest):
    # TODO: Implement actual OpenCV analysis
    # For now, return a demo/stub response
    # In production, this would decode the base64 image and run object detection
    return {
        "ok": True,
        "sku": "DEMO-001",
        "name": "Demo Product",
        "confidence": 0.95
    }

class ReturnToBaseRequest(BaseModel):
    source: str = "user_ui"

class UserCancelOverrideRequest(BaseModel):
    source: str = "user_ui"

class StopRequest(BaseModel):
    source: str = "manager_dashboard"

class AdminOverrideRequest(BaseModel):
    source: str = "manager_dashboard"

class PowerOffRequest(BaseModel):
    source: str = "manager_dashboard"

class PowerOnRequest(BaseModel):
    source: str = "manager_dashboard"

@app.post("/api/return_to_base")
def return_to_base(req: ReturnToBaseRequest):
    command_id = db.log_command(
        source=req.source,
        command_type="return_to_base",
        payload={},
        status="sent",
        result=None,
        error_message=None,
    )

    try:
        ctrl = get_controller()
        ctrl.return_to_base()
        db.update_command_result(command_id, "success", None, status="received")
        return {"ok": True}
    except Exception as e:
        db.update_command_result(command_id, "failed", str(e), status="received")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/user/override/cancel_navigation")
def user_cancel_navigation(req: UserCancelOverrideRequest):
    command_id = db.log_command(
        source=req.source,
        command_type="user_cancel_navigation_override",
        payload={"mode": "STOP_THEN_PATROL"},
        status="sent",
        result=None,
        error_message=None,
    )

    try:
        ctrl = get_controller()
        ctrl.override_and_execute("cancel_navigation")
        mark_latest_navigation_overridden("Navigation was overridden by user cancel request.")
        db.update_command_result(command_id, "success", None, status="received")
        return {
            "ok": True,
            "overridden": True,
            "message": "User cancel override executed.",
        }
    except Exception as e:
        error_text = f"User cancel override failed. Possible CoppeliaSim/controller issue: {e}"
        db.update_command_result(command_id, "failed", error_text, status="received")
        raise HTTPException(status_code=500, detail=error_text)

@app.post("/api/admin/override/stop")
def admin_override_stop(req: AdminOverrideRequest):
    command_id = db.log_command(
        source=req.source,
        command_type="admin_override_stop",
        payload={"override": True},
        status="sent",
        result=None,
        error_message=None,
    )

    try:
        ctrl = get_controller()
        ctrl.override_and_execute("stop")
        mark_latest_navigation_overridden("Navigation was overridden by admin STOP.")
        db.update_command_result(command_id, "success", None, status="received")
        return {"ok": True, "message": "Admin override stop executed."}
    except Exception as e:
        error_text = f"Admin override stop failed: {e}"
        db.update_command_result(command_id, "failed", error_text, status="received")
        raise HTTPException(status_code=500, detail=error_text)

@app.post("/api/admin/override/patrol")
def admin_override_patrol(req: AdminOverrideRequest):
    command_id = db.log_command(
        source=req.source,
        command_type="admin_override_patrol",
        payload={"override": True, "mode": "STOP_THEN_PATROL"},
        status="sent",
        result=None,
        error_message=None,
    )

    try:
        ctrl = get_controller()
        ctrl.override_and_execute("cancel_navigation")
        mark_latest_navigation_overridden("Navigation was overridden by admin PATROL MODE.")
        db.update_command_result(command_id, "success", None, status="received")
        return {"ok": True, "message": "Admin override patrol executed (STOP -> PATROL)."}
    except Exception as e:
        error_text = f"Admin override patrol failed: {e}"
        db.update_command_result(command_id, "failed", error_text, status="received")
        raise HTTPException(status_code=500, detail=error_text)

@app.post("/api/admin/override/power_off")
def admin_override_power_off(req: AdminOverrideRequest):
    command_id = db.log_command(
        source=req.source,
        command_type="admin_override_power_off",
        payload={"override": True},
        status="sent",
        result=None,
        error_message=None,
    )

    # Mark accepted immediately so customer screen can transition instantly to shutdown.
    db.update_command_result(command_id, "success", None, status="received")

    try:
        ctrl = get_controller()
        ctrl.override_and_execute("power_off")
        mark_latest_navigation_overridden("Navigation was overridden by admin POWER OFF.")
        return {"ok": True, "message": "Admin override power off executed."}
    except Exception as e:
        error_text = f"Admin override power off failed: {e}"
        db.update_command_result(command_id, "failed", error_text, status="received")
        raise HTTPException(status_code=500, detail=error_text)

@app.post("/api/admin/override/return_to_base")
def admin_override_return_to_base(req: AdminOverrideRequest):
    command_id = db.log_command(
        source=req.source,
        command_type="admin_override_return_to_base",
        payload={"override": True},
        status="sent",
        result=None,
        error_message=None,
    )

    try:
        ctrl = get_controller()
        ctrl.override_and_execute("return_to_base")
        mark_latest_navigation_overridden("Navigation was overridden by admin RETURN TO BASE.")
        db.update_command_result(command_id, "success", None, status="received")
        return {"ok": True, "message": "Admin override return-to-base executed."}
    except Exception as e:
        error_text = f"Admin override return-to-base failed: {e}"
        db.update_command_result(command_id, "failed", error_text, status="received")
        raise HTTPException(status_code=500, detail=error_text)


@app.post("/api/admin/override/reset_references")
def admin_override_reset_references(req: AdminOverrideRequest):
    command_id = db.log_command(
        source=req.source,
        command_type="admin_override_reset_references",
        payload={"override": True, "target": "refrences"},
        status="sent",
        result=None,
        error_message=None,
    )

    try:
        ctrl = get_controller()
        cleared = ctrl.clear_inventory_references()
        if not cleared:
            error_text = "Failed to clear Supabase references."
            db.update_command_result(command_id, "failed", error_text, status="received")
            raise HTTPException(status_code=500, detail=error_text)

        db.update_command_result(command_id, "success", None, status="received")
        return {
            "ok": True,
            "message": "Supabase references cleared. Next patrol will gather references.",
        }
    except HTTPException:
        raise
    except Exception as e:
        error_text = f"Admin reset references failed: {e}"
        db.update_command_result(command_id, "failed", error_text, status="received")
        raise HTTPException(status_code=500, detail=error_text)

@app.post("/api/power_off")
def power_off(req: PowerOffRequest):
    command_id = db.log_command(
        source=req.source,
        command_type="power_off",
        payload={},
        status="sent",
        result=None,
        error_message=None,
    )

    try:
        ctrl = get_controller()
        ctrl.turn_off_robot()
        db.update_command_result(command_id, "success", None, status="received")
        return {"ok": True}
    except Exception as e:
        db.update_command_result(command_id, "failed", str(e), status="received")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/stop")
def stop_robot(req: StopRequest):
    command_id = db.log_command(
        source=req.source,
        command_type="manual_stop",
        payload={},
        status="sent",
        result=None,
        error_message=None,
    )

    try:
        ctrl = get_controller()
        ctrl.override_and_execute("stop")
        mark_latest_navigation_overridden("Navigation was overridden by manual stop request.")
        db.update_command_result(command_id, "success", None, status="received")
        return {"ok": True, "message": "Robot stop override executed."}
    except Exception as e:
        db.update_command_result(command_id, "failed", str(e), status="received")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/power_on")
def power_on(req: PowerOnRequest):
    command_id = db.log_command(
        source=req.source,
        command_type="power_on",
        payload={},
        status="sent",
        result=None,
        error_message=None,
    )

    # Mark accepted immediately so UI state can switch without waiting on controller timing.
    db.update_command_result(command_id, "success", None, status="received")

    try:
        ctrl = get_controller()
        ctrl.turn_on_robot()
        return {"ok": True}
    except Exception as e:
        db.update_command_result(command_id, "failed", str(e), status="received")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/navigate")
def navigate(req: NavigateRequest):
    # 1) get product
    try:
        product = db.get_product_by_name(req.product_name)
    except Exception as e:
        # log DB failure attempt
        db.log_command(
            source=req.source,
            command_type="navigate",
            payload={"product_name": req.product_name},
            status="received",
            result="failed",
            error_message=f"Database error: {e}",
        )
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    # 2) product not found -> log failed
    if not product:
        db.log_command(
            source=req.source,
            command_type="navigate",
            payload={"product_name": req.product_name},
            status="received",
            result="failed",
            error_message="Product not found",
        )
        raise HTTPException(status_code=404, detail="Product not found")

    # 3) log command as sent (pending result)
    try:
        command_id = db.log_command(
            source=req.source,
            command_type="navigate",
            payload={"product_name": req.product_name, "product": product},
            status="sent",
            result=None,
            error_message=None,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to log command: {e}")

    # 4) try robot navigation
    try:
        ctrl = get_controller()  # may raise HTTPException(503)

        # Customer product navigation should preempt patrol mode.
        if req.source == "user_ui" and getattr(ctrl, "mode", None) == "PATROL":
            ctrl.override_active_navigation()

        target = ctrl.get_product_coords(req.product_name)
        if not target:
            db.update_command_result(command_id, result="failed", error_message="Product not found", status="received")
            raise HTTPException(status_code=404, detail="Product not found")

        ctrl.stop_event.clear()
        nav_result = ctrl.navigate_to_target(target, req.product_name)

        if nav_result == "busy":
            db.update_command_result(
                command_id,
                result="failed",
                error_message="Another navigation command is already active.",
                status="received",
            )
            raise HTTPException(status_code=409, detail="Another navigation is already in progress")

        if nav_result == "aborted":
            db.update_command_result(
                command_id,
                result="overridden",
                error_message="Navigation was interrupted by an override action.",
                status="received",
            )
            raise HTTPException(status_code=409, detail="Navigation was overridden")

        if nav_result != "success":
            db.update_command_result(
                command_id,
                result="failed",
                error_message="Navigation failed to reach destination.",
                status="received",
            )
            raise HTTPException(status_code=500, detail="Navigation failed to reach destination")

        # success
        db.update_command_result(command_id, result="success", error_message=None, status="received")
        return {"ok": True, "product": product}

    except HTTPException as he:
        # Keep overridden state when navigation was intentionally interrupted.
        if he.status_code != 409:
            db.update_command_result(command_id, result="failed", error_message=str(he.detail), status="received")
        raise he

    except Exception as e:
        db.update_command_result(command_id, result="failed", error_message=str(e), status="received")
        raise HTTPException(status_code=500, detail=f"Robot navigation failed: {e}")
