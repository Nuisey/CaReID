import os
import pandas as pd
from PIL import Image
import torch
from torch.utils.data import Dataset

class CarDataset(Dataset):
    def __init__(self, csv_file, img_dir, transform=None, label_map=None):
        self.df = pd.read_csv(csv_file)
        self.img_dir = img_dir
        self.transform = transform
        
        # Create combined label
        self.df['combined'] = self.df['make'].astype(str) + '_' + self.df['model'].astype(str) + '_' + self.df['color'].astype(str)
        
        if label_map is None:
            # Create a label map dynamically from this dataframe (useful for train)
            unique_labels = sorted(self.df['combined'].unique())
            self.label_map = {lbl: i for i, lbl in enumerate(unique_labels)}
        else:
            self.label_map = label_map
            
        # Filter out rows that have unseen labels (for val set)
        self.df = self.df[self.df['combined'].isin(self.label_map.keys())].reset_index(drop=True)
            
    def __len__(self):
        return len(self.df)
        
    def __getitem__(self, idx):
        img_name = os.path.join(self.img_dir, self.df.iloc[idx]['path'])
        image = Image.open(img_name).convert('RGB')
        
        label_str = self.df.iloc[idx]['combined']
        label = self.label_map[label_str]
        
        if self.transform:
            image = self.transform(image)
            
        return image, label

def get_label_map(train_csv):
    df = pd.read_csv(train_csv)
    df['combined'] = df['make'].astype(str) + '_' + df['model'].astype(str) + '_' + df['color'].astype(str)
    unique_labels = sorted(df['combined'].unique())
    return {lbl: i for i, lbl in enumerate(unique_labels)}
