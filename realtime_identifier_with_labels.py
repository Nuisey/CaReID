import argparse
import time
import os
import sys
import shutil
import csv
from threading import Lock
from datetime import datetime
from zoneinfo import ZoneInfo

import torch
import torch.nn.functional as F
from torchvision import transforms
from PIL import Image
import numpy as np
import pandas as pd

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(SCRIPT_DIR)

from load_model import load_model_from_opts
from dataset import ImageDataset
from tool.extract import extract_feature

h, w = 224, 224
data_transforms = transforms.Compose([
    transforms.Resize((h, w), interpolation=transforms.InterpolationMode.BICUBIC),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
])

def load_label_mapping(filepath):
    try:
        df = pd.read_csv(filepath, header=None, names=['id', 'label'])
        df['id'] = df['id'].astype(str)
        return pd.Series(df.label.values, index=df.id).to_dict()
    except Exception as e:
        print(f"ERROR loading map: {e}")
        return {}

def preprocess_image(image_path):
    try:
        image = Image.open(image_path).convert("RGB")
        return data_transforms(image).unsqueeze(0)
    except:
        return None

def extract_single_feature(model, image_tensor, device):
    with torch.no_grad():
        return model(image_tensor.to(device))

class NewImageHandler(FileSystemEventHandler):
    def __init__(self, model, device, gallery_features, gallery_labels, label_mapping, processed_folder_path, log_csv_path, csv_lock):
        self.model = model
        self.device = device
        self.gallery_features = gallery_features
        self.gallery_labels = gallery_labels
        self.label_mapping = label_mapping
        
        self.processed_folder_path = os.path.abspath(processed_folder_path)
        self.log_csv_path = os.path.abspath(log_csv_path)
        self.csv_lock = csv_lock
        
        self.processed_files = set()
        self.track_features = {} # Stores features per track_id for Feature Averaging
        os.makedirs(self.processed_folder_path, exist_ok=True)

    def log_to_csv(self, filename, direction, predicted_label, predicted_id, confidence, track_id):
        with self.csv_lock:
            try:
                file_exists = os.path.isfile(self.log_csv_path)
                with open(self.log_csv_path, mode='a', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    if not file_exists:
                        writer.writerow(['filename', 'direction', 'predicted_label', 'ID', 'confidence', 'track_id'])
                    writer.writerow([filename, direction, predicted_label, predicted_id, f"{confidence:.4f}", track_id])
            except Exception as e:
                print(f"Error writing to CSV: {e}")

    def process_image(self, image_path, wait_for_write=True):
        if image_path in self.processed_files:
            return
        self.processed_files.add(image_path)

        filename = os.path.basename(image_path)
        
        parts = filename.split("__")
        direction = "unknown"
        track_id = "unknown"
        if len(parts) >= 3:
            direction = parts[1]
            track_id = parts[2]

        if wait_for_write:
            time.sleep(0.5)

        query_tensor = preprocess_image(image_path)
        if query_tensor is None:
            self.processed_files.remove(image_path)
            return

        query_feature = extract_single_feature(self.model, query_tensor, self.device)
        
        # Strategy 4: Feature Averaging
        if track_id not in self.track_features:
            self.track_features[track_id] = []
        self.track_features[track_id].append(query_feature)
        
        # Average all features collected for this track_id so far
        stacked_features = torch.stack(self.track_features[track_id])
        avg_feature = torch.mean(stacked_features, dim=0)

        similarities = F.cosine_similarity(avg_feature, self.gallery_features)

        best_match_index = torch.argmax(similarities).item()
        predicted_id = self.gallery_labels[best_match_index]
        confidence = similarities[best_match_index].item()

        natural_label = self.label_mapping.get(str(predicted_id), "Unknown")
        print(f"Car: {natural_label} | Dir: {direction} | Track: {track_id} | Conf: {confidence:.2f}")

        self.log_to_csv(filename, direction, natural_label, predicted_id, confidence, track_id)

        try:
            dest_path = os.path.join(self.processed_folder_path, filename)
            shutil.move(image_path, dest_path)
        except Exception as e:
            if image_path in self.processed_files:
                self.processed_files.remove(image_path)

    def on_created(self, event):
        if not event.is_directory and event.src_path.lower().endswith(('.png', '.jpg', '.jpeg')):
            self.process_image(event.src_path, wait_for_write=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model_opts", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--gallery_csv_path", required=True)
    parser.add_argument("--label_mapping", required=True)
    parser.add_argument("--data_dir", required=True)
    parser.add_argument("--watch_folder", required=True)
    parser.add_argument("--processed_folder", required=True)
    parser.add_argument("--log_csv", required=True)
    parser.add_argument('--gpu_ids', default='0')
    opt = parser.parse_args()

    device = torch.device("cuda:" + opt.gpu_ids if torch.cuda.is_available() else "cpu")
    label_mapping = load_label_mapping(opt.label_mapping)

    print("Loading Re-ID model...")
    model = load_model_from_opts(opt.model_opts, ckpt=opt.checkpoint, remove_classifier=True)
    model.to(device)
    model.eval()

    print("Pre-processing gallery...")
    gallery_df = pd.read_csv(opt.gallery_csv_path)
    gallery_dataset = ImageDataset(opt.data_dir, gallery_df, "id", transform=data_transforms)
    gallery_loader = torch.utils.data.DataLoader(gallery_dataset, batch_size=32, shuffle=False)
    
    gallery_features, gallery_labels = extract_feature(model, gallery_loader, device)
    gallery_features = gallery_features.to(device)

    csv_lock = Lock()
    event_handler = NewImageHandler(
        model, device, gallery_features, np.array(gallery_labels), 
        label_mapping, opt.processed_folder, opt.log_csv, csv_lock
    )
    
    watch_folder_abs = os.path.abspath(opt.watch_folder)
    os.makedirs(watch_folder_abs, exist_ok=True)

    observer = Observer()
    observer.schedule(event_handler, watch_folder_abs, recursive=False)
    observer.start()
    
    print(f"Watching for images in {watch_folder_abs}...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
