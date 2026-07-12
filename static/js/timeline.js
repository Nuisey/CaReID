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
        
        const isChecked = persistedSelectedFilenames.has(item.filename) ? 'checked' : '';
        
        div.innerHTML = `
            <div style="display: flex; align-items: center; margin-right: 15px;">
                <input type="checkbox" class="timeline-checkbox" value="${item.filename}" style="width: 20px; height: 20px; cursor: pointer;" ${isChecked}>
            </div>
            <img src="/images/${item.filename}" alt="Car crop">
            <div class="timeline-details">
                <h3>${item.predicted_label} (ID: ${item.id}) 
                    <button onclick="openEditModal('${item.filename}')" style="margin-left: 10px; font-size: 12px; padding: 4px 8px; cursor:pointer; background-color: #666; color: white; border: none; border-radius: 4px;">✎ Edit</button>
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
