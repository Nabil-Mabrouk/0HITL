// 0-HITL Mission Control Client

let socket = null;
let sessionID = document.getElementById("session-input").value;
let currentUser = null;
let authMode = "login";
let reconnectInterval = null;

const streamContainer = document.getElementById("stream-container");
const sessionInput = document.getElementById("session-input");
const connectBtn = document.getElementById("connect-btn");
const sendBtn = document.getElementById("send-btn");
const chatInput = document.getElementById("chat-input");
const statusBadge = document.getElementById("status-badge");
const statusText = document.getElementById("status-text");
const logoutBtn = document.getElementById("logout-btn");
const killBtn = document.getElementById("kill-btn");

const authOverlay = document.getElementById("auth-overlay");
const authForm = document.getElementById("auth-form");
const authTitle = document.getElementById("auth-title");
const authSubtitle = document.getElementById("auth-subtitle");
const authModeNote = document.getElementById("auth-mode-note");
const authError = document.getElementById("auth-error");
const authSubmit = document.getElementById("auth-submit");
const authDisplayGroup = document.getElementById("auth-display-group");
const authDisplayName = document.getElementById("auth-display-name");
const authUsername = document.getElementById("auth-username");
const authPassword = document.getElementById("auth-password");
const authUserChip = document.getElementById("auth-user-chip");
const authUserLabel = document.getElementById("auth-user-label");
const ownerPanel = document.getElementById("owner-panel");
const ownerUsers = document.getElementById("owner-users");
const ownerEmpty = document.getElementById("owner-empty");
const ownerError = document.getElementById("owner-error");
const ownerSuccess = document.getElementById("owner-success");
const ownerUserForm = document.getElementById("owner-user-form");
const ownerDisplayName = document.getElementById("owner-display-name");
const ownerUsername = document.getElementById("owner-username");
const ownerPassword = document.getElementById("owner-password");
const ownerRole = document.getElementById("owner-role");
const sharePanel = document.getElementById("share-panel");
const shareModeBadge = document.getElementById("share-mode-badge");
const shareNote = document.getElementById("share-note");
const shareError = document.getElementById("share-error");
const shareSuccess = document.getElementById("share-success");
const shareList = document.getElementById("share-list");
const shareEmpty = document.getElementById("share-empty");
const shareForm = document.getElementById("share-form");
const shareUsername = document.getElementById("share-username");
const sharePermission = document.getElementById("share-permission");

let dockerCount = 0;
let tokenCount = 0;
let l3Hits = 0;

function updateStats() {
    document.getElementById("stat-docker").innerText = dockerCount;
    document.getElementById("stat-tokens").innerText = tokenCount;
    document.getElementById("stat-l3").innerText = l3Hits;
}

function setStatus(online) {
    const dot = statusBadge.querySelector("span");
    statusBadge.classList.toggle("bg-emerald-500", online);
    statusBadge.classList.toggle("bg-slate-800", !online);
    dot.classList.toggle("bg-emerald-500", online);
    dot.classList.toggle("bg-slate-500", !online);
    statusText.innerText = online ? "ONLINE" : "DISCONNECTED";
}

function setControlsDisabled(disabled) {
    [sessionInput, connectBtn, sendBtn, chatInput].forEach((element) => {
        element.disabled = disabled;
        element.classList.toggle("opacity-50", disabled);
        element.classList.toggle("cursor-not-allowed", disabled);
    });
}

function setAuthError(message) {
    if (!message) {
        authError.classList.add("hidden");
        authError.innerText = "";
        return;
    }
    authError.classList.remove("hidden");
    authError.innerText = message;
}

function setOwnerMessage(element, message) {
    if (!message) {
        element.classList.add("hidden");
        element.innerText = "";
        return;
    }
    element.classList.remove("hidden");
    element.innerText = message;
}

function clearOwnerMessages() {
    setOwnerMessage(ownerError, "");
    setOwnerMessage(ownerSuccess, "");
}

