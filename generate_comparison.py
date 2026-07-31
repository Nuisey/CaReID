import os
import glob
import json
import torch
import torch.nn.functional as F
from torchvision import transforms
from PIL import Image
import pandas as pd
import numpy as np
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(SCRIPT_DIR)

from load_model import load_model_from_opts
from dataset import ImageDataset
from tool.extract import extract_feature

device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

def load_label_mapping(filepath):
    df = pd.read_csv(filepath, header=None, names=['id', 'label'])
    df['id'] = df['id'].astype(str)
    return pd.Series(df.label.values, index=df.id).to_dict()

h, w = 224, 224
data_transforms = transforms.Compose([
    transforms.Resize((h, w), interpolation=transforms.InterpolationMode.BICUBIC),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
])

def extract_feat(model, img_tensor, is_mtl=False):
    with torch.no_grad():
        out = model(img_tensor.to(device))
        if is_mtl: return out[0], out[1], out[2], out[3]
        return out

def norm_label(s):
    return s.replace(',', ' ').replace('  ', ' ').strip().title()

def main():
    print("Loading label maps...")
    label_map = load_label_mapping('Data/label_map.csv')
    make_map = load_label_mapping('Data/make_map.csv')
    model_map = load_label_mapping('Data/model_map.csv')
    color_map = load_label_mapping('Data/color_map.csv')
    
    print("Loading ResNet...")
    resnet = load_model_from_opts('Brain/opts.yaml', ckpt='Brain/Final10232025.pth', remove_classifier=True).to(device).eval()
    
    print("Loading DINOv3 MTL...")
    dino = load_model_from_opts('Brain/opts_mtl.yaml', ckpt='Brain/best_mtl_model.pth', remove_classifier=True).to(device).eval()
    
    print("Preparing Gallery...")
    
    # Build valid Make+Model pairs for constrained inference
    inv_make = {v.strip(): int(k) for k, v in make_map.items()}
    inv_model = {v.strip(): int(k) for k, v in model_map.items()}
    valid_pairs = set()
    for lbl_str in label_map.values():
        parts = [p.strip() for p in lbl_str.split(',')]
        if len(parts) >= 3:
            m_str, md_str = parts[1], parts[2]
            if m_str in inv_make and md_str in inv_model:
                valid_pairs.add((inv_make[m_str], inv_model[md_str]))
                
    gallery_df = pd.read_csv('Data/Gallery/Gallery.csv')
    gallery_dataset = ImageDataset('Data/Gallery/LabeledCarDataPhotos', gallery_df, "id", transform=data_transforms)
    gallery_loader = torch.utils.data.DataLoader(gallery_dataset, batch_size=32, shuffle=False)
    
    print("Extracting Gallery Features (ResNet)...")
    res_gal_feat, res_gal_lbl = extract_feature(resnet, gallery_loader, device)
    res_gal_feat = res_gal_feat.to(device)
    
    print("Extracting Gallery Features (DINOv3)...")
    dino_gal_feat, dino_gal_lbl = extract_feature(dino, gallery_loader, device)
    dino_gal_feat = dino_gal_feat.to(device)
    
    test_images = []
    for ext in ['*.jpg', '*.png', '*.jpeg']:
        test_images.extend(glob.glob(f'Data/Unconfirmed/{ext}'))
        test_images.extend(glob.glob(f'Data/unsynced/**/{ext}', recursive=True))
        
    print(f"\nFound {len(test_images)} unconfirmed/unsynced images to test.")
    
    results = []
    
    for i, img_path in enumerate(test_images):
        print(f"Processing {i+1}/{len(test_images)}...", end='\r')
        try:
            img = Image.open(img_path).convert("RGB")
            tensor = data_transforms(img).unsqueeze(0)
        except:
            continue
            
        # ResNet Pred
        res_f = extract_feat(resnet, tensor, False)
        res_sim = F.cosine_similarity(res_f, res_gal_feat)
        res_idx = torch.argmax(res_sim).item()
        res_real_id = gallery_dataset.classes[res_gal_lbl[res_idx].item()]
        res_lbl = norm_label(label_map.get(str(res_real_id), "Unknown"))
        
        # DINO Pred
        dino_f, make_l, model_l, color_l = extract_feat(dino, tensor, True)
        dino_sim = F.cosine_similarity(dino_f, dino_gal_feat)
        dino_idx = torch.argmax(dino_sim).item()
        dino_real_id = gallery_dataset.classes[dino_gal_lbl[dino_idx].item()]
        dino_reid_lbl = norm_label(label_map.get(str(dino_real_id), "Unknown"))
        
        # DINO MTL (Constrained Inference)
        make_l_np = make_l.squeeze(0).cpu().numpy()
        model_l_np = model_l.squeeze(0).cpu().numpy()
        
        best_score = -float('inf')
        make_p, model_p = -1, -1
        for m_idx, md_idx in valid_pairs:
            score = make_l_np[m_idx] + model_l_np[md_idx]
            if score > best_score:
                best_score = score
                make_p = m_idx
                model_p = md_idx
                
        color_p = torch.argmax(color_l, dim=1).item()
        
        make_s = make_map.get(str(make_p), str(make_p))
        model_s = model_map.get(str(model_p), str(model_p))
        color_s = color_map.get(str(color_p), str(color_p))
        
        dino_mtl_lbl = norm_label(f"{color_s} {make_s} {model_s}")
        
        agree = (res_lbl == dino_reid_lbl) and (dino_reid_lbl == dino_mtl_lbl)
        
        results.append({
            "path": os.path.abspath(img_path).replace('\\', '/'),
            "resnet_reid": res_lbl,
            "dino_reid": dino_reid_lbl,
            "dino_class": dino_mtl_lbl,
            "agree": agree
        })
        
    with open('Data/comparison_results.json', 'w') as f:
        json.dump(results, f, indent=4)
        
    print(f"\nDone! Saved {len(results)} results to Data/comparison_results.json")

if __name__ == '__main__':
    main()
