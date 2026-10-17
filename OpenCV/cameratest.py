import cv2
import time
from collections import deque
from ultralytics import YOLO

model = YOLO("best.pt")
cap = cv2.VideoCapture(0)

# --- CONFIG ---
CONF_THRESHOLD = 0.70
FRAMES_REQUIRED = 10
BUFFER_SIZE = 20

class_buffer = deque(maxlen=BUFFER_SIZE)
locked = False
scan_result = None
frozen_frame = None

while True:
    ret, frame = cap.read()
    if not ret:
        break

    if not locked:
        results = model(frame, conf=CONF_THRESHOLD, verbose=False)
        annotated = results[0].plot()

        if len(results[0].boxes) > 0:
            boxes = results[0].boxes
            areas = (boxes.xyxy[:, 2] - boxes.xyxy[:, 0]) * (boxes.xyxy[:, 3] - boxes.xyxy[:, 1])
            best_idx = areas.argmax()

            cls_id = int(boxes.cls[best_idx])
            class_name = model.names[cls_id]
            class_buffer.append(class_name)

            if class_buffer.count(class_name) >= FRAMES_REQUIRED:
                locked = True
                scan_result = {
                    "product_name": class_name,
                    "confidence": round(float(boxes.conf[best_idx]), 3),
                    "timestamp": time.time()
                }
                # Capture the current annotated frame to "freeze" it
                frozen_frame = annotated.copy()
                print("SCAN_RESULT:", scan_result)

        # While scanning, show the live annotated feed
        cv2.imshow("YOLO Live", annotated)

    else:
        # Show the frozen image indefinitely until 'q' or 'r' (reset)
        cv2.imshow("YOLO Live", frozen_frame)

    key = cv2.waitKey(1) & 0xFF
    if key == ord("q"):
        break
    elif key == ord("r"): # Added a reset button for convenience
        locked = False
        class_buffer.clear()

cap.release()
cv2.destroyAllWindows()