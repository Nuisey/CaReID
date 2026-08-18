import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision import models
from transformers import CLIPModel, CLIPProcessor, ViTModel, ViTImageProcessor
from peft import get_peft_model, LoraConfig

class ArcFace(nn.Module):
    def __init__(self, in_features, out_features, s=30.0, m=0.30):
        super(ArcFace, self).__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.s = s
        self.m = m
        self.weight = nn.Parameter(torch.FloatTensor(out_features, in_features))
        nn.init.xavier_uniform_(self.weight)

    def forward(self, input, label=None):
        cosine = F.linear(F.normalize(input), F.normalize(self.weight))
        return cosine * self.s

class ArcFaceModel(nn.Module):
    def __init__(self, num_classes):
        super(ArcFaceModel, self).__init__()
        self.backbone = models.efficientnet_b0(pretrained=False)
        in_features = self.backbone.classifier[1].in_features
        self.backbone.classifier = nn.Identity()
        self.arcface = ArcFace(in_features, num_classes)

    def forward(self, x):
        features = self.backbone(x)
        return self.arcface(features)

class CLIPFineTuner(nn.Module):
    def __init__(self, num_classes, model_name="openai/clip-vit-large-patch14"):
        super(CLIPFineTuner, self).__init__()
        clip = CLIPModel.from_pretrained(model_name)
        self.vision_model = clip.vision_model
        in_features = self.vision_model.post_layernorm.normalized_shape[0]
        self.arcface = ArcFace(in_features, num_classes)

    def forward(self, pixel_values):
        vision_outputs = self.vision_model(pixel_values=pixel_values)
        features = vision_outputs.pooler_output
        return self.arcface(features)

class ViTArcFaceModel(nn.Module):
    def __init__(self, num_classes, model_name="google/vit-base-patch16-224-in21k"):
        super(ViTArcFaceModel, self).__init__()
        self.backbone = ViTModel.from_pretrained(model_name)
        peft_config = LoraConfig(
            r=64,
            lora_alpha=64,
            target_modules=["q_proj", "v_proj"],
            lora_dropout=0.1,
            bias="none",
            modules_to_save=["pooler"]
        )
        self.backbone = get_peft_model(self.backbone, peft_config)
        in_features = 768
        self.arcface = ArcFace(in_features, num_classes)

    def forward(self, pixel_values):
        outputs = self.backbone(pixel_values=pixel_values)
        features = outputs.pooler_output
        return self.arcface(features)

def load_testing_models(device):
    num_classes = 93
    
    # CNN
    cnn = ArcFaceModel(num_classes)
    cnn.load_state_dict(torch.load('export810/best_cnn_model.pth', map_location='cpu'), strict=False)
    cnn.to(device).eval()
    
    # CLIP
    clip = CLIPFineTuner(num_classes)
    clip.load_state_dict(torch.load('export810/best_clip_finetune_model.pth', map_location='cpu'), strict=False)
    clip.to(device).eval()
    clip_processor = CLIPProcessor.from_pretrained("openai/clip-vit-large-patch14")
    
    # ViT
    vit = ViTArcFaceModel(num_classes)
    vit.load_state_dict(torch.load('export810/best_vit_lora_model.pth', map_location='cpu'), strict=False)
    vit.to(device).eval()
    vit_processor = ViTImageProcessor.from_pretrained("google/vit-base-patch16-224-in21k")
    
    return cnn, clip, clip_processor, vit, vit_processor
