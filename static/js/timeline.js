const feedContainer = document.getElementById('feed');
let currentEditFilenames = [];
let persistedSelectedFilenames = new Set();

feedContainer.addEventListener('change', function(e) {
    if (e.target && e.target.classList.contains('timeline-checkbox')) {
        if (e.target.checked) {
            persistedSelectedFilenames.add(e.target.value);
        } else {
            persistedSelectedFilenames.delete(e.target.value);
        }
    }
});

async function loadLabels() {
    const res = await fetch('/api/labels');
    const data = await res.json();
    const list = document.getElementById('edit-car-list');
    list.innerHTML = '';
    data.forEach(item => {
        const option = document.createElement('option');
        option.value = `${item.label} (ID: ${item.id})`;
        list.appendChild(option);
    });
}
loadLabels();

window.openEditModal = function(filename) {
    if (persistedSelectedFilenames.has(filename)) {
        currentEditFilenames = Array.from(persistedSelectedFilenames);
    } else {
        currentEditFilenames = [filename];
    }
    document.getElementById('edit-car-select').value = '';
    document.getElementById('edit-modal').style.display = 'block';
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

document.getElementById('edit-save-btn').onclick = async function() {
    const selection = document.getElementById('edit-car-select').value;
    if (!selection) return;
    
    const match = selection.match(/(.+) \(ID: (.+)\)/);
    if (!match) {
        alert("Invalid selection format");
        return;
    }
    const new_label = match[1];
    const new_id = match[2];
    
    await fetch('/api/update_label', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            filenames: currentEditFilenames,
            new_id: new_id,
            new_label: new_label
        })
    });
    
    currentEditFilenames.forEach(f => persistedSelectedFilenames.delete(f));
    document.getElementById('edit-modal').style.display = 'none';
    loadFeed();
}

let loggedEventIds = new Set();
let isFirstLoad = true;

async function loadFeed() {
    const res = await fetch('/api/timeline');
    const data = await res.json();
    
    // Sort chronologically for logs (oldest first)
    const reversedData = [...data].reverse();
    
    reversedData.forEach(item => {
        const eventId = `${item.id}-${item.direction}`;
        if (!loggedEventIds.has(eventId)) {
            loggedEventIds.add(eventId);
            
            // Only add visual text logs for newly arriving items (skip initial flood unless it's just a few)
            if (!isFirstLoad || data.length < 10) {
                const logContainer = document.getElementById('system-logs');
                const logDiv = document.createElement('div');
                logDiv.style.marginBottom = '5px';
                logDiv.innerHTML = `<span style="color: #aaa;">[${item.time}]</span> <span style="color: #0ff;">[Camera 0]</span> Detected <b>${item.predicted_label}</b> (ID: ${item.id}) - Direction: <span style="color: #ff0;">${item.direction.toUpperCase()}</span> - Conf: ${parseFloat(item.confidence).toFixed(2)}`;
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
        
        const isChecked = persistedSelectedFilenames.has(item.filename) ? 'checked' : '';
        
        const burstStr = encodeURIComponent(JSON.stringify(item.burst_images));
        const labelStr = `${item.predicted_label} (ID: ${item.id})`;
        
        div.innerHTML = `
            <div style="display: flex; align-items: center; margin-right: 15px;">
                <input type="checkbox" class="timeline-checkbox" value="${item.filename}" style="width: 20px; height: 20px; cursor: pointer;" ${isChecked}>
            </div>
            <img src="/images/${item.filename}" alt="Car crop" style="cursor: pointer;" onclick="openInspectModal('${burstStr}', '${labelStr}')" title="Click to view all ${item.burst_images.length} images">
            <div class="timeline-details">
                <h3 style="margin-top: 0;">${item.predicted_label} (ID: ${item.id})</h3>
                <p>Time: ${item.time}</p>
                <p>Confidence: ${parseFloat(item.confidence).toFixed(2)}</p>
                <p style="font-size: 12px; color: #aaa;">Burst size: ${item.burst_images.length} image(s)</p>
                <span class="status-badge ${statusClass}">${statusText}</span>
            </div>
            <button class="timeline-edit-btn" onclick="openEditModal('${item.filename}')">✎ Edit</button>
        `;
        feedContainer.appendChild(div);
    });
}

loadFeed();
setInterval(loadFeed, 3000);
