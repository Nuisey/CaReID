import argparse
import time
import os
import sys
import shutil
import csv
from threading import Lock
from datetime import datetime
import torch
import torch.nn.functional as F
from torchvision import transforms
from PIL import Image
import pandas as pd
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import open_clip

# Import the new models
from export.models import MultiTaskCNN, MultiTaskViT, ClipLinearProbe, get_clip_base

def load_label_mapping(filepath):
    try:
        if not filepath or not os.path.exists(filepath): return {}
        df = pd.read_csv(filepath, header=None, names=['id', 'label'])
        df['id'] = df['id'].astype(str)
        return pd.Series(df.label.values, index=df.id).to_dict()
    except Exception as e:
        print(f"ERROR loading map from {filepath}: {e}")
        return {}

def load_reverse_label_mapping(filepath):
    try:
        if not filepath or not os.path.exists(filepath): return {}
        df = pd.read_csv(filepath, header=None, names=['id', 'label'])
        df['id'] = df['id'].astype(str)
        return pd.Series(df.id.values, index=df.label).to_dict()
    except Exception as e:
        return {}

class EnsembleNewImageHandler(FileSystemEventHandler):
    def __init__(self, cnn, vit, clip_probe, clip_base, clip_preprocess, device, 
                 make_map, model_map, color_map, label_map, rev_label_map, label_map_path,
                 processed_folder_path, log_csv_path, csv_lock):
        self.cnn = cnn
        self.vit = vit
        self.clip_probe = clip_probe
        self.clip_base = clip_base
        self.clip_preprocess = clip_preprocess
        self.device = device
        
        self.make_map = make_map
        self.model_map = model_map
        self.color_map = color_map
        self.label_map = label_map
        self.rev_label_map = rev_label_map
        self.label_map_path = label_map_path
        
        self.processed_folder_path = os.path.abspath(processed_folder_path)
        self.log_csv_path = os.path.abspath(log_csv_path)
        self.csv_lock = csv_lock
        
        self.processed_files = set()
        os.makedirs(self.processed_folder_path, exist_ok=True)
        
        self.standard_transform = transforms.Compose([
            transforms.Resize((224, 224), interpolation=transforms.InterpolationMode.BICUBIC),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
        ])
        
    def get_or_create_label_id(self, natural_label):
        if natural_label in self.rev_label_map:
            return self.rev_label_map[natural_label]
            
        # Create new ID
        with self.csv_lock:
            # reload in case it changed
            self.rev_label_map = load_reverse_label_mapping(self.label_map_path)
            if natural_label in self.rev_label_map:
                return self.rev_label_map[natural_label]
            
            existing_ids = [int(v) for v in self.rev_label_map.values() if v.isdigit()]
            new_id = str(max(existing_ids) + 1) if existing_ids else "0"
            
            with open(self.label_map_path, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([new_id, natural_label])
                
            self.rev_label_map[natural_label] = new_id
            self.label_map[new_id] = natural_label
            return new_id

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

        try:
            image_pil = Image.open(image_path).convert("RGB")
        except:
            self.processed_files.remove(image_path)
            return

        std_tensor = self.standard_transform(image_pil).unsqueeze(0).to(self.device)
        clip_tensor = self.clip_preprocess(image_pil).unsqueeze(0).to(self.device)

        with torch.no_grad():
            # 1. CNN
            cnn_mk, cnn_md, cnn_cl = self.cnn(std_tensor)
            cnn_mk, cnn_md, cnn_cl = F.softmax(cnn_mk, dim=1), F.softmax(cnn_md, dim=1), F.softmax(cnn_cl, dim=1)
            
            # 2. ViT
            vit_mk, vit_md, vit_cl = self.vit(std_tensor)
            vit_mk, vit_md, vit_cl = F.softmax(vit_mk, dim=1), F.softmax(vit_md, dim=1), F.softmax(vit_cl, dim=1)
            
            # 3. CLIP
            clip_feat = self.clip_base.encode_image(clip_tensor)
            clip_feat /= clip_feat.norm(dim=-1, keepdim=True)
            clp_mk, clp_md, clp_cl = self.clip_probe(clip_feat.float())
            clp_mk, clp_md, clp_cl = F.softmax(clp_mk, dim=1), F.softmax(clp_md, dim=1), F.softmax(clp_cl, dim=1)
            
            # Soft Voting Average
            avg_mk = (cnn_mk + vit_mk + clp_mk) / 3.0
            avg_md = (cnn_md + vit_md + clp_md) / 3.0
            avg_cl = (cnn_cl + vit_cl + clp_cl) / 3.0
            
            # Get max
            conf_mk, idx_mk = torch.max(avg_mk, 1)
            conf_md, idx_md = torch.max(avg_md, 1)
            conf_cl, idx_cl = torch.max(avg_cl, 1)
            
            idx_mk = str(idx_mk.item())
            idx_md = str(idx_md.item())
            idx_cl = str(idx_cl.item())
            
            make_str = self.make_map.get(idx_mk, f"Make_{idx_mk}")
            model_str = self.model_map.get(idx_md, f"Model_{idx_md}")
            color_str = self.color_map.get(idx_cl, f"Color_{idx_cl}")
            
            # Form natural label
            natural_label = f"{color_str},{make_str},{model_str}"
            overall_confidence = (conf_mk.item() + conf_md.item() + conf_cl.item()) / 3.0
            
            predicted_id = self.get_or_create_label_id(natural_label)

        print(f"Car (Ensemble): {natural_label} | Dir: {direction} | Track: {track_id} | Conf: {overall_confidence:.2f}")

        new_filename = f"{parts[0]}__{direction}__{track_id}__{natural_label.replace(',', '_').replace(' ', '_')}__{overall_confidence:.4f}.jpg"
        self.log_to_csv(new_filename, direction, natural_label, predicted_id, overall_confidence, track_id)

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
    parser.add_argument("--watch_folder", required=True)
    parser.add_argument("--processed_folder", required=True)
    parser.add_argument("--log_csv", required=True)
    parser.add_argument("--label_map_path", required=True)
    parser.add_argument("--make_map_path", required=True)
    parser.add_argument("--model_map_path", required=True)
    parser.add_argument("--color_map_path", required=True)
    parser.add_argument("--cnn_pth", required=True)
    parser.add_argument("--vit_pth", required=True)
    parser.add_argument("--clip_pth", required=True)
    parser.add_argument('--gpu_ids', default='0')
    opt = parser.parse_args()

    device = torch.device("cuda:" + opt.gpu_ids if torch.cuda.is_available() else "cpu")
    
    make_map = load_label_mapping(opt.make_map_path)
    model_map = load_label_mapping(opt.model_map_path)
    color_map = load_label_mapping(opt.color_map_path)
    label_map = load_label_mapping(opt.label_map_path)
    rev_label_map = load_reverse_label_mapping(opt.label_map_path)
    
    print("Loading Ensemble Models...")
    
    # Infer sizes from checkpoint to prevent size mismatch if label maps were updated
    cnn_sd = torch.load(opt.cnn_pth, map_location=device)
    num_makes = cnn_sd['fc_make.weight'].shape[0]
    num_models = cnn_sd['fc_model.weight'].shape[0]
    num_colors = cnn_sd['fc_color.weight'].shape[0]
    
    cnn = MultiTaskCNN(num_makes, num_models, num_colors).to(device)
    cnn.load_state_dict(cnn_sd)
    cnn.eval()
    print("- CNN Loaded.")

    vit = MultiTaskViT(num_makes, num_models, num_colors).to(device)
    vit.load_state_dict(torch.load(opt.vit_pth, map_location=device))
    vit.eval()
    print("- ViT Loaded.")

    clip_base, clip_preprocess = get_clip_base(device)
    clip_base.eval()
    
    clip_probe = ClipLinearProbe(num_makes, num_models, num_colors).to(device)
    clip_probe.load_state_dict(torch.load(opt.clip_pth, map_location=device))
    clip_probe.eval()
    print("- CLIP Probe Loaded.")

    csv_lock = Lock()
    event_handler = EnsembleNewImageHandler(
        cnn, vit, clip_probe, clip_base, clip_preprocess, device,
        make_map, model_map, color_map, label_map, rev_label_map, opt.label_map_path,
        opt.processed_folder, opt.log_csv, csv_lock
    )
    
    watch_folder_abs = os.path.abspath(opt.watch_folder)
    os.makedirs(watch_folder_abs, exist_ok=True)

    observer = Observer()
    observer.schedule(event_handler, watch_folder_abs, recursive=False)
    observer.start()
    
    print(f"Watching for images in {watch_folder_abs} using Ensemble Model...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
