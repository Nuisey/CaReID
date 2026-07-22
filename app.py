from flask import Flask, render_template, request, jsonify, send_from_directory, Response
import os
import json
import csv
from datetime import datetime
import time
from dotenv import load_dotenv

load_dotenv()
import threading
import shutil
import queue

try:
    from google import genai
    import base64
    import PIL.Image
except ImportError:
    pass

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GEMINI_STATE_FILE = os.path.join(BASE_DIR, "Data", "gemini_state.json")

def load_gemini_state():
    if os.path.exists(GEMINI_STATE_FILE):
        try:
            with open(GEMINI_STATE_FILE, "r") as f:
                data = json.load(f)
                return data.get("tasks", {}), data.get("results", {}), data.get("batch_job_name", None), data.get("batch_start_time", None), data.get("logs", [])
        except:
            pass
    return {}, {}, None, None, []

def save_gemini_state():
    with open(GEMINI_STATE_FILE, "w") as f:
        json.dump({
            "tasks": gemini_tasks,
            "results": gemini_results,
            "batch_job_name": current_batch_job_name,
            "batch_start_time": current_batch_start_time,
            "logs": sync_logs
        }, f)

gemini_tasks, gemini_results, current_batch_job_name, current_batch_start_time, sync_logs = load_gemini_state()

def add_sync_log(msg):
    t = datetime.now().strftime('%H:%M:%S')
    sync_logs.append(f"[{t}] {msg}")
    if len(sync_logs) > 100: sync_logs.pop(0)
    save_gemini_state()

def gemini_worker():
    global current_batch_job_name
    while True:
        queued_tracks = [t for t, s in gemini_tasks.items() if s == 'queued']
        if not queued_tracks:
            time.sleep(2)
            continue
            
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            time.sleep(5)
            continue
            
        client = genai.Client(api_key=api_key)
        
        try:
            feed = get_timeline_feed()
            feed_dict = {(e.get('track_id') or e.get('id')): e for e in feed}
            
            labels = []
            if os.path.exists(LABEL_MAP):
                with open(LABEL_MAP, "r", encoding="utf-8") as f:
                    for row in csv.reader(f):
                        if len(row) >= 2: labels.append(row[1])
                        
            prompt_text = f"You are a car identification expert. Look at these images of the EXACT SAME car from different angles. Here is a list of known cars in my database: {', '.join(labels)}. If the car exactly matches one of these, reply ONLY with the exact label from the list. If it is a completely new car not on the list, propose a short descriptive label (e.g. 'Silver 2020 Honda Civic') prefixed with 'NEW - '."
            
            current_backoff = 5.0
            
            for t_id in queued_tracks:
                if gemini_tasks.get(t_id) != 'queued':
                    continue
                    
                event = feed_dict.get(t_id)
                if not event: continue
                
                contents = [prompt_text]
                valid = False
                for img_name in event['burst_images']:
                    img_path = os.path.join(UNCONFIRMED_DIR, img_name)
                    if os.path.exists(img_path):
                        contents.append(PIL.Image.open(img_path))
                        valid = True
                        
                if not valid:
                    gemini_tasks[t_id] = 'error'
                    save_gemini_state()
                    continue
                    
                gemini_tasks[t_id] = 'checking'
                save_gemini_state()
                add_sync_log(f"Started checking track {t_id}...")
                
                while True:
                    try:
                        response = client.models.generate_content(
                            model="gemini-3.5-flash",
                            contents=contents
                        )
                        result = response.text.strip() if response.text else ""
                        gemini_results[t_id] = result
                        gemini_tasks[t_id] = 'checked'
                        add_sync_log(f"Finished track {t_id} -> {result}")
                        save_gemini_state()
                        current_backoff = 5.0
                        break
                    except Exception as e:
                        err_str = str(e)
                        if '429' in err_str or 'quota' in err_str.lower() or 'exhausted' in err_str.lower() or '503' in err_str or 'unavailable' in err_str.lower():
                            add_sync_log(f"API busy ({'503' if '503' in err_str else '429'}). Pausing {current_backoff}s...")
                            time.sleep(current_backoff)
                            current_backoff = min(current_backoff * 2, 300)
                        else:
                            add_sync_log(f"Gemini API Error on {t_id}: {err_str[:100]}")
                            gemini_tasks[t_id] = 'error'
                            save_gemini_state()
                            break
                            
                time.sleep(4.0)
                        
                # Auto-sync tracks that agree
                feed = get_timeline_feed()
                auto_sync_ids = []
                for event in feed:
                    t_id = event.get('track_id') or event.get('id')
                    if gemini_tasks.get(t_id) == 'checked':
                        gem_label = gemini_results.get(t_id, '')
                        if gem_label and gem_label.lower() == event['predicted_label'].lower():
                            auto_sync_ids.append(t_id)
                            
                if auto_sync_ids:
                    sync_tracks(auto_sync_ids, feed)
                        
        except Exception as e:
            add_sync_log(f"Worker Error: {str(e)[:100]}")
            time.sleep(10)

