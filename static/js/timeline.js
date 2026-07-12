const feedContainer = document.getElementById('feed');

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
                <h3>${item.predicted_label} (ID: ${item.id})</h3>
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
