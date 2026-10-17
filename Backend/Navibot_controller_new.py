import time
import sys
import math
import heapq
import threading
import os
import cv2  # pip install opencv-python
import numpy as np  # pip install numpy
import easyocr  # pip install easyocr
import re
import warnings
import db
from pathlib import Path


# Suppress annoying non-fatal torch warnings
warnings.filterwarnings("ignore", category=UserWarning, module="torch")
sys.path.append(str(Path(__file__).resolve().parent / "src"))
from coppeliasim_zmqremoteapi_client import RemoteAPIClient


class NaviBotController:
    def __init__(self):
        print("Connecting to CoppeliaSim...")
        self.client = RemoteAPIClient()
        self.sim = self.client.getObject('sim')
        self.client.setStepping(True)

        # ---------------- BASIC STATE ----------------
        self.mode = "IDLE"  # IDLE | ACTIVE | PATROL
        self.robot_name = 'NaviBot'

        # ---------------- NAVIGATION PARAMETERS ----------------
        self.STOPPING_DISTANCE = 0.12
        self.SLOWDOWN_RADIUS = 0.6
        self.SAFE_OFFSET = 0.6

        # ---------------- MAP PARAMETERS ----------------
        self.grid_resolution = 0.25
        self.inflation_radius = 1
        self.obstacles = set()

        # ---------------- STORE CONFIG ----------------
        self.store_center = (0.0, 0.0)
        self.home_base = None
        self.shelves_data = []

        # ---------------- THREAD CONTROL ----------------
        self.stop_event = threading.Event()
        self.nav_thread = None
        self.navigation_running = threading.Event()
        self.navigation_lock = threading.Lock()
        self._generation_lock = threading.Lock()
        self._navigation_generation = 0

        # ---------------- INTIALIZE SHELF REGIONS ----------------
        self.shelf_regions = []
        self.shelf_levels = [1.45, 0.95, 0.45]

        # ---------------- OCR SETUP ----------------
        print("Loading OCR Model (EasyOCR)... This may take a moment on the first run.")
        # gpu=False ensures maximum compatibility across all computers
        self.ocr_reader = easyocr.Reader(['en'], gpu=False)

        # ---------------- VISION SENSOR & REFERENCES ----------------
        try:
            self.vision_sensor = self.sim.getObject(f'/{self.robot_name}/visionSensor')
            print("Vision Sensor Found.")
        except:
            raise Exception("Vision sensor not found.")

        # Setup Supabase references folder (configurable, defaults to your current folder name)
        self.reference_prefix = os.getenv("REFERENCE_PREFIX", "refrences").strip("/")
        self.alert_prefix = f"{self.reference_prefix}/alerts"
        self.is_reference_run = not self._has_supabase_references()

        if self.is_reference_run:
            print(f"[*] Supabase references are empty under '{self.reference_prefix}'. Next patrol will be a REFERENCE GATHERING run.")
        else:
            print(f"[*] Supabase references found under '{self.reference_prefix}'. Next patrol will check for DISCREPANCIES.")

        # ---------------- BUILD PATROL STRUCTURE ----------------
        self.patrol_groups = []

        print("Connected. Finding Object Handles...")
        try:
            self.robot_base = self.sim.getObject(f'/{self.robot_name}')
            print(f"Found Robot Base: {self.robot_name}")

            self.motor_left = self.find_object_handle(['leftMotor'], self.robot_name)
            self.motor_right = self.find_object_handle(['rightMotor'], self.robot_name)

            if self.motor_left == -1 or self.motor_right == -1:
                raise Exception("Could not find motors")

            self.sensors = []
            print("Scanning for Ultrasonic Sensors...")
            for h in self.sim.getObjectsInTree(self.robot_base):
                try:
                    if "ultrasonic" in self.sim.getObjectAlias(h).lower():
                        self.sensors.append(h)
                except:
                    pass
            print(f"Sensors Configured: Found {len(self.sensors)} active sensors.")

            self.load_static_obstacles_from_db()
            self.product_db = self.load_planogram_from_db()
            self.shelf_regions = self.load_shelf_regions()
            self.patrol_groups = self.build_patrol_points()

            print("System Ready.")
        except Exception as e:
            print(f"\nCRITICAL ERROR: {e}")
            sys.exit()

    def find_object_handle(self, names, parent):
        for n in names:
            try:
                return self.sim.getObject(f'/{parent}/{n}')
            except:
                pass
        return -1

    def _reference_object_path(self, checkpoint_id: str) -> str:
        return f"{self.reference_prefix}/{checkpoint_id}.png"

    def _alert_object_path(self, checkpoint_id: str) -> str:
        return f"{self.alert_prefix}/ALERT_{checkpoint_id}.png"

    def _has_supabase_references(self) -> bool:
        files = db.storage_list_files(self.reference_prefix)
        return any(p.lower().endswith(".png") for p in files)

    def _save_reference_to_supabase(self, checkpoint_id: str, frame_bgr) -> bool:
        ok, encoded = cv2.imencode('.png', frame_bgr)
        if not ok:
            return False
        path = self._reference_object_path(checkpoint_id)
        saved = db.storage_upload_bytes(path, encoded.tobytes(), content_type="image/png", upsert=True)
        if not saved:
            print(f"Supabase reference upload failed for path: {path}")
        return saved

    def _load_reference_from_supabase(self, checkpoint_id: str):
        payload = db.storage_download_bytes(self._reference_object_path(checkpoint_id))
        if not payload:
            return None

        np_buffer = np.frombuffer(payload, dtype=np.uint8)
        if np_buffer.size == 0:
            return None

        return cv2.imdecode(np_buffer, cv2.IMREAD_COLOR)

    def _save_alert_to_supabase(self, checkpoint_id: str, alert_img) -> bool:
        ok, encoded = cv2.imencode('.png', alert_img)
        if not ok:
            return False
        return db.storage_upload_bytes(self._alert_object_path(checkpoint_id), encoded.tobytes(), content_type="image/png", upsert=True)

    def _clear_supabase_inventory_refs(self) -> bool:
        ref_files = db.storage_list_files(self.reference_prefix)
        alert_files = db.storage_list_files(self.alert_prefix)
        return db.storage_delete_files(ref_files + alert_files)

    def clear_inventory_references(self) -> bool:
        cleared = self._clear_supabase_inventory_refs()
        if cleared:
            self.is_reference_run = True
        return cleared

    def load_planogram_from_db(self):
        try:
            products = db.get_planogram_products_for_vision()
            print(f"Planogram Loaded from DB: {len(products)} products found.")
            return products
        except Exception as e:
            print(f"Error loading planogram from DB: {e}")
            return []

    def load_static_obstacles_from_db(self):
        print("Loading Static Obstacles from DB store_layout...")

        base = db.get_robot_base()
        if base:
            self.home_base = (base["x"], base["y"])
            print(f"Home Base Loaded at: {self.home_base}")
        else:
            self.home_base = None
            print("No robot_base found in DB.")

        shelves = db.get_static_obstacles()
        self.shelves_data = shelves

        shelf_count = 0
        for shelf in shelves:
            corners = shelf.get("corners", [])
            if not corners:
                continue

            xs = [p["x"] for p in corners]
            ys = [p["y"] for p in corners]
            min_gx, min_gy = self.world_to_grid(min(xs), min(ys))
            max_gx, max_gy = self.world_to_grid(max(xs), max(ys))

            for gx in range(min_gx, max_gx + 1):
                for gy in range(min_gy, max_gy + 1):
                    self.obstacles.add((gx, gy))
                    for ox in range(-self.inflation_radius, self.inflation_radius + 1):
                        for oy in range(-self.inflation_radius, self.inflation_radius + 1):
                            self.obstacles.add((gx + ox, gy + oy))

            shelf_count += 1

        print(f"Static Map Built: {shelf_count} obstacles loaded. {len(self.obstacles)} cells blocked.")

    def world_to_grid(self, x, y):
        return int(round(x / self.grid_resolution)), int(round(y / self.grid_resolution))

    def grid_to_world(self, gx, gy):
        return gx * self.grid_resolution, gy * self.grid_resolution

    def set_velocity(self, l, r):
        self.sim.setJointTargetVelocity(self.motor_left, l)
        self.sim.setJointTargetVelocity(self.motor_right, r)

    def safe_stop_motion(self):
        try:
            self.set_velocity(0, 0)
            self.client.step()
        except Exception as e:
            print(f"Warning: safe stop motion failed: {e}")

    def get_robot_position(self):
        p = self.sim.getObjectPosition(self.robot_base, -1)
        return p[0], p[1]

    def get_robot_orientation(self):
        return self.sim.getObjectOrientation(self.robot_base, -1)[2]

    def update_map_from_sensors(self):
        if not self.sensors:
            return False

        updated = False
        front_indices = [2, 3, 4, 5, 6, 7]

        for i in front_indices:
            if i < len(self.sensors):
                res, dist, point, _, _ = self.sim.readProximitySensor(self.sensors[i])
                if res > 0 and dist < 1.5:
                    mat = self.sim.getObjectMatrix(self.sensors[i], -1)
                    world_point = self.sim.multiplyVector(mat, point)
                    gx, gy = self.world_to_grid(world_point[0], world_point[1])

                    for ox in range(-self.inflation_radius, self.inflation_radius + 1):
                        for oy in range(-self.inflation_radius, self.inflation_radius + 1):
                            cell = (gx + ox, gy + oy)
                            if cell not in self.obstacles:
                                self.obstacles.add(cell)
                                updated = True
        return updated

    def stop_and_wait(self, t=1.0):
        self.set_velocity(0, 0)
        start = time.time()
        while time.time() - start < t:
            self.client.step()

    def _current_generation(self):
        with self._generation_lock:
            return self._navigation_generation

    def _bump_generation(self):
        with self._generation_lock:
            self._navigation_generation += 1
            return self._navigation_generation

    # ---------------- PRODUCT TARGET FIX ----------------
    def get_product_coords(self, product_name):
        for item in self.product_db:
            if item['name'].lower() == product_name.lower():
                px, py = item['location']['x'], item['location']['y']

                for radius in [0.4, 0.6, 0.8, 1.0]:
                    for angle in range(0, 360, 15):
                        ax = px + radius * math.cos(math.radians(angle))
                        ay = py + radius * math.sin(math.radians(angle))
                        gx, gy = self.world_to_grid(ax, ay)

                        if (gx, gy) not in self.obstacles:
                            return ax, ay

                print("No reachable approach point found for product.")
                return None

        print(f"Product '{product_name}' not found.")
        return None

    def get_aisle_target(self, input_name):
        """
        Calculates the best entry point for a specific aisle/shelf using DB-backed shelf data.
        e.g. 'Aisle A1' -> Finds matching shelf -> Returns nearest open side.
        """
        search_key = input_name.lower().replace(" ", "").replace("_", "")

        target_shelf = None
        for shelf in self.shelves_data:
            shelf_id = shelf.get('obstacle_id', '').lower().replace(" ", "").replace("_", "")
            shelf_desc = shelf.get('description', '').lower().replace(" ", "").replace("_", "")
            if search_key in shelf_id or search_key in shelf_desc:
                target_shelf = shelf
                break

        if not target_shelf:
            return None

        corners = target_shelf.get('corners', [])
        if not corners:
            return None

        xs = [p['x'] for p in corners]
        ys = [p['y'] for p in corners]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        cx = (min_x + max_x) / 2
        cy = (min_y + max_y) / 2

        candidates = [
            (min_x - self.SAFE_OFFSET, cy),
            (max_x + self.SAFE_OFFSET, cy),
            (cx, min_y - self.SAFE_OFFSET),
            (cx, max_y + self.SAFE_OFFSET),
        ]

        valid_candidates = []
        for cand_x, cand_y in candidates:
            gx, gy = self.world_to_grid(cand_x, cand_y)
            if (gx, gy) not in self.obstacles:
                valid_candidates.append((cand_x, cand_y))

        if not valid_candidates:
            print("Found aisle, but all approach points are blocked.")
            return None

        rx, ry = self.get_robot_position()
        best_target = min(valid_candidates, key=lambda p: math.hypot(p[0] - rx, p[1] - ry))
        return best_target

    # ---------------- SHELF REGION LOADER ----------------
    def load_shelf_regions(self):
        shelves_src = self.shelves_data if self.shelves_data else db.get_static_obstacles()
        shelves = []
        for shelf in shelves_src:
            corners = shelf.get("corners", [])
            if not corners:
                continue
            xs = [p["x"] for p in corners]
            ys = [p["y"] for p in corners]

            shelves.append({
                "id": shelf.get("obstacle_id") or shelf.get("id") or "unknown_shelf",
                "min_x": min(xs),
                "max_x": max(xs),
                "min_y": min(ys),
                "max_y": max(ys)
            })
        return shelves

    # ---------------- CAPTURE IMAGE FROM VISION SENSOR ----------------
    def compare_histograms(self, ref_img, current_img):
        hist_ref = cv2.calcHist([ref_img], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
        hist_curr = cv2.calcHist([current_img], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])

        cv2.normalize(hist_ref, hist_ref)
        cv2.normalize(hist_curr, hist_curr)

        correlation = cv2.compareHist(hist_ref, hist_curr, cv2.HISTCMP_CORREL)
        return correlation

    def capture_image(self):
        image, resolution = self.sim.getVisionSensorImg(self.vision_sensor)
        img = np.frombuffer(image, dtype=np.uint8)
        img = img.reshape(resolution[1], resolution[0], 3)
        img = np.flipud(img)
        return img

    # ---------------- IMAGE COMPARISON LOGIC ----------------
    def process_shelf_view(self, checkpoint_id):
        frame = self.capture_image()
        if frame is None:
            return
        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

        # 1. Get Robot's current world position
        rx, ry = self.get_robot_position()

        # 2. Filter products that are physically in front of the robot's sensor
        # We look for items within a small radius (e.g., 1.2m) of the checkpoint
        current_shelf_items = []
        for item in self.product_db:
            ix, iy, iz = item['location']['x'], item['location']['y'], item['location']['z']
            dist = math.hypot(ix - rx, iy - ry)

            if dist < 1.2:
                current_shelf_items.append(item)

        # 3. Sort by Height (Z)
        # Highest Z = Top Shelf (Zone 0), Lowest Z = Bottom Shelf (Zone 2)
        # Map names to a list of 3 (handling cases where a shelf might be empty)

        expected_names = []

        for level_z in self.shelf_levels:
            items_at_level = [
                item
                for item in current_shelf_items
                if abs(item['location']['z'] - level_z) < 0.1
            ]

            if items_at_level:
                expected_names.append(items_at_level[0])
            else:
                expected_names.append(None)

        # 4. Run Comparison
        ref_img = self._load_reference_from_supabase(checkpoint_id)
        if ref_img is None:
            saved = self._save_reference_to_supabase(checkpoint_id, frame_bgr)
            if saved:
                print(f"Saved Supabase reference for {checkpoint_id}: {expected_names}")
            else:
                print(f"Failed to save Supabase reference for {checkpoint_id}")
        else:
            self.compare_images(ref_img, frame_bgr, checkpoint_id, expected_names)

    def extract_price_from_image(self, tag_roi):

        gray = cv2.cvtColor(tag_roi, cv2.COLOR_BGR2GRAY)

        # Increase contrast
        gray = cv2.convertScaleAbs(gray, alpha=2.0, beta=30)

        # Upscale
        gray = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)

        # Adaptive threshold (better than fixed threshold)
        thresh = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            11,
            2
        )

        results = self.ocr_reader.readtext(thresh, detail=0)

        if not results:
            return None

        text = " ".join(results)
        text = text.replace(",", ".")
        text = text.replace("O", "0")

        numbers = re.findall(r"\d+(?:\.\d{1,2})?", text)

        if numbers:
            # Price is almost always the LAST number on the tag
            return numbers[-1]

        return None

    def get_price_discrepancy(self, aligned_img, zone_h, margin, y_start, y_end, item):
        """
        Standalone function to check for price discrepancies.
        """
        expected_price = None
        detected_price = None

        if not item or 'price' not in item:
            return "N/A", "OK"

        expected_match = re.search(r'\d+', str(item['price']))
        expected_price = expected_match.group(0) if expected_match else "0"

        tag_y_start = y_start + int(zone_h * 0.60)
        tag_roi = aligned_img[tag_y_start:y_end, margin:aligned_img.shape[1] - margin]

        detected_price = self.extract_price_from_image(tag_roi)
        if detected_price:
            if detected_price == expected_price:
                return f"Exp: {expected_price}, Found: {detected_price} | OK", "OK"
            else:
                return f"Exp: {expected_price}, Found: {detected_price}  | PRICE MISMATCH", "PRICE MISMATCH"

        return f"Exp: {expected_price}, Found: ???  | UNREADABLE", "UNREADABLE"

    def compare_images(self, ref_img, current_img, checkpoint_id, expected_items):
        # 1. Align the images to fix robot positioning errors
        aligned_img = self.align_images(ref_img, current_img)

        h, w, _ = aligned_img.shape
        zone_h = h // 3

        # --- NEW: Edge Masking ---
        # We ignore the outer 10% of the left and right sides to prevent
        # neighboring products from triggering "Low Stock" alerts.
        margin = int(w * 0.10)

        # 2. Stronger Blur to ignore texture details/bands on boxes
        blur_ref = cv2.GaussianBlur(ref_img, (15, 15), 0)
        blur_curr = cv2.GaussianBlur(aligned_img, (15, 15), 0)

        # 3. Color-based difference
        diff_bgr = cv2.absdiff(blur_ref, blur_curr)
        diff_max = np.max(diff_bgr, axis=2)

        # 4. Thresholding
        _, thresh = cv2.threshold(diff_max, 45, 255, cv2.THRESH_BINARY)

        # 5. Morphological Cleaning (Removes small speckles of noise)
        kernel = np.ones((9, 9), np.uint8)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)

        alert_img = aligned_img.copy()
        discrepancy_found = False

        for i in range(3):
            y_start, y_end = i * zone_h, (i + 1) * zone_h
            zone_ref_rgb = ref_img[y_start:y_end, margin:w - margin]
            zone_curr_rgb = aligned_img[y_start:y_end, margin:w - margin]
            zone_data = thresh[y_start:y_end, margin:w - margin]

            changed_pixels = np.count_nonzero(zone_data)
            total_zone_pixels = zone_data.shape[0] * zone_data.shape[1]
            change_ratio = (changed_pixels / total_zone_pixels) * 100

            hist_score = self.compare_histograms(zone_ref_rgb, zone_curr_rgb)

            item = expected_items[i]
            product_name = item['name'] if item else "Empty Slot"
            price_detail, price_status = self.get_price_discrepancy(current_img, zone_h, margin, y_start, y_end, item)

            # --- SHELF-SPECIFIC SENSITIVITY ---
            # i=0 is Top, i=1 is Middle, i=2 is Bottom
            # We increase the 'Out of Stock' requirement as we go down the shelves
            if i == 0:  # Top Shelf (Far away, small visual footprint)
                oos_threshold = 7.0
                low_threshold = 1.0
            elif i == 1:  # Middle Shelf
                oos_threshold = 12.0
                low_threshold = 0.5
            else:  # Bottom Shelf (Close to camera, large visual footprint)
                oos_threshold = 20.0
                low_threshold = 2.0

            is_out_of_stock = (change_ratio > oos_threshold) or (hist_score < 0.90)
            is_low_stock = (change_ratio > low_threshold)

            if is_out_of_stock:
                status = "OUT OF STOCK"
                color = (0, 0, 255)
                discrepancy_found = True
            elif is_low_stock:
                status = "LOW STOCK"
                color = (0, 165, 255)
                discrepancy_found = True
            else:
                status = "OK"
                color = (0, 255, 0)

            if price_status == "PRICE MISMATCH":
                discrepancy_found = True
                if status == "OK":
                    color = (255, 0, 255)  # 
            if item:
                pd_disc = status
                pc_disc = "OK" if price_status == "OK" else "PRICE MISMATCH"
                try:
                    db.upsert_inventory_alert(product_name=product_name, pd_disc=pd_disc, pc_disc=pc_disc)
                except Exception as e:
                    print(f"Alert DB write failed for {product_name}: {e}")

            print(
                f"| {product_name.ljust(15)} | {status.ljust(22)} | Change: {change_ratio:.2f}% | Hist: {hist_score:.2f} | Price: {price_detail}")

            # Draw the restricted search area for visual debugging
            cv2.rectangle(alert_img, (margin, y_start), (w - margin, y_end), color, 2)
            if status != "OK" or price_status == "PRICE MISMATCH":
                display_msg = status if price_status == "OK" else f"{status} / PRICE ERR"
                cv2.putText(alert_img, f"{product_name}: {display_msg} ({change_ratio:.1f}%)",
                            (10, y_start + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

        if discrepancy_found:
            self._save_alert_to_supabase(checkpoint_id, alert_img)

    def align_images(self, ref_img, current_img):
        """
        Aligns the current image to the reference image using ORB feature matching.
        This fixes the false positives caused by slight changes in the robot's stopping position.
        """
        gray_ref = cv2.cvtColor(ref_img, cv2.COLOR_BGR2GRAY)
        gray_curr = cv2.cvtColor(current_img, cv2.COLOR_BGR2GRAY)

        # Detect ORB features and compute descriptors (FIXED: nfeatures)
        orb = cv2.ORB_create(nfeatures=1000)
        kp1, des1 = orb.detectAndCompute(gray_curr, None)
        kp2, des2 = orb.detectAndCompute(gray_ref, None)

        if des1 is None or des2 is None:
            return current_img  # Fallback if no features found

        # Match features using Brute Force Hamming
        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        matches = bf.match(des1, des2)

        # Sort matches by distance (best matches first)
        matches = sorted(matches, key=lambda x: x.distance)

        # Keep the top 15% of matches to ensure clean alignment
        good_matches = int(len(matches) * 0.15)
        if good_matches < 10:
            return current_img  # Fallback if poor matching

        matches = matches[:good_matches]

        # Extract coordinates of good matches
        pts_curr = np.zeros((len(matches), 2), dtype=np.float32)
        pts_ref = np.zeros((len(matches), 2), dtype=np.float32)

        for i, match in enumerate(matches):
            pts_curr[i, :] = kp1[match.queryIdx].pt
            pts_ref[i, :] = kp2[match.trainIdx].pt

        # Find Homography matrix and warp the image
        matrix, mask = cv2.findHomography(pts_curr, pts_ref, cv2.RANSAC, 5.0)
        if matrix is not None:
            h, w = ref_img.shape[:2]
            aligned_img = cv2.warpPerspective(current_img, matrix, (w, h))
            return aligned_img

        return current_img

    # ---------------- A* ----------------
    def plan_path(self, start, goal):
        start = self.world_to_grid(*start)
        goal = self.world_to_grid(*goal)

        if goal in self.obstacles:
            print("Warning: Goal inside obstacle, shifting...")
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    g = (goal[0] + dx, goal[1] + dy)
                    if g not in self.obstacles:
                        goal = g
                        break

        open_set = [(0, start)]
        came = {}
        g = {start: 0}

        while open_set:
            _, cur = heapq.heappop(open_set)
            if cur == goal:
                path = []
                while cur in came:
                    path.append(self.grid_to_world(*cur))
                    cur = came[cur]
                return path[::-1]

            for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]:
                nb = (cur[0] + dx, cur[1] + dy)
                if nb in self.obstacles:
                    continue

                cost = g[cur] + (1.414 if dx and dy else 1)
                if cost < g.get(nb, 1e9):
                    came[nb] = cur
                    g[nb] = cost
                    f = cost + math.hypot(nb[0] - goal[0], nb[1] - goal[1])
                    heapq.heappush(open_set, (f, nb))

        return None

    # ---------------- NAVIGATION ----------------
    def navigate_to_target(self, target, target_name):
        if not self.navigation_lock.acquire(blocking=False):
            print(f"Navigation rejected: another navigation is already active while requesting {target_name}.")
            return "busy"

        nav_generation = self._current_generation()
        self.stop_event.clear()
        try:
            print(f"Planning Path to: {target_name}...")
            path = self.plan_path(self.get_robot_position(), target)
            if not path:
                print("No path found initially.")
                return "no_path"

            print(f"Path found with {len(path)} waypoints.")
            idx = 0
            nav_status = "aborted"
            self.navigation_running.set()

            while True:
                if nav_generation != self._current_generation():
                    print(f"\n>>> PREEMPTED: Navigation to {target_name} was superseded by an override. <<<")
                    self.set_velocity(0, 0)
                    break

                if self.stop_event.is_set():
                    print(f"\n>>> STOPPING: Navigation to {target_name} aborted by user. <<<")
                    self.set_velocity(0, 0)
                    break

                rx, ry = self.get_robot_position()
                tx, ty = target
                dist_to_goal = math.hypot(tx - rx, ty - ry)

                if dist_to_goal < self.STOPPING_DISTANCE:
                    self.set_velocity(0, 0)
                    print(f"\n>>> ARRIVED at {target_name} <<<")
                    nav_status = "success"
                    break

                map_changed = self.update_map_from_sensors()
                if map_changed:
                    if idx < len(path):
                        wx, wy = path[idx]
                    else:
                        wx, wy = tx, ty

                    if math.hypot(wx - rx, wy - ry) < 0.8:
                        new_path = self.plan_path((rx, ry), target)
                        if new_path:
                            path = new_path
                            idx = 0

                if dist_to_goal < 0.8:
                    wx, wy = tx, ty
                else:
                    if idx < len(path):
                        wx, wy = path[idx]
                        if math.hypot(wx - rx, wy - ry) < 0.25:
                            idx += 1
                            continue
                    else:
                        wx, wy = tx, ty

                theta = self.get_robot_orientation()
                ang = math.atan2(wy - ry, wx - rx)
                err = (ang - theta + math.pi) % (2 * math.pi) - math.pi

                if dist_to_goal < self.SLOWDOWN_RADIUS:
                    speed = 0.4
                    turn = 1.5 * err
                else:
                    speed = 1.6 if abs(err) < 0.4 else 0.2
                    turn = 2.5 * err

                self.set_velocity(speed - turn, speed + turn)
                self.client.step()

            return nav_status
        finally:
            self.navigation_running.clear()
            self.safe_stop_motion()
            self.navigation_lock.release()

    def go_to_product(self, names):
        # Allow single string OR list
        if isinstance(names, str):
            names = [n.strip() for n in names.split(",")]

        if len(names) == 0:
            return

        if len(names) > 5:
            print("Maximum 5 products allowed.")
            return

        # Optimize order if more than 1 item
        if len(names) > 1:
            names = self.optimize_item_order(names)
            print("Optimized order:", names)

        for name in names:
            target = self.get_product_coords(name)
            if target:
                self.navigate_to_target(target, name)
    def optimize_item_order(self, items):
        """
        Greedy nearest-neighbor TSP for max 5 items.
        """
        remaining = items.copy()
        ordered = []

        current_pos = self.get_robot_position()

        while remaining:
            closest_item = None
            closest_dist = float('inf')

            for item in remaining:
                coords = self.get_product_coords(item)
                if not coords:
                    continue

                dist = math.hypot(coords[0] - current_pos[0],
                                coords[1] - current_pos[1])

                if dist < closest_dist:
                    closest_dist = dist
                    closest_item = item

            if not closest_item:
                break

            ordered.append(closest_item)
            current_pos = self.get_product_coords(closest_item)
            remaining.remove(closest_item)

        return ordered

    def return_to_base(self):
        if self.home_base:
            return self.navigate_to_target(self.home_base, "Home Base")
        else:
            print("Home base coordinates not loaded!")
            return "no_home_base"

    def override_active_navigation(self):
        self._bump_generation()
        self.stop_event.set()
        if self.mode == "PATROL":
            self.mode = "ACTIVE"

        wait_start = time.time()
        while self.navigation_running.is_set() and (time.time() - wait_start) < 5.0:
            time.sleep(0.05)
        if self.navigation_running.is_set():
            print("Warning: navigation did not stop in time; proceeding with override command.")

        if self.nav_thread and self.nav_thread.is_alive():
            self.nav_thread.join(timeout=5.0)
            if self.nav_thread.is_alive():
                print("Warning: navigation/patrol thread did not stop in time.")

        self.safe_stop_motion()

    def override_and_execute(self, action: str):
        self.override_active_navigation()

        if action == "stop":
            self.stop_robot()
            return

        if action == "cancel_navigation":
            self.turn_off_robot()
            self.stop_event.clear()
            self.mode = "PATROL"
            if self.nav_thread and self.nav_thread.is_alive():
                print("Cannot start patrol: robot thread is still active.")
            else:
                self.nav_thread = threading.Thread(target=self.patrol_mode)
                self.nav_thread.start()
            return

        if action == "power_off":
            self.turn_off_robot()
            return

        if action == "return_to_base":
            time.sleep(0.25)
            self.stop_event.clear()
            self.mode = "ACTIVE"
            result = self.return_to_base()
            if result != "success":
                raise RuntimeError(f"Return-to-base override failed to start/complete: {result}")
            return

        raise ValueError(f"Unsupported override action: {action}")

    def stop_robot(self):
        """
        Stops robot motion immediately without switching to power-off/idle state.
        Keeps the system available while forcing zero wheel speed.
        """
        if self.nav_thread and self.nav_thread.is_alive():
            print("Stopping robot motion...")
            self.stop_event.set()
            self.nav_thread.join()
            print("Robot motion stopped.")
        else:
            print("Robot is already stationary.")

        self.safe_stop_motion()

    def turn_off_robot(self):
        """
        Stops the robot immediately and cancels active navigation.
        Acts as the 'Turn Off' or 'Idle' switch.
        """
        self.mode = "IDLE"

        if self.nav_thread and self.nav_thread.is_alive():
            print("Initiating Shutdown Sequence (Idle Mode)...")
            self.stop_event.set()
            self.nav_thread.join()
            print("Robot has stopped.")
        else:
            print("Robot is already idle.")

        self.safe_stop_motion()

    def turn_on_robot(self):
        """
        Clears stop state so navigation can resume and restores normal motion control.
        """
        self.mode = "ACTIVE"
        self.stop_event.clear()
        self.safe_stop_motion()

    def _navigation_thread(self, cmd):
        if cmd.lower() in ['home', 'base']:
            self.return_to_base()
        else:
            self.go_to_product(cmd)

    def turn_to_angle(self, target_theta):
        """Rotates the robot in place to face a specific angle."""
        while not self.stop_event.is_set():
            theta = self.get_robot_orientation()
            err = (target_theta - theta + math.pi) % (2 * math.pi) - math.pi

            # Reduced tolerance to ~0.5 degrees for high precision framing
            if abs(err) < 0.01:
                self.set_velocity(0, 0)
                break

            turn_speed = 2.0 * err
            # Cap the turn speed
            turn_speed = max(min(turn_speed, 1.0), -1.0)

            self.set_velocity(-turn_speed, turn_speed)
            self.client.step()

        self.set_velocity(0, 0)
        self.client.step()

    def build_patrol_points(self):
        shelves_src = self.shelves_data if self.shelves_data else db.get_static_obstacles()
        patrol_groups = []

        for shelf in shelves_src:
            corners = shelf.get('corners', [])
            if not corners:
                continue
            xs = [p['x'] for p in corners]
            ys = [p['y'] for p in corners]

            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)

            width = max_x - min_x
            height = max_y - min_y

            if width > height:
                scan_xs = [
                    min_x + width * 0.200,
                    min_x + width * 0.450,
                    min_x + width * 0.650,
                    min_x + width * 0.850
                ]

                a_y = min_y - self.SAFE_OFFSET
                b_y = max_y + self.SAFE_OFFSET

                # Horizontal shelf: Angle exactly straight (pi/2 or -pi/2) to face the shelf flat
                group_a = [(sx, a_y, math.pi / 2) for sx in scan_xs]
                group_b = [(sx, b_y, -math.pi / 2) for sx in reversed(scan_xs)]

                patrol_groups.append(group_a)
                patrol_groups.append(group_b)
            else:
                scan_ys = [
                    min_y + height * 0.200,
                    min_y + height * 0.450,
                    min_y + height * 0.650,
                    min_y + height * 0.850
                ]

                a_x = max_x + self.SAFE_OFFSET
                b_x = min_x - self.SAFE_OFFSET

                # Vertical shelf: Angle exactly straight (pi or 0) to face the shelf flat
                group_a = [(a_x, sy, math.pi) for sy in scan_ys]
                group_b = [(b_x, sy, 0.0) for sy in reversed(scan_ys)]

                patrol_groups.append(group_a)
                patrol_groups.append(group_b)

        print(f"Built {len(patrol_groups)} patrol groups.")
        return patrol_groups

    def patrol_mode(self):
        mode_text = "REFERENCE GATHERING" if self.is_reference_run else "DISCREPANCY CHECK"
        print(f"\n>>> PATROL MODE STARTED | Mode: {mode_text} <<<")
        self.mode = "PATROL"

        rx, ry = self.get_robot_position()

        # Find closest group
        closest_group_index = 0
        min_dist = float('inf')

        for i, group in enumerate(self.patrol_groups):
            gx, gy, _ = group[0]
            d = math.hypot(gx - rx, gy - ry)
            if d < min_dist:
                min_dist = d
                closest_group_index = i

        group_index = closest_group_index

        while self.mode == "PATROL":
            group = self.patrol_groups[group_index]
            rx, ry = self.get_robot_position()

            # Determine scan direction dynamically
            start_dist = math.hypot(group[0][0] - rx, group[0][1] - ry)
            end_dist = math.hypot(group[-1][0] - rx, group[-1][1] - ry)

            if end_dist < start_dist:
                group = list(reversed(group))
                pt_indices = list(reversed(range(len(group))))
            else:
                pt_indices = list(range(len(group)))

            for point_data, pt_idx in zip(group, pt_indices):
                if self.stop_event.is_set():
                    break

                target_x, target_y, target_theta = point_data

                # Navigate to the scan point
                # Navigate to the scan point
                self.navigate_to_target((target_x, target_y), f"Checkpoint G{group_index}-P{pt_idx}")

                # Turn to face the shelf directly
                if not self.stop_event.is_set():
                    self.turn_to_angle(target_theta)

                # Halt and wait for camera to stabilize
                self.stop_and_wait(1.5)

                # ---------------------------------------------------------
                # NEW FIX: HARD SNAP TO EXACT COORDINATES
                # ---------------------------------------------------------
                if not self.stop_event.is_set():
                    # 1. Get the current Z height so the robot doesn't sink into the floor
                    current_z = self.sim.getObjectPosition(self.robot_base, -1)[2]

                    # 2. Force the robot to the mathematically perfect X, Y, and Angle
                    self.sim.setObjectPosition(self.robot_base, -1, [target_x, target_y, current_z])
                    self.sim.setObjectOrientation(self.robot_base, -1, [0, 0, target_theta])

                    # 3. Wait a split second to let the camera buffer refresh the perfect view
                    time.sleep(0.5)
                # ---------------------------------------------------------

                # Trigger image capture and discrepancy check
                checkpoint_id = f"shelf_view_G{group_index}_P{pt_idx}"
                self.process_shelf_view(checkpoint_id)

            group_index += 1
            if group_index >= len(self.patrol_groups):
                group_index = 0

            if self.stop_event.is_set():
                break

        self.set_velocity(0, 0)
        print(">>> PATROL MODE STOPPED <<<")

        # If we just gathered references, toggle the flag so the next run compares them
        if self.is_reference_run:
            print(">>> Reference images collected successfully! <<<")
            print(">>> Next patrol run will automatically compare against these images. <<<")
            self.is_reference_run = False

    def run_tech_demo(self):
        self.sim.startSimulation()
        print("NaviBot Ready.")
        print("Commands: [product name] | 'p' (patrol) | 'reset refs' | 'stop' | 'home' | 'q'")

        try:
            while True:
                cmd = input("\nEnter command: ")

                if cmd.lower() == 'q':
                    if self.nav_thread and self.nav_thread.is_alive():
                        self.stop_event.set()
                        self.nav_thread.join()
                    break

                if cmd.lower() == 'reset refs':
                    if self._clear_supabase_inventory_refs():
                        self.is_reference_run = True
                        print(">>> Supabase inventory references cleared. Next patrol will capture new reference images. <<<")
                    else:
                        print(">>> Failed to clear Supabase inventory references. <<<")
                    continue

                if cmd.lower() == 'p':
                    if self.nav_thread and self.nav_thread.is_alive():
                        print("Robot busy. Type 'stop' first.")
                        continue

                    self.stop_event.clear()
                    self.nav_thread = threading.Thread(target=self.patrol_mode)
                    self.nav_thread.start()
                    continue

                if cmd.lower() == 'stop':
                    if self.nav_thread and self.nav_thread.is_alive():
                        print("Stopping robot...")
                        self.stop_event.set()
                        self.nav_thread.join()
                        print("Robot stopped.")
                    else:
                        print("Robot is not currently moving.")
                    continue

                if self.nav_thread and self.nav_thread.is_alive():
                    print("Robot is currently moving! Type 'stop' first to change target.")
                    continue

                self.stop_event.clear()
                self.nav_thread = threading.Thread(target=self._navigation_thread, args=(cmd,))
                self.nav_thread.start()

        except KeyboardInterrupt:
            print("Stopped.")
        finally:
            self.set_velocity(0, 0)
            self.sim.stopSimulation()


if __name__ == "__main__":
    bot = NaviBotController()
    bot.run_tech_demo()
