import pandas as pd
import numpy as np

# Load label map
labels_df = pd.read_csv('Data/label_map.csv', header=None, names=['id', 'label'])
labels_df['id'] = labels_df['id'].astype(int)

# Split label into color, make, model
# E.g., 'Blue,Honda,Pilot'
labels_df[['color_str', 'make_str', 'model_str']] = labels_df['label'].str.split(',', expand=True)

# Strip spaces
labels_df['color_str'] = labels_df['color_str'].str.strip()
labels_df['make_str'] = labels_df['make_str'].str.strip()
labels_df['model_str'] = labels_df['model_str'].str.strip()

# Create mappings
makes = {m: i for i, m in enumerate(labels_df['make_str'].unique())}
models = {m: i for i, m in enumerate(labels_df['model_str'].unique())}
colors = {c: i for i, c in enumerate(labels_df['color_str'].unique())}

# Save maps for inference later
pd.Series({v: k for k, v in makes.items()}).to_csv('Data/make_map.csv', header=False)
pd.Series({v: k for k, v in models.items()}).to_csv('Data/model_map.csv', header=False)
pd.Series({v: k for k, v in colors.items()}).to_csv('Data/color_map.csv', header=False)

# Map labels to integers
labels_df['make'] = labels_df['make_str'].map(makes)
labels_df['model'] = labels_df['model_str'].map(models)
labels_df['color'] = labels_df['color_str'].map(colors)

# Load Gallery
gallery_df = pd.read_csv('Data/Gallery/Gallery.csv')
gallery_df['id'] = gallery_df['id'].astype(int)

# Merge
merged = pd.merge(gallery_df, labels_df[['id', 'make', 'model', 'color']], on='id', how='left')

# Drop NA in case some IDs don't have mapping
merged = merged.dropna()
merged['make'] = merged['make'].astype(int)
merged['model'] = merged['model'].astype(int)
merged['color'] = merged['color'].astype(int)

# Split into train/val (stratified by id so % of each car is roughly the same)
train_df = merged.groupby('id', group_keys=False).apply(lambda x: x.sample(frac=0.8, random_state=42) if len(x) > 1 else x, include_groups=False)
val_df = merged.drop(train_df.index)

# Save
train_df.to_csv('Data/train.csv', index=False)
val_df.to_csv('Data/val.csv', index=False)

print(f'Train: {len(train_df)}, Val: {len(val_df)}')
print(f'Makes: {len(makes)}, Models: {len(models)}, Colors: {len(colors)}')
