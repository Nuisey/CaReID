import os
import csv
from collections import defaultdict
try:
    from PIL import Image
except ImportError:
    pass

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
    # Check all possible locations where the image might be sitting right now
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

def review_date(target_date):
    tracks = defaultdict(list)
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            filename = row.get('filename', '')
            if filename.startswith(target_date):
                tracks[(row['track_id'], row['predicted_label'])].append(row)
                
    if not tracks:
        print(f"No records found for {target_date}")
        return
        
    print(f"Found {len(tracks)} unique tracks for {target_date}.")
    print("An image will pop up for each track. Check the image, then type 'y' if the model guessed correctly, 'n' if it was wrong, or 's' to skip.")
    
    results = []
    correct = 0
    total = 0
    
    for (track_id, label), rows in tracks.items():
        img_path = find_image(rows[0]['filename'], label)
        if not img_path:
            continue
            
        print(f"\n--- Track: {track_id} ---")
        print(f"Model Guessed: {label} (Conf: {rows[0].get('confidence', 'N/A')})")
        
        try:
            img = Image.open(img_path)
            img.show()
        except:
            print(f"(Could not automatically display {img_path})")
            
        ans = ""
        while ans not in ['y', 'n', 's']:
            ans = input("Was this prediction correct? [y/n/s (skip)]: ").strip().lower()
            
        if ans == 's': 
            continue
        
        is_correct = (ans == 'y')
        if is_correct: correct += 1
        total += 1
        
        results.append({
            "track_id": track_id,
            "label": label,
            "confidence": rows[0].get('confidence', 'N/A'),
            "image": img_path,
            "correct": is_correct
        })
        
    if total == 0:
        print("No evaluations completed.")
        return
        
    acc = (correct / total) * 100
    print(f"\n==========================================")
    print(f"Done! Accuracy for {target_date}: {acc:.1f}% ({correct}/{total})")
    
    # Generate MD report
    report_path = f"Daily_Report_{target_date}.md"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(f"# Daily Accuracy Report: {target_date}\n\n")
        f.write(f"**Total Unique Tracks Reviewed:** {total}  \n")
        f.write(f"**Correct Predictions:** {correct}  \n")
        f.write(f"**Overall Accuracy Rate:** {acc:.1f}%  \n\n")
        
        f.write("## Detailed Results\n\n")
        f.write("| Image | Model Prediction | Confidence | Result |\n")
        f.write("|-------|------------------|------------|--------|\n")
        for r in results:
            img_rel = r['image'].replace("\\", "/")
            res_str = "✅ Correct" if r['correct'] else "❌ Incorrect"
            f.write(f"| <img src='{img_rel}' width='150'> | **{r['label']}** | {r['confidence']} | {res_str} |\n")
            
    print(f"\nGitHub-ready report generated: {report_path}")
    print("Commit this Markdown file to GitHub to showcase your proof!")

if __name__ == "__main__":
    dates = get_dates()
    if not dates:
        print("No dates found in CSV.")
    else:
        print("Available dates:")
        for i, d in enumerate(dates):
            print(f"[{i}] {d}")
        try:
            idx = int(input("\nSelect a date index to review: "))
            review_date(dates[idx])
        except (ValueError, IndexError):
            print("Invalid selection.")
