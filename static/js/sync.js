let selectedTracks = new Set();
let isPolling = true;

async function fetchSyncStatus() {
    if (!isPolling) return;
    try {
        const response = await fetch('/api/sync_status');
        const data = await response.json();
        
        document.getElementById('metric-gallery-images').textContent = data.gallery_image_count;
        document.getElementById('metric-unsynced-images').textContent = data.unsynced_image_count;
        document.getElementById('metric-unconfirmed').textContent = data.unconfirmed_count;
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
    
    if (conflictingTracks.length === 0) {
        grid.innerHTML = '<div style="grid-column: 1 / -1; text-align: center; color: #666; padding: 40px; font-size: 18px; background: #111; border-radius: 8px; border: 1px dashed #333;">No conflicting images to review! Gemini agrees with all remaining tracks.</div>';
        return;
    }
    
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
                <strong style="color: #fff; display: block; margin-bottom: 8px; font-size: 14px; text-align: center; letter-spacing: 1px;">THE UNIDENTIFIED CAR</strong>
                ${carouselHtml}
                <div style="display: flex; gap: 15px; margin-top: 15px; text-align: center; min-width: 0;">
                    <div style="flex: 1; min-width: 0; background: #222; padding: 12px; border-radius: 6px; border: 1px solid #333; display: flex; flex-direction: column;">
                        <strong style="color: #bbb; display: block; margin-bottom: 5px; font-size: 14px;">YOLO'S GUESS</strong>
                        <div title="${track.local_label}" style="font-size: 16px; color: #fff; margin-bottom: 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${track.local_label}</div>
                        <div style="display: flex; gap: 4px; justify-content: center; min-width: 0; margin-bottom: 10px;">
                            <img src="/api/reference_image/${encodeURIComponent(track.local_label)}/${track.direction}/0" style="flex: 1; min-width: 0; max-width: 32%; height: 100px; object-fit: contain; border-radius: 4px; background: #000;" onerror="this.outerHTML='<div style=\\'flex: 1; min-width: 0; max-width: 32%; height: 100px; display: flex; align-items: center; justify-content: center; background: #111; color: #555; font-size: 11px; border-radius: 4px; border: 1px dashed #333;\\'>NO REF</div>'">
                            <img src="/api/reference_image/${encodeURIComponent(track.local_label)}/${track.direction}/1" style="flex: 1; min-width: 0; max-width: 32%; height: 100px; object-fit: contain; border-radius: 4px; background: #000;" onerror="this.outerHTML=''">
                            <img src="/api/reference_image/${encodeURIComponent(track.local_label)}/${track.direction}/2" style="flex: 1; min-width: 0; max-width: 32%; height: 100px; object-fit: contain; border-radius: 4px; background: #000;" onerror="this.outerHTML=''">
                        </div>
                        <button onclick="updateGeminiLabel('${track.track_id}', '${track.local_label}')" style="margin-top: auto; padding: 8px; background: #444; color: #fff; border: 1px solid #666; border-radius: 4px; cursor: pointer; font-weight: bold; transition: background 0.2s;">✓ CONFIRM YOLO</button>
                    </div>
                    <div style="flex: 1; min-width: 0; background: #222; padding: 12px; border-radius: 6px; border: 1px solid #333; display: flex; flex-direction: column;">
                        <strong style="color: #0ff; display: block; margin-bottom: 5px; font-size: 14px;">GEMINI'S GUESS</strong>
                        <div title="${track.gemini_label}" style="font-size: 16px; color: #fff; margin-bottom: 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${track.gemini_label}</div>
                        <div style="display: flex; gap: 4px; justify-content: center; min-width: 0; margin-bottom: 10px;">
                            <img src="/api/reference_image/${encodeURIComponent(track.gemini_label)}/${track.direction}/0" style="flex: 1; min-width: 0; max-width: 32%; height: 100px; object-fit: contain; border-radius: 4px; background: #000;" onerror="this.outerHTML='<div style=\\'flex: 1; min-width: 0; max-width: 32%; height: 100px; display: flex; align-items: center; justify-content: center; background: #111; color: #555; font-size: 11px; border-radius: 4px; border: 1px dashed #333;\\'>NO REF</div>'">
                            <img src="/api/reference_image/${encodeURIComponent(track.gemini_label)}/${track.direction}/1" style="flex: 1; min-width: 0; max-width: 32%; height: 100px; object-fit: contain; border-radius: 4px; background: #000;" onerror="this.outerHTML=''">
                            <img src="/api/reference_image/${encodeURIComponent(track.gemini_label)}/${track.direction}/2" style="flex: 1; min-width: 0; max-width: 32%; height: 100px; object-fit: contain; border-radius: 4px; background: #000;" onerror="this.outerHTML=''">
                        </div>
                        <button onclick="updateGeminiLabel('${track.track_id}', '${track.gemini_label}')" style="margin-top: auto; padding: 8px; background: #0056b3; color: #fff; border: 1px solid #004085; border-radius: 4px; cursor: pointer; font-weight: bold; transition: background 0.2s;">✓ CONFIRM GEMINI</button>
                    </div>
                </div>
                <div style="margin-top: 15px; text-align: center; background: #111; padding: 12px; border-radius: 6px;">
                    <strong style="color: #ffc107; font-size: 14px; display: block; margin-bottom: 5px;">MANUAL OVERRIDE</strong> 
                    <input type="text" value="${track.gemini_label || ''}" 
                           placeholder="Type completely different label..."
                           onchange="updateGeminiLabel('${track.track_id}', this.value)"
                           style="background: #000; color: #fff; border: 1px solid #444; padding: 10px; border-radius: 4px; width: 90%; font-size: 16px; text-align: center;">
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
