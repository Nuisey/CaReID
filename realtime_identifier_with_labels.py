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
        if not filepath or not os.path.exists(filepath): return {}
        df = pd.read_csv(filepath, header=None, names=['id', 'label'])
        df['id'] = df['id'].astype(str)
        return pd.Series(df.label.values, index=df.id).to_dict()
    except Exception as e:
        print(f"ERROR loading map from {filepath}: {e}")
        return {}

def preprocess_image(image_path):
    try:
        image = Image.open(image_path).convert("RGB")
        return data_transforms(image).unsqueeze(0)
    except:
        return None

def extract_single_feature(model, image_tensor, device, is_mtl=False):
    with torch.no_grad():
        out = model(image_tensor.to(device))
        if is_mtl:
            return out[0], out[1], out[2], out[3] # features, make, model, color
        return out

class NewImageHandler(FileSystemEventHandler):
    def __init__(self, model, device, gallery_features, gallery_labels, label_mapping, processed_folder_path, log_csv_path, csv_lock, is_mtl=False, mtl_maps=None, valid_pairs=None):
        self.model = model
        self.device = device
        self.gallery_features = gallery_features
        self.gallery_labels = gallery_labels
        self.label_mapping = label_mapping
        self.is_mtl = is_mtl
        self.mtl_maps = mtl_maps or {}
        self.valid_pairs = valid_pairs or set()
        
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

        if self.is_mtl:
            query_feature, make_logits, model_logits, color_logits = extract_single_feature(self.model, query_tensor, self.device, True)
            
            make_l_np = make_logits.squeeze(0).cpu().numpy()
            model_l_np = model_logits.squeeze(0).cpu().numpy()
            best_score = -float('inf')
            make_pred, model_pred = -1, -1
            
            for m_idx, md_idx in self.valid_pairs:
                score = make_l_np[m_idx] + model_l_np[md_idx]
                if score > best_score:
                    best_score = score
                    make_pred = m_idx
                    model_pred = md_idx
                    
            if make_pred == -1:
                make_pred = torch.argmax(make_logits, dim=1).item()
                model_pred = torch.argmax(model_logits, dim=1).item()
                
            color_pred = torch.argmax(color_logits, dim=1).item()
            
            make_str = self.mtl_maps.get('make', {}).get(str(make_pred), str(make_pred))
            model_str = self.mtl_maps.get('model', {}).get(str(model_pred), str(model_pred))
            color_str = self.mtl_maps.get('color', {}).get(str(color_pred), str(color_pred))
            mtl_result_str = f"[MTL: {make_str} {model_str} ({color_str})]"
        else:
            query_feature = extract_single_feature(self.model, query_tensor, self.device, False)
            mtl_result_str = ""
        
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
        print(f"Car (Re-ID): {natural_label} | {mtl_result_str} | Dir: {direction} | Track: {track_id} | Conf: {confidence:.2f}")

        new_filename = f"{parts[0]}__{direction}__{track_id}__{natural_label.replace(' ', '_')}__{confidence:.4f}.jpg"
        self.log_to_csv(new_filename, direction, natural_label, predicted_id, confidence, track_id)

        try:
            dest_path = os.path.join(self.processed_folder_path, new_filename)
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
    parser.add_argument('--mtl', action='store_true', help='Use MTL dual-head model')
    parser.add_argument('--make_map', default='', help='Path to make label map')
    parser.add_argument('--model_map', default='', help='Path to model label map')
    parser.add_argument('--color_map', default='', help='Path to color label map')
    opt = parser.parse_args()

    device = torch.device("cuda:" + opt.gpu_ids if torch.cuda.is_available() else "cpu")
    label_mapping = load_label_mapping(opt.label_mapping)
    
    mtl_maps = {}
    valid_pairs = set()
    if opt.mtl:
        mtl_maps['make'] = load_label_mapping(opt.make_map)
        mtl_maps['model'] = load_label_mapping(opt.model_map)
        mtl_maps['color'] = load_label_mapping(opt.color_map)
        
        inv_make = {v.strip(): int(k) for k, v in mtl_maps['make'].items()}
        inv_model = {v.strip(): int(k) for k, v in mtl_maps['model'].items()}
        for lbl_str in label_mapping.values():
            parts = [p.strip() for p in lbl_str.split(',')]
            if len(parts) >= 3:
                m_str, md_str = parts[1], parts[2]
                if m_str in inv_make and md_str in inv_model:
                    valid_pairs.add((inv_make[m_str], inv_model[md_str]))

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
        label_mapping, opt.processed_folder, opt.log_csv, csv_lock,
        is_mtl=opt.mtl, mtl_maps=mtl_maps, valid_pairs=valid_pairs
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
