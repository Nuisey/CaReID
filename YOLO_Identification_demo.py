import cv2
from ultralytics import YOLO
import time
import os
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
import threading
import argparse

parser = argparse.ArgumentParser(description="Run YOLO Identification in demo mode on a video file")
parser.add_argument("--video", type=str, required=True, help="Path to video file")
args = parser.parse_args()

latest_buffer = None

def frame_sender():
    global latest_buffer
    while True:
        if latest_buffer is not None:
            try:
                requests.post('http://127.0.0.1:5000/api/push_frame', data=latest_buffer, timeout=1.0)
            except:
                pass
        time.sleep(0.05)

threading.Thread(target=frame_sender, daemon=True).start()

# Track vehicles and their Y-coordinate history
# Format: {track_id: {'first_seen_time': timestamp, 'last_save_time': timestamp, 'y_history': []}}
track_history = {}

# data setup
SAVE_DIR = "HotFolder_Demo"
os.makedirs(SAVE_DIR, exist_ok=True)

import shutil
DEMO_DIR = "demo"
os.makedirs(DEMO_DIR, exist_ok=True)

# Demo video variables
video_segment_duration = 300  # 5 minutes
current_segment_start = time.time()
current_segment_car_ids = set()
highest_car_count = 0
video_writer = None
temp_video_path = "temp_demo_segment.mp4"
video_fps = 30.0

# load model
model = YOLO("yolo11m.pt") 

# video source
cap = cv2.VideoCapture(args.video)

cam_fps = cap.get(cv2.CAP_PROP_FPS)
if cam_fps and cam_fps > 0:
    video_fps = cam_fps

# configure window
main_window_name = "Car Identification (DEMO MODE)"
cv2.namedWindow(main_window_name, cv2.WINDOW_NORMAL)

import numpy as np