function setShareMessage(element, message) {
    if (!message) {
        element.classList.add("hidden");
        element.innerText = "";
        return;
    }
    element.classList.remove("hidden");
    element.innerText = message;
}

function clearShareMessages() {
    setShareMessage(shareError, "");
    setShareMessage(shareSuccess, "");
}

function setOwnerPanelVisible(visible) {
    ownerPanel.classList.toggle("hidden", !visible);
}

function setSharePanelVisible(visible) {
    sharePanel.classList.toggle("hidden", !visible);
}

function setAuthMode(mode) {
    authMode = mode;
    setAuthError("");

    if (mode === "bootstrap") {
        authTitle.innerText = "Initialize This Private Instance";
        authSubtitle.innerText = "Create the first owner account for Mission Control.";
        authModeNote.innerText = "This local owner account protects chat, files, WebSocket telemetry and future shared access.";
        authSubmit.innerText = "Create Owner Account";
        authDisplayGroup.classList.remove("hidden");
        authPassword.autocomplete = "new-password";
    } else {
        authTitle.innerText = "Sign In To Mission Control";
        authSubtitle.innerText = "Use a local account created on this instance.";
        authModeNote.innerText = "This private instance keeps access local. Your browser session is secured with an HttpOnly cookie.";
        authSubmit.innerText = "Sign In";
        authDisplayGroup.classList.add("hidden");
        authPassword.autocomplete = "current-password";
    }
}

function showAuthOverlay(show) {
    authOverlay.classList.toggle("hidden", !show);
}

function handleAuthFailure(message = "Your session is no longer valid. Sign in again.") {
    clearAuthenticatedUser();
    setAuthMode("login");
    showAuthOverlay(true);
    setAuthError(message);
}

function clearEmptyState() {
    const emptyState = streamContainer.querySelector(".text-center");
    if (emptyState) {
        emptyState.remove();
    }
}

function parseCurrentSessionReference() {
    const raw = sessionInput.value.trim();
    if (!raw) {
        return {
            raw: "",
            ownerUsername: currentUser ? currentUser.username : null,
            publicSessionId: "",
            isSharedReference: false,
            isOwnReference: true,
        };
    }

    if (raw.includes(":")) {
        const [ownerUsername, ...rest] = raw.split(":");
        const publicSessionId = rest.join(":").trim();
        return {
            raw,
            ownerUsername: ownerUsername.trim().toLowerCase(),
            publicSessionId,
            isSharedReference: true,
            isOwnReference: currentUser ? ownerUsername.trim().toLowerCase() === currentUser.username : false,
        };
    }

    return {
        raw,
        ownerUsername: currentUser ? currentUser.username : null,
        publicSessionId: raw,
        isSharedReference: false,
        isOwnReference: true,
    };
}

function resolveSessionFileLink(link) {
    const currentSession = parseCurrentSessionReference();
    if (!currentSession.isSharedReference || currentSession.isOwnReference) {
        return link;
    }

    const match = link.match(/^\/session-files\/([^/]+)\/(.+)$/);
    if (!match) {
        return link;
    }

    const sessionSegment = match[1];
    const filePath = match[2];
    if (sessionSegment.includes(":")) {
        return link;
    }

    if (sessionSegment !== currentSession.publicSessionId) {
        return link;
    }

    return `/session-files/${currentSession.raw}/${filePath}`;
}

function escapeHtml(text) {
    return String(text)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll("\"", "&quot;")
        .replaceAll("'", "&#39;");
}

function formatLogText(text) {
    const escaped = escapeHtml(text);
    return escaped.replace(
        /(https?:\/\/[^\s<]+|\/session-files\/[^\s<]+)/g,
        (match) => {
            const resolved = resolveSessionFileLink(match);
            return `<a href="${resolved}" target="_blank" rel="noopener noreferrer" class="text-blue-400 underline">${resolved}</a>`;
        }
    );
}

function setLogContent(contentDiv, text) {
    const rawText = String(text);
    contentDiv.dataset.rawText = rawText;
    contentDiv.innerHTML = formatLogText(rawText);
}

