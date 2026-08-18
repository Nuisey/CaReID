import argparse
import os
import torch
import torch.nn as nn
import torch.optim as optim
from torch.optim import lr_scheduler
from torch.utils.data import DataLoader
from torchvision import transforms
import pandas as pd
from tqdm import tqdm

from dataset import ImageDataset

# 1. EfficientNetV2
import timm

# 2. ViT + LoRA
from transformers import ViTForImageClassification, CLIPModel, CLIPProcessor
try:
    from peft import LoraConfig, get_peft_model
except ImportError:
    print("Warning: PEFT library not installed. Please `pip install peft` to use LoRA.")

def get_transforms(model_name):
    # Different models expect different input sizes, but we'll standardise for simplicity, 
    # except CLIP handles its own via processor if needed, but we'll use torchvision for dataloader
    if model_name == 'efficientnet':
        h, w = 384, 384 # tf_efficientnetv2_l often uses 384
    else:
        h, w = 224, 224
        
    train_transforms = transforms.Compose([
        transforms.Resize((h, w), interpolation=transforms.InterpolationMode.BICUBIC),
        transforms.RandomHorizontalFlip(),
        transforms.RandomRotation(15),
        transforms.ColorJitter(brightness=0.3, contrast=0.3, saturation=0.3, hue=0.1),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ])
    
    val_transforms = transforms.Compose([
        transforms.Resize((h, w), interpolation=transforms.InterpolationMode.BICUBIC),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ])
    return train_transforms, val_transforms


def create_efficientnet(num_classes):
    print("Initializing EfficientNetV2...")
    model = timm.create_model('tf_efficientnetv2_s.in21k', pretrained=True, num_classes=num_classes)
    
    # Freeze early layers, unfreeze top blocks (e.g. blocks 5 and 6 and head)
    for name, param in model.named_parameters():
        if 'blocks.5' in name or 'blocks.6' in name or 'head' in name or 'classifier' in name:
            param.requires_grad = True
        else:
            param.requires_grad = False
    return model

def create_vit_lora(num_classes):
    print("Initializing ViT with LoRA...")
    model = ViTForImageClassification.from_pretrained(
        'google/vit-base-patch16-224', 
        num_labels=num_classes, 
        ignore_mismatched_sizes=True
    )
    
    # LoRA Configuration
    config = LoraConfig(
        r=16,
        lora_alpha=16,
        target_modules=["query", "value"],
        lora_dropout=0.1,
        bias="none",
        modules_to_save=["classifier"]
    )
    model = get_peft_model(model, config)
    model.print_trainable_parameters()
    return model

def train_classification_model(model, dataloaders, criterion, optimizer, scheduler, num_epochs, device, save_path, model_type):
    best_acc = 0.0
    for epoch in range(num_epochs):
        print(f'Epoch {epoch}/{num_epochs - 1}')
        print('-' * 10)

        for phase in ['train', 'val']:
            if phase == 'train':
                model.train()
            else:
                model.eval()

            running_loss = 0.0
            running_corrects = 0

            for inputs, labels in tqdm(dataloaders[phase], desc=phase):
                inputs = inputs.to(device)
                labels = labels.to(device)

                optimizer.zero_grad()

                with torch.set_grad_enabled(phase == 'train'):
                    if model_type == 'vit':
                        outputs = model(inputs).logits
                    else:
                        outputs = model(inputs)
                        
                    _, preds = torch.max(outputs, 1)
                    loss = criterion(outputs, labels)

                    if phase == 'train':
                        loss.backward()
                        optimizer.step()

                running_loss += loss.item() * inputs.size(0)
                running_corrects += torch.sum(preds == labels.data)

            if phase == 'train' and scheduler is not None:
                scheduler.step()

            epoch_loss = running_loss / len(dataloaders[phase].dataset)
            epoch_acc = running_corrects.double() / len(dataloaders[phase].dataset)

            print(f'{phase} Loss: {epoch_loss:.4f} Acc: {epoch_acc:.4f}')

            if phase == 'val' and epoch_acc > best_acc:
                best_acc = epoch_acc
                torch.save(model.state_dict(), save_path)
                print(f"Saved best model with Acc: {best_acc:.4f}")
        print()
    print(f'Best val Acc: {best_acc:4f}')
    return model


