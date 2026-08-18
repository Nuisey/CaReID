import os
import json

LOG_FILE = 'experiments_log.md'
TOP_MODELS_JSON = 'top_models.json'

def init_files():
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'w') as f:
            f.write("# Model Ensembling Experiment Logs\n\n")
    if not os.path.exists(TOP_MODELS_JSON):
        with open(TOP_MODELS_JSON, 'w') as f:
            json.dump([], f)

def log_experiment(architecture, strategy, val_acc, hyperparams, file_path, status="Success"):
    init_files()
    with open(LOG_FILE, 'a') as f:
        f.write(f"## {architecture} - {strategy}\n")
        f.write(f"- **Status**: {status}\n")
        f.write(f"- **Validation Accuracy**: {val_acc:.4f}\n")
        f.write(f"- **Hyperparameters**: {hyperparams}\n")
        f.write(f"- **Saved Model**: {file_path}\n\n")
        
def manage_top_models(model_name, val_acc, file_path):
    init_files()
    with open(TOP_MODELS_JSON, 'r') as f:
        top_models = json.load(f)
        
    top_models.append({"name": model_name, "val_acc": val_acc, "path": file_path})
    top_models.sort(key=lambda x: x['val_acc'], reverse=True)
    
    # Only keep top 5
    if len(top_models) > 5:
        to_delete = top_models[5:]
        top_models = top_models[:5]
        for model in to_delete:
            if os.path.exists(model['path']):
                os.remove(model['path'])
                print(f"Deleted model {model['path']} (score: {model['val_acc']:.4f}) to keep top 5.")
                
    with open(TOP_MODELS_JSON, 'w') as f:
        json.dump(top_models, f, indent=4)
