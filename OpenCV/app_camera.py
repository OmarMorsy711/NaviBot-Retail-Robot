from flask import Flask, Response, jsonify, request
from flask_cors import CORS
import cv2
import time
import base64
import numpy as np
from collections import deque
from ultralytics import YOLO
import threading
import easyocr

app = Flask(__name__)
CORS(app)

# ========== CONFIGURATION ==========
CONF_THRESHOLD = 0.70
FRAMES_REQUIRED = 5
BUFFER_SIZE = 20
MODEL_PATH = "best.pt"
PORT = 5001

# ========== GLOBAL VARIABLES ==========
model = YOLO(MODEL_PATH)
ocr_reader = easyocr.Reader(['en'])
print("✅ OCR engine ready")

# Camera is NOT opened at startup - will be opened on demand
camera = None
class_buffer = deque(maxlen=BUFFER_SIZE)
locked = False
scan_result = None
frozen_frame = None
latest_frame = None
frame_lock = threading.Lock()
detection_active = False
detect_thread = None
current_mode = None  # 'product' or 'list'

def open_camera():
    """Open camera if not already open"""
    global camera
    if camera is None or not camera.isOpened():
        print("📸 Opening camera...")
        camera = cv2.VideoCapture(0)
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        return True
    return False

def close_camera():
    """Close camera to free it for other applications"""
    global camera, detection_active, detect_thread, latest_frame
    detection_active = False
    if detect_thread and detect_thread.is_alive():
        detect_thread.join(timeout=1.0)
    with frame_lock:
        if camera and camera.isOpened():
            camera.release()
            camera = None
            latest_frame = None
            print("📸 Camera closed")
    return True

def detect_objects():
    """Background thread that continuously runs YOLO detection"""
    global locked, scan_result, frozen_frame, latest_frame, detection_active
    
    while detection_active and camera and camera.isOpened():
        success, frame = camera.read()
        if not success:
            break
            
        with frame_lock:
            if not locked:
                # Run YOLO detection on the frame
                results = model(frame, conf=CONF_THRESHOLD, verbose=False)
                annotated = results[0].plot()
                latest_frame = annotated
                
                # Check if any objects detected
                if len(results[0].boxes) > 0:
                    boxes = results[0].boxes
                    areas = (boxes.xyxy[:, 2] - boxes.xyxy[:, 0]) * (boxes.xyxy[:, 3] - boxes.xyxy[:, 1])
                    best_idx = areas.argmax()
                    
                    cls_id = int(boxes.cls[best_idx])
                    class_name = model.names[cls_id]
                    class_buffer.append(class_name)
                    
                    # Check if we've seen the same object enough times
                    if class_buffer.count(class_name) >= FRAMES_REQUIRED:
                        locked = True
                        scan_result = {
                            "product_name": class_name,
                            "confidence": round(float(boxes.conf[best_idx]), 3),
                            "timestamp": time.time()
                        }
                        frozen_frame = annotated.copy()
                        print(f"✅ LOCKED: {class_name} (conf: {scan_result['confidence']})")
            else:
                # When locked, just keep showing the frozen frame
                latest_frame = frozen_frame
        
        time.sleep(0.03)
    
    # When detection stops, close camera
    close_camera()

# ========== PRODUCT SCANNING ENDPOINTS ==========
@app.route('/start_product_scan', methods=['POST'])
def start_product_scan():
    """Start product scanning mode"""
    global detection_active, detect_thread, current_mode, locked, scan_result, frozen_frame, class_buffer
    
    # Close any existing camera session
    close_camera()
    
    # Reset state
    locked = False
    scan_result = None
    frozen_frame = None
    class_buffer.clear()
    current_mode = 'product'
    
    # Open camera and start detection
    open_camera()
    detection_active = True
    detect_thread = threading.Thread(target=detect_objects, daemon=True)
    detect_thread.start()
    
    print("🔄 Product scanning started")
    return jsonify({"status": "started", "mode": "product"})