def train_clip_probe(dataloaders, num_classes, device, save_path):
    print("Initializing CLIP Linear Probe...")
    clip_model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32").to(device)
    clip_model.eval()
    
    # Simple MLP on top of CLIP embeddings (512 dims for base patch32)
    mlp = nn.Sequential(
        nn.Linear(512, 256),
        nn.ReLU(),
        nn.Dropout(0.3),
        nn.Linear(256, num_classes)
    ).to(device)
    
    optimizer = optim.AdamW(mlp.parameters(), lr=1e-3)
    criterion = nn.CrossEntropyLoss(label_smoothing=0.1)
    
    best_acc = 0.0
    num_epochs = 15
    
    for epoch in range(num_epochs):
        print(f'Epoch {epoch}/{num_epochs - 1}')
        for phase in ['train', 'val']:
            if phase == 'train':
                mlp.train()
            else:
                mlp.eval()

            running_loss = 0.0
            running_corrects = 0

            for inputs, labels in tqdm(dataloaders[phase], desc=phase):
                inputs = inputs.to(device)
                labels = labels.to(device)

                with torch.no_grad():
                    # Get image features from CLIP
                    features = clip_model.get_image_features(pixel_values=inputs)
                    features = features / features.norm(p=2, dim=-1, keepdim=True) # Normalize

                optimizer.zero_grad()
                with torch.set_grad_enabled(phase == 'train'):
                    outputs = mlp(features.float())
                    _, preds = torch.max(outputs, 1)
                    loss = criterion(outputs, labels)

                    if phase == 'train':
                        loss.backward()
                        optimizer.step()

                running_loss += loss.item() * inputs.size(0)
                running_corrects += torch.sum(preds == labels.data)

            epoch_acc = running_corrects.double() / len(dataloaders[phase].dataset)
            print(f'CLIP Probe {phase} Acc: {epoch_acc:.4f}')

            if phase == 'val' and epoch_acc > best_acc:
                best_acc = epoch_acc
                torch.save(mlp.state_dict(), save_path)
    print(f'Best CLIP val Acc: {best_acc:4f}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', type=str, choices=['efficientnet', 'vit', 'clip'], required=True)
    parser.add_argument('--data_dir', default='Data/TrainingData')
    parser.add_argument('--train_csv', default='Data/train.csv')
    parser.add_argument('--val_csv', default='Data/val.csv')
    parser.add_argument('--save_dir', default='Brain/Ensemble')
    parser.add_argument('--batch_size', default=32, type=int)
    parser.add_argument('--epochs', default=20, type=int)
    parser.add_argument('--lr', default=1e-4, type=float)
    parser.add_argument('--id_classes', default=751, type=int)
    opt = parser.parse_args()

    os.makedirs(opt.save_dir, exist_ok=True)
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

    train_df = pd.read_csv(opt.train_csv)
    val_df = pd.read_csv(opt.val_csv)

    train_trans, val_trans = get_transforms(opt.model)

    train_dataset = ImageDataset(opt.data_dir, train_df, target_label='id', transform=train_trans, return_mtl=False)
    val_dataset = ImageDataset(opt.data_dir, val_df, target_label='id', classes=train_dataset.classes, transform=val_trans, return_mtl=False)

    dataloaders = {
        'train': DataLoader(train_dataset, batch_size=opt.batch_size, shuffle=True, num_workers=4),
        'val': DataLoader(val_dataset, batch_size=opt.batch_size, shuffle=False, num_workers=4)
    }

    if opt.model == 'efficientnet':
        model = create_efficientnet(opt.id_classes)
        model = model.to(device)
        optimizer = optim.AdamW(filter(lambda p: p.requires_grad, model.parameters()), lr=opt.lr, weight_decay=1e-4)
        scheduler = lr_scheduler.CosineAnnealingLR(optimizer, T_max=opt.epochs)
        criterion = nn.CrossEntropyLoss(label_smoothing=0.1)
        train_classification_model(model, dataloaders, criterion, optimizer, scheduler, opt.epochs, device, os.path.join(opt.save_dir, 'efficientnet_best.pth'), 'efficientnet')
        
    elif opt.model == 'vit':
        model = create_vit_lora(opt.id_classes)
        model = model.to(device)
        optimizer = optim.AdamW(model.parameters(), lr=opt.lr)
        scheduler = lr_scheduler.CosineAnnealingLR(optimizer, T_max=opt.epochs)
        criterion = nn.CrossEntropyLoss(label_smoothing=0.1)
        train_classification_model(model, dataloaders, criterion, optimizer, scheduler, opt.epochs, device, os.path.join(opt.save_dir, 'vit_lora_best.pth'), 'vit')
        
    elif opt.model == 'clip':
        train_clip_probe(dataloaders, opt.id_classes, device, os.path.join(opt.save_dir, 'clip_probe_best.pth'))
