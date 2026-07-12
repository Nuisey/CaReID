const feedContainer = document.getElementById('feed');
let currentEditFilenames = [];

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

window.openEditModal = function(filenames) {
    if (!Array.isArray(filenames)) {
        currentEditFilenames = [filenames];
    } else {
        currentEditFilenames = filenames;
    }
    document.getElementById('edit-car-select').value = '';
    document.getElementById('edit-modal').style.display = 'block';
}

document.getElementById('edit-selected-btn').onclick = function() {
    const checkboxes = document.querySelectorAll('.timeline-checkbox:checked');
    if (checkboxes.length === 0) {
        alert("Please select at least one item to edit.");
        return;
    }
    const filenames = Array.from(checkboxes).map(cb => cb.value);
    openEditModal(filenames);
};

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
            <div style="display: flex; align-items: center; margin-right: 15px;">
                <input type="checkbox" class="timeline-checkbox" value="${item.filename}" style="width: 20px; height: 20px; cursor: pointer;">
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
