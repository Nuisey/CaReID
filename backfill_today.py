import os
import csv
import torch
import torch.nn.functional as F
import pandas as pd
from PIL import Image
from torchvision import transforms
from export810.models import load_testing_models

def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("Loading test models for backfill...")
    test_models = load_testing_models(device)
    cnn, clip, clip_processor, vit, vit_processor = test_models

    df_comb = pd.concat([pd.read_csv('Data/train.csv'), pd.read_csv('Data/val.csv')])
    sorted_uids = sorted(df_comb['id'].unique())
    
    label_map = {}
    with open('Data/label_map.csv', 'r') as f:
        reader = csv.reader(f)
        for row in reader:
            label_map[int(row[0])] = row[1]
            
    index_to_string = {idx: label_map.get(uid, "Unknown") for idx, uid in enumerate(sorted_uids)}

    data_transforms = transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ])

    csv_path = "Data/CarLabels_Unprocessed.csv"
    unconfirmed_dir = "Data/unconfirmed"
    
    rows = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        header = next(reader)
        if len(header) < 9:
            header.extend(['cnn_guess', 'vit_guess', 'clip_guess'])
        rows.append(header)
        
        for row in reader:
            if len(row) < 9:
                row.extend([""] * (9 - len(row)))
                
            filename = row[0].replace('"', '')
            if filename.startswith("2026-08-11"):
                img_path = os.path.join(unconfirmed_dir, filename)
                if os.path.exists(img_path):
                    try:
                        image_pil = Image.open(img_path).convert('RGB')
                        
                        # CNN inference
                        cnn_input = data_transforms(image_pil).unsqueeze(0).to(device)
                        with torch.no_grad():
                            cnn_out = cnn(cnn_input)
                            cnn_pred = torch.argmax(cnn_out, dim=1).item()
                            cnn_conf = torch.max(F.softmax(cnn_out, dim=1)).item()
                            cnn_str = f"{index_to_string.get(cnn_pred, str(cnn_pred))} ({cnn_conf:.2f})"
                        
                        # CLIP inference
                        clip_input = clip_processor(images=image_pil, return_tensors="pt").pixel_values.to(device)
                        with torch.no_grad():
                            clip_out = clip(clip_input)
                            clip_pred = torch.argmax(clip_out, dim=1).item()
                            clip_conf = torch.max(F.softmax(clip_out, dim=1)).item()
                            clip_str = f"{index_to_string.get(clip_pred, str(clip_pred))} ({clip_conf:.2f})"
                            
                        # ViT inference
                        vit_input = vit_processor(images=image_pil, return_tensors="pt").pixel_values.to(device)
                        with torch.no_grad():
                            vit_out = vit(vit_input)
                            vit_pred = torch.argmax(vit_out, dim=1).item()
                            vit_conf = torch.max(F.softmax(vit_out, dim=1)).item()
                            vit_str = f"{index_to_string.get(vit_pred, str(vit_pred))} ({vit_conf:.2f})"
                            
                        row[6] = cnn_str
                        row[7] = vit_str
                        row[8] = clip_str
                        print(f"Processed {filename}")
                    except Exception as e:
                        print(f"Error on {filename}: {e}")
            rows.append(row)
            
    with open(csv_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerows(rows)
        
    print("Backfill complete.")

if __name__ == "__main__":
    main()
