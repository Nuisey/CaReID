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

def extract_make(label):
    parts = label.split(',')
    if len(parts) >= 2:
        return parts[1].strip()
    return label.strip()

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
    print("For each track, type 'y' if the model was fully correct.")
    print("If incorrect, type 'n' and you will be asked for the correct MAKE (e.g. Ford, Toyota).")
    
    results = []
    
    # Metrics tracked by MAKE
    # tp = true positive, fp = false positive, fn = false negative
    make_metrics = defaultdict(lambda: {'tp': 0, 'fp': 0, 'fn': 0})
    confusion_matrix = defaultdict(lambda: defaultdict(int)) # truth -> prediction
    
    total = 0
    correct = 0
    
    for (track_id, label), rows in tracks.items():
        img_path = find_image(rows[0]['filename'], label)
        if not img_path:
            continue
            
        print(f"\n--- Track: {track_id} ---")
        predicted_make = extract_make(label)
        print(f"Model Guessed Label: {label}")
        print(f"Model Guessed Make:  {predicted_make} (Conf: {rows[0].get('confidence', 'N/A')})")
        
        try:
            img = Image.open(img_path)
            img.show()
        except:
            print(f"(Could not automatically display {img_path})")
            
        ans = ""
        while ans not in ['y', 'n', 's']:
            ans = input("Was this prediction completely correct? [y/n/s (skip)]: ").strip().lower()
            
        if ans == 's': 
            continue
        
        is_correct = (ans == 'y')
        total += 1
        
        if is_correct:
            correct += 1
            true_make = predicted_make
            make_metrics[true_make]['tp'] += 1
        else:
            true_make = input(f"What was the ACTUAL Make? (Press Enter for 'Unknown'): ").strip()
            if not true_make:
                true_make = "Unknown"
            
            make_metrics[predicted_make]['fp'] += 1
            make_metrics[true_make]['fn'] += 1
            
        confusion_matrix[true_make][predicted_make] += 1
        
        results.append({
            "track_id": track_id,
            "predicted_label": label,
            "predicted_make": predicted_make,
            "true_make": true_make,
            "confidence": rows[0].get('confidence', 'N/A'),
            "image": img_path,
            "correct": is_correct
        })
        
    if total == 0:
        print("No evaluations completed.")
        return
        
    overall_acc = (correct / total) * 100
    
    # Generate MD report
    report_path = f"Academic_Report_{target_date}.md"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(f"# Academic Evaluation Report: {target_date}\n\n")
        f.write(f"## 1. Quantitative Summary\n\n")
        f.write(f"- **Total Tracks Evaluated:** {total}\n")
        f.write(f"- **Overall Accuracy:** {overall_acc:.1f}%\n\n")
        
        f.write("### 1.1 Metrics by Car Make\n")
        f.write("| Make | Precision | Recall | F1-Score | True Positives | False Positives | False Negatives |\n")
        f.write("|------|-----------|--------|----------|----------------|-----------------|-----------------|\n")
        
        # Calculate Precision, Recall, F1 for each make
        sorted_makes = sorted(make_metrics.keys())
        for m in sorted_makes:
            metrics = make_metrics[m]
            tp = metrics['tp']
            fp = metrics['fp']
            fn = metrics['fn']
            
            precision = (tp / (tp + fp)) if (tp + fp) > 0 else 0
            recall = (tp / (tp + fn)) if (tp + fn) > 0 else 0
            f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
            
            f.write(f"| **{m}** | {precision:.2f} | {recall:.2f} | {f1:.2f} | {tp} | {fp} | {fn} |\n")
            
        f.write("\n### 1.2 Confusion Matrix (Errors Only)\n")
        f.write("When the model got it wrong, what did it guess?\n\n")
        f.write("| True Make | Model Guessed Make | Count |\n")
        f.write("|-----------|--------------------|-------|\n")
        has_errors = False
        for t_make in confusion_matrix:
            for p_make in confusion_matrix[t_make]:
                if t_make != p_make:
                    f.write(f"| {t_make} | {p_make} | {confusion_matrix[t_make][p_make]} |\n")
                    has_errors = True
        if not has_errors:
            f.write("| (None) | (None) | 0 |\n")
            
        f.write("\n## 2. Qualitative Results\n\n")
        f.write("| Image | Predicted Make (Label) | True Make | Confidence | Result |\n")
        f.write("|-------|------------------------|-----------|------------|--------|\n")
        for r in results:
            img_rel = r['image'].replace("\\", "/")
            res_str = "✅ Correct" if r['correct'] else "❌ Incorrect"
            f.write(f"| <img src='{img_rel}' width='150'> | **{r['predicted_make']}**<br>*( {r['predicted_label']} )* | {r['true_make']} | {r['confidence']} | {res_str} |\n")
            
    print(f"\n==========================================")
    print(f"Academic Report generated: {report_path}")
    print("This file now contains Precision, Recall, F1-Scores, and a Confusion Matrix!")

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
