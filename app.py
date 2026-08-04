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

@app.route("/label")
def label():
    return render_template("label.html")

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

PAUSE_FILE = os.path.join(BASE_DIR, "pause_camera.txt")

@app.route("/api/pause_status", methods=["GET"])
def api_pause_status():
    return jsonify({"paused": os.path.exists(PAUSE_FILE)})

@app.route("/api/toggle_pause", methods=["POST"])
def api_toggle_pause():
    if os.path.exists(PAUSE_FILE):
        os.remove(PAUSE_FILE)
        return jsonify({"paused": False})
    else:
        with open(PAUSE_FILE, "w") as f:
            f.write("paused")
        return jsonify({"paused": True})

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
            rows = list(reader)
            for row in reversed(rows):
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
                    "burst_images": [filename],
                    "all_track_ids": [filename]
                }
                
                is_burst = False
                for past_event in reversed(feed):
                    time_diff = abs((dt - past_event["timestamp_obj"]).total_seconds())
                    if time_diff > 60: break
                        
                    past_track = past_event.get("local_id")
                    curr_track = event.get("local_id")
                    
                    past_primary = past_event.get("track_id")
                    curr_primary = event.get("track_id")
                    
                    past_gemini = gemini_results.get(past_primary)
                    curr_gemini = gemini_results.get(curr_primary)
                    
                    same_track = (past_track == curr_track)
                    same_gemini = (past_gemini and curr_gemini and past_gemini == curr_gemini and time_diff <= 30)
                        
                    if (same_track or same_gemini) and past_event["direction"] == event["direction"]:
                        is_burst = True
                        past_event["burst_images"].append(filename)
                        if curr_primary not in past_event["all_track_ids"]:
                            past_event["all_track_ids"].append(curr_primary)
                        
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
                    
    return feed

@app.route("/api/reference_image/<path:label>/<direction>/<int:index>")
def api_reference_image(label, direction, index):
    label_folder = label.replace(" ", "_")
    matches = []
    
    # 1. Search unsynced folder directly
    unsynced_path = os.path.join(BASE_DIR, "Data", "unsynced", label_folder)
    if os.path.exists(unsynced_path):
        for f in os.listdir(unsynced_path):
            if f.endswith((".jpg", ".png")):
                matches.append((unsynced_path, f))
                
    # 2. Search Gallery via CSV mappings
    gallery_dir = os.path.join(BASE_DIR, "Data", "Gallery", "LabeledCarDataPhotos")
    if os.path.exists(gallery_dir):
        import csv
        label_id = None
        label_map_path = os.path.join(BASE_DIR, "Data", "label_map.csv")
        if os.path.exists(label_map_path):
            with open(label_map_path, 'r', encoding='utf-8') as f:
                for row in csv.reader(f):
                    if len(row) >= 2 and row[1].lower() == label.lower():
                        label_id = row[0]
                        break
        
        if label_id is not None:
            gallery_csv_path = os.path.join(BASE_DIR, "Data", "Gallery", "Gallery.csv")
            if os.path.exists(gallery_csv_path):
                with open(gallery_csv_path, 'r', encoding='utf-8') as f:
                    for row in csv.DictReader(f):
                        if row.get('id') == label_id:
                            fname = row.get('path')
                            if fname and os.path.exists(os.path.join(gallery_dir, fname)):
                                matches.append((gallery_dir, fname))

    # Filter by direction first
    dir_matches = [m for m in matches if f"__{direction}__" in m[1] or f"__{direction.capitalize()}__" in m[1]]
    if len(dir_matches) > index:
        return send_from_directory(dir_matches[index][0], dir_matches[index][1])
        
    # Fallback to any direction if not enough
    if len(matches) > index:
        return send_from_directory(matches[index][0], matches[index][1])
        
    return "Not Found", 404

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

@app.route("/api/unsynced_groups", methods=["GET"])
def api_unsynced_groups():
    UNSYNCED_DIR = os.path.join(BASE_DIR, "Data", "unsynced")
    groups = {}
    if os.path.exists(UNSYNCED_DIR):
        for label_folder in os.listdir(UNSYNCED_DIR):
            folder_path = os.path.join(UNSYNCED_DIR, label_folder)
            if os.path.isdir(folder_path):
                images = [f for f in os.listdir(folder_path) if f.endswith(('.jpg', '.png'))]
                if images:
                    groups[label_folder.replace("_", " ")] = images
    return jsonify(groups)

