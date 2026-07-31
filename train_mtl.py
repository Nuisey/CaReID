import argparse
import os
import time
import torch
import torch.nn as nn
import torch.optim as optim
from torch.optim import lr_scheduler
from torch.utils.data import DataLoader
from torchvision import transforms
import pandas as pd
from tqdm import tqdm

from dataset import ImageDataset, BatchSampler
from load_model import create_model

def get_transforms():
    h, w = 224, 224
    train_transforms = transforms.Compose([
        transforms.Resize((h, w), interpolation=transforms.InterpolationMode.BICUBIC),
        transforms.RandomHorizontalFlip(),
        transforms.RandomRotation(10),
        transforms.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2, hue=0.1),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ])
    
    val_transforms = transforms.Compose([
        transforms.Resize((h, w), interpolation=transforms.InterpolationMode.BICUBIC),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ])
    return train_transforms, val_transforms

def train_model(model, dataloaders, criterion, optimizer, scheduler, num_epochs, device, save_dir):
    best_acc = 0.0
    
    for epoch in range(num_epochs):
        print(f'Epoch {epoch}/{num_epochs - 1}')
        print('-' * 10)

        # Each epoch has a training and validation phase
        for phase in ['train', 'val']:
            if phase == 'train':
                model.train()
            else:
                model.eval()

            running_loss = 0.0
            running_corrects_id = 0
            running_corrects_make = 0
            running_corrects_model = 0
            running_corrects_color = 0

            # Iterate over data.
            for inputs, labels_id, labels_make, labels_model, labels_color in tqdm(dataloaders[phase], desc=phase):
                inputs = inputs.to(device)
                labels_id = labels_id.to(device)
                labels_make = labels_make.to(device)
                labels_model = labels_model.to(device)
                labels_color = labels_color.to(device)

                optimizer.zero_grad()

                with torch.set_grad_enabled(phase == 'train'):
                    out_id, out_make, out_model, out_color = model(inputs)

                    _, preds_id = torch.max(out_id, 1)
                    _, preds_make = torch.max(out_make, 1)
                    _, preds_model = torch.max(out_model, 1)
                    _, preds_color = torch.max(out_color, 1)

                    loss_id = criterion(out_id, labels_id)
                    loss_make = criterion(out_make, labels_make)
                    loss_model = criterion(out_model, labels_model)
                    loss_color = criterion(out_color, labels_color)

                    # Multi-task loss (equally weighted for now, can be tuned)
                    loss = loss_id + loss_make + loss_model + loss_color

                    if phase == 'train':
                        loss.backward()
                        optimizer.step()

                running_loss += loss.item() * inputs.size(0)
                running_corrects_id += torch.sum(preds_id == labels_id.data)
                running_corrects_make += torch.sum(preds_make == labels_make.data)
                running_corrects_model += torch.sum(preds_model == labels_model.data)
                running_corrects_color += torch.sum(preds_color == labels_color.data)

            if phase == 'train' and scheduler is not None:
                scheduler.step()

            epoch_loss = running_loss / len(dataloaders[phase].dataset)
            acc_id = running_corrects_id.double() / len(dataloaders[phase].dataset)
            acc_make = running_corrects_make.double() / len(dataloaders[phase].dataset)
            acc_model = running_corrects_model.double() / len(dataloaders[phase].dataset)
            acc_color = running_corrects_color.double() / len(dataloaders[phase].dataset)
            
            # Use average accuracy across tasks to determine best model
            avg_acc = (acc_id + acc_make + acc_model + acc_color) / 4.0

            print(f'{phase} Loss: {epoch_loss:.4f}')
            print(f'{phase} Acc: ID: {acc_id:.4f} | Make: {acc_make:.4f} | Model: {acc_model:.4f} | Color: {acc_color:.4f} | Avg: {avg_acc:.4f}')

            # deep copy the model
            if phase == 'val' and avg_acc > best_acc:
                best_acc = avg_acc
                torch.save(model.state_dict(), os.path.join(save_dir, 'best_mtl_model.pth'))
        print()

    print(f'Best val Avg Acc: {best_acc:4f}')
    return model


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Train DINOv3 MTL Model')
    parser.add_argument('--data_dir', default='Data/TrainingData', help='Path to images')
    parser.add_argument('--train_csv', default='Data/train.csv', help='Path to train labels')
    parser.add_argument('--val_csv', default='Data/val.csv', help='Path to val labels')
    parser.add_argument('--save_dir', default='Brain', help='Path to save checkpoints')
    parser.add_argument('--batch_size', default=32, type=int)
    parser.add_argument('--epochs', default=20, type=int)
    parser.add_argument('--lr', default=0.0003, type=float)
    parser.add_argument('--make_classes', default=100, type=int)
    parser.add_argument('--model_classes', default=500, type=int)
    parser.add_argument('--color_classes', default=20, type=int)
    parser.add_argument('--id_classes', default=751, type=int)
    opt = parser.parse_args()

    os.makedirs(opt.save_dir, exist_ok=True)
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

    # Load DataFrames
    print("Loading data...")
    train_df = pd.read_csv(opt.train_csv)
    val_df = pd.read_csv(opt.val_csv)

    train_transforms, val_transforms = get_transforms()

    # Note: dataset classes need to map integer labels. If your CSV has string labels, 
    # you might need to convert them to integers beforehand or let the dataset infer them.
    # We assume 'id', 'make', 'model', 'color' columns are integers mapped via your label_maps.
    train_dataset = ImageDataset(
        opt.data_dir, train_df, target_label='id', 
        transform=train_transforms, return_mtl=True,
        make_col='make', model_col='model', color_col='color'
    )
    val_dataset = ImageDataset(
        opt.data_dir, val_df, target_label='id', 
        classes=train_dataset.classes, # Share the same class index mapping!
        transform=val_transforms, return_mtl=True,
        make_col='make', model_col='model', color_col='color'
    )

    dataloaders = {
        'train': DataLoader(train_dataset, batch_size=opt.batch_size, shuffle=True, num_workers=4),
        'val': DataLoader(val_dataset, batch_size=opt.batch_size, shuffle=False, num_workers=4)
    }

    # Initialize Model
    print("Initializing DINOv3 MTL model...")
    model = create_model(
        opt.id_classes, kind="dinov3_mtl", droprate=0.5, 
        make_classes=opt.make_classes, model_classes=opt.model_classes, color_classes=opt.color_classes
    )
    model = model.to(device)

    # Only optimize parameters that require gradients (unfrozen blocks + heads)
    optimizer_ft = optim.AdamW(filter(lambda p: p.requires_grad, model.parameters()), lr=opt.lr, weight_decay=1e-4)
    
    # Cosine Annealing learning rate schedule
    exp_lr_scheduler = lr_scheduler.CosineAnnealingLR(optimizer_ft, T_max=opt.epochs)

    criterion = nn.CrossEntropyLoss()

    print("Starting training...")
    trained_model = train_model(
        model, dataloaders, criterion, optimizer_ft, exp_lr_scheduler, 
        opt.epochs, device, opt.save_dir
    )