threading.Thread(target=gemini_worker, daemon=True).start()

latest_frame = None

# Paths
# BASE_DIR is defined at top of file
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

@app.route("/sync")
def sync():
    return render_template("sync.html")

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

def get_timeline_feed():
    feed = []
    if os.path.exists(LOG_CSV):
        with open(LOG_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                filename = row.get("filename", "")
                parts = filename.split("__")
                timestamp_str = parts[0] if len(parts) > 0 else "Unknown"
                
                try: dt = datetime.strptime(timestamp_str, "%Y-%m-%d_%H-%M-%S-%f")
                except: continue
                
                display_time = dt.strftime("%b %d, %Y - %I:%M:%S %p")
                local_id = row.get('track_id', row.get('ID', str(len(feed))))
                
                event = {
                    "id": local_id,
                    "filename": filename,
                    "track_id": filename,
                    "local_id": local_id,
                    "time": display_time,
                    "timestamp_obj": dt,
                    "direction": row.get("direction", "unknown"),
                    "predicted_label": row.get("predicted_label", ""),
                    "confidence": float(row.get("confidence", 0.0)) if row.get("confidence") else 0.0,
                    "burst_images": [filename]
                }
                
                is_burst = False
                for past_event in reversed(feed):
                    time_diff = abs((dt - past_event["timestamp_obj"]).total_seconds())
                    if time_diff > 60: break
                        
                    past_track = past_event.get("local_id")
                    curr_track = event.get("local_id")
                        
                    if (past_track == curr_track) and past_event["direction"] == event["direction"]:
                        is_burst = True
                        past_event["burst_images"].append(filename)
                        
                        if event["confidence"] > past_event["confidence"]:
                            past_event["filename"] = event["filename"]
                            past_event["id"] = event["id"]
                            past_event["track_id"] = event["track_id"]
                            past_event["confidence"] = event["confidence"]
                            past_event["time"] = event["time"]
                            past_event["timestamp_obj"] = event["timestamp_obj"]
                            past_event["predicted_label"] = event["predicted_label"]
                        break
                        
                if not is_burst:
                    feed.append(event)
                    
    feed.reverse()
    return feed

@app.route("/api/timeline", methods=["GET"])
def api_timeline():
    feed = get_timeline_feed()
    for e in feed:
        if "timestamp_obj" in e: del e["timestamp_obj"]
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
    
@app.route("/api/unconfirmed_image/<filename>")
def serve_unconfirmed_image(filename):
    return send_from_directory(UNCONFIRMED_DIR, filename)

@app.route("/api/sync_status", methods=["GET"])
def api_sync_status():
    feed = get_timeline_feed()
    tracks = []
    checking = 0
    checked = 0
    for event in feed:
        track_id = event.get('track_id') or event.get('id')
        status = gemini_tasks.get(track_id, 'pending')
        gem_label = gemini_results.get(track_id, '')
        if status in ['checking', 'queued']: checking += 1
        elif status == 'checked': checked += 1
        
        tracks.append({
            "track_id": track_id,
            "local_label": event['predicted_label'],
            "images": event['burst_images'],
            "status": status,
            "gemini_label": gem_label,
            "gemini_agrees": (gem_label.lower() == event['predicted_label'].lower()) if gem_label else False
        })
        
    total_unsynced_images = sum(len(e['burst_images']) for e in feed)

    return jsonify({
        "unconfirmed_count": len(feed),
        "total_unsynced_images": total_unsynced_images,
        "checking_count": checking,
        "checked_count": checked,
        "tracks": tracks,
        "logs": list(reversed(sync_logs)),
        "batch_start_time": current_batch_start_time if current_batch_job_name else None
    })

    # Process handled by background queue

@app.route("/api/run_gemini_batch", methods=["POST"])
def api_run_gemini_batch():
    api_key = os.getenv("GEMINI_API_KEY") or request.json.get("api_key")
    if not api_key: return jsonify({"error": "No API key"}), 400
    
    feed = get_timeline_feed()
    
    queued_count = 0
    auto_checked = 0
    for event in feed:
        t_id = event.get('track_id') or event.get('id')
        if gemini_tasks.get(t_id) in ['checking', 'checked', 'queued']: continue
        
        # Bypass Gemini for high confidence tracks and auto-approve YOLO's guess
        if event['confidence'] >= 0.75:
            gemini_results[t_id] = event['predicted_label']
            gemini_tasks[t_id] = 'checked'
            auto_checked += 1
            continue
            
        gemini_tasks[t_id] = 'queued'
        queued_count += 1
        
    if queued_count > 0 or auto_checked > 0:
        save_gemini_state()
        log_msg = []
        if auto_checked > 0: log_msg.append(f"Auto-approved {auto_checked} high-confidence tracks.")
        if queued_count > 0: log_msg.append(f"Queued {queued_count} low-confidence tracks for Gemini.")
        add_sync_log(" ".join(log_msg))
    return jsonify({"status": "started"})

@app.route("/api/cancel_gemini_batch", methods=["POST"])
def api_cancel_gemini_batch():
    global current_batch_job_name, current_batch_start_time
    
    current_batch_job_name = None
    current_batch_start_time = None
        
    canceled_count = 0
    for track_id, status in list(gemini_tasks.items()):
        if status in ['queued', 'checking']:
            del gemini_tasks[track_id]
            canceled_count += 1
            
    save_gemini_state()
    add_sync_log(f"Canceled. {canceled_count} tracks removed from queue.")
    return jsonify({"status": "canceled"})

@app.route("/api/update_gemini_label", methods=["POST"])
def api_update_gemini_label():
    data = request.json
    t_id = data.get("track_id")
    new_label = data.get("label")
    if t_id and new_label is not None:
        gemini_results[t_id] = new_label.strip()
        gemini_tasks[t_id] = 'checked'
        save_gemini_state()
    return jsonify({"status": "success"})

def sync_tracks(track_ids, feed):
    if not track_ids: return
    
    to_delete_filenames = set()
    UNSYNCED_DIR = os.path.join(BASE_DIR, "Data", "unsynced")
    os.makedirs(UNSYNCED_DIR, exist_ok=True)
    
    for event in feed:
        t_id = event.get('track_id') or event.get('id')
        if t_id in track_ids:
            label = gemini_results.get(t_id) or event['predicted_label']
            if label.startswith("NEW - "): label = label[6:]
            
            car_dir = os.path.join(UNSYNCED_DIR, label.replace(" ", "_"))
            os.makedirs(car_dir, exist_ok=True)
            
            for img in event['burst_images']:
                to_delete_filenames.add(img)
                src = os.path.join(UNCONFIRMED_DIR, img)
                if os.path.exists(src):
                    shutil.move(src, os.path.join(car_dir, img))
                    
    if os.path.exists(LOG_CSV):
        rows = []
        with open(LOG_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            for row in reader:
                if row.get("filename") not in to_delete_filenames:
                    rows.append(row)
        with open(LOG_CSV, "w", newline='', encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
            
    for tid in track_ids:
        if tid in gemini_tasks: del gemini_tasks[tid]
        if tid in gemini_results: del gemini_results[tid]
        
    save_gemini_state()
    add_sync_log(f"Auto-Synced {len(track_ids)} verified tracks to the 'unsynced' folder.")

@app.route("/api/approve_sync", methods=["POST"])
def api_approve_sync():
    track_ids = request.json.get("track_ids", [])
    if track_ids:
        feed = get_timeline_feed()
        sync_tracks(track_ids, feed)
    return jsonify({"status": "success"})

if __name__ == "__main__":
    app.run(debug=True, port=5000)
