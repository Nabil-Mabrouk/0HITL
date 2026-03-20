// 0-HITL Mission Control Client

let socket = null;
let sessionID = document.getElementById('session-input').value;
const streamContainer = document.getElementById('stream-container');
const connectBtn = document.getElementById('connect-btn');
const sendBtn = document.getElementById('send-btn');
const chatInput = document.getElementById('chat-input');
const statusBadge = document.getElementById('status-badge');
const statusText = document.getElementById('status-text');

let reconnectInterval = null;

// Stats counters
let dockerCount = 0;
let tokenCount = 0;
let l3Hits = 0;

function updateStats() {
    document.getElementById('stat-docker').innerText = dockerCount;
    document.getElementById('stat-tokens').innerText = tokenCount;
    document.getElementById('stat-l3').innerText = l3Hits;
}

function connect() {
    sessionID = document.getElementById('session-input').value;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/mission-control/${sessionID}`;

    if (socket) socket.close();

    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
        clearInterval(reconnectInterval);
        reconnectInterval = null;
        statusBadge.classList.replace('bg-slate-500', 'bg-emerald-500');
        statusBadge.querySelector('span').classList.replace('bg-slate-500', 'bg-emerald-500');
        statusText.innerText = 'ONLINE';
        addLog('System', `Connected to session ${sessionID}`, 'info');
    };

    socket.onclose = () => {
        statusBadge.classList.replace('bg-emerald-500', 'bg-slate-500');
        statusBadge.querySelector('span').classList.replace('bg-emerald-500', 'bg-slate-500');
        statusText.innerText = 'DISCONNECTED';
        
        // Auto-reconnect
        if (!reconnectInterval) {
            addLog('System', 'Connection lost. Retrying in 5s...', 'security');
            reconnectInterval = setInterval(connect, 5000);
        }
    };

    socket.onmessage = (event) => {
        const payload = JSON.parse(event.data);
        handleEvent(payload.type, payload.data);
    };
}

function handleEvent(type, data) {
    switch(type) {
        case 'THOUGHT_START':
            addLog('Orchestrator', 'Thinking...', 'thought');
            break;
        case 'THOUGHT':
            appendLastLog(data.content);
            break;
        case 'TOOL_START':
            addLog('Docker', `Executing: ${data.name}`, 'tool');
            break;
        case 'RUNTIME_STATUS':
            dockerCount = data.active_runtimes || 0;
            tokenCount = data.session_execs || 0;
            updateStats();
            break;
        case 'MEMORY_HIT':
            l3Hits += data.count || 1;
            updateStats();
            break;
        case 'TOOL_SUCCESS':
            appendLastLog(`\nResult: ${data.result}`);
            break;
        case 'SECURITY_WARNING':
            addLog('SuperEgo', data.msg || data.result || 'Security warning.', 'security');
            break;
        case 'SECURITY_ALERT':
            addLog('SuperEgo', data.msg || data.result || 'Security alert.', 'security');
            break;
        case 'TOOL_ERROR':
            addLog('System', `Error: ${data.error || data.result || 'Unknown tool error.'}`, 'security');
            break;
        case 'ENGINE_CRITICAL':
            addLog('System', `Critical: ${data.error || 'Unknown engine error.'}`, 'security');
            break;
    }
}

function escapeHtml(text) {
    return String(text)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function formatLogText(text) {
    const escaped = escapeHtml(text);
    return escaped.replace(/(https?:\/\/[^\s<]+|\/session-files\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer" class="text-blue-400 underline">$1</a>');
}

function setLogContent(contentDiv, text) {
    const rawText = String(text);
    contentDiv.dataset.rawText = rawText;
    contentDiv.innerHTML = formatLogText(rawText);
}

function addLog(source, text, type) {
    const div = document.createElement('div');
    let classes = "p-4 rounded-xl border border-slate-800 transition-all ";
    let icon = "activity";
    
    if (type === 'thought') {
        classes += "thought-bubble";
        icon = "brain";
    } else if (type === 'tool') {
        classes += "tool-bubble";
        icon = "box";
    } else if (type === 'security') {
        classes += "security-alert";
        icon = "shield-alert";
    }

    div.className = classes;
    div.innerHTML = `
        <div class="flex items-center gap-2 mb-2">
            <i data-lucide="${icon}" class="w-4 h-4 opacity-50"></i>
            <span class="text-[10px] font-bold uppercase tracking-widest text-slate-500">${source}</span>
        </div>
        <div class="text-sm mono whitespace-pre-wrap leading-relaxed"></div>
    `;
    setLogContent(div.querySelector('.text-sm'), text);
    streamContainer.appendChild(div);
    streamContainer.scrollTop = streamContainer.scrollHeight;
    lucide.createIcons();
}

function appendLastLog(text) {
    const lastDiv = streamContainer.lastElementChild;
    if (lastDiv && lastDiv.querySelector('.text-sm')) {
        const contentDiv = lastDiv.querySelector('.text-sm');
        const previousText = contentDiv.dataset.rawText || contentDiv.innerText;
        const baseText = previousText === 'Thinking...' ? '' : previousText;
        setLogContent(contentDiv, `${baseText}${text}`);
        streamContainer.scrollTop = streamContainer.scrollHeight;
    }
}

async function sendMission() {
    const text = chatInput.value;
    if (!text) return;
    
    chatInput.value = '';
    addLog('User', text, 'info');

    try {
        await fetch('/chat', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                user_input: text,
                session_id: sessionID
            })
        });
    } catch(e) {
        addLog('System', 'Failed to send mission. Check server status.', 'security');
    }
}

connectBtn.onclick = connect;
sendBtn.onclick = sendMission;
chatInput.onkeypress = (e) => { if(e.key === 'Enter') sendMission(); };

// Init Lucide
lucide.createIcons();
