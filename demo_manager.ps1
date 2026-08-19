param (
    [ValidateSet("start", "stop")]
    [string]$Action = "start",
    [string]$VideoPath = "Demo/Demo files/best5min.mp4"
)

$ProjectDirectory = "C:\Users\nolan\OneDrive\Programming\CarID_Final"

function Stop-System {
    Write-Host "Stopping all CarID background processes..." -ForegroundColor Yellow
    Get-WmiObject Win32_Process | Where-Object { 
        $_.CommandLine -match "YOLO_Identification_demo.py" -or
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
    Write-Host "Starting CarID Demo System..." -ForegroundColor Yellow
    
    if (-not (Test-Path $VideoPath)) {
        Write-Host "Error: Video file not found at $VideoPath" -ForegroundColor Red
        return
    }

    if (Test-Path "ai_ready.txt") {
        Remove-Item "ai_ready.txt" -Force
    }

    Write-Host "Starting YOLO Tracker Demo on $VideoPath..."
    Start-Process powershell -WindowStyle Minimized -ArgumentList "-NoExit -Command `"conda activate CarReID; cd '$ProjectDirectory'; python YOLO_Identification_demo.py --video '$VideoPath'`""

    Write-Host "Starting ReID Inference..."
    Start-Process powershell -WindowStyle Minimized -ArgumentList "-NoExit -Command `"conda activate CarReID; cd '$ProjectDirectory'; python realtime_identifier_with_labels.py --model_opts Brain/opts.yaml --checkpoint Brain/Final10232025.pth --gallery_csv_path Data/Gallery/Gallery.csv --label_mapping Data/label_map.csv --data_dir Data/Gallery/LabeledCarDataPhotos --watch_folder HotFolder_Demo --processed_folder Data/Demo_Output/Unconfirmed --log_csv Data/CarLabels_Unprocessed.csv`""

    Write-Host "Starting Flask Web App..."
    Start-Process powershell -WindowStyle Minimized -ArgumentList "-NoExit -Command `"conda activate CarReID; cd '$ProjectDirectory'; python app.py`""

    Write-Host "All 3 components are now running in their own windows! The demo is processing the video." -ForegroundColor Green
}

if ($Action -eq "stop") {
    Stop-System
} elseif ($Action -eq "start") {
    Start-System
}
