param (
    [ValidateSet("start", "stop", "restart")]
    [string]$Action = "start"
)

$ProjectDirectory = "C:\Users\nolan\OneDrive\Programming\CarID_Final"

function Stop-System {
    Write-Host "Stopping all CarID background processes..." -ForegroundColor Yellow
    Get-WmiObject Win32_Process | Where-Object { 
        $_.CommandLine -match "YOLO_Identification.py" -or 
        $_.CommandLine -match "realtime_identifier_with_labels.py" -or 
        $_.CommandLine -match "app.py" 
    } | ForEach-Object {
        Write-Host "Killing Process ID: $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Write-Host "All processes have been terminated." -ForegroundColor Green
}

function Start-System {
    Write-Host "Starting CarID System..." -ForegroundColor Yellow
    
    # We use -NoExit so that if a script crashes, the window stays open for you to read the error!
    Write-Host "Starting YOLO Tracker..."
    Start-Process powershell -WindowStyle Minimized -ArgumentList "-NoExit -Command `"conda activate CarReID; cd '$ProjectDirectory'; python YOLO_Identification.py`""

    Write-Host "Starting ReID Inference..."
    Start-Process powershell -WindowStyle Minimized -ArgumentList "-NoExit -Command `"conda activate CarReID; cd '$ProjectDirectory'; python realtime_identifier_with_labels.py --model_opts Brain/opts.yaml --checkpoint Brain/Final10232025.pth --gallery_csv_path Data/Gallery/Gallery.csv --label_mapping Data/label_map.csv --data_dir Data/Gallery/LabeledCarDataPhotos --watch_folder HotFolder --processed_folder Data/Unconfirmed --log_csv Data/CarLabels_Unprocessed.csv`""

    Write-Host "Starting Flask Web App..."
    Start-Process powershell -WindowStyle Minimized -ArgumentList "-NoExit -Command `"conda activate CarReID; cd '$ProjectDirectory'; python app.py`""

    Write-Host "All 3 components are now running in their own windows!" -ForegroundColor Green
}

if ($Action -eq "stop") {
    Stop-System
} elseif ($Action -eq "start") {
    Start-System
} elseif ($Action -eq "restart") {
    Stop-System
    Start-Sleep -Seconds 2
    Start-System
}