@app.route("/api/commit_labels", methods=["POST"])
def api_commit_labels():
    data = request.json
    commits = data.get("commits", {})
    deletions = data.get("deletions", {})
    rejected_count = sum(len(imgs) for imgs in deletions.values())
    
    UNSYNCED_DIR = os.path.join(BASE_DIR, "Data", "unsynced")
    GALLERY_DIR = os.path.join(BASE_DIR, "Data", "Gallery", "LabeledCarDataPhotos")
    TRASH_DIR = os.path.join(BASE_DIR, "Data", "Trash")
    os.makedirs(GALLERY_DIR, exist_ok=True)
    os.makedirs(TRASH_DIR, exist_ok=True)
    
    label_to_id = {}
    if os.path.exists(LABEL_MAP):
        with open(LABEL_MAP, 'r', encoding='utf-8') as f:
            for row in csv.reader(f):
                if len(row) >= 2:
                    label_to_id[row[1].lower()] = row[0]
                    
    gallery_csv_path = os.path.join(BASE_DIR, "Data", "Gallery", "Gallery.csv")
    new_rows = []
    
    undo_state = {
        "confirmed": [],
        "deleted": []
    }
    
    confirmed_count = 0
    # Process commits
    for label, images in commits.items():
        label_id = label_to_id.get(label.lower())
        if not label_id:
            continue
        
        label_folder = label.replace(" ", "_")
        src_dir = os.path.join(UNSYNCED_DIR, label_folder)
        
        for img in images:
            src = os.path.join(src_dir, img)
            dst = os.path.join(GALLERY_DIR, img)
            if os.path.exists(src):
                shutil.move(src, dst)
                new_rows.append({"path": img, "id": label_id})
                confirmed_count += 1
                undo_state["confirmed"].append({"label": label, "image": img, "id": label_id})
                
    # Process deletions
    for label, images in deletions.items():
        label_folder = label.replace(" ", "_")
        src_dir = os.path.join(UNSYNCED_DIR, label_folder)
        for img in images:
            src = os.path.join(src_dir, img)
            dst = os.path.join(TRASH_DIR, img)
            if os.path.exists(src):
                shutil.move(src, dst)
                undo_state["deleted"].append({"label": label, "image": img})
                
    undo_state_path = os.path.join(BASE_DIR, "Data", "last_commit_state.json")
    with open(undo_state_path, 'w') as f:
        json.dump(undo_state, f)
        
    if new_rows:
        file_exists = os.path.exists(gallery_csv_path)
        with open(gallery_csv_path, 'a', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=["path", "id"])
            if not file_exists:
                writer.writeheader()
            writer.writerows(new_rows)
            
    # Save undo state
    state_path = os.path.join(BASE_DIR, "Data", "last_commit_state.json")
    with open(state_path, 'w') as f:
        json.dump(undo_state, f)
            
    metrics_path = os.path.join(BASE_DIR, "Data", "sync_metrics.json")
    metrics = {"total_processed": 0, "total_rejected": 0}
    if os.path.exists(metrics_path):
        with open(metrics_path, 'r') as f:
            metrics = json.load(f)
            
    metrics["total_processed"] += confirmed_count + rejected_count
    metrics["total_rejected"] += rejected_count
    
    with open(metrics_path, 'w') as f:
        json.dump(metrics, f)
        
    return jsonify({"status": "success", "confirmed": confirmed_count})

@app.route("/api/reassign_unsynced", methods=["POST"])
def api_reassign_unsynced():
    data = request.json
    old_label = data.get("old_label")
    new_label = data.get("new_label")
    images = data.get("images", [])
    
    if not old_label or not new_label or not images:
        return jsonify({"status": "error", "message": "Missing parameters"}), 400
        
    old_dir = os.path.join(BASE_DIR, "Data", "unsynced", old_label.replace(" ", "_"))
    new_dir = os.path.join(BASE_DIR, "Data", "unsynced", new_label.replace(" ", "_"))
    os.makedirs(new_dir, exist_ok=True)
    
    moved_count = 0
    for img in images:
        src = os.path.join(old_dir, img)
        dst = os.path.join(new_dir, img)
        if os.path.exists(src):
            shutil.move(src, dst)
            moved_count += 1
            
    return jsonify({"status": "success", "moved_count": moved_count})

