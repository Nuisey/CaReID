let houses = [];
let carStatus = {};
let availableLabels = [];

let draggedHouse = null;
let offsetX = 0;
let offsetY = 0;

let currentAssigningCar = null;

const mapContainer = document.getElementById('mapContainer');
const imageWrapper = document.getElementById('imageWrapper');
const customizeBtn = document.getElementById('customizeBtn');
const assignModal = document.getElementById('assignModal');
const carSearch = document.getElementById('carSearch');
const carOptions = document.getElementById('carOptions');
const saveAssignBtn = document.getElementById('saveAssignBtn');
const cancelAssignBtn = document.getElementById('cancelAssignBtn');

async function loadInitialData() {
    const res = await fetch('/api/state');
    const data = await res.json();
    houses = data.houses || [];
    carStatus = data.car_status || {};
    
    const labelRes = await fetch('/api/labels');
    availableLabels = await labelRes.json();
    
    populateLabelDropdown();
    renderHouses();
    
    // Poll for status updates every 2 seconds
    setInterval(pollStatus, 2000);
}

function populateLabelDropdown() {
    carOptions.innerHTML = '';
    availableLabels.forEach(lbl => {
        const opt = document.createElement('option');
        // Setting value to show text + ID so they can search by text, but we can parse ID on save
        opt.value = `${lbl.label} (ID: ${lbl.id})`;
        carOptions.appendChild(opt);
    });
}

function renderHouses() {
    // We only want to remove houses, not the background image
    const existingHouses = imageWrapper.querySelectorAll('.house-box');
    existingHouses.forEach(h => h.remove());

    houses.forEach((house, hIndex) => {
        const hDiv = document.createElement('div');
        hDiv.className = 'house-box';
        hDiv.style.left = house.x_percent + '%';
        hDiv.style.top = house.y_percent + '%';
        
        if (house.width) hDiv.style.width = house.width;
        if (house.height) hDiv.style.height = house.height;
        
        // Resize observer to save dimensions
        let resizeTimeout;
        const ro = new ResizeObserver(() => {
            if (hDiv.style.width || hDiv.style.height) {
                if (house.width !== hDiv.style.width || house.height !== hDiv.style.height) {
                    house.width = hDiv.style.width;
                    house.height = hDiv.style.height;
                    clearTimeout(resizeTimeout);
                    resizeTimeout = setTimeout(saveState, 500);
                }
            }
        });
        ro.observe(hDiv);
        
        // Drag functionality
        hDiv.addEventListener('mousedown', (e) => {
            if(e.target.classList.contains('car-icon')) return;
            
            const rect = hDiv.getBoundingClientRect();
            // Prevent drag if clicking on the bottom-right resize handle
            if (e.clientX > rect.right - 20 && e.clientY > rect.bottom - 20) return;
            
            draggedHouse = { div: hDiv, index: hIndex };
            offsetX = e.clientX - rect.left;
            offsetY = e.clientY - rect.top;
        });

        // Render cars
        house.cars.forEach((car, cIndex) => {
            const cDiv = document.createElement('div');
            cDiv.className = 'car-icon';
            cDiv.textContent = car.assigned_reid_id ? car.assigned_reid_id : '?';
            
            // Check status
            if (car.assigned_reid_id && carStatus[car.assigned_reid_id] === 'home') {
                cDiv.classList.add('home');
            }
            
            cDiv.addEventListener('click', () => {
                currentAssigningCar = { hIndex, cIndex };
                
                // Pre-fill if previously assigned
                if (car.assigned_reid_id) {
                    const lbl = availableLabels.find(l => l.id == car.assigned_reid_id);
                    carSearch.value = lbl ? `${lbl.label} (ID: ${lbl.id})` : car.assigned_reid_id;
                } else {
                    carSearch.value = "";
                }
                assignModal.classList.remove('hidden');
                carSearch.focus();
            });
            hDiv.appendChild(cDiv);
        });
        
        imageWrapper.appendChild(hDiv);
    });
}

customizeBtn.addEventListener('click', () => {
    const num = prompt("Enter number of cars for this new house:");
    const count = parseInt(num);
    if (!isNaN(count) && count > 0) {
        const newHouse = {
            house_id: 'h_' + Date.now(),
            x_percent: 50,
            y_percent: 50,
            cars: Array(count).fill().map((_, i) => ({
                icon_id: 'c_' + Date.now() + '_' + i,
                assigned_reid_id: null
            }))
        };
        houses.push(newHouse);
        saveState();
        renderHouses();
    }
});

// Dragging logic
document.addEventListener('mousemove', (e) => {
    if (!draggedHouse) return;
    
    // We bind relative to the imageWrapper itself, not mapContainer
    const wrapperRect = imageWrapper.getBoundingClientRect();
    let newX = e.clientX - offsetX - wrapperRect.left;
    let newY = e.clientY - offsetY - wrapperRect.top;
    
    // Clamp to boundaries
    newX = Math.max(0, Math.min(newX, wrapperRect.width - draggedHouse.div.offsetWidth));
    newY = Math.max(0, Math.min(newY, wrapperRect.height - draggedHouse.div.offsetHeight));
    
    draggedHouse.div.style.left = newX + 'px';
    draggedHouse.div.style.top = newY + 'px';
});

document.addEventListener('mouseup', (e) => {
    if (draggedHouse) {
        const wrapperRect = imageWrapper.getBoundingClientRect();
        const rect = draggedHouse.div.getBoundingClientRect();
        
        const x_percent = ((rect.left - wrapperRect.left) / wrapperRect.width) * 100;
        const y_percent = ((rect.top - wrapperRect.top) / wrapperRect.height) * 100;
        
        houses[draggedHouse.index].x_percent = x_percent;
        houses[draggedHouse.index].y_percent = y_percent;
        
        saveState();
        draggedHouse = null;
    }
});

cancelAssignBtn.addEventListener('click', () => {
    assignModal.classList.add('hidden');
    currentAssigningCar = null;
});

saveAssignBtn.addEventListener('click', () => {
    if (currentAssigningCar) {
        const val = carSearch.value;
        let selectedId = null;
        
        // Extract ID from the string formatting: "Silver Toyota (ID: 15)"
        const match = val.match(/\(ID: (.*?)\)/);
        if (match) {
            selectedId = match[1];
        } else if (val) {
            // Fallback if they just typed an ID directly
            selectedId = val;
        }
        
        houses[currentAssigningCar.hIndex].cars[currentAssigningCar.cIndex].assigned_reid_id = selectedId;
        saveState();
        renderHouses();
    }
    assignModal.classList.add('hidden');
    currentAssigningCar = null;
});

async function saveState() {
    await fetch('/api/state', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ houses })
    });
}

async function pollStatus() {
    const res = await fetch('/api/state');
    const data = await res.json();
    if(JSON.stringify(carStatus) !== JSON.stringify(data.car_status)) {
        carStatus = data.car_status || {};
        renderHouses();
    }
}

loadInitialData();
