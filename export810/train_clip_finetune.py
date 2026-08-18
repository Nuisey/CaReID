import torch
import torch.nn as nn
from transformers import CLIPModel, CLIPProcessor
from torch.utils.data import DataLoader
from dataset import CarDataset, get_label_map
from PIL import Image
import pandas as pd
import os
import copy
import copy
from tracker import log_experiment, manage_top_models
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

class CLIPFineTuner(nn.Module):
    def __init__(self, vision_model, num_classes):
        super().__init__()
        self.vision_model = vision_model
        
        for param in self.vision_model.parameters():
            param.requires_grad = False
            
        # Unfreeze last 6 layers
        for param in self.vision_model.encoder.layers[-6:].parameters():
            param.requires_grad = True
        for param in self.vision_model.post_layernorm.parameters():
            param.requires_grad = True
            
        self.arcface = ArcFace(1024, num_classes, s=30.0, m=0.30)
        
    def forward(self, pixel_values, labels=None):
        outputs = self.vision_model(pixel_values=pixel_values)
        features = outputs.pooler_output
        if self.training and labels is not None:
            return self.arcface(features, labels)
        else:
            return F.linear(F.normalize(features), F.normalize(self.arcface.weight)) * 30.0

class CLIPCarDataset(torch.utils.data.Dataset):
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
    
    print("Loading CLIP Model...")
    model_id = "openai/clip-vit-large-patch14"
    model = CLIPModel.from_pretrained(model_id)
    processor = CLIPProcessor.from_pretrained(model_id)
    
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
        transforms.Normalize((0.48145466, 0.4578275, 0.40821073), (0.26862954, 0.26130258, 0.27577711)),
    ])
    val_transform = transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize((0.48145466, 0.4578275, 0.40821073), (0.26862954, 0.26130258, 0.27577711)),
    ])

    train_dataset = CLIPCarDataset(train_csv, img_dir, train_transform, label_map)
    val_dataset = CLIPCarDataset(val_csv, img_dir, val_transform, label_map)

    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=32, shuffle=False)
    
    clf_model = CLIPFineTuner(model.vision_model, num_classes).to(device)
    
    optimizer = torch.optim.AdamW(filter(lambda p: p.requires_grad, clf_model.parameters()), lr=1e-4, weight_decay=1e-2)
    criterion = nn.CrossEntropyLoss()
    
    epochs = 20
    scheduler = torch.optim.lr_scheduler.OneCycleLR(optimizer, max_lr=1e-4, steps_per_epoch=len(train_loader), epochs=epochs)
    
    best_acc = 0.0
    for epoch in range(epochs):
        clf_model.train()
        train_loss = 0.0
        train_correct = 0
        for inputs, labels in train_loader:
            inputs = inputs.to(device)
            labels = labels.to(device)
            optimizer.zero_grad()
            outputs = clf_model(inputs, labels)
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()
            scheduler.step()
            
            train_loss += loss.item() * inputs.size(0)
            preds = outputs.argmax(dim=1)
            train_correct += (preds == labels).sum().item()
            
        train_acc = train_correct / len(train_dataset)
        
        clf_model.eval()
        val_loss = 0.0
        val_correct = 0
        with torch.no_grad():
            for inputs, labels in val_loader:
                inputs = inputs.to(device)
                labels = labels.to(device)
                outputs = clf_model(inputs)
                val_loss += criterion(outputs, labels).item() * inputs.size(0)
                preds = outputs.argmax(dim=1)
                val_correct += (preds == labels).sum().item()
                
        val_acc = val_correct / len(val_dataset)
        print(f"Epoch {epoch}: Train Loss {train_loss/len(train_dataset):.4f} Acc {train_acc:.4f} | Val Loss {val_loss/len(val_dataset):.4f} Acc {val_acc:.4f}")
        
        if val_acc > best_acc:
            best_acc = val_acc
            torch.save(clf_model.state_dict(), 'best_clip_finetune_model.pth')
            print("Saved best model.")
            
    print(f"Best Val Acc: {best_acc:.4f}")
    
    log_experiment("CLIP", "Vision Fine-Tuning ArcFace", best_acc, "lr=1e-4, epochs=20, unfreeze_6, augs, arcface", 'best_clip_finetune_model.pth')
    try:
        manage_top_models('CLIP_Finetune_ArcFace', best_acc, 'best_clip_finetune_model.pth')
    except Exception as e:
        pass
    
    with open('clip_ft_results.txt', 'w') as f:
        f.write(f"Best Validation Accuracy: {best_acc:.4f}\n")

if __name__ == '__main__':
    main()
