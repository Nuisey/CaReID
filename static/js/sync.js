let selectedTracks = new Set();
let isPolling = true;

async function fetchSyncStatus() {
    if (!isPolling) return;
    try {
        const response = await fetch('/api/sync_status');
        const data = await response.json();
        
        document.getElementById('metric-unconfirmed').textContent = data.unconfirmed_count;
        document.getElementById('metric-unsynced-images').textContent = data.unsynced_image_count;
        document.getElementById('metric-checking').textContent = data.checking_count;
        document.getElementById('metric-checked').textContent = data.checked_count;
        
        const runBtn = document.getElementById('btn-run-gemini');
        if (data.checking_count > 0) {
            runBtn.disabled = true;
            runBtn.textContent = "Checking...";
            runBtn.style.opacity = '0.5';
            runBtn.style.cursor = 'not-allowed';
        } else {
            runBtn.disabled = false;
            runBtn.textContent = "Gemini Check";
            runBtn.style.opacity = '1';
            runBtn.style.cursor = 'pointer';
        }
        
        const batchTimeDisplay = document.getElementById('batch-time-display');
        if (batchTimeDisplay) {
            if (data.batch_start_time) {
                batchTimeDisplay.textContent = `Active batch pushed to Google at: ${data.batch_start_time}`;
            } else {
                batchTimeDisplay.textContent = "";
            }
        }
        
        renderGrid(data.tracks);
        if (data.logs && data.logs.length > 0) {
            renderLogs(data.logs);
        }
    } catch (error) {
        console.error("Error fetching sync status:", error);
    }
}

function renderGrid(tracks) {
    const grid = document.getElementById('sync-grid');
    grid.innerHTML = '';
    
    // Only show tracks where Gemini has checked it, and it DISAGREES with YOLO's label
    const conflictingTracks = tracks.filter(t => t.status === 'checked' && !t.gemini_agrees);
    
    // Group tracks by their predicted Gemini label, fallback to local label
    const groupedTracks = {};
    conflictingTracks.forEach(track => {
        const groupKey = track.gemini_label || track.local_label;
        if (!groupedTracks[groupKey]) groupedTracks[groupKey] = [];
        groupedTracks[groupKey].push(track);
    });

    Object.keys(groupedTracks).forEach(label => {
        const groupHeader = document.createElement('h3');
        groupHeader.style.gridColumn = '1 / -1';
        groupHeader.style.color = 'white';
        groupHeader.style.marginTop = '20px';
        groupHeader.style.borderBottom = '1px solid #555';
        groupHeader.style.paddingBottom = '5px';
        groupHeader.textContent = `Predicted Identity: ${label}`;
        grid.appendChild(groupHeader);

        groupedTracks[label].forEach(track => {
            const card = document.createElement('div');
            let borderClass = '';
            let badgeHtml = '';
            
            if (track.status === 'checked') {
                if (track.gemini_agrees) {
                    borderClass = 'agree';
                    badgeHtml = '<div class="badge">✓</div>';
                } else {
                    borderClass = 'disagree';
                }
            }
            
            card.className = `track-card ${borderClass}`;
            
            let carouselHtml = '<div class="carousel">';
            track.images.forEach(img => {
                carouselHtml += `<img src="/api/unconfirmed_image/${img}" alt="Car Image">`;
            });
            carouselHtml += '</div>';
            
            card.innerHTML = `
                ${badgeHtml}
                ${carouselHtml}
                <div style="display: flex; align-items: center; justify-content: space-between; margin-top: 10px;">
                    <div>
                        <strong style="color: #bbb;">Local:</strong> ${track.local_label} <br>
                        <strong style="color: #bbb;">Gemini:</strong> 
                        <input type="text" value="${track.gemini_label || ''}" 
                               placeholder="Enter label"
                               onchange="updateGeminiLabel('${track.track_id}', this.value)"
                               style="background: #222; color: #fff; border: 1px solid #444; padding: 3px 5px; border-radius: 4px; width: 150px; font-size: 12px; margin-top: 4px; display: block;">
                    </div>
                </div>
            `;
            grid.appendChild(card);
        });
    });
}

function renderLogs(logs) {
    const logContainer = document.getElementById('sync-logs');
    logContainer.innerHTML = logs.map(log => `<div>${log}</div>`).join('');
}

window.toggleSelection = function(trackId, isChecked) {
    if (isChecked) selectedTracks.add(trackId);
    else selectedTracks.delete(trackId);
}

window.updateGeminiLabel = async function(trackId, newLabel) {
    if (!newLabel.trim()) return;
    await fetch('/api/update_gemini_label', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ track_id: trackId, label: newLabel })
    });
    fetchSyncStatus();
}

document.getElementById('btn-run-gemini').addEventListener('click', async (e) => {
    e.target.disabled = true;
    e.target.textContent = "Starting...";
    e.target.style.opacity = '0.5';
    
    // Attempt to run using the backend's hidden .env key first
    let response = await fetch('/api/run_gemini_batch', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({})
    });
    
    // If the backend doesn't have an API key stored, ask the user manually
    if (response.status === 400) {
        const apiKey = prompt("Please enter your Gemini API Key to start the Batch Check:");
        if (!apiKey) return;
        
        response = await fetch('/api/run_gemini_batch', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ api_key: apiKey })
        });
    }
    
    fetchSyncStatus();
});

document.getElementById('btn-cancel-gemini').addEventListener('click', async () => {
    const confirmed = confirm("Are you sure you want to cancel the remaining batch checks?");
    if (!confirmed) return;
    
    await fetch('/api/cancel_gemini_batch', { method: 'POST' });
    fetchSyncStatus();
});

// Poll every 5 seconds
setInterval(fetchSyncStatus, 5000);
fetchSyncStatus();
