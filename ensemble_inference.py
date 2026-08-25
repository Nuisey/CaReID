import torch
import torch.nn as nn
from torchvision import transforms
from PIL import Image
import os

# Import model creation functions from training script
from train_ensemble import create_efficientnet, create_vit_lora
from transformers import CLIPModel, CLIPProcessor
try:
    from peft import PeftModel
except ImportError:
    pass

class CarClassifierEnsemble:
    def __init__(self, num_classes, model_dir, device='cuda' if torch.cuda.is_available() else 'cpu'):
        self.device = device
        self.num_classes = num_classes
        
        # 1. Load EfficientNetV2
        self.efficientnet = create_efficientnet(num_classes)
        eff_path = os.path.join(model_dir, 'efficientnet_best.pth')
        if os.path.exists(eff_path):
            self.efficientnet.load_state_dict(torch.load(eff_path, map_location=device))
        self.efficientnet.to(device)
        self.efficientnet.eval()
        
        # 2. Load ViT with LoRA
        self.vit = create_vit_lora(num_classes)
        vit_path = os.path.join(model_dir, 'vit_lora_best.pth')
        if os.path.exists(vit_path):
            self.vit.load_state_dict(torch.load(vit_path, map_location=device))
        self.vit.to(device)
        self.vit.eval()
        
        # 3. Load CLIP Probe
        self.clip_model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32").to(device)
        self.clip_model.eval()
        
        self.clip_mlp = nn.Sequential(
            nn.Linear(512, 256),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(256, num_classes)
        ).to(device)
        clip_path = os.path.join(model_dir, 'clip_probe_best.pth')
        if os.path.exists(clip_path):
            self.clip_mlp.load_state_dict(torch.load(clip_path, map_location=device))
        self.clip_mlp.eval()
        
        # Transforms (align with training)
        self.eff_transform = transforms.Compose([
            transforms.Resize((384, 384), interpolation=transforms.InterpolationMode.BICUBIC),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
        ])
        
        self.vit_transform = transforms.Compose([
            transforms.Resize((224, 224), interpolation=transforms.InterpolationMode.BICUBIC),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
        ])

    def predict(self, image_path):
        image = Image.open(image_path).convert('RGB')
        
        # Preprocess
        eff_input = self.eff_transform(image).unsqueeze(0).to(self.device)
        vit_input = self.vit_transform(image).unsqueeze(0).to(self.device)
        
        with torch.no_grad():
            # EfficientNet Prediction
            eff_logits = self.efficientnet(eff_input)
            eff_probs = (eff_logits / 30.0).clamp(0.0, 1.0)
            
            # ViT Prediction
            vit_logits = self.vit(vit_input).logits
            vit_probs = (vit_logits / 30.0).clamp(0.0, 1.0)
            
            # CLIP Probe Prediction
            clip_features = self.clip_model.get_image_features(pixel_values=vit_input)
            clip_features = clip_features / clip_features.norm(p=2, dim=-1, keepdim=True)
            clip_logits = self.clip_mlp(clip_features.float())
            clip_probs = (clip_logits / 30.0).clamp(0.0, 1.0)
            
            # SOFT VOTING ENSEMBLE
            ensemble_probs = (eff_probs + vit_probs + clip_probs) / 3.0
            
            # Get final prediction
            confidence, class_id = torch.max(ensemble_probs, 1)
            
        return {
            'class_id': class_id.item(),
            'confidence': confidence.item(),
            'model_breakdown': {
                'efficientnet_prob': eff_probs[0][class_id].item(),
                'vit_prob': vit_probs[0][class_id].item(),
                'clip_prob': clip_probs[0][class_id].item()
            }
        }

if __name__ == "__main__":
    # Example usage:
    # 1. Provide the number of classes (e.g., 751) and directory where models are saved
    # ensemble = CarClassifierEnsemble(num_classes=751, model_dir='Brain/Ensemble')
    # 2. Predict on a test image
    # result = ensemble.predict("path_to_test_image.jpg")
    # print(result)
    print("Ensemble inference module loaded successfully.")
