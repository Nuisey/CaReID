import torch
import torch.nn as nn
import torch.optim as optim
from transformers import ViTForImageClassification, ViTImageProcessor
from peft import get_peft_model, LoraConfig, TaskType
from torch.utils.data import DataLoader
from dataset import CarDataset, get_label_map
from PIL import Image
import copy
import pandas as pd
import os
import copy
from tracker import log_experiment, manage_top_models
from transformers import ViTModel
import math
import torch.nn.functional as F

class ArcFace(nn.Module):
    def __init__(self, in_features, out_features, s=30.0, m=0.30):
        super(ArcFace, self).__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.s = s
        self.m = m
        self.weight = nn.Parameter(torch.FloatTensor(out_features, in_features))
        nn.init.xavier_uniform_(self.weight)
        
        self.cos_m = math.cos(m)
        self.sin_m = math.sin(m)
        self.th = math.cos(math.pi - m)
        self.mm = math.sin(math.pi - m) * m

    def forward(self, input, label):
        cosine = F.linear(F.normalize(input), F.normalize(self.weight))
        sine = torch.sqrt(1.0 - torch.pow(cosine, 2))
        phi = cosine * self.cos_m - sine * self.sin_m
        phi = torch.where(cosine > self.th, phi, cosine - self.mm)
        
        one_hot = torch.zeros(cosine.size(), device=input.device)
        one_hot.scatter_(1, label.view(-1, 1).long(), 1)
        output = (one_hot * phi) + ((1.0 - one_hot) * cosine)
        output *= self.s
        return output

class ViTArcFaceModel(nn.Module):
    def __init__(self, backbone, num_classes):
        super().__init__()
        self.backbone = backbone
        self.arcface = ArcFace(768, num_classes, s=30.0, m=0.30)
        
    def forward(self, pixel_values, labels=None):
        outputs = self.backbone(pixel_values=pixel_values)
        features = outputs.pooler_output
        if self.training and labels is not None:
            return self.arcface(features, labels)
        else:
            return F.linear(F.normalize(features), F.normalize(self.arcface.weight)) * 30.0

class ViTCarDataset(torch.utils.data.Dataset):
    def __init__(self, csv_file, img_dir, transform, label_map=None):
        self.df = pd.read_csv(csv_file)
        self.img_dir = img_dir
        self.transform = transform
        
        if label_map is None:
            unique_labels = sorted(self.df['id'].unique())
            self.label_map = {lbl: i for i, lbl in enumerate(unique_labels)}
        else:
            self.label_map = label_map
            
        self.df = self.df[self.df['id'].isin(self.label_map.keys())].reset_index(drop=True)
            
    def __len__(self):
        return len(self.df)
        
    def __getitem__(self, idx):
        img_name = os.path.join(self.img_dir, self.df.iloc[idx]['path'])
        image = Image.open(img_name).convert('RGB')
        
        uid = self.df.iloc[idx]['id']
        label = self.label_map[uid]
        
        pixel_values = self.transform(image)
        return pixel_values, label

def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")
    
    print("Loading ViT Model and Processor...")
    model_id = "google/vit-base-patch16-224-in21k"
    processor = ViTImageProcessor.from_pretrained(model_id)
    
    img_dir = 'Gallery/LabeledCarDataPhotos'
    
    train_df = pd.read_csv('train.csv')
    val_df = pd.read_csv('val.csv')
    combined_df = pd.concat([train_df, val_df]).reset_index(drop=True)
    combined_df.to_csv('combined.csv', index=False)
    
    train_csv = 'combined.csv'
    val_csv = 'val.csv'
    
    def get_id_map(csv_file):
        df = pd.read_csv(csv_file)
        return {lbl: i for i, lbl in enumerate(sorted(df['id'].unique()))}
        
    label_map = get_id_map(train_csv)
    num_classes = len(label_map)
    print(f"Number of classes: {num_classes}")

    from torchvision import transforms
    train_transform = transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.RandomHorizontalFlip(),
        transforms.ColorJitter(0.1, 0.1, 0.1),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5]),
    ])
    val_transform = transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5]),
    ])
    
    base_model = ViTModel.from_pretrained(model_id)
    
    peft_config = LoraConfig(
        r=64,
        lora_alpha=64,
        target_modules=["q_proj", "v_proj"],
        lora_dropout=0.1,
        bias="none",
    )
    base_model = get_peft_model(base_model, peft_config)
    base_model.print_trainable_parameters()
    
    model = ViTArcFaceModel(base_model, num_classes).to(device)
    train_dataset = ViTCarDataset(train_csv, img_dir, train_transform, label_map)
    val_dataset = ViTCarDataset(val_csv, img_dir, val_transform, label_map)

    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=32, shuffle=False)
    
    optimizer = optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-2)
    criterion = nn.CrossEntropyLoss()
    
    best_acc = 0.0
    epochs = 30
    scheduler = optim.lr_scheduler.OneCycleLR(optimizer, max_lr=1e-3, steps_per_epoch=len(train_loader), epochs=epochs)
    
    for epoch in range(epochs):
        print(f"Epoch {epoch+1}/{epochs}")
        model.train()
        train_loss = 0.0
        train_correct = 0
        
        for inputs, labels in train_loader:
            inputs = inputs.to(device)
            labels = labels.to(device)
            optimizer.zero_grad()
            outputs = model(pixel_values=inputs, labels=labels)
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()
            scheduler.step()
            
            train_loss += loss.item() * inputs.size(0)
            preds = outputs.argmax(dim=1)
            train_correct += (preds == labels).sum().item()
            
        train_acc = train_correct / len(train_dataset)
        
        model.eval()
        val_loss = 0.0
        val_correct = 0
        with torch.no_grad():
            for inputs, labels in val_loader:
                inputs = inputs.to(device)
                labels = labels.to(device)
                outputs = model(inputs)
                val_loss += criterion(outputs, labels).item() * inputs.size(0)
                preds = outputs.argmax(dim=1)
                val_correct += (preds == labels).sum().item()
                
        val_acc = val_correct / len(val_dataset)
        print(f"Train Loss: {train_loss/len(train_dataset):.4f} Acc: {train_acc:.4f} | Val Loss: {val_loss/len(val_dataset):.4f} Acc: {val_acc:.4f}")
        
        if val_acc > best_acc:
            best_acc = val_acc
            torch.save(model.state_dict(), 'best_vit_lora_model.pth')
            print("Saved best model.")
            
    log_experiment("ViT", "ViT-Base LoRA ArcFace", best_acc, "r=64, alpha=64, epochs=30, arcface", 'best_vit_lora_model.pth')
    try:
        manage_top_models('ViT_LoRA_ArcFace', best_acc, 'best_vit_lora_model.pth')
    except Exception as e:
        pass
            
    with open('vit_results.txt', 'w') as f:
        f.write(f"Best Validation Accuracy: {best_acc:.4f}\n")

if __name__ == '__main__':
    main()
