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
from export810.models import load_testing_models

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(SCRIPT_DIR)

from load_model import load_model_from_opts
from dataset import ImageDataset
from tool.extract import extract_feature

h, w = 224, 224

clip_transforms = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize([0.48145466, 0.4578275, 0.40821073], [0.26862954, 0.26130258, 0.27577711])
])

vit_transforms = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize([0.5, 0.5, 0.5], [0.5, 0.5, 0.5])
])

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
    def __init__(self, model, device, gallery_features, gallery_labels, label_mapping, processed_folder_path, log_csv_path, csv_lock, is_mtl=False, mtl_maps=None, valid_pairs=None, test_models=None, test_label_map=None):
        self.model = model
        self.device = device
        self.gallery_features = gallery_features
        self.gallery_labels = gallery_labels
        self.label_mapping = label_mapping
        self.test_models = test_models
        self.test_label_map = test_label_map
        self.is_mtl = is_mtl
        self.mtl_maps = mtl_maps or {}
        self.valid_pairs = valid_pairs or set()
        
        self.processed_folder_path = os.path.abspath(processed_folder_path)
        self.log_csv_path = os.path.abspath(log_csv_path)
        self.csv_lock = csv_lock
        
        self.processed_files = set()
        self.track_features = {} # Stores features per track_id for Feature Averaging
        os.makedirs(self.processed_folder_path, exist_ok=True)

    def log_to_csv(self, filename, direction, predicted_label, predicted_id, confidence, track_id, cnn_guess="", vit_guess="", clip_guess="", resnet_guess=""):
        with self.csv_lock:
            try:
                file_exists = os.path.isfile(self.log_csv_path)
                with open(self.log_csv_path, mode='a', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    if not file_exists:
                        writer.writerow(['filename', 'direction', 'predicted_label', 'ID', 'confidence', 'track_id', 'cnn_guess', 'vit_guess', 'clip_guess', 'resnet_guess'])
                    writer.writerow([filename, direction, predicted_label, predicted_id, f"{confidence:.4f}", track_id, cnn_guess, vit_guess, clip_guess, resnet_guess])
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

        # Test models predictions
        cnn_str, vit_str, clip_str = "", "", ""
        cnn_label, vit_label, clip_label = "", "", ""
        cnn_conf, vit_conf, clip_conf = 0.0, 0.0, 0.0
        
        if self.test_models:
            cnn, clip, clip_processor, vit, vit_processor = self.test_models
            try:
                image_pil = Image.open(image_path).convert('RGB')
                
                # CNN inference
                cnn_input = data_transforms(image_pil).unsqueeze(0).to(self.device)
                with torch.no_grad():
                    cnn_out = cnn(cnn_input)
                    cnn_pred = torch.argmax(cnn_out, dim=1).item()
                    cnn_conf = torch.max(F.softmax(cnn_out, dim=1)).item()
                    cnn_label = self.test_label_map.get(cnn_pred, str(cnn_pred))
                    cnn_str = f"{cnn_label} ({cnn_conf:.2f})"
                
                # CLIP inference
                clip_input = clip_transforms(image_pil).unsqueeze(0).to(self.device)
                with torch.no_grad():
                    clip_out = clip(clip_input)
                    clip_pred = torch.argmax(clip_out, dim=1).item()
                    clip_conf = torch.max(F.softmax(clip_out, dim=1)).item()
                    clip_label = self.test_label_map.get(clip_pred, str(clip_pred))
                    clip_str = f"{clip_label} ({clip_conf:.2f})"
                    
                # ViT inference
                vit_input = vit_transforms(image_pil).unsqueeze(0).to(self.device)
                with torch.no_grad():
                    vit_out = vit(vit_input)
                    vit_pred = torch.argmax(vit_out, dim=1).item()
                    vit_conf = torch.max(F.softmax(vit_out, dim=1)).item()
                    vit_label = self.test_label_map.get(vit_pred, str(vit_pred))
                    vit_str = f"{vit_label} ({vit_conf:.2f})"
                    
            except Exception as e:
                print(f"Test models error: {e}")

        # Voting logic
        preds = [
            {'label': natural_label, 'conf': confidence},
            {'label': cnn_label, 'conf': cnn_conf},
            {'label': clip_label, 'conf': clip_conf},
            {'label': vit_label, 'conf': vit_conf}
        ]
        
        votes = {}
        for p in preds:
            if p['conf'] >= 0.80 and p['label']:
                votes[p['label']] = votes.get(p['label'], 0) + 1
                
        best_votes = 0
        best_label = None
        for lbl, count in votes.items():
            if count > best_votes:
                best_votes = count
                best_label = lbl
                
        data_dir = os.path.dirname(self.processed_folder_path)
        if best_votes >= 3:
            final_label = best_label
            target_folder = os.path.join(data_dir, "Unsynced")
        elif best_votes == 2:
            final_label = best_label
            target_folder = self.processed_folder_path # Unconfirmed
        else:
            final_label = "Unseen Car"
            target_folder = os.path.join(data_dir, "Unseen")
            predicted_id = "Unseen"
            
        os.makedirs(target_folder, exist_ok=True)

        new_filename = f"{parts[0]}__{direction}__{track_id}__{final_label.replace(' ', '_')}__{confidence:.4f}.jpg"
        resnet_str = f"{natural_label} ({confidence:.2f})"
        self.log_to_csv(new_filename, direction, final_label, predicted_id, confidence, track_id, cnn_str, vit_str, clip_str, resnet_str)

        try:
            dest_path = os.path.join(target_folder, new_filename)
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

    print("Loading Testing Models...")
    test_models = None
    try:
        test_models = load_testing_models(device)
        df_comb = pd.concat([pd.read_csv('Data/train.csv'), pd.read_csv('Data/val.csv')])
        sorted_uids = sorted(df_comb['id'].unique())
        index_to_string = {idx: label_mapping.get(str(uid), "Unknown") for idx, uid in enumerate(sorted_uids)}
    except Exception as e:
        print(f"Skipping test models loading (weights missing?): {e}")
        index_to_string = {}

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
        is_mtl=opt.mtl, mtl_maps=mtl_maps, valid_pairs=valid_pairs,
        test_models=test_models, test_label_map=index_to_string
    )
    
    watch_folder_abs = os.path.abspath(opt.watch_folder)
    os.makedirs(watch_folder_abs, exist_ok=True)
    with open("ai_ready.txt", "w") as f:
        f.write("ready")
        
    print(f"Processing existing images in {watch_folder_abs}...")
    for fname in os.listdir(watch_folder_abs):
        if fname.lower().endswith(('.png', '.jpg', '.jpeg')):
            event_handler.process_image(os.path.join(watch_folder_abs, fname), wait_for_write=False)

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