function addLog(source, text, type) {
    clearEmptyState();

    const div = document.createElement("div");
    let classes = "p-4 rounded-xl border border-slate-800 transition-all ";
    let icon = "activity";

    if (type === "thought") {
        classes += "thought-bubble";
        icon = "brain";
    } else if (type === "tool") {
        classes += "tool-bubble";
        icon = "box";
    } else if (type === "security") {
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
    setLogContent(div.querySelector(".text-sm"), text);
    streamContainer.appendChild(div);
    streamContainer.scrollTop = streamContainer.scrollHeight;
    lucide.createIcons();
}

function appendLastLog(text) {
    const lastDiv = streamContainer.lastElementChild;
    if (lastDiv && lastDiv.querySelector(".text-sm")) {
        const contentDiv = lastDiv.querySelector(".text-sm");
        const previousText = contentDiv.dataset.rawText || contentDiv.innerText;
        const baseText = previousText === "Thinking..." ? "" : previousText;
        setLogContent(contentDiv, `${baseText}${text}`);
        streamContainer.scrollTop = streamContainer.scrollHeight;
    }
}

async function fetchJson(url, options = {}) {
    const response = await fetch(url, options);
    let payload = {};

    try {
        payload = await response.json();
    } catch (error) {
        payload = {};
    }

    if (!response.ok) {
        throw new Error(payload.detail || `${response.status} ${response.statusText}`);
    }

    return payload;
}

function disconnectSocket({ silent = false } = {}) {
    if (reconnectInterval) {
        clearInterval(reconnectInterval);
        reconnectInterval = null;
    }

    if (socket) {
        const activeSocket = socket;
        socket = null;
        activeSocket.onclose = null;
        activeSocket.close();
    }

    setStatus(false);
    if (!silent) {
        addLog("System", "Mission Control disconnected.", "security");
    }
}

function formatOwnerCreatedAt(value) {
    if (!value) {
        return "";
    }

    try {
        return new Date(value).toLocaleDateString();
    } catch (error) {
        return "";
    }
}

function setShareMode(mode) {
    shareModeBadge.innerText = mode;
}

function renderSessionPermissions(permissions) {
    shareList.innerHTML = "";
    shareEmpty.classList.toggle("hidden", permissions.length !== 0);

    permissions.forEach((share) => {
        const row = document.createElement("div");
        row.className = "rounded-xl border border-slate-700/60 bg-slate-800/40 px-3 py-2";

        const removeButton = document.createElement("button");
        removeButton.type = "button";
        removeButton.className = "text-[10px] uppercase tracking-widest text-red-300 hover:text-red-200";
        removeButton.innerText = "Revoke";
        removeButton.onclick = () => revokeSessionPermission(share.user.username);

        row.innerHTML = `
            <div class="flex items-center justify-between gap-3">
                <div class="min-w-0">
                    <div class="text-sm font-medium text-slate-100 truncate">${escapeHtml(share.user.display_name)}</div>
                    <div class="text-[11px] text-slate-400 mono truncate">${escapeHtml(share.user.username)}</div>
                </div>
                <span class="text-[10px] uppercase tracking-widest rounded-full px-2 py-1 border border-slate-600 text-slate-300">${escapeHtml(share.permission)}</span>
            </div>
            <div class="text-[11px] text-slate-500 mt-2 flex items-center justify-between gap-2">
                <span>${formatOwnerCreatedAt(share.updated_at || share.created_at)}</span>
            </div>
        `;
        row.querySelector(".text-slate-500").appendChild(removeButton);
        shareList.appendChild(row);
    });
}

function renderOwnerUsers(users) {
    ownerUsers.innerHTML = "";
    ownerEmpty.classList.toggle("hidden", users.length !== 0);

    users.forEach((user) => {
        const row = document.createElement("div");
        row.className = "rounded-xl border border-slate-700/60 bg-slate-800/40 px-3 py-2";
        row.innerHTML = `
            <div class="flex items-center justify-between gap-3">
                <div class="min-w-0">
                    <div class="text-sm font-medium text-slate-100 truncate">${escapeHtml(user.display_name)}</div>
                    <div class="text-[11px] text-slate-400 mono truncate">${escapeHtml(user.username)}</div>
                </div>
                <span class="text-[10px] uppercase tracking-widest rounded-full px-2 py-1 border border-slate-600 text-slate-300">${escapeHtml(user.role)}</span>
            </div>
            <div class="text-[11px] text-slate-500 mt-2">${formatOwnerCreatedAt(user.created_at)}</div>
        `;
        ownerUsers.appendChild(row);
    });
}

async function loadOwnerUsers() {
    if (!currentUser || currentUser.role !== "owner") {
        setOwnerPanelVisible(false);
        ownerUsers.innerHTML = "";
        return;
    }

    setOwnerPanelVisible(true);
    clearOwnerMessages();

    try {
        const payload = await fetchJson("/auth/users");
        renderOwnerUsers(payload.users || []);
    } catch (error) {
        if (String(error.message).includes("Authentication required")) {
            handleAuthFailure();
            return;
        }
        setOwnerMessage(ownerError, error.message || "Unable to load local accounts.");
    }
}

async function loadSessionPermissions() {
    if (!currentUser) {
        setSharePanelVisible(false);
        return;
    }

    const sessionRef = parseCurrentSessionReference();
    if (!sessionRef.raw) {
        setSharePanelVisible(false);
        return;
    }

    setSharePanelVisible(true);
    clearShareMessages();

    if (sessionRef.isSharedReference && !sessionRef.isOwnReference) {
        setShareMode("Shared View");
        shareNote.innerText = `You are viewing ${sessionRef.raw}. Access is managed by ${sessionRef.ownerUsername}.`;
        shareForm.classList.add("hidden");
        shareList.innerHTML = "";
        shareEmpty.classList.add("hidden");
        return;
    }

    setShareMode("Owner");
    shareNote.innerText = "Grant read or operator access to this session explicitly.";
    shareForm.classList.remove("hidden");

    try {
        const payload = await fetchJson(
            `/sessions/${encodeURIComponent(sessionRef.raw)}/permissions`
        );
        renderSessionPermissions(payload.permissions || []);
    } catch (error) {
        if (String(error.message).includes("Authentication required")) {
            handleAuthFailure();
            return;
        }
        setShareMessage(shareError, error.message || "Unable to load session permissions.");
    }
}

async function revokeSessionPermission(username) {
    clearShareMessages();

    const sessionRef = parseCurrentSessionReference();
    if (!currentUser || !sessionRef.raw) {
        return;
    }

    try {
        await fetchJson(
            `/sessions/${encodeURIComponent(sessionRef.raw)}/permissions/${encodeURIComponent(username)}`,
            { method: "DELETE" }
        );
        await loadSessionPermissions();
        setShareMessage(shareSuccess, `Access revoked for ${username}.`);
    } catch (error) {
        if (String(error.message).includes("Authentication required")) {
            handleAuthFailure();
            return;
        }
        setShareMessage(shareError, error.message || "Unable to revoke session access.");
    }
}

async function submitSessionPermission(event) {
    event.preventDefault();
    clearShareMessages();

    const sessionRef = parseCurrentSessionReference();
    if (!currentUser || !sessionRef.raw) {
        return;
    }

    const payload = {
        username: shareUsername.value.trim(),
        permission: sharePermission.value,
    };

    try {
        const result = await fetchJson(`/sessions/${encodeURIComponent(sessionRef.raw)}/permissions`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        shareForm.reset();
        sharePermission.value = "viewer";
        await loadSessionPermissions();
        setShareMessage(
            shareSuccess,
            `Access granted to ${result.share.user.display_name} (${result.share.permission}).`
        );
    } catch (error) {
        if (String(error.message).includes("Authentication required")) {
            handleAuthFailure();
            return;
        }
        setShareMessage(shareError, error.message || "Unable to grant session access.");
    }
}

function setAuthenticatedUser(user) {
    currentUser = user;
    authUserChip.classList.remove("hidden");
    authUserChip.classList.add("flex");
    authUserLabel.innerText = `${user.display_name} (${user.role})`;
    logoutBtn.classList.remove("hidden");
    showAuthOverlay(false);
    setControlsDisabled(false);
    loadSessionPermissions();
    loadOwnerUsers();
}

function clearAuthenticatedUser() {
    currentUser = null;
    authUserChip.classList.add("hidden");
    authUserChip.classList.remove("flex");
    logoutBtn.classList.add("hidden");
    setControlsDisabled(true);
    setSharePanelVisible(false);
    shareList.innerHTML = "";
    shareForm.reset();
    sharePermission.value = "viewer";
    clearShareMessages();
    setOwnerPanelVisible(false);
    ownerUsers.innerHTML = "";
    ownerUserForm.reset();
    ownerRole.value = "member";
    clearOwnerMessages();
    disconnectSocket({ silent: true });
}

async function initializeAuth() {
    setControlsDisabled(true);
    setAuthError("");

    try {
        const setupStatus = await fetchJson("/auth/setup-status");

        try {
            const me = await fetchJson("/auth/me");
            setAuthenticatedUser(me.user);
            addLog("System", `Authenticated as ${me.user.display_name}.`, "info");
            return;
        } catch (error) {
            setAuthMode(setupStatus.bootstrap_required ? "bootstrap" : "login");
            showAuthOverlay(true);
        }
    } catch (error) {
        setAuthMode("login");
        showAuthOverlay(true);
        setAuthError("Unable to reach the authentication service.");
    }
}

function connect() {
    if (!currentUser) {
        showAuthOverlay(true);
        setAuthError("Sign in before connecting to a session.");
        return;
    }

    sessionID = sessionInput.value;
    loadSessionPermissions();
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/mission-control/${sessionID}`;

    if (socket) {
        disconnectSocket({ silent: true });
    }

    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
        if (reconnectInterval) {
            clearInterval(reconnectInterval);
            reconnectInterval = null;
        }
        setStatus(true);
        addLog("System", `Connected to session ${sessionID}`, "info");
    };

    socket.onclose = (event) => {
        setStatus(false);

        if (event.code === 1008) {
            handleAuthFailure();
            return;
        }

        if (currentUser && !reconnectInterval) {
            addLog("System", "Connection lost. Retrying in 5s...", "security");
            reconnectInterval = setInterval(connect, 5000);
        }
    };

    socket.onmessage = (event) => {
        const payload = JSON.parse(event.data);
        handleEvent(payload.type, payload.data);
    };
}

function handleEvent(type, data) {
    switch (type) {
        case "THOUGHT_START":
            addLog("Orchestrator", "Thinking...", "thought");
            break;
        case "THOUGHT":
            appendLastLog(data.content);
            break;
        case "TOOL_START":
            addLog("Docker", `Executing: ${data.name}`, "tool");
            break;
        case "RUNTIME_STATUS":
            dockerCount = data.active_runtimes || 0;
            tokenCount = data.session_execs || 0;
            updateStats();
            break;
        case "MEMORY_HIT":
            l3Hits += data.count || 1;
            updateStats();
            break;
        case "TOOL_SUCCESS":
            appendLastLog(`\nResult: ${data.result}`);
            break;
        case "SECURITY_WARNING":
            addLog("SuperEgo", data.msg || data.result || "Security warning.", "security");
            break;
        case "SECURITY_ALERT":
            addLog("SuperEgo", data.msg || data.result || "Security alert.", "security");
            break;
        case "EMERGENCY_STOP":
            addLog("System", data.message || "Emergency stop executed.", "security");
            break;
        case "TOOL_ERROR":
            addLog("System", `Error: ${data.error || data.result || "Unknown tool error."}`, "security");
            break;
        case "ENGINE_CRITICAL":
            addLog("System", `Critical: ${data.error || "Unknown engine error."}`, "security");
            break;
    }
}

async function emergencyStop() {
    if (!currentUser) {
        showAuthOverlay(true);
        setAuthError("Sign in before stopping a session.");
        return;
    }

    sessionID = sessionInput.value.trim();
    if (!sessionID) {
        addLog("System", "Select a session before triggering emergency stop.", "security");
        return;
    }

    try {
        const payload = await fetchJson(`/sessions/${encodeURIComponent(sessionID)}/emergency-stop`, {
            method: "POST",
        });
        addLog(
            "System",
            `Emergency stop requested for session ${payload.session_id}.`,
            "security"
        );
    } catch (error) {
        if (String(error.message).includes("Authentication required")) {
            handleAuthFailure();
            return;
        }
        addLog("System", error.message || "Emergency stop failed.", "security");
    }
}

async function submitOwnerUserForm(event) {
    event.preventDefault();
    clearOwnerMessages();

    if (!currentUser || currentUser.role !== "owner") {
        return;
    }

    const payload = {
        username: ownerUsername.value.trim(),
        password: ownerPassword.value,
        display_name: ownerDisplayName.value.trim(),
        role: ownerRole.value,
    };

    try {
        const result = await fetchJson("/auth/users", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        ownerUserForm.reset();
        ownerRole.value = "member";
        await loadOwnerUsers();
        setOwnerMessage(
            ownerSuccess,
            `Account created for ${result.user.display_name} (${result.user.role}).`
        );
    } catch (error) {
        if (String(error.message).includes("Authentication required")) {
            handleAuthFailure();
            return;
        }
        setOwnerMessage(ownerError, error.message || "Unable to create local account.");
    }
}

async function sendMission() {
    if (!currentUser) {
        showAuthOverlay(true);
        setAuthError("Sign in before sending a mission.");
        return;
    }

    const text = chatInput.value.trim();
    if (!text) {
        return;
    }

    chatInput.value = "";
    addLog("User", text, "info");

    try {
        const payload = await fetchJson("/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                user_input: text,
                session_id: sessionID,
            }),
        });

        sessionID = payload.session_id || sessionID;
        sessionInput.value = sessionID;
        loadSessionPermissions();
    } catch (error) {
        if (String(error.message).includes("Authentication required")) {
            handleAuthFailure();
        }
        addLog("System", error.message || "Failed to send mission. Check server status.", "security");
    }
}

async function submitAuthForm(event) {
    event.preventDefault();
    setAuthError("");

    const payload = {
        username: authUsername.value.trim(),
        password: authPassword.value,
    };

    if (authMode === "bootstrap") {
        payload.display_name = authDisplayName.value.trim();
    }

    try {
        const endpoint = authMode === "bootstrap" ? "/auth/bootstrap" : "/auth/login";
        const result = await fetchJson(endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        authPassword.value = "";
        setAuthenticatedUser(result.user);
        addLog("System", `${result.user.display_name} authenticated successfully.`, "info");
        connect();
    } catch (error) {
        setAuthError(error.message || "Authentication failed.");
    }
}

async function logout() {
    try {
        await fetchJson("/auth/logout", { method: "POST" });
    } catch (error) {
        addLog("System", "Sign-out request failed, clearing local UI state anyway.", "security");
    }

    clearAuthenticatedUser();
    setAuthMode("login");
    showAuthOverlay(true);
    setAuthError("");
}

connectBtn.onclick = connect;
sendBtn.onclick = sendMission;
logoutBtn.onclick = logout;
killBtn.onclick = emergencyStop;
authForm.onsubmit = submitAuthForm;
shareForm.onsubmit = submitSessionPermission;
ownerUserForm.onsubmit = submitOwnerUserForm;
sessionInput.onchange = loadSessionPermissions;
chatInput.onkeypress = (event) => {
    if (event.key === "Enter") {
        sendMission();
    }
};

lucide.createIcons();
setStatus(false);
initializeAuth();
