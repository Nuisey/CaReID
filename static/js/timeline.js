const feedContainer = document.getElementById('feed');
let currentEditFilename = null;

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
    currentEditFilename = filename;
    document.getElementById('edit-car-select').value = '';
    document.getElementById('edit-modal').style.display = 'block';
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
            filename: currentEditFilename,
            new_id: new_id,
            new_label: new_label
        })
    });
    
    document.getElementById('edit-modal').style.display = 'none';
    loadFeed();
}

async function loadFeed() {
    const res = await fetch('/api/timeline');
    const data = await res.json();
    
    feedContainer.innerHTML = '';
    data.forEach(item => {
        const div = document.createElement('div');
        div.className = 'timeline-item';
        
        const statusClass = item.direction === 'arriving' ? 'status-arriving' : 
                            (item.direction === 'leaving' ? 'status-leaving' : 'status-unknown');
        const statusText = item.direction.toUpperCase();
        
        div.innerHTML = `
            <img src="/images/${item.filename}" alt="Car crop">
            <div class="timeline-details">
                <h3>${item.predicted_label} (ID: ${item.id}) 
                    <button onclick="openEditModal('${item.filename}')" style="margin-left: 10px; font-size: 12px; padding: 2px 5px; cursor:pointer;">✎ Edit</button>
                </h3>
                <p>Time: ${item.time}</p>
                <p>Confidence: ${parseFloat(item.confidence).toFixed(2)}</p>
                <span class="status-badge ${statusClass}">${statusText}</span>
            </div>
        `;
        feedContainer.appendChild(div);
    });
}

loadFeed();
setInterval(loadFeed, 3000);
