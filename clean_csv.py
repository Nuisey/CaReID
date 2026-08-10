import os
import csv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_CSV = os.path.join(BASE_DIR, "Data", "CarLabels_Unprocessed.csv")
UNCONFIRMED_DIR = os.path.join(BASE_DIR, "Data", "Unconfirmed")

if os.path.exists(LOG_CSV):
    # Build valid images set once for fast O(1) lookups
    valid_images = set()
    if os.path.exists(UNCONFIRMED_DIR):
        valid_images.update(os.listdir(UNCONFIRMED_DIR))
    for base in [os.path.join(BASE_DIR, "Data", "unsynced"), os.path.join(BASE_DIR, "Data", "Gallery")]:
        if os.path.exists(base):
            for label_dir in os.listdir(base):
                d = os.path.join(base, label_dir)
                if os.path.isdir(d):
                    valid_images.update(os.listdir(d))

    rows_to_keep = []
    total = 0
    with open(LOG_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        # Fix missing track_id in header
        if "track_id" not in fieldnames:
            fieldnames.append("track_id")
            
        for row in reader:
            total += 1
            if row.get("filename", "") in valid_images:
                # Handle the case where row was parsed with extra columns due to missing header
                if None in row:
                    extra = row[None]
                    if isinstance(extra, list) and len(extra) > 0:
                        row["track_id"] = extra[0]
                    del row[None]
                rows_to_keep.append(row)
                
    with open(LOG_CSV, "w", encoding="utf-8", newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_to_keep)
    print(f"Cleaned up CSV. Kept {len(rows_to_keep)} out of {total} rows. Removed {total - len(rows_to_keep)} missing entries.")
