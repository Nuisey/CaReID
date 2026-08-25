import os
import csv
import tkinter as tk
from tkinter import ttk, messagebox
from collections import defaultdict
from PIL import Image, ImageTk

DATA_FILE = os.path.join("Data", "CarLabels_Unprocessed.csv")

def get_dates():
    dates = set()
    if not os.path.exists(DATA_FILE): return dates
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            filename = row.get('filename', '')
            if '_' in filename:
                date_str = filename.split('_')[0]
                dates.add(date_str)
    return sorted(list(dates), reverse=True)

def find_image(filename, label):
    possible_dirs = [
        os.path.join("Data", "Unprocessed"),
        os.path.join("Data", "Unsynced"),
        os.path.join("Data", "unsynced", label.replace(" ", "_")),
        os.path.join("Data", "Unseen"),
        os.path.join("Data", "Unconfirmed"),
        os.path.join("Data", "Trash"),
        os.path.join("Data", "Gallery", "LabeledCarDataPhotos")
    ]
    for d in possible_dirs:
        p = os.path.join(d, filename)
        if os.path.exists(p):
            return p
    return None

class ReviewApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Daily Accuracy Reviewer")
        self.geometry("800x600")
        
        self.dates = get_dates()
        self.tracks = []
        self.current_idx = 0
        self.results = []
        self.correct_count = 0
        self.total_count = 0
        self.target_date = ""
        
        self.setup_ui()
        
    def setup_ui(self):
        # Top frame for date selection
        top_frame = tk.Frame(self)
        top_frame.pack(pady=10)
        
        tk.Label(top_frame, text="Select Date:", font=("Arial", 12)).pack(side=tk.LEFT, padx=5)
        
        self.date_var = tk.StringVar()
        if self.dates:
            self.date_var.set(self.dates[0])
            
        self.date_dropdown = ttk.Combobox(top_frame, textvariable=self.date_var, values=self.dates, state="readonly", width=15, font=("Arial", 12))
        self.date_dropdown.pack(side=tk.LEFT, padx=5)
        
        self.start_btn = tk.Button(top_frame, text="Start Review", command=self.load_date, font=("Arial", 12), bg="#4CAF50", fg="white")
        self.start_btn.pack(side=tk.LEFT, padx=5)
        
        # Info label
        self.info_label = tk.Label(self, text="Ready to review.", font=("Arial", 14, "bold"))
        self.info_label.pack(pady=10)
        
        # Image label
        self.img_label = tk.Label(self)
        self.img_label.pack(expand=True, pady=10)
        
        # Buttons frame
        self.btn_frame = tk.Frame(self)
        self.btn_frame.pack(pady=20)
        
        self.correct_btn = tk.Button(self.btn_frame, text="Correct (y)", command=lambda: self.record_result(True), font=("Arial", 14), bg="#2196F3", fg="white", width=12)
        self.correct_btn.pack(side=tk.LEFT, padx=10)
        
        self.incorrect_btn = tk.Button(self.btn_frame, text="Incorrect (n)", command=lambda: self.record_result(False), font=("Arial", 14), bg="#f44336", fg="white", width=12)
        self.incorrect_btn.pack(side=tk.LEFT, padx=10)
        
        self.skip_btn = tk.Button(self.btn_frame, text="Skip (s)", command=lambda: self.record_result(None), font=("Arial", 14), width=12)
        self.skip_btn.pack(side=tk.LEFT, padx=10)
        
        self.disable_buttons()
        
        # Bind keys for faster reviewing
        self.bind('<y>', lambda e: self.record_result(True) if str(self.correct_btn['state']) == 'normal' else None)
        self.bind('<n>', lambda e: self.record_result(False) if str(self.incorrect_btn['state']) == 'normal' else None)
        self.bind('<s>', lambda e: self.record_result(None) if str(self.skip_btn['state']) == 'normal' else None)
        
    def disable_buttons(self):
        self.correct_btn.config(state=tk.DISABLED)
        self.incorrect_btn.config(state=tk.DISABLED)
        self.skip_btn.config(state=tk.DISABLED)
        
    def enable_buttons(self):
        self.correct_btn.config(state=tk.NORMAL)
        self.incorrect_btn.config(state=tk.NORMAL)
        self.skip_btn.config(state=tk.NORMAL)

    def load_date(self):
        target_date = self.date_var.get()
        if not target_date:
            return
            
        self.target_date = target_date
        raw_tracks = defaultdict(list)
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                filename = row.get('filename', '')
                if filename.startswith(target_date):
                    raw_tracks[(row['track_id'], row['predicted_label'])].append(row)
                    
        self.tracks = []
        for (track_id, label), rows in raw_tracks.items():
            img_path = find_image(rows[0]['filename'], label)
            if img_path:
                self.tracks.append({
                    'track_id': track_id,
                    'label': label,
                    'confidence': rows[0].get('confidence', 'N/A'),
                    'image': img_path
                })
                
        if not self.tracks:
            messagebox.showinfo("Empty", f"No valid images found for {target_date}")
            return
            
        self.current_idx = 0
        self.results = []
        self.correct_count = 0
        self.total_count = 0
        
        self.start_btn.config(state=tk.DISABLED)
        self.date_dropdown.config(state=tk.DISABLED)
        self.enable_buttons()
        self.show_current_track()
        
    def show_current_track(self):
        if self.current_idx >= len(self.tracks):
            self.finish_review()
            return
            
        track = self.tracks[self.current_idx]
        self.info_label.config(text=f"Track {self.current_idx+1}/{len(self.tracks)}\nGuess: {track['label']} (Conf: {track['confidence']})")
        
        try:
            img = Image.open(track['image'])
            img.thumbnail((700, 400)) # Resize to fit GUI
            photo = ImageTk.PhotoImage(img)
            self.img_label.config(image=photo)
            self.img_label.image = photo
        except Exception as e:
            self.img_label.config(image='', text=f"Error loading image:\n{e}")
            
    def record_result(self, is_correct):
        track = self.tracks[self.current_idx]
        
        if is_correct is not None:
            if is_correct:
                self.correct_count += 1
            self.total_count += 1
            
            self.results.append({
                "track_id": track['track_id'],
                "label": track['label'],
                "confidence": track['confidence'],
                "image": track['image'],
                "correct": is_correct
            })
            
        self.current_idx += 1
        self.show_current_track()
        
    def finish_review(self):
        self.disable_buttons()
        self.start_btn.config(state=tk.NORMAL)
        self.date_dropdown.config(state=tk.NORMAL)
        self.img_label.config(image='', text="")
        
        if self.total_count == 0:
            self.info_label.config(text="Review finished. No evaluations completed.")
            return
            
        acc = (self.correct_count / self.total_count) * 100
        self.info_label.config(text=f"Done! Accuracy: {acc:.1f}% ({self.correct_count}/{self.total_count})\nReport saved to Daily_Report_{self.target_date}.md")
        
        report_path = f"Daily_Report_{self.target_date}.md"
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(f"# Daily Accuracy Report: {self.target_date}\n\n")
            f.write(f"**Total Unique Tracks Reviewed:** {self.total_count}  \n")
            f.write(f"**Correct Predictions:** {self.correct_count}  \n")
            f.write(f"**Overall Accuracy Rate:** {acc:.1f}%  \n\n")
            
            f.write("## Detailed Results\n\n")
            f.write("| Image | Model Prediction | Confidence | Result |\n")
            f.write("|-------|------------------|------------|--------|\n")
            for r in self.results:
                img_rel = r['image'].replace("\\", "/")
                res_str = "✔️ Correct" if r['correct'] else "❌ Incorrect"
                f.write(f"| <img src='{img_rel}' width='150'> | **{r['label']}** | {r['confidence']} | {res_str} |\n")
                
if __name__ == "__main__":
    app = ReviewApp()
    app.mainloop()
