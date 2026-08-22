const feedContainer = document.getElementById('feed');

let expandedBreakdowns = new Set();

window.toggleBreakdown = function(filename) {
    if (expandedBreakdowns.has(filename)) {
        expandedBreakdowns.delete(filename);
    } else {
        expandedBreakdowns.add(filename);
    }
    const el = document.getElementById('models-' + filename);
    if (el) {
        el.style.display = expandedBreakdowns.has(filename) ? 'block' : 'none';
    }
}



window.openInspectModal = function(burstImagesStr, labelTitle) {
    const images = JSON.parse(decodeURIComponent(burstImagesStr));
    document.getElementById('inspect-title').innerText = labelTitle;
    const gallery = document.getElementById('inspect-gallery');
    gallery.innerHTML = '';
    images.forEach(img => {
        const el = document.createElement('img');
        el.src = `/images/${img}`;
        el.style.width = '150px';
        el.style.height = '150px';
        el.style.objectFit = 'contain';
        el.style.backgroundColor = '#222';
        el.style.borderRadius = '8px';
        gallery.appendChild(el);
    });
    document.getElementById('inspect-modal').style.display = 'block';
}



let loggedEventIds = new Set();
let isFirstLoad = true;

async function loadFeed() {
    const res = await fetch('/api/timeline');
    const data = await res.json();
    
    // Sort chronologically for logs (oldest first)
    const reversedData = [...data].reverse();
    
    reversedData.forEach(item => {
        const eventId = item.filename;
        if (!loggedEventIds.has(eventId)) {
            loggedEventIds.add(eventId);
            
            // Only add visual text logs for newly arriving items (skip initial flood unless it's just a few)
            if (!isFirstLoad || data.length < 10) {
                const logContainer = document.getElementById('system-logs');
                const logDiv = document.createElement('div');
                logDiv.style.marginBottom = '5px';
                logDiv.innerHTML = `<span style="color: #aaa;">[${item.time}]</span> <span style="color: #0ff;">[Camera 0]</span> Detected <b>${item.predicted_label}</b> - Direction: <span style="color: #ff0;">${item.direction.toUpperCase()}</span> - Conf: ${parseFloat(item.confidence).toFixed(2)}`;
                logContainer.appendChild(logDiv);
                logContainer.scrollTop = logContainer.scrollHeight;
            }
        }
    });
    
    isFirstLoad = false;
    
    feedContainer.innerHTML = '';
    data.forEach(item => {
        const div = document.createElement('div');
        div.className = 'timeline-item';
        
        const statusClass = item.direction === 'arriving' ? 'status-arriving' : 
                            (item.direction === 'leaving' ? 'status-leaving' : 'status-unknown');
        const statusText = item.direction.toUpperCase();
        
        const burstStr = encodeURIComponent(JSON.stringify(item.burst_images));
        const labelStr = `${item.predicted_label}`;
        
        div.innerHTML = `
            <img src="/images/${item.filename}" alt="Car crop" style="cursor: pointer;" onclick="openInspectModal('${burstStr}', '${labelStr}')" title="Click to view all ${item.burst_images.length} images">
            <div class="timeline-details">
                <h3 style="margin-top: 0; margin-bottom: 5px;">${item.predicted_label}</h3>
                <button class="timeline-breakdown-btn" onclick="toggleBreakdown('${item.filename}')">Model Breakdown</button>
                <div id="models-${item.filename}" style="display: ${expandedBreakdowns.has(item.filename) ? 'block' : 'none'}; font-size: 12px; color: #a8b2d1; margin-bottom: 10px; background-color: #1a1a2e; padding: 6px; border-radius: 4px; border-left: 3px solid #88c0d0;">
                    <div style="margin-bottom: 3px;"><b>ResNet:</b> ${item.resnet_guess || 'N/A'}</div>
                    <div style="margin-bottom: 3px;"><b>CNN:</b> ${item.cnn_guess || 'N/A'}</div>
                    <div style="margin-bottom: 3px;"><b>ViT:</b> ${item.vit_guess || 'N/A'}</div>
                    <div><b>CLIP:</b> ${item.clip_guess || 'N/A'}</div>
                </div>
                <p>Time: ${item.time}</p>
                <p>Confidence: ${parseFloat(item.confidence).toFixed(2)}</p>
                <p style="font-size: 12px; color: #aaa;">Burst size: ${item.burst_images.length} image(s)</p>
                <span class="status-badge ${statusClass}">${statusText}</span>
            </div>
        `;
        feedContainer.appendChild(div);
    });
}

loadFeed();
setInterval(loadFeed, 3000);