@app.route('/video_feed')
def video_feed():
    """Stream the video feed with detections"""
    def generate():
        while True:
            with frame_lock:
                if latest_frame is not None:
                    ret, buffer = cv2.imencode('.jpg', latest_frame)
                    frame = buffer.tobytes()
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            time.sleep(0.03)
    
    return Response(generate(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/detection_status')
def detection_status():
    """Return the current detection status"""
    global locked, scan_result
    
    if locked and scan_result:
        return jsonify({
            "locked": True,
            "result": scan_result
        })
    else:
        return jsonify({
            "locked": False,
            "result": None
        })

@app.route('/reset_detection', methods=['POST'])
def reset_detection():
    """Reset the detection lock to scan again"""
    global locked, scan_result, frozen_frame, class_buffer
    locked = False
    scan_result = None
    frozen_frame = None
    class_buffer.clear()
    print("🔄 Detection reset")
    return jsonify({"status": "reset"})

# ========== SHOPPING LIST OCR ENDPOINT ==========
@app.route('/extract_list', methods=['POST', 'OPTIONS'])
def extract_list():
    """Extract text from a shopping list image"""
    # Handle preflight OPTIONS request for CORS
    if request.method == 'OPTIONS':
        response = jsonify({"status": "ok"})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response
        
    try:
        # Get image data from request
        data = request.json
        image_data = data.get('image', '')
        
        # Remove base64 header if present
        if ',' in image_data:
            image_data = image_data.split(',')[1]
        
        # Decode base64 to image
        img_bytes = base64.b64decode(image_data)
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        # Run OCR on the image
        results = ocr_reader.readtext(img)
        
        # Common misspellings mapping
        corrections = {
            'woter': 'water',
            'watter': 'water',
            'watr': 'water',
            'snocks': 'snacks',
            'snak': 'snacks',
            'snaks': 'snacks',
            'milke': 'milk',
            'mik': 'milk',
            'bred': 'bread',
            'bredd': 'bread',
            'eggs': 'eggs',
            'egg': 'eggs',
            'cereal': 'cereal',
            'cerial': 'cereal',
            'juice': 'juice',
            'jus': 'juice',
            'coke': 'coca cola',
            'pepsi': 'pepsi',
            'chips': 'chips',
            'candy': 'candy'
        }
        
        # Extract text from results
        extracted_texts = []
        for (bbox, text, confidence) in results:
            # Only include text with reasonable confidence
            if confidence > 0.4:  # Slightly lower threshold
                # Clean up the text
                cleaned = text.strip().lower()
                
                # Remove any numbers (like '345', '347')
                if any(c.isdigit() for c in cleaned):
                    cleaned = ''.join([c for c in cleaned if not c.isdigit()])
                    cleaned = cleaned.strip()
                
                # Skip if empty after removing numbers
                if not cleaned:
                    continue
                
                # Remove common non-product words
                skip_words = ['shopping', 'list', 'grocery', 'items', 'item', 'buy', 'need', 'and', 'the', 'for', 'with']
                if cleaned in skip_words:
                    continue
                
                # Apply spelling corrections
                if cleaned in corrections:
                    cleaned = corrections[cleaned]
                    print(f"🔄 Corrected '{text}' to '{cleaned}'")
                
                # Only keep if length > 1 (meaningful words)
                if len(cleaned) > 1:
                    extracted_texts.append(cleaned)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_texts = []
        for text in extracted_texts:
            if text not in seen:
                seen.add(text)
                unique_texts.append(text)
        
        # Limit to 5 items (as requested)
        unique_texts = unique_texts[:5]
        
        print(f"📝 OCR extracted: {unique_texts}")
        
        response = jsonify({
            "success": True,
            "items": unique_texts,
            "count": len(unique_texts)
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response
        
    except Exception as e:
        print(f"❌ OCR Error: {str(e)}")
        response = jsonify({
            "success": False,
            "error": str(e)
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 500

# ========== SHOPPING LIST SCANNING ==========
@app.route('/start_list_scan', methods=['POST'])
def start_list_scan():
    """Start shopping list scanning mode (single photo, no continuous detection)"""
    global current_mode
    
    # Close any existing camera session
    close_camera()
    
    # Just open camera without starting detection thread
    current_mode = 'list'
    open_camera()
    
    print("📝 List scanning mode ready")
    return jsonify({"status": "ready", "mode": "list"})

@app.route('/capture_list', methods=['POST'])
def capture_list():
    """Capture a single frame and extract text using OCR"""
    global camera, current_mode
    
    if not camera or not camera.isOpened():
        return jsonify({"success": False, "error": "Camera not open"}), 400
    
    # Capture a single frame
    success, frame = camera.read()
    if not success:
        return jsonify({"success": False, "error": "Failed to capture image"}), 500
    
    # Convert to JPEG
    _, buffer = cv2.imencode('.jpg', frame)
    img_bytes = buffer.tobytes()
    
    # Run OCR
    img_np = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
    results = ocr_reader.readtext(img_np)
    
    # Extract text
    extracted_texts = []
    for (bbox, text, confidence) in results:
        if confidence > 0.5:
            cleaned = text.strip().lower()
            skip_words = ['shopping', 'list', 'grocery', 'items', 'item', 'buy', 'need']
            if cleaned not in skip_words and len(cleaned) > 1:
                extracted_texts.append(cleaned)
    
    # Remove duplicates
    seen = set()
    unique_texts = []
    for text in extracted_texts:
        if text not in seen:
            seen.add(text)
            unique_texts.append(text)
    
    # Limit to 5 items
    unique_texts = unique_texts[:5]
    
    print(f"📝 List captured: {unique_texts}")
    
    # Close camera after capture
    close_camera()
    
    return jsonify({
        "success": True,
        "items": unique_texts,
        "count": len(unique_texts)
    })

@app.route('/cancel_scan', methods=['POST'])
def cancel_scan():
    """Cancel any ongoing scan and close camera"""
    close_camera()
    return jsonify({"status": "cancelled"})

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "running",
        "camera_open": camera is not None and camera.isOpened(),
        "mode": current_mode
    })

if __name__ == '__main__':
    print(f"🚀 OpenCV Server starting on port {PORT}")
    print(f"🤖 Model loaded: {MODEL_PATH}")
    print("📸 Camera will be opened on demand only")
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)