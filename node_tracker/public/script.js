const socket = io({
    reconnection: true,
    reconnectionAttempts: Infinity,
    reconnectionDelay: 1000
});

let currentUser = null;

// DOM Elements
const authSection = document.getElementById('auth-section');
const dashboardSection = document.getElementById('dashboard-section');
const addProjectCard = document.getElementById('add-project-card');
const authForm = document.getElementById('auth-form');
const projectForm = document.getElementById('project-form');
const projectsList = document.getElementById('projects-list');
const roleDisplay = document.getElementById('user-role-display');
const logoutBtn = document.getElementById('logout-btn');
const connectionStatus = document.getElementById('connection-status');
const toastContainer = document.getElementById('toast-container');

// Socket Connection Status
socket.on('connect', () => {
    connectionStatus.textContent = 'Online';
    connectionStatus.className = 'online';
    if(currentUser) {
        // Re-join rooms if reconnected
        if(currentUser.role === 'admin') {
            socket.emit('join_admin');
        } else {
            socket.emit('join_dashboard', currentUser.id);
        }
    }
});

socket.on('disconnect', () => {
    connectionStatus.textContent = 'Offline';
    connectionStatus.className = 'offline';
});

// Toast Notification Function
function showToast(message) {
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.textContent = message;
    toastContainer.appendChild(toast);
    setTimeout(() => {
        toast.remove();
    }, 4000);
}

// Authentication
authForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;

    try {
        const res = await fetch('/api/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        const data = await res.json();
        
        if(data.success) {
            currentUser = data.user;
            authSection.classList.add('hidden');
            dashboardSection.classList.remove('hidden');
            roleDisplay.textContent = currentUser.role.toUpperCase();
            
            if(currentUser.role === 'user') {
                addProjectCard.classList.remove('hidden');
                socket.emit('join_dashboard', currentUser.id);
            } else {
                addProjectCard.classList.add('hidden');
                socket.emit('join_admin');
            }
            
            loadProjects();
        } else {
            alert(data.message);
        }
    } catch (err) {
        console.error(err);
        alert('Login failed');
    }
});

logoutBtn.addEventListener('click', () => {
    currentUser = null;
    dashboardSection.classList.add('hidden');
    authSection.classList.remove('hidden');
});

// Load Projects
async function loadProjects() {
    try {
        const res = await fetch(`/api/projects?userId=${currentUser.id}&role=${currentUser.role}`);
        const projects = await res.json();
        renderProjects(projects);
    } catch (err) {
        console.error(err);
    }
}

// Create Project
projectForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = document.getElementById('proj-name').value;
    const description = document.getElementById('proj-desc').value;

    try {
        const res = await fetch('/api/projects', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, description, ownerId: currentUser.id })
        });
        const project = await res.json();
        
        // Let server know so it can notify admins
        socket.emit('project_created', project);
        
        loadProjects(); // Reload to see the new project
        projectForm.reset();
        showToast('Project submitted successfully!');
    } catch (err) {
        console.error(err);
    }
});

// Render Projects
function renderProjects(projects) {
    projectsList.innerHTML = '';
    projects.forEach(project => {
        const card = document.createElement('div');
        card.className = `project-card ${project.status.toLowerCase()}`;
        card.id = `project-${project._id}`;
        
        let adminControls = '';
        if(currentUser.role === 'admin') {
            adminControls = `
                <div style="margin-top: 15px;">
                    <select onchange="updateProjectStatus('${project._id}', this.value)">
                        <option value="Pending" ${project.status === 'Pending' ? 'selected' : ''}>Pending</option>
                        <option value="Approved" ${project.status === 'Approved' ? 'selected' : ''}>Approved</option>
                        <option value="Rejected" ${project.status === 'Rejected' ? 'selected' : ''}>Rejected</option>
                    </select>
                </div>
            `;
        }

        const date = new Date(project.updatedAt).toLocaleString();

        card.innerHTML = `
            <h4>${project.name}</h4>
            <p style="font-size: 13px; color: #7f8c8d; margin-bottom: 10px;">By: ${project.owner ? project.owner.username : 'Unknown'} | Updated: <span id="time-${project._id}">${date}</span></p>
            <p>${project.description}</p>
            <span class="status-badge status-${project.status}" id="status-${project._id}">${project.status}</span>
            ${adminControls}
        `;
        projectsList.appendChild(card);
    });
}

// Update Status from Admin
window.updateProjectStatus = function(projectId, status) {
    socket.emit('update_project_status', { projectId, status });
}

// Socket Events
socket.on('new_project', (project) => {
    showToast(`New project submitted: ${project.name}`);
    loadProjects();
});

socket.on('project_updated', (data) => {
    // Show toast
    if(data.message) {
        showToast(data.message);
    } else if (currentUser.role === 'admin' && data.project) {
        showToast(`Project updated: ${data.project.name} is now ${data.status}`);
    }

    // Update UI directly instead of full reload for best performance
    const cardContainer = document.getElementById(`project-${data.projectId}`);
    if(cardContainer) {
        cardContainer.className = `project-card ${data.status.toLowerCase()}`;
        const statusBadge = document.getElementById(`status-${data.projectId}`);
        if(statusBadge) {
            statusBadge.className = `status-badge status-${data.status}`;
            statusBadge.textContent = data.status;
        }
        const timeSpan = document.getElementById(`time-${data.projectId}`);
        if(timeSpan) {
            timeSpan.textContent = new Date().toLocaleString();
        }
    } else {
        loadProjects(); // Fallback if card isn't found
    }
});
