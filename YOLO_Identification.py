import cv2
from ultralytics import YOLO
import time
import os
from datetime import datetime
from zoneinfo import ZoneInfo

# Track vehicles and their Y-coordinate history
# Format: {track_id: {'first_seen_time': timestamp, 'last_save_time': timestamp, 'y_history': []}}
track_history = {}

# data setup
SAVE_DIR = "HotFolder"
os.makedirs(SAVE_DIR, exist_ok=True)

# load model
model = YOLO("yolo11m.pt") 

# video source
liveCamera = 0 
cap = cv2.VideoCapture(liveCamera)

# try to set camera to 4k
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 3840)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 2160)

# configure window
main_window_name = "Car Identification"
cv2.namedWindow(main_window_name, cv2.WINDOW_NORMAL)

# Track program start time so we can skip initial detections
program_start_time = time.time()

while cap.isOpened():
    success, frame = cap.read()
    if not success:
        print("video issue")
        break
    
    start = time.perf_counter()

    # Get frame dimensions for rule of thirds calculation
    frame_h, frame_w, _ = frame.shape
    rule_of_thirds_left_boundary = frame_w // 3
    rule_of_thirds_right_boundary = 2 * (frame_w // 3)

    cv2.line(frame, (rule_of_thirds_left_boundary, 0), (rule_of_thirds_left_boundary, frame_h), (0, 255, 0), 1)
    cv2.line(frame, (rule_of_thirds_right_boundary, 0), (rule_of_thirds_right_boundary, frame_h), (0, 255, 0), 1)

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
                
                if rule_of_thirds_left_boundary < box_center_x < rule_of_thirds_right_boundary:
                    class_name = model.names[cls]
                    
                    if class_name in ['car', 'truck', 'bus']:
                        current_time = time.time()
                        
                        if track_id not in track_history:
                            track_history[track_id] = {
                                'first_seen_time': current_time,
                                'last_save_time': 0,
                                'y_history': []
                            }
                        
                        # Store Y coordinate to detect direction
                        track_history[track_id]['y_history'].append(box_center_y)
                        if len(track_history[track_id]['y_history']) > 30:
                            track_history[track_id]['y_history'].pop(0)

                        duration_in_frame = current_time - track_history[track_id]['first_seen_time']
                        time_since_last_save = current_time - track_history[track_id]['last_save_time']

                        if (
                            current_time - program_start_time >= 20
                            and duration_in_frame < 15
                            and time_since_last_save >= 1
                        ):
                            track_history[track_id]['last_save_time'] = current_time
                            
                            # Determine Direction (Arriving vs Leaving)
                            y_hist = track_history[track_id]['y_history']
                            direction = "unknown"
                            if len(y_hist) >= 5:
                                dy = y_hist[-1] - y_hist[0]
                                if dy > 10:
                                    direction = "arriving" # moving downward
                                elif dy < -10:
                                    direction = "leaving"  # moving upward

                            cropped_vehicle = frame[y1:y2, x1:x2]

                            if cropped_vehicle.size > 0:
                                timestamp = datetime.now(ZoneInfo("America/New_York")).strftime('%Y-%m-%d_%H-%M-%S-%f')
                                filename = f"{timestamp}__{direction}__{class_name}_{track_id}.jpg"
                                save_path = os.path.join(SAVE_DIR, filename)
                                cv2.imwrite(save_path, cropped_vehicle)
                                print(f"Saved: {class_name} ID: {track_id} | Dir: {direction} | Duration: {duration_in_frame:.1f}s")

        # Clean up old tracks
        obsolete_ids = set(track_history.keys()) - current_frame_track_ids
        for t_id in obsolete_ids:
            del track_history[t_id]

    end = time.perf_counter()
    fps = 1 / (end - start)

    cv2.putText(annotated_frame, f"FPS: {int(fps)}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
    cv2.imshow(main_window_name, annotated_frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
print(f"Program finished. Images are saved in the '{SAVE_DIR}' folder.")