@app.route("/api/undo_commit", methods=["POST"])
def api_undo_commit():
    state_path = os.path.join(BASE_DIR, "Data", "last_commit_state.json")
    if not os.path.exists(state_path):
        return jsonify({"status": "error", "message": "Nothing to undo"}), 400
        
    with open(state_path, 'r') as f:
        undo_state = json.load(f)
        
    UNSYNCED_DIR = os.path.join(BASE_DIR, "Data", "unsynced")
    GALLERY_DIR = os.path.join(BASE_DIR, "Data", "Gallery", "LabeledCarDataPhotos")
    TRASH_DIR = os.path.join(BASE_DIR, "Data", "Trash")
    
    confirmed = undo_state.get("confirmed", [])
    deleted = undo_state.get("deleted", [])
    
    if not confirmed and not deleted:
        return jsonify({"status": "error", "message": "Nothing to undo"}), 400
        
    # Undo confirmed images
    reverted_paths = set()
    for item in confirmed:
        label = item["label"]
        img = item["image"]
        label_folder = label.replace(" ", "_")
        dest_dir = os.path.join(UNSYNCED_DIR, label_folder)
        os.makedirs(dest_dir, exist_ok=True)
        
        src = os.path.join(GALLERY_DIR, img)
        dst = os.path.join(dest_dir, img)
        if os.path.exists(src):
            shutil.move(src, dst)
            reverted_paths.add(img)
            
    # Undo deleted images
    for item in deleted:
        label = item["label"]
        img = item["image"]
        label_folder = label.replace(" ", "_")
        dest_dir = os.path.join(UNSYNCED_DIR, label_folder)
        os.makedirs(dest_dir, exist_ok=True)
        
        src = os.path.join(TRASH_DIR, img)
        dst = os.path.join(dest_dir, img)
        if os.path.exists(src):
            shutil.move(src, dst)
            
    # Remove from Gallery.csv
    gallery_csv_path = os.path.join(BASE_DIR, "Data", "Gallery", "Gallery.csv")
    if os.path.exists(gallery_csv_path) and reverted_paths:
        rows = []
        with open(gallery_csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            for row in reader:
                if row.get("path") not in reverted_paths:
                    rows.append(row)
        with open(gallery_csv_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
            
    # Revert metrics
    metrics_path = os.path.join(BASE_DIR, "Data", "sync_metrics.json")
    if os.path.exists(metrics_path):
        with open(metrics_path, 'r') as f:
            metrics = json.load(f)
        metrics["total_processed"] = max(0, metrics["total_processed"] - len(confirmed) - len(deleted))
        metrics["total_rejected"] = max(0, metrics["total_rejected"] - len(deleted))
        with open(metrics_path, 'w') as f:
            json.dump(metrics, f)
            
    # Clear undo state
    os.remove(state_path)
    
    return jsonify({"status": "success"})

@app.route("/api/sync_metrics", methods=["GET"])
def api_sync_metrics():
    metrics_path = os.path.join(BASE_DIR, "Data", "sync_metrics.json")
    if os.path.exists(metrics_path):
        with open(metrics_path, 'r') as f:
            return jsonify(json.load(f))
    return jsonify({"total_processed": 0, "total_rejected": 0})

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

@app.route("/gallery")
def gallery_page():
    return render_template("gallery.html")

@app.route("/api/gallery_data", methods=["GET"])
def api_gallery_data():
    label_map_path = os.path.join(BASE_DIR, "Data", "label_map.csv")
    id_to_label = {}
    if os.path.exists(label_map_path):
        with open(label_map_path, 'r', encoding='utf-8') as f:
            for row in csv.reader(f):
                if len(row) >= 2:
                    id_to_label[row[0]] = row[1]
                    
    gallery_csv_path = os.path.join(BASE_DIR, "Data", "Gallery", "Gallery.csv")
    gallery_data = {}
    
    if os.path.exists(gallery_csv_path):
        with open(gallery_csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                img = row.get("path")
                lbl_id = row.get("id")
                label_name = id_to_label.get(lbl_id, f"Unknown ID {lbl_id}")
                
                if label_name not in gallery_data:
                    gallery_data[label_name] = []
                gallery_data[label_name].append(img)
                
    return jsonify(gallery_data)

@app.route("/api/update_gallery_labels", methods=["POST"])
def api_update_gallery_labels():
    data = request.json
    images = data.get("images", [])
    new_label_str = data.get("new_label", "").strip()
    
    if not images or not new_label_str:
        return jsonify({"status": "error", "message": "Missing images or label"}), 400
        
    label_map_path = os.path.join(BASE_DIR, "Data", "label_map.csv")
    label_to_id = {}
    max_id = -1
    
    # Read existing labels
    if os.path.exists(label_map_path):
        with open(label_map_path, 'r', encoding='utf-8') as f:
            for row in csv.reader(f):
                if len(row) >= 2:
                    label_to_id[row[1].lower()] = row[0]
                    try:
                        max_id = max(max_id, int(row[0]))
                    except:
                        pass
                        
    # Find or create new label ID
    new_label_lower = new_label_str.lower()
    if new_label_lower in label_to_id:
        new_label_id = label_to_id[new_label_lower]
    else:
        new_label_id = str(max_id + 1)
        with open(label_map_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([new_label_id, new_label_str])
            
    # Update Gallery.csv
    gallery_csv_path = os.path.join(BASE_DIR, "Data", "Gallery", "Gallery.csv")
    updated_rows = []
    
    if os.path.exists(gallery_csv_path):
        with open(gallery_csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("path") in images:
                    row["id"] = new_label_id
                updated_rows.append(row)
                
        with open(gallery_csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=["path", "id"])
            writer.writeheader()
            writer.writerows(updated_rows)
            
    return jsonify({"status": "success"})

def find_image_path(filename):
    p = os.path.join(UNCONFIRMED_DIR, filename)
    if os.path.exists(p): return UNCONFIRMED_DIR
    
    for base in [os.path.join(BASE_DIR, "Data", "unsynced"), os.path.join(BASE_DIR, "Data", "Gallery")]:
        if os.path.exists(base):
            for label_dir in os.listdir(base):
                d = os.path.join(base, label_dir)
                if os.path.isdir(d):
                    p = os.path.join(d, filename)
                    if os.path.exists(p):
                        return d
    return None

@app.route("/images/<filename>")
def serve_image(filename):
    d = find_image_path(filename)
    if d: return send_from_directory(d, filename)
    return "Not found", 404
    
@app.route("/api/unconfirmed_image/<filename>")
def serve_unconfirmed_image(filename):
    d = find_image_path(filename)
    if d: return send_from_directory(d, filename)
    return "Not found", 404

def get_unsynced_feed():
    feed = get_timeline_feed()
    unsynced = []
    for e in feed:
        if e['burst_images']:
            first_img = e['burst_images'][0]
            if os.path.exists(os.path.join(UNCONFIRMED_DIR, first_img)):
                unsynced.append(e)
    return unsynced

@app.route("/api/sync_status", methods=["GET"])
def api_sync_status():
    feed = get_unsynced_feed()
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
        
    UNSYNCED_DIR = os.path.join(BASE_DIR, "Data", "unsynced")
    unsynced_image_count = 0
    if os.path.exists(UNSYNCED_DIR):
        for root, dirs, files in os.walk(UNSYNCED_DIR):
            unsynced_image_count += len([f for f in files if f.endswith(".jpg") or f.endswith(".png")])
            
    GALLERY_DIR = os.path.join(BASE_DIR, "Data", "Gallery")
    gallery_image_count = 0
    if os.path.exists(GALLERY_DIR):
        for root, dirs, files in os.walk(GALLERY_DIR):
            gallery_image_count += len([f for f in files if f.endswith(".jpg") or f.endswith(".png")])

    return jsonify({
        "unconfirmed_count": len(feed),
        "unsynced_image_count": unsynced_image_count,
        "gallery_image_count": gallery_image_count,
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
    
    feed = get_unsynced_feed()
    
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

def save_sync_undo_state(state):
    path = os.path.join(BASE_DIR, "Data", "last_sync_undo.json")
    with open(path, "w") as f:
        json.dump(state, f)

def get_sync_undo_state():
    path = os.path.join(BASE_DIR, "Data", "last_sync_undo.json")
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {}

def sync_tracks(track_ids, feed):
    if not track_ids: return
    
    to_delete_filenames = set()
    UNSYNCED_DIR = os.path.join(BASE_DIR, "Data", "unsynced")
    os.makedirs(UNSYNCED_DIR, exist_ok=True)
    
    undo_tracks = []
    
    for event in feed:
        t_id = event.get('track_id') or event.get('id')
        if t_id in track_ids:
            label = gemini_results.get(t_id) or event['predicted_label']
            if label.startswith("NEW - "): label = label[6:]
            
            car_dir = os.path.join(UNSYNCED_DIR, label.replace(" ", "_"))
            os.makedirs(car_dir, exist_ok=True)
            
            undo_track = {
                "track_id": t_id,
                "label": label,
                "images": list(event['burst_images']),
                "all_track_ids": list(event.get('all_track_ids', [t_id])),
                "gemini_results": {},
                "gemini_tasks": {}
            }
            
            for img in event['burst_images']:
                to_delete_filenames.add(img)
                src = os.path.join(UNCONFIRMED_DIR, img)
                if os.path.exists(src):
                    shutil.move(src, os.path.join(car_dir, img))
                    
            for sub_id in event.get('all_track_ids', [t_id]):
                if sub_id in gemini_results:
                    undo_track["gemini_results"][sub_id] = gemini_results[sub_id]
                    del gemini_results[sub_id]
                if sub_id in gemini_tasks:
                    undo_track["gemini_tasks"][sub_id] = gemini_tasks[sub_id]
                    del gemini_tasks[sub_id]
                    
            undo_tracks.append(undo_track)
            
    save_sync_undo_state({"type": "approve", "tracks": undo_tracks})
    save_gemini_state()
    add_sync_log(f"Auto-Synced {len(track_ids)} verified tracks to the 'unsynced' folder.")

@app.route("/api/approve_sync", methods=["POST"])
def api_approve_sync():
    track_ids = request.json.get("track_ids", [])
    if track_ids:
        feed = get_timeline_feed()
        sync_tracks(track_ids, feed)
    return jsonify({"status": "success"})

@app.route("/api/trash_track", methods=["POST"])
def api_trash_track():
    track_id = request.json.get("track_id")
    if not track_id: return jsonify({"status": "error"}), 400
    
    feed = get_timeline_feed()
    all_ids_to_clean = [track_id]
    
    TRASH_UNCONFIRMED_DIR = os.path.join(BASE_DIR, "Data", "Trash", "Unconfirmed")
    os.makedirs(TRASH_UNCONFIRMED_DIR, exist_ok=True)
    
    undo_track = {
        "track_id": track_id,
        "images": [],
        "all_track_ids": [],
        "gemini_results": {},
        "gemini_tasks": {}
    }
    
    for event in feed:
        t_id = event.get('track_id') or event.get('id')
        if t_id == track_id:
            all_ids_to_clean = event.get('all_track_ids', [track_id])
            undo_track["all_track_ids"] = list(all_ids_to_clean)
            undo_track["images"] = list(event.get('burst_images', []))
            
            for img in event.get('burst_images', []):
                src = os.path.join(UNCONFIRMED_DIR, img)
                dst = os.path.join(TRASH_UNCONFIRMED_DIR, img)
                if os.path.exists(src):
                    shutil.move(src, dst)
            break
            
    for sub_id in all_ids_to_clean:
        if sub_id in gemini_tasks:
            undo_track["gemini_tasks"][sub_id] = gemini_tasks[sub_id]
            del gemini_tasks[sub_id]
        if sub_id in gemini_results:
            undo_track["gemini_results"][sub_id] = gemini_results[sub_id]
            del gemini_results[sub_id]
            
    save_sync_undo_state({"type": "trash", "tracks": [undo_track]})
    save_gemini_state()
    return jsonify({"status": "success"})

@app.route("/api/undo_sync", methods=["POST"])
def api_undo_sync():
    state = get_sync_undo_state()
    if not state or not state.get("tracks"):
        return jsonify({"status": "error", "message": "Nothing to undo"}), 400
        
    UNSYNCED_DIR = os.path.join(BASE_DIR, "Data", "unsynced")
    TRASH_UNCONFIRMED_DIR = os.path.join(BASE_DIR, "Data", "Trash", "Unconfirmed")
    
    restored_count = 0
    for track in state["tracks"]:
        images = track.get("images", [])
        
        # Restore images
        if state["type"] == "approve":
            label = track.get("label", "")
            car_dir = os.path.join(UNSYNCED_DIR, label.replace(" ", "_"))
            for img in images:
                src = os.path.join(car_dir, img)
                dst = os.path.join(UNCONFIRMED_DIR, img)
                if os.path.exists(src):
                    shutil.move(src, dst)
        elif state["type"] == "trash":
            for img in images:
                src = os.path.join(TRASH_UNCONFIRMED_DIR, img)
                dst = os.path.join(UNCONFIRMED_DIR, img)
                if os.path.exists(src):
                    shutil.move(src, dst)
                    
        # Restore Gemini state
        for sub_id, res in track.get("gemini_results", {}).items():
            gemini_results[sub_id] = res
        for sub_id, status in track.get("gemini_tasks", {}).items():
            gemini_tasks[sub_id] = status
            
        restored_count += 1
        
    save_gemini_state()
    save_sync_undo_state({}) # Clear undo state
    
    return jsonify({"status": "success", "restored_count": restored_count})

if __name__ == "__main__":
    app.run(debug=True, port=5000)
