from flask import Flask, render_template_string, send_file
import json
import os

app = Flask(__name__)

HTML = """
<!DOCTYPE html>
<html lang="en" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <title>Model Comparison Viewer</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #121212; color: #e0e0e0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        .card { background-color: #1e1e1e; border-color: #333; margin-bottom: 20px; transition: transform 0.2s; }
        .card:hover { transform: translateY(-5px); }
        .img-container { height: 250px; display: flex; align-items: center; justify-content: center; background: #000; overflow: hidden; border-radius: 4px 4px 0 0; }
        .img-container img { max-height: 100%; max-width: 100%; object-fit: contain; }
        .agreed-header { color: #4ade80; margin-top: 50px; margin-bottom: 20px; font-weight: 300; border-bottom: 1px solid #333; padding-bottom: 10px; }
        .disagreed-header { color: #f87171; margin-top: 40px; margin-bottom: 20px; font-weight: 300; }
        .pred-label { font-size: 0.9rem; padding: 4px 8px; border-radius: 4px; display: inline-block; margin-bottom: 6px; font-weight: 500; }
        .pred-resnet { background-color: rgba(59, 130, 246, 0.15); border: 1px solid rgba(59, 130, 246, 0.5); color: #93c5fd; }
        .pred-dino { background-color: rgba(168, 85, 247, 0.15); border: 1px solid rgba(168, 85, 247, 0.5); color: #d8b4fe; }
        .pred-mtl { background-color: rgba(234, 179, 8, 0.15); border: 1px solid rgba(234, 179, 8, 0.5); color: #fde047; }
        
        .nav-pills .nav-link.active { background-color: #3b82f6; }
        .nav-link { color: #9ca3af; }
        .nav-link:hover { color: #f3f4f6; }
        
        .badge-custom { background-color: #334155; font-size: 0.8rem; vertical-align: middle; }
    </style>
</head>
<body class="container py-5">
    <h1 class="mb-4 text-center fw-light">🤖 Model Comparison Arena</h1>
    <p class="text-center text-muted mb-5">Scroll through and visually compare the accuracies of ResNet, DINOv3 Re-ID, and DINOv3 MTL Classification</p>
    
    <ul class="nav nav-pills justify-content-center mb-5" id="myTab" role="tablist">
        <li class="nav-item" role="presentation">
            <button class="nav-link active px-4 rounded-pill me-2" id="disagree-tab" data-bs-toggle="tab" data-bs-target="#disagree" type="button" role="tab">
                Disagreements <span class="badge bg-danger ms-2">{{ disagreed|length }}</span>
            </button>
        </li>
        <li class="nav-item" role="presentation">
            <button class="nav-link px-4 rounded-pill" id="agree-tab" data-bs-toggle="tab" data-bs-target="#agree" type="button" role="tab">
                Agreements <span class="badge bg-success ms-2">{{ agreed_keys|length }} Categories</span>
            </button>
        </li>
    </ul>

    <div class="tab-content" id="myTabContent">
        <!-- Disagreements Tab -->
        <div class="tab-pane fade show active" id="disagree" role="tabpanel">
            <h3 class="disagreed-header text-center mb-5">Where they disagreed</h3>
            <div class="row row-cols-1 row-cols-md-2 row-cols-lg-3 g-4">
                {% for item in disagreed %}
                <div class="col">
                    <div class="card h-100 shadow">
                        <div class="img-container">
                            <img src="/image?path={{ item.path }}" loading="lazy">
                        </div>
                        <div class="card-body">
                            <div class="d-flex flex-column gap-2">
                                <div>
                                    <div class="pred-label pred-resnet w-100"><strong>ResNet:</strong> {{ item.resnet_reid }}</div>
                                </div>
                                <div>
                                    <div class="pred-label pred-dino w-100"><strong>DINOv3 Re-ID:</strong> {{ item.dino_reid }}</div>
                                </div>
                                <div>
                                    <div class="pred-label pred-mtl w-100"><strong>DINOv3 MTL:</strong> {{ item.dino_class }}</div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>

        <!-- Agreements Tab -->
        <div class="tab-pane fade" id="agree" role="tabpanel">
            <h3 class="text-center text-success fw-light mb-5">Perfect Unanimous Agreement</h3>
            {% for cat in agreed_keys %}
                <h4 class="agreed-header">{{ cat }} <span class="badge badge-custom rounded-pill ms-2">{{ agreed[cat]|length }} images</span></h4>
                <div class="row row-cols-2 row-cols-md-4 row-cols-lg-5 g-3">
                    {% for item in agreed[cat] %}
                    <div class="col">
                        <div class="card h-100 shadow-sm border-success border-opacity-25">
                            <div class="img-container" style="height: 150px;">
                                <img src="/image?path={{ item.path }}" loading="lazy">
                            </div>
                        </div>
                    </div>
                    {% endfor %}
                </div>
            {% endfor %}
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
"""

@app.route('/')
def index():
    if not os.path.exists('Data/comparison_results.json'):
        return "<h3>No data found! Please run `python generate_comparison.py` first to analyze the images.</h3>"
        
    with open('Data/comparison_results.json', 'r') as f:
        results = json.load(f)
        
    agreed = {}
    disagreed = []
    
    for r in results:
        if r['agree']:
            cat = r['resnet_reid']
            if cat not in agreed:
                agreed[cat] = []
            agreed[cat].append(r)
        else:
            disagreed.append(r)
            
    agreed_keys = sorted(list(agreed.keys()))
    
    return render_template_string(HTML, agreed=agreed, agreed_keys=agreed_keys, disagreed=disagreed)

@app.route('/image')
def serve_image():
    from flask import request
    path = request.args.get('path')
    if os.path.exists(path):
        return send_file(path)
    return "Not found", 404

if __name__ == '__main__':
    print("==================================================")
    print("Starting Comparison Viewer UI!")
    print("Open this link in your browser: http://127.0.0.1:5005")
    print("==================================================")
    app.run(port=5005, debug=False)
