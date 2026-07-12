# CarID Tracking System

## Overview
This system is an automated, real-time pipeline that uses computer vision (YOLO) and a deep learning Re-Identification (ReID) model to track, identify, and log vehicles arriving at or leaving a specific neighborhood area. The data is instantly visualized on a local web dashboard.

## How It Works

### 1. Object Tracking & Cropping (`YOLO_Identification.py`)
- Continuously captures video from the live camera feed (default Camera 0).
- Uses a YOLO model to detect and track vehicles (Cars, Trucks, Buses) in real-time.
- Stores the vertical (`y`) coordinate history of each tracked vehicle. 
- Analyzes the change in the `y` coordinate. If the vehicle moves down the screen, it is marked as **Arriving**; if it moves up, it is marked as **Leaving**.
- Crops the vehicle from the frame and saves the image to the `HotFolder`. The exact timestamp and direction are injected directly into the filename.

### 2. Vehicle Identification (`realtime_identifier_with_labels.py`)
- Continuously monitors the `HotFolder` for new images in the background.
- When a new crop appears, it passes the image through a ResNet-IBN ReID model to extract visual features.
- Computes cosine similarity between the extracted feature and a pre-compiled `Gallery` of known vehicles to find the best match.
- References `label_map.csv` to translate the mathematical ID into a human-readable label (e.g., "Silver Toyota Camry").
- Logs the timestamp, identity, confidence, and direction into `Data/CarLabels_Unprocessed.csv`.
- Moves the processed crop into `Data/Unconfirmed` so the web server can safely read it.

### 3. Web Dashboard (`app.py` & Frontend)
- A Flask web server serves an interactive dashboard at `http://localhost:5000`.
- Continuously reads `CarLabels_Unprocessed.csv` to construct a real-time **Timeline** of recent events (including the cropped image, timestamp, and Arriving/Leaving status).
- The **Homepage** features an interactive, drag-and-drop map where you can create "house boxes". 
- Car icons inside these boxes can be assigned to known vehicles via a searchable dropdown.
- The icons automatically change color (Blue = Home, Gray = Away) by polling the latest Arriving/Leaving events processed by the ReID pipeline. State is persistently saved in `state.json`.