# Wait for AI models to load
print("Waiting for AI models to finish loading...")
while not os.path.exists("ai_ready.txt"):
    # Create a blank waiting frame
    waiting_frame = np.zeros((480, 854, 3), dtype=np.uint8)
    cv2.putText(waiting_frame, "WAITING FOR AI MODELS TO LOAD (~2 mins)...", (50, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
    cv2.imshow(main_window_name, waiting_frame)
    
    # Send waiting frame to Flask Dashboard so the UI updates
    success_encode, buffer = cv2.imencode('.jpg', waiting_frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
    if success_encode:
        latest_buffer = buffer.tobytes()
        
    if cv2.waitKey(1000) & 0xFF == ord('q'):
        break
print("AI models loaded! Starting video playback.")

# Track program start time so we can skip initial detections
program_start_time = time.time()

while cap.isOpened():
    success, frame = cap.read()
    if not success:
        print("video issue")
        break
    
    # Check if system is paused
    if os.path.exists("pause_camera.txt"):
        cv2.putText(frame, "SYSTEM PAUSED", (frame.shape[1]//2 - 300, frame.shape[0]//2), cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 0, 255), 8)
        small_frame = cv2.resize(frame, (854, 480))
        success_encode, buffer = cv2.imencode('.jpg', small_frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
        if success_encode:
            latest_buffer = buffer.tobytes()
        cv2.imshow(main_window_name, frame)
        if cv2.waitKey(1) & 0xFF == ord('q'): break
        continue

    start = time.perf_counter()

    # Get frame dimensions for rule of thirds calculation
    frame_h, frame_w, _ = frame.shape
    rule_of_thirds_left_boundary = frame_w // 3
    rule_of_thirds_right_boundary = 2 * (frame_w // 3)



    # input frame into model
    results = model.track(frame, persist=True, verbose=False)
    annotated_frame = results[0].plot(font_size=0.3, line_width=2)

    if results[0].boxes.id is not None:
        boxes = results[0].boxes.xyxy.cpu()
        track_ids = results[0].boxes.id.int().cpu().tolist()
        confs = results[0].boxes.conf.float().cpu().tolist()
        clss = results[0].boxes.cls.cpu().tolist()
        
        current_frame_track_ids = set()

        for box, track_id, conf, cls in zip(boxes, track_ids, confs, clss):
            current_frame_track_ids.add(track_id)
            
            if conf > 0.75:
                x1, y1, x2, y2 = map(int, box)
                box_center_x = (x1 + x2) // 2
                box_center_y = (y1 + y2) // 2
                
                class_name = model.names[cls]
                
                if class_name in ['car', 'truck', 'bus']:
                    current_time = time.time()
                    
                    if track_id not in track_history:
                        track_history[track_id] = {
                            'first_seen_time': current_time,
                            'last_save_time': 0,
                            'y_history': [],
                            'x_history': []
                        }
                    
                    # Store X, Y, W, H coordinates to detect ANY movement or scaling
                    track_history[track_id]['y_history'].append(box_center_y)
                    track_history[track_id]['x_history'].append(box_center_x)
                    
                    w = x2 - x1
                    h = y2 - y1
                    if 'w_history' not in track_history[track_id]:
                        track_history[track_id]['w_history'] = []
                        track_history[track_id]['h_history'] = []
                    track_history[track_id]['w_history'].append(w)
                    track_history[track_id]['h_history'].append(h)
                    
                    if len(track_history[track_id]['y_history']) > 30:
                        track_history[track_id]['y_history'].pop(0)
                        track_history[track_id]['x_history'].pop(0)
                        track_history[track_id]['w_history'].pop(0)
                        track_history[track_id]['h_history'].pop(0)

                    time_since_last_save = current_time - track_history[track_id]['last_save_time']

                    if time_since_last_save >= 0.2:
                        
                        y_hist = track_history[track_id]['y_history']
                        x_hist = track_history[track_id]['x_history']
                        w_hist = track_history[track_id]['w_history']
                        h_hist = track_history[track_id]['h_history']
                        
                        direction = "unknown"
                        
                        if len(y_hist) >= 5:
                            dy = y_hist[-1] - y_hist[0]
                            dx = x_hist[-1] - x_hist[0]
                            dw = w_hist[-1] - w_hist[0]
                            dh = h_hist[-1] - h_hist[0]
                            
                            # A car is moving if its center changes by > 3 pixels OR its size changes by > 3 pixels
                            if abs(dy) > 3 or abs(dx) > 3 or abs(dw) > 3 or abs(dh) > 3:
                                if dy > 3:
                                    direction = "arriving"
                                elif dy < -3:
                                    direction = "leaving"
                                else:
                                    direction = "moving"
                        
                        if direction != "unknown":
                            current_segment_car_ids.add(track_id)
                            track_history[track_id]['last_save_time'] = current_time
                            
                            cropped_vehicle = frame[y1:y2, x1:x2]

                            if cropped_vehicle.size > 0:
                                timestamp = datetime.now(ZoneInfo("America/New_York")).strftime('%Y-%m-%d_%H-%M-%S-%f')
                                filename = f"{timestamp}__{direction}__track{track_id}__{class_name}.jpg"
                                save_path = os.path.join(SAVE_DIR, filename)
                                cv2.imwrite(save_path, cropped_vehicle)
                                print(f"Saved: {class_name} ID: {track_id} | Dir: {direction}")

        # Clean up old tracks
        obsolete_ids = set(track_history.keys()) - current_frame_track_ids
        for t_id in obsolete_ids:
            del track_history[t_id]

    end = time.perf_counter()
    fps = 1 / (end - start)

    cv2.imshow(main_window_name, annotated_frame)
    
    # Send compressed frame to Flask Dashboard
    small_frame = cv2.resize(annotated_frame, (854, 480))
    success_encode, buffer = cv2.imencode('.jpg', small_frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
    if success_encode:
        latest_buffer = buffer.tobytes()

    # --- Busiest 5-minute video logic ---
    if video_writer is None:
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        video_writer = cv2.VideoWriter(temp_video_path, fourcc, video_fps, (854, 480))
    video_writer.write(small_frame)

    if time.time() - current_segment_start >= video_segment_duration:
        video_writer.release()
        video_writer = None
        
        target_video_path = os.path.join(DEMO_DIR, "busiest_5min.mp4")
        if not os.path.exists(target_video_path):
            highest_car_count = -1  # Force save if the file was moved/deleted
            
        if len(current_segment_car_ids) > highest_car_count:
            highest_car_count = len(current_segment_car_ids)
            shutil.copy(temp_video_path, target_video_path)
            print(f"*** New Busiest 5-Minute Interval Found! ({highest_car_count} cars) - Saved to demo/busiest_5min.mp4 ***")
            
        current_segment_start = time.time()
        current_segment_car_ids = set()
    # ------------------------------------

    # Enforce real-time playback speed
    processing_time = time.perf_counter() - start
    expected_time = 1.0 / video_fps
    delay = 1
    if expected_time > processing_time:
        delay = max(1, int((expected_time - processing_time) * 1000))

    if cv2.waitKey(delay) & 0xFF == ord('q'):
        break

cap.release()
if video_writer is not None:
    video_writer.release()
cv2.destroyAllWindows()
print(f"Program finished. Images are saved in the '{SAVE_DIR}' folder.")
