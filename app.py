from flask import Flask, render_template, request, jsonify, send_from_directory, Response
import os
import json
import csv
from datetime import datetime
import time

app = Flask(__name__)

latest_frame = None

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "state.json")
LOG_CSV = os.path.join(BASE_DIR, "Data", "CarLabels_Unprocessed.csv")
UNCONFIRMED_DIR = os.path.join(BASE_DIR, "Data", "Unconfirmed")
LABEL_MAP = os.path.join(BASE_DIR, "Data", "label_map.csv")

# Initialize state.json if not exists
if not os.path.exists(STATE_FILE):
    with open(STATE_FILE, "w") as f:
        json.dump({"houses": [], "car_status": {}}, f)

def get_state():
    with open(STATE_FILE, "r") as f:
        return json.load(f)

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)

def update_car_statuses():
    # Read the latest CSV and update the home/away status for cars
    if not os.path.exists(LOG_CSV):
        return
    
    latest_status = {}
    with open(LOG_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            car_id = row.get("ID")
            direction = row.get("direction")
            if direction == "arriving":
                latest_status[car_id] = "home"
            elif direction == "leaving":
                latest_status[car_id] = "away"
            
    state = get_state()
    changed = False
    for cid, status in latest_status.items():
        if state["car_status"].get(cid) != status:
            state["car_status"][cid] = status
            changed = True
            
    if changed:
        save_state(state)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/live")
def live():
    return render_template("timeline.html")

@app.route("/api/push_frame", methods=["POST"])
def api_push_frame():
    global latest_frame
    latest_frame = request.data
    return "OK", 200

def gen_frames():
    global latest_frame
    while True:
        if latest_frame is not None:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + latest_frame + b'\r\n')
        time.sleep(0.05)

@app.route("/video_feed")
def video_feed():
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route("/api/state", methods=["GET", "POST"])
def api_state():
    if request.method == "POST":
        data = request.json
        state = get_state()
        state["houses"] = data.get("houses", state["houses"])
        save_state(state)
        return jsonify({"status": "success"})
    else:
        update_car_statuses()
        return jsonify(get_state())

@app.route("/api/timeline", methods=["GET"])
def api_timeline():
    # Parse CSV for the timeline
    feed = []
    if os.path.exists(LOG_CSV):
        with open(LOG_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                filename = row.get("filename", "")
                parts = filename.split("__")
                timestamp_str = parts[0] if len(parts) > 0 else "Unknown"
                
                try:
                    dt = datetime.strptime(timestamp_str, "%Y-%m-%d_%H-%M-%S-%f")
                except:
                    continue
                
                display_time = dt.strftime("%b %d, %Y - %I:%M:%S %p")
                conf_val = float(row.get("confidence", 0.0)) if row.get("confidence") else 0.0
                
                event = {
                    "filename": filename,
                    "direction": row.get("direction", "unknown"),
                    "predicted_label": row.get("predicted_label", ""),
                    "id": row.get("ID", ""),
                    "track_id": row.get("track_id", ""),
                    "time": display_time,
                    "timestamp_obj": dt,
                    "confidence": conf_val,
                    "burst_images": [filename]
                }
                
                # Deduplication logic (Strategy 1 & 3 combined)
                is_burst = False
                for past_event in reversed(feed):
                    time_diff = (dt - past_event["timestamp_obj"]).total_seconds()
                    if time_diff > 60:
                        break # Stop looking if the event is older than 60 seconds
                        
                    past_track = past_event.get("track_id", past_event["id"])
                    curr_track = event.get("track_id", event["id"])
                        
                    if (past_track == curr_track) and past_event["direction"] == event["direction"]:
                        is_burst = True
                        past_event["burst_images"].append(filename)
                        
                        # Strategy 3: Max-Confidence Override overrides the label for the entire burst
                        if event["confidence"] > past_event["confidence"]:
                            past_event["filename"] = event["filename"]
                            past_event["confidence"] = event["confidence"]
                            past_event["time"] = event["time"]
                            past_event["timestamp_obj"] = event["timestamp_obj"]
                            past_event["predicted_label"] = event["predicted_label"]
                            past_event["id"] = event["id"]
                        break
                        
                if not is_burst:
                    feed.append(event)
                    
    for e in feed:
        del e["timestamp_obj"]
        
    # Reverse to have newest at top
    feed.reverse()
    return jsonify(feed)

@app.route("/api/labels", methods=["GET"])
def api_labels():
    labels = []
    if os.path.exists(LABEL_MAP):
        with open(LABEL_MAP, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) >= 2:
                    labels.append({"id": row[0], "label": row[1]})
    return jsonify(labels)

@app.route("/api/update_label", methods=["POST"])
def api_update_label():
    data = request.json
    filenames = data.get("filenames")
    new_id = data.get("new_id")
    new_label = data.get("new_label")
    
    if not filenames or not new_id or not new_label:
        return jsonify({"status": "error", "message": "Missing data"}), 400
        
    if not os.path.exists(LOG_CSV):
        return jsonify({"status": "error", "message": "CSV not found"}), 404
        
    rows = []
    updated = False
    with open(LOG_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            if row.get("filename") in filenames:
                row["ID"] = new_id
                row["predicted_label"] = new_label
                updated = True
            rows.append(row)
            
    if updated:
        with open(LOG_CSV, "w", encoding="utf-8", newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
            
    return jsonify({"status": "success"})

@app.route("/images/<filename>")
def serve_image(filename):
    return send_from_directory(UNCONFIRMED_DIR, filename)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
