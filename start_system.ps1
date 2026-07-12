$ProjectDirectory = "C:\Users\nolan\OneDrive\Programming\CarID_Final"
$CondaEnvName = "CarReID"

Write-Host "Activating Conda environment: $CondaEnvName"
# This requires conda to be available in PowerShell
conda activate $CondaEnvName

Set-Location $ProjectDirectory

Write-Host "Starting YOLO Tracker..."
Start-Process -FilePath "python" -ArgumentList "YOLO_Identification.py" -WorkingDirectory $ProjectDirectory

Write-Host "Starting ReID Inference..."
Start-Process -FilePath "python" -ArgumentList "realtime_identifier_with_labels.py --model_opts Brain/opts.yaml --checkpoint Brain/Final10232025.pth --gallery_csv_path Data/Gallery/Gallery.csv --label_mapping Data/label_map.csv --data_dir Data/Gallery/LabeledCarDataPhotos --watch_folder HotFolder --processed_folder Data/Unconfirmed --log_csv Data/CarLabels_Unprocessed.csv" -WorkingDirectory $ProjectDirectory

Write-Host "Starting Flask Web App..."
python app.py
