// ✅ RUN AFTER DOM LOAD (more reliable than window.onload)

// Server auto-shutdown heartbeat: the backend process (console window in
// source mode, or the packaged .exe) keeps running behind this page until
// something stops it. To avoid leaving that process orphaned in the
// background, ping /api/heartbeat while this page is open, and send a
// best-effort close signal when it unloads (tab/window closed or
// navigated away) so the backend's watchdog can shut itself down promptly
// instead of waiting for the heartbeat to simply time out.
(function() {
    function sendHeartbeat() {
        fetch('/api/heartbeat', { method: 'POST', keepalive: true }).catch(() => {});
    }
    sendHeartbeat();
    setInterval(sendHeartbeat, 5000);

    // Browsers throttle setInterval heavily in background/inactive tabs, so
    // catch up immediately the moment this tab becomes active again rather
    // than waiting for the next (possibly delayed) interval tick.
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
            sendHeartbeat();
        }
    });

    function signalClosing() {
        try {
            navigator.sendBeacon('/api/dashboard-closed');
        } catch (e) {}
    }
    window.addEventListener('pagehide', signalClosing);
    window.addEventListener('beforeunload', signalClosing);
})();

// Style injection for custom dialog modals
(function() {
    const css = `
    @keyframes customModalFadeIn {
        from { opacity: 0; }
        to { opacity: 1; }
    }
    @keyframes customModalScaleIn {
        from { transform: scale(0.95) translateY(10px); opacity: 0; }
        to { transform: scale(1) translateY(0); opacity: 1; }
    }

    .custom-modal-overlay {
        position: fixed;
        top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(15, 23, 42, 0.45);
        backdrop-filter: blur(8px);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 10000;
        animation: customModalFadeIn 0.2s ease forwards;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }

    .custom-modal-card {
        background: #ffffff;
        border-radius: 16px;
        box-shadow: 0 25px 50px -12px rgba(15, 23, 42, 0.15);
        border: 1px solid rgba(226, 232, 240, 0.8);
        width: 90%;
        max-width: 440px;
        padding: 1.75rem;
        animation: customModalScaleIn 0.25s cubic-bezier(0.16, 1, 0.3, 1) forwards;
    }

    .custom-modal-header {
        display: flex;
        align-items: center;
        gap: 0.75rem;
        margin-bottom: 1rem;
    }

    .custom-modal-icon {
        font-size: 1.25rem;
        display: flex;
        align-items: center;
        justify-content: center;
        width: 38px;
        height: 38px;
        border-radius: 50%;
        flex-shrink: 0;
    }

    .custom-modal-icon.danger {
        background-color: #fef2f2;
        color: #ef4444;
    }

    .custom-modal-icon.success {
        background-color: #f0fdf4;
        color: #10b981;
    }

    .custom-modal-icon.info {
        background-color: #eff6ff;
        color: #3b82f6;
    }

    .custom-modal-title {
        font-size: 1.1rem;
        font-weight: 700;
        color: #0f172a;
        margin: 0;
    }

    .custom-modal-body {
        font-size: 0.9rem;
        color: #475569;
        line-height: 1.5;
        margin-bottom: 1.5rem;
    }

    .custom-modal-actions {
        display: flex;
        justify-content: flex-end;
        gap: 0.75rem;
    }

    .custom-modal-btn {
        padding: 0.6rem 1.25rem;
        font-size: 0.85rem;
        font-weight: 600;
        border-radius: 8px;
        cursor: pointer;
        border: none;
        transition: all 0.15s ease;
    }

    .custom-modal-btn.cancel {
        background-color: #f1f5f9;
        color: #475569;
    }

    .custom-modal-btn.cancel:hover {
        background-color: #e2e8f0;
        color: #1e293b;
    }

    .custom-modal-btn.confirm-danger {
        background-color: #ef4444;
        color: #ffffff;
    }

    .custom-modal-btn.confirm-danger:hover {
        background-color: #dc2626;
    }

    .custom-modal-btn.confirm-primary {
        background-color: #10b981;
        color: #ffffff;
    }

    .custom-modal-btn.confirm-primary:hover {
        background-color: #059669;
    }

    @keyframes globalToastSlideIn {
        from { transform: translateX(20px); opacity: 0; }
        to { transform: translateX(0); opacity: 1; }
    }

    #global-toast-container {
        position: fixed;
        top: 1.25rem;
        right: 1.25rem;
        z-index: 10500;
        display: flex;
        flex-direction: column;
        gap: 0.6rem;
        pointer-events: none;
    }

    .global-toast {
        pointer-events: auto;
        display: flex;
        align-items: center;
        gap: 0.6rem;
        background: #ffffff;
        border-radius: 8px;
        padding: 0.65rem 1rem;
        font-size: 0.85rem;
        font-weight: 600;
        box-shadow: 0 10px 25px -5px rgba(15, 23, 42, 0.15), 0 4px 8px rgba(15, 23, 42, 0.08);
        border: 1px solid #e2e8f0;
        max-width: 360px;
        animation: globalToastSlideIn 0.2s ease forwards;
    }
    `;
    const styleEl = document.createElement('style');
    styleEl.innerHTML = css;
    document.head.appendChild(styleEl);
})();

// Shared color/icon set for floating toasts (showGlobalToast and
// showUploadStatus both render into the same popup style).
const _TOAST_STYLES = {
    success: { color: '#047857', icon: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>` },
    error: { color: '#b91c1c', icon: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>` },
    warning: { color: '#b45309', icon: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>` },
    info: { color: '#1d4ed8', icon: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#2563eb" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>` }
};

function _getGlobalToastContainer() {
    let container = document.getElementById('global-toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'global-toast-container';
        document.body.appendChild(container);
    }
    return container;
}

// Lightweight floating toast notification, independent of any specific view.
// Used for feedback that must work no matter which screen is currently
// active - e.g. the header "Refresh" button, which is shared across the
// dashboard, home, and email-settings views.
function showGlobalToast(message, type = 'info', autoCloseMs = 2000) {
    const container = _getGlobalToastContainer();
    const { color, icon } = _TOAST_STYLES[type] || _TOAST_STYLES.info;

    const toast = document.createElement('div');
    toast.className = 'global-toast';
    toast.style.color = color;
    toast.innerHTML = `${icon}<span>${escapeHtml(message)}</span>`;
    container.appendChild(toast);

    if (autoCloseMs > 0) {
        setTimeout(() => {
            toast.style.transition = 'opacity 0.25s ease';
            toast.style.opacity = '0';
            setTimeout(() => toast.remove(), 250);
        }, autoCloseMs);
    }
}

// Custom UI Alert / Confirm Promise Helpers
function showCustomConfirm({ title, message, type = 'danger', confirmText = 'OK', cancelText = 'Cancel' }) {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className = 'custom-modal-overlay';
        
        let iconHtml = '⚠️';
        let btnClass = 'confirm-primary';
        if (type === 'danger') {
            iconHtml = '🗑️';
            btnClass = 'confirm-danger';
        } else if (type === 'success') {
            iconHtml = '✓';
        } else if (type === 'info') {
            iconHtml = 'ℹ️';
        }

        overlay.innerHTML = `
            <div class="custom-modal-card">
                <div class="custom-modal-header">
                    <div class="custom-modal-icon ${type}">${iconHtml}</div>
                    <h3 class="custom-modal-title">${escapeHtml(title)}</h3>
                </div>
                <div class="custom-modal-body">
                    ${escapeHtml(message)}
                </div>
                <div class="custom-modal-actions">
                    <button class="custom-modal-btn cancel" id="custom-modal-cancel-btn">${escapeHtml(cancelText)}</button>
                    <button class="custom-modal-btn ${btnClass}" id="custom-modal-confirm-btn">${escapeHtml(confirmText)}</button>
                </div>
            </div>
        `;

        document.body.appendChild(overlay);

        const cleanUp = (value) => {
            overlay.style.animation = 'customModalFadeIn 0.15s ease reverse forwards';
            overlay.querySelector('.custom-modal-card').style.animation = 'customModalScaleIn 0.15s ease reverse forwards';
            setTimeout(() => {
                overlay.remove();
                resolve(value);
            }, 150);
        };

        const cancelBtn = overlay.querySelector('#custom-modal-cancel-btn');
        if (cancelBtn) {
            cancelBtn.addEventListener('click', () => cleanUp(false));
        }
        const confirmBtn = overlay.querySelector('#custom-modal-confirm-btn');
        if (confirmBtn) {
            confirmBtn.addEventListener('click', () => cleanUp(true));
        }
        
        const keyHandler = (e) => {
            if (e.key === 'Escape') {
                document.removeEventListener('keydown', keyHandler);
                cleanUp(false);
            }
        };
        document.addEventListener('keydown', keyHandler);
    });
}

function showCustomAlert({ title, message, type = 'info', confirmText = 'OK' }) {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className = 'custom-modal-overlay';

        let iconHtml = 'ℹ️';
        let btnClass = 'confirm-primary';
        if (type === 'danger') {
            iconHtml = '⚠️';
        } else if (type === 'success') {
            iconHtml = '✓';
        }

        overlay.innerHTML = `
            <div class="custom-modal-card">
                <div class="custom-modal-header">
                    <div class="custom-modal-icon ${type}">${iconHtml}</div>
                    <h3 class="custom-modal-title">${escapeHtml(title)}</h3>
                </div>
                <div class="custom-modal-body">
                    ${escapeHtml(message)}
                </div>
                <div class="custom-modal-actions">
                    <button class="custom-modal-btn ${btnClass}" id="custom-modal-confirm-btn" style="width: 100%;">${escapeHtml(confirmText)}</button>
                </div>
            </div>
        `;

        document.body.appendChild(overlay);

        const cleanUp = () => {
            overlay.style.animation = 'customModalFadeIn 0.15s ease reverse forwards';
            overlay.querySelector('.custom-modal-card').style.animation = 'customModalScaleIn 0.15s ease reverse forwards';
            setTimeout(() => {
                overlay.remove();
                resolve();
            }, 150);
        };

        const confirmBtn = overlay.querySelector('#custom-modal-confirm-btn');
        if (confirmBtn) {
            confirmBtn.addEventListener('click', cleanUp);
        }
        
        const keyHandler = (e) => {
            if (e.key === 'Escape' || e.key === 'Enter') {
                document.removeEventListener('keydown', keyHandler);
                cleanUp();
            }
        };
        document.addEventListener('keydown', keyHandler);
    });
}

function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

let growthChart = null;
let tablespaceChart = null;
let sessionChart = null;
let sessionCpuChart = null;
let sessionCpu24hChart = null;
let backupChart = null;
let latestBackupChart = null;
let sgaChart = null;
let pgaChart = null;
let sessionMemoryChart = null;
let sessionHistory = {
    labels: [],
    total: [],
    active: []
};

// Pre-populate with 24 hours of data
function initSessionHistory() {
    sessionHistory.labels = [];
    sessionHistory.total = [];
    sessionHistory.active = [];
    for (let i = 23; i >= 0; i--) {
        let d = new Date();
        d.setHours(d.getHours() - i);
        let hr = d.getHours();
        let ampm = hr >= 12 ? 'pm' : 'am';
        hr = hr % 12;
        hr = hr ? hr : 12;
        sessionHistory.labels.push(`${hr}:00 ${ampm}`);
        sessionHistory.total.push(40 + Math.floor(Math.random() * 10));
        sessionHistory.active.push(2 + Math.floor(Math.random() * 5));
    }
}
initSessionHistory();

let currentOsPlatform = null;
let allDisksData = [];

function selectOsPlatform(platform) {
    currentOsPlatform = platform;
    
    document.getElementById('os-selection-screen').style.display = 'none';
    document.getElementById('os-data-screen').style.display = 'flex';
    document.getElementById('os-back-btn').style.display = 'block';
    
    const nameEl = document.getElementById('os-name');
    if (nameEl) nameEl.innerText = 'Loading...';
    document.getElementById('os-error-msg').style.display = 'none';
    document.getElementById('os-content-body').style.display = 'flex';
    
    checkOsInfo();
}

document.addEventListener("DOMContentLoaded", function () {
    console.log("JS LOADED ✅");

    // Clear connection load history on page refresh/reload
    localStorage.removeItem('importHistory');

    // Sidebar toggle logic
    const sidebarToggle = document.getElementById('sidebar-toggle');
    if (sidebarToggle) {
        sidebarToggle.addEventListener('click', () => {
            const sidebar = document.querySelector('.sidebar');
            if (sidebar) {
                sidebar.classList.toggle('hidden');
            }
        });
    }

    // Load databases initially
    loadDatabases(true);

    // Initialize multi-db quick connect cards
    initQuickConnectCards();

    // Render import history initially
    renderImportHistory();
    
    // Initialize status filter dropdown
    initStatusFilterDropdown();

    // Recent Databases history dropdown toggle logic
    const historyBtn = document.getElementById('history-dropdown-btn');
    const historyDropdown = document.getElementById('history-dropdown');
    if (historyBtn && historyDropdown) {
        historyBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            historyDropdown.classList.toggle('open');
        });
        document.addEventListener('click', (e) => {
            if (!historyDropdown.contains(e.target)) {
                historyDropdown.classList.remove('open');
            }
        });
    }

    // Attach search input listeners
    const sidebarSearchInput = document.getElementById('sidebar-search-input');
    if (sidebarSearchInput) {
        ['input', 'keyup', 'change', 'search'].forEach(evt => {
            sidebarSearchInput.addEventListener(evt, filterDatabases);
        });
    }

    // Collapsible DB Connection History logic
    const importToggle = document.getElementById('import-history-toggle');
    const importCollapse = document.getElementById('import-history-collapse');
    const importChevron = document.getElementById('import-history-chevron');
    if (importToggle && importCollapse && importChevron) {
        importToggle.addEventListener('click', (e) => {
            e.stopPropagation();
            const isOpen = importCollapse.style.maxHeight && importCollapse.style.maxHeight !== '0px';
            if (isOpen) {
                importCollapse.style.maxHeight = '0px';
                importChevron.style.transform = 'rotate(0deg)';
            } else {
                importCollapse.style.maxHeight = '450px';
                importChevron.style.transform = 'rotate(180deg)';
            }
        });
    }

    // Load DB upload history & db access history initially
    loadImportHistory();
    renderDbHistoryDropdown();

    // Auto refresh
    setInterval(checkDB, 5000);
    setInterval(checkListener, 30000);
    setInterval(checkTablespace, 15000);
    setInterval(checkBlockingSessions, 10000);
    setInterval(checkBackup, 30000);
    setInterval(fetchMemoryInfo, 15000);
    setInterval(checkSessionMemory, 15000);
    setInterval(fetchArchiveLogInfo, 15000);
    setInterval(fetchArchiveCleanupInfo, 15000);
    setInterval(checkOsInfo, 15000);

    // Auto-refresh home page server overview
    setInterval(() => {
        const homeView = document.getElementById('home-view');
        if (homeView && homeView.style.display !== 'none') {
            loadServersOverview();
        }
    }, 15000);

    // Back button logic for OS Selection
    const backBtn = document.getElementById('os-back-btn');
    if (backBtn) {
        backBtn.addEventListener('click', () => {
            currentOsPlatform = null;
            document.getElementById('os-selection-screen').style.display = 'flex';
            document.getElementById('os-data-screen').style.display = 'none';
            document.getElementById('os-back-btn').style.display = 'none';
        });
    }

    // Refresh button event listeners
    const dbRefreshBtn = document.getElementById('dashboard-refresh-btn');
    if (dbRefreshBtn) {
        dbRefreshBtn.addEventListener('click', (e) => {
            // Spin animation
            const icon = dbRefreshBtn.querySelector('.refresh-icon');
            if (icon) {
                icon.style.transform = 'rotate(360deg)';
                icon.style.transition = 'transform 0.5s ease';
                setTimeout(() => {
                    icon.style.transform = 'none';
                    icon.style.transition = 'none';
                }, 500);
            }

            // e.isTrusted is false for the synthetic clicks the Auto Refresh
            // timer fires on this same button, so only a real user click
            // shows the toast - otherwise it would repeat on every tick.
            if (e.isTrusted) {
                showGlobalToast('Refreshing data...', 'info', 1800);
            }

            // Check active view
            const homeView = document.getElementById('home-view');
            const emailView = document.getElementById('email-settings-view');
            if (emailView && emailView.style.display !== 'none') {
                clearEmailSettingsFields();
                loadEmailHistory();
            } else if (homeView && homeView.style.display !== 'none') {
                loadDatabases(true);
            } else {
                refreshDashboard(true);
            }
        });
    }

    // Disconnect button - explicit, immediate alternative to just closing
    // the browser tab and waiting on the auto-shutdown heartbeat timeout.
    const disconnectBtn = document.getElementById('dashboard-disconnect-btn');
    if (disconnectBtn) {
        disconnectBtn.addEventListener('click', () => {
            showCustomConfirm({
                title: 'Disconnect & Stop Server?',
                message: 'This will shut down the Database Monitor server completely. You will need to relaunch it to use the dashboard again.',
                type: 'danger',
                confirmText: 'Disconnect',
                cancelText: 'Cancel'
            }).then(confirmed => {
                if (!confirmed) return;
                showGlobalToast('Disconnecting - server is shutting down...', 'warning', 0);
                fetch('/api/shutdown', { method: 'POST' }).catch(() => {});
                setTimeout(() => {
                    document.body.innerHTML = `
                        <div style="display: flex; align-items: center; justify-content: center; height: 100vh; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #475569; font-size: 1.05rem; background: #f8fafc;">
                            Database Monitor has been disconnected. You may close this window.
                        </div>
                    `;
                }, 800);
            });
        });
    }

    // Auto Refresh drop down/input control
    const autoRefreshInput = document.getElementById('auto-refresh-input');
    const autoRefreshUnit = document.getElementById('auto-refresh-unit');
    const autoRefreshToggle = document.getElementById('auto-refresh-toggle');
    let autoRefreshTimeoutId = null;

    function updateAutoRefresh() {
        if (autoRefreshTimeoutId) {
            clearTimeout(autoRefreshTimeoutId);
            autoRefreshTimeoutId = null;
        }

        if (autoRefreshToggle && autoRefreshToggle.checked) {
            let val = parseInt(autoRefreshInput.value, 10);
            const unit = autoRefreshUnit ? autoRefreshUnit.value : 's';

            // Safety defaults
            if (isNaN(val) || val < 1) {
                val = 1;
                autoRefreshInput.value = 1;
            }
            if (unit === 's' && val < 2) {
                val = 2;
                autoRefreshInput.value = 2;
            }

            let ms = val * 1000;
            if (unit === 'm') {
                ms = val * 60 * 1000;
            } else if (unit === 'h') {
                ms = val * 60 * 60 * 1000;
            }

            // One-shot: refresh once after the configured delay, then
            // uncheck the box automatically instead of repeating forever.
            autoRefreshTimeoutId = setTimeout(() => {
                autoRefreshTimeoutId = null;
                const btn = document.getElementById('dashboard-refresh-btn');
                if (btn) {
                    btn.click();
                }
                autoRefreshToggle.checked = false;
            }, ms);
        }
    }

    if (autoRefreshInput && autoRefreshToggle) {
        autoRefreshToggle.addEventListener('change', updateAutoRefresh);
        if (autoRefreshUnit) {
            autoRefreshUnit.addEventListener('change', updateAutoRefresh);
        }
        autoRefreshInput.addEventListener('input', () => {
            if (autoRefreshToggle.checked) {
                updateAutoRefresh();
            }
        });
    }

    const homeRefreshBtn = document.getElementById('home-refresh-btn');
    if (homeRefreshBtn) {
        homeRefreshBtn.addEventListener('click', () => {
            // Spin animation
            const icon = homeRefreshBtn.querySelector('.refresh-icon');
            if (icon) {
                icon.style.transform = 'rotate(360deg)';
                icon.style.transition = 'transform 0.5s ease';
                setTimeout(() => {
                    icon.style.transform = 'none';
                    icon.style.transition = 'none';
                }, 500);
            }
            showUploadStatus("Refreshing database configurations and connection status...", "info", 1800);
            loadDatabases(true, [], true);
        });
    }
});

let globalDatabases = [];
let globalActiveDbId = null;
let dbSearchQuery = '';

function filterDatabases() {
    const input = document.getElementById('sidebar-search-input');
    if (input) {
        dbSearchQuery = input.value.toLowerCase().trim();
        renderSidebarList();
        renderQuickConnectCards(globalDatabases);
    }
}

function renderSidebarList() {
    const listEl = document.getElementById('db-list');
    if (!listEl) return;
    
    listEl.innerHTML = '';
    
    // 1. Home Page Navigation Item
    const homeLi = document.createElement('li');
    homeLi.innerHTML = `
        <div style="display: flex; align-items: center; gap: 0.6rem;">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-top: -1px;">
                <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path>
                <polyline points="9 22 9 12 15 12 15 22"></polyline>
            </svg>
            <span>Home Page</span>
        </div>
    `;
    if (!globalActiveDbId) {
        homeLi.classList.add('active');
    }
    homeLi.onclick = () => {
        goHome();
    };
    listEl.appendChild(homeLi);

    if (!globalDatabases || globalDatabases.length === 0) {
        return;
    }

    // Filter databases
    const filteredDbs = globalDatabases.filter(db => {
        if (!dbSearchQuery) return true;
        const q = dbSearchQuery.toLowerCase();
        return (db.db_id && db.db_id.toLowerCase().includes(q)) ||
               (db.host && db.host.toLowerCase().includes(q)) ||
               (db.service_name && db.service_name.toLowerCase().includes(q));
    });

    if (filteredDbs.length === 0 && dbSearchQuery) {
        const emptyLi = document.createElement('li');
        emptyLi.style.cssText = 'color: #94a3b8; font-style: italic; font-size: 0.75rem; padding: 0.5rem 0.75rem; text-align: center; list-style: none;';
        emptyLi.innerText = 'No matching databases';
        listEl.appendChild(emptyLi);
        return;
    }

    // Group databases into Warnings, Connected, and Offline
    const warningDbs = [];
    const connectedDbs = [];
    const offlineDbs = [];

    filteredDbs.forEach(db => {
        const dbKey = db.db_id.toLowerCase().replace(/[^a-z0-9]/g, '_');
        const summary = dbStatuses[dbKey];
        
        const isConnected = summary && summary.db_status === 'Connected';
        const criticalTSStrings = summary && summary.full_tablespaces ? summary.full_tablespaces.filter(item => {
            const match = item.match(/\((\d+(\.\d+)?)\%\)/);
            return match && parseFloat(match[1]) >= 90;
        }) : [];
        const hasTablespaceWarning = criticalTSStrings.length > 0;
        const isListenerWarning = summary && summary.listener_status === 'Running' && summary.db_status !== 'Connected';
        
        const isBackupCompleted = summary && summary.last_backup_status && (
            summary.last_backup_status.toUpperCase() === 'COMPLETED' || 
            summary.last_backup_status.toUpperCase() === 'SUCCESS'
        );
        const backupYesterday = summary && summary.backup_yesterday === true;
        const backupIsHealthy = isBackupCompleted && backupYesterday;
        const hasBackupWarning = summary && isConnected && !backupIsHealthy;
        const hasLockWarning = summary && isConnected && summary.blocking_sessions_count > 0;
        const hasMountWarning = summary && isConnected && summary.has_critical_mount === true;

        if (hasTablespaceWarning || isListenerWarning || hasBackupWarning || hasLockWarning || hasMountWarning) {
            warningDbs.push(db);
        } else if (isConnected) {
            connectedDbs.push(db);
        } else {
            offlineDbs.push(db);
        }
    });

    // Helper to render db item
    const createDbLi = (db, groupType) => {
        const dbKey = db.db_id.toLowerCase().replace(/[^a-z0-9]/g, '_');
        const summary = dbStatuses[dbKey];
        let colorClass = 'grey';
        let isBackupCompleted = true;
        if (summary) {
            isBackupCompleted = summary.last_backup_status && (
                summary.last_backup_status.toUpperCase() === 'COMPLETED' || 
                summary.last_backup_status.toUpperCase() === 'SUCCESS'
            );
            const backupYesterday = summary.backup_yesterday === true;
            const backupIsHealthy = isBackupCompleted && backupYesterday;
            const criticalTSStrings = summary.full_tablespaces ? summary.full_tablespaces.filter(item => {
                const match = item.match(/\((\d+(\.\d+)?)\%\)/);
                return match && parseFloat(match[1]) >= 90;
            }) : [];
            const hasTablespaceWarning = criticalTSStrings.length > 0;
            
            if (summary.db_status === 'Connected') {
                if (summary.has_critical_mount || hasTablespaceWarning) {
                    colorClass = 'red';
                } else if (summary.blocking_sessions_count > 0 || !backupIsHealthy) {
                    colorClass = 'orange'; // yellow/orange warning
                } else {
                    colorClass = 'green';
                }
            } else if (summary.db_status === 'Checking...' || summary.db_status === 'Fetching...') {
                colorClass = 'grey';
            } else if (summary.listener_status === 'Running') {
                colorClass = 'orange';
            } else {
                colorClass = 'red';
            }
        }
        
        const li = document.createElement('li');
        li.className = (groupType === 'connected') ? 'connected-item' : ((groupType === 'warning') ? 'warning-item' : 'offline-item');
        
        let alertHTML = '';
        if (summary) {
            const criticalTSStrings = summary.full_tablespaces ? summary.full_tablespaces.filter(item => {
                const match = item.match(/\((\d+(\.\d+)?)\%\)/);
                return match && parseFloat(match[1]) >= 90;
            }) : [];
            const hasTablespaceWarning = criticalTSStrings.length > 0;
            const backupYesterday = summary.backup_yesterday === true;
            const backupIsHealthy = isBackupCompleted && backupYesterday;

            if (hasTablespaceWarning) {
                alertHTML = `
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink: 0; animation: pulse 2s infinite;" title="Critical Tablespace:\n${criticalTSStrings.join('\n')}">
                        <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
                        <line x1="12" y1="9" x2="12" y2="13"></line>
                        <line x1="12" y1="17" x2="12.01" y2="17"></line>
                    </svg>
                `;
            } else if (summary.db_status === 'Connected' && summary.has_critical_mount) {
                alertHTML = `
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink: 0; animation: pulse 2s infinite;" title="Critical Host Mount:\n${(summary.critical_mount_points || []).join('\n')}">
                        <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
                        <line x1="12" y1="9" x2="12" y2="13"></line>
                        <line x1="12" y1="17" x2="12.01" y2="17"></line>
                    </svg>
                `;
            } else if (summary.db_status === 'Connected' && summary.blocking_sessions_count > 0) {
                alertHTML = `
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink: 0; animation: pulse 2s infinite;" title="Deadlock/Lock Warning:\n${summary.blocking_sessions_count} blocked session(s) detected.">
                        <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
                        <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
                    </svg>
                `;
            } else if (summary.db_status === 'Connected' && !backupIsHealthy) {
                alertHTML = `
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink: 0;" title="Backup Warning:\nLast RMAN Backup status: ${summary.last_backup_status || 'Unknown'}">
                        <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
                        <line x1="12" y1="9" x2="12" y2="13"></line>
                        <line x1="12" y1="17" x2="12.01" y2="17"></line>
                    </svg>
                `;
            }
        }

        li.innerHTML = `
            <div class="sidebar-db-item" style="display: flex; align-items: center; justify-content: space-between; width: 100%;">
                <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 150px;">${db.db_id}</span>
                <div style="display: flex; align-items: center; gap: 0.35rem; flex-shrink: 0;">
                    ${alertHTML}
                    <span class="sidebar-status-dot ${colorClass}" id="sidebar-dot-${dbKey}"></span>
                </div>
            </div>
        `;
        if (db.db_id === globalActiveDbId) {
            li.classList.add('active');
            const dashboardView = document.getElementById('dashboard-view');
            if (dashboardView && dashboardView.style.display !== 'none') {
                const subtitle = document.getElementById('dashboard-subtitle');
                if (subtitle) {
                    if (db.service_name && db.service_name.toLowerCase() !== db.db_id.toLowerCase()) {
                        subtitle.innerHTML = `Database system overview &middot; ${db.service_name} &middot; <strong style="font-weight: 700;">${db.db_id}</strong>`;
                    } else {
                        subtitle.innerHTML = `Database system overview &middot; <strong style="font-weight: 700;">${db.db_id}</strong>`;
                    }
                }
            }
        }
        li.onclick = () => {
            setActiveDatabase(db.db_id);
        };
        return li;
    };

    // 1. Render WARNINGS Group (First Priority at the top)
    if (warningDbs.length > 0) {
        const header = document.createElement('div');
        header.className = 'sidebar-group-header warning-header';
        header.style.cssText = 'color: #d97706; font-size: 0.72rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; padding: 0.6rem 2rem 0.25rem; display: flex; align-items: center; gap: 0.35rem;';
        header.innerHTML = `
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#d97706" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
                <line x1="12" y1="9" x2="12" y2="13"></line>
                <line x1="12" y1="17" x2="12.01" y2="17"></line>
            </svg>
            Warnings (${warningDbs.length})
        `;
        listEl.appendChild(header);
        
        warningDbs.forEach(db => {
            listEl.appendChild(createDbLi(db, 'warning'));
        });
    }

    // 2. Render CONNECTED Group
    if (connectedDbs.length > 0) {
        const header = document.createElement('div');
        header.className = 'sidebar-group-header';
        header.innerText = 'Connected';
        listEl.appendChild(header);
        
        connectedDbs.forEach(db => {
            listEl.appendChild(createDbLi(db, 'connected'));
        });
    }

    // 3. Render OFFLINE Group
    if (offlineDbs.length > 0) {
        const header = document.createElement('div');
        header.className = 'sidebar-group-header';
        header.innerText = 'Offline';
        listEl.appendChild(header);
        
        offlineDbs.forEach(db => {
            listEl.appendChild(createDbLi(db, 'offline'));
        });
    }
}

// The Disconnect button should only be offered on the home page - inside a
// database's dashboard (or the email settings view) it's hidden, since it's
// a whole-server shutdown action, not something tied to a specific
// database view.
function updateDisconnectButtonVisibility() {
    const disconnectBtn = document.getElementById('dashboard-disconnect-btn');
    if (!disconnectBtn) return;
    const homeView = document.getElementById('home-view');
    const isHome = homeView && homeView.style.display !== 'none';
    disconnectBtn.style.display = isHome ? 'flex' : 'none';
}

function loadDatabases(isInitialLoad = false, autoConnectIds = [], showRefreshNotify = false) {
    if (isInitialLoad) {
        globalActiveDbId = null;
    }
    fetch('/api/databases')
        .then(res => res.json())
        .then(data => {
            globalDatabases = data.databases || [];
            if (isInitialLoad) {
                globalActiveDbId = null;
            } else {
                globalActiveDbId = data.active_db_id;
            }
            
            if (showRefreshNotify) {
                const count = globalDatabases.length;
                if (count > 0) {
                    showUploadStatus(`✅ Refreshed: ${count} database configuration(s) active & ready.`, 'success', 5000);
                } else {
                    showUploadStatus(`⚠️ Refreshed: No database configurations found. Upload a .csv or .txt file to configure.`, 'warning', 6000);
                }
            }

            // Render the sidebar list initially
            renderSidebarList();

            // Dynamically render Quick Connect cards on the home page
            renderQuickConnectCards(globalDatabases, autoConnectIds);
            
            // Populate Email Settings DB Dropdown
            const dbSelect = document.getElementById('email-settings-db-select');
            if (dbSelect) {
                const prevVal = dbSelect.value;
                dbSelect.innerHTML = '';
                globalDatabases.forEach(db => {
                    const opt = document.createElement('option');
                    opt.value = db.db_id;
                    opt.innerText = db.db_id;
                    dbSelect.appendChild(opt);
                });
                if (prevVal && [...dbSelect.options].some(o => o.value === prevVal)) {
                    dbSelect.value = prevVal;
                }
            }
            
            // Hide Email Settings view on standard loads
            const emailSettingsView = document.getElementById('email-settings-view');
            if (emailSettingsView) emailSettingsView.style.display = 'none';
            const emailSidebarSec = document.getElementById('email-settings-sidebar-section');
            if (emailSidebarSec) emailSidebarSec.style.backgroundColor = '';

            // Poll statuses immediately so they display online/offline without delay
            pollDatabaseStatuses(globalDatabases);

            // Load server overview on home page
            loadServersOverview();
            loadHomeMountPoints();
            loadHomeOracleProcesses();

            if (isInitialLoad || !globalActiveDbId) {
                globalActiveDbId = null;
                document.getElementById('home-view').style.display = 'flex';
                document.getElementById('dashboard-view').style.display = 'none';
                const backBtn = document.getElementById('header-back-btn');
                if (backBtn) backBtn.style.display = 'none';
                const subtitle = document.getElementById('dashboard-subtitle');
                if (subtitle) subtitle.innerText = 'Database system overview';
                const statusCard = document.getElementById('status-card-container');
                if (statusCard) statusCard.style.display = 'none';
                renderSidebarList();
                updateDisconnectButtonVisibility();
            } else {
                document.getElementById('home-view').style.display = 'none';
                document.getElementById('dashboard-view').style.display = '';
                const backBtn = document.getElementById('header-back-btn');
                if (backBtn) backBtn.style.display = 'flex';
                const statusCard = document.getElementById('status-card-container');
                if (statusCard) statusCard.style.display = 'flex';
                updateDisconnectButtonVisibility();
                refreshDashboard(true);
            }
        })
        .catch(err => console.error("Error loading databases:", err));
}

function goHome() {
    fetch('/api/clear-database', {
        method: 'POST'
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            document.getElementById('home-view').style.display = 'flex';
            document.getElementById('dashboard-view').style.display = 'none';
            const emailSettingsView = document.getElementById('email-settings-view');
            if (emailSettingsView) emailSettingsView.style.display = 'none';
            const emailSidebarSec = document.getElementById('email-settings-sidebar-section');
            if (emailSidebarSec) emailSidebarSec.style.backgroundColor = '';
            
            const backBtn = document.getElementById('header-back-btn');
            if (backBtn) backBtn.style.display = 'none';

            const subtitle = document.getElementById('dashboard-subtitle');
            if (subtitle) subtitle.innerText = 'Database system overview';
            const statusCard = document.getElementById('status-card-container');
            if (statusCard) statusCard.style.display = 'none';
            
            // Reload databases sidebar with home active
            loadDatabases(true);
        }
    })
    .catch(err => console.error("Error going home:", err));
}


function setActiveDatabase(db_id) {
    fetch('/api/set-database', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ db_id: db_id })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            const emailSettingsView = document.getElementById('email-settings-view');
            if (emailSettingsView) emailSettingsView.style.display = 'none';
            const emailSidebarSec = document.getElementById('email-settings-sidebar-section');
            if (emailSidebarSec) emailSidebarSec.style.backgroundColor = '';

            const backBtn = document.getElementById('header-back-btn');
            if (backBtn) backBtn.style.display = 'flex';

            // Update UI to loading states
            const ids = ['ts-status', 'backup-status-title', 'session-count', 'total-session-count', 'growth-used-space'];
            ids.forEach(id => {
                let el = document.getElementById(id);
                if(el) el.innerText = 'Checking...';
            });
            const backupMeta = document.getElementById('backup-status-meta');
            if (backupMeta) backupMeta.innerText = 'Loading backup metadata...';
            const backupBadge = document.getElementById('backup-type-badge');
            if (backupBadge) backupBadge.style.display = 'none';
            const backupCard = document.getElementById('backup-card-container');
            if (backupCard) {
                const iconContainer = document.getElementById('backup-status-icon-container');
                if (iconContainer) {
                    iconContainer.innerHTML = `
                        <div class="backup-status-circle unknown">
                            <span>?</span>
                        </div>
                    `;
                }
            }
            const statusCard = document.getElementById('status-card-container');
            if (statusCard) {
                statusCard.style.display = 'flex';
                statusCard.className = 'status-card';
            }
            
            const dbText = document.getElementById('db-status-text');
            if (dbText) dbText.innerText = 'Checking...';
            
            const lisText = document.getElementById('listener-status-text');
            if (lisText) lisText.innerText = 'Checking...';

            currentDbStatus = "Checking...";
            currentListenerStatus = "Checking...";

            // Reset memory elements
            const memIds = ['sga-large-value', 'pga-large-value', 'sga-pill-badge', 'pga-pill-badge', 'sga-subtext', 'pga-subtext', 'sga-segmented-label', 'pga-segmented-label'];
            memIds.forEach(id => {
                let el = document.getElementById(id);
                if(el) {
                    if (id.includes('badge')) el.innerText = '0%';
                    else if (id.includes('subtext')) el.innerText = 'Checking...';
                    else if (id.includes('segmented-label')) el.innerText = 'Checking memory segments...';
                    else el.innerText = '...';
                }
            });

            // Reset Archive Log elements
            const arcSize = document.getElementById('archive-size-val');
            if (arcSize) arcSize.innerText = '...';
            const arcBadge = document.getElementById('archive-count-badge');
            if (arcBadge) {
                arcBadge.innerText = '0 logs';
                arcBadge.className = 'pill-badge green';
            }
            const arcPercent = document.getElementById('archive-fra-percent-label');
            if (arcPercent) {
                arcPercent.innerText = 'Checking...';
                arcPercent.style.color = '#b45309';
            }
            const arcProgress = document.getElementById('archive-fra-progress');
            if (arcProgress) {
                arcProgress.style.width = '0%';
                arcProgress.style.backgroundColor = '#f59e0b';
            }
            const arcMeta = document.getElementById('archive-fra-meta');
            if (arcMeta) {
                arcMeta.innerText = 'Checking...';
                arcMeta.title = 'Checking...';
            }
            const arcLoc = document.getElementById('archive-loc-display');
            if (arcLoc) {
                arcLoc.innerText = 'Loc: N/A';
                arcLoc.title = 'Location: N/A';
            }

            // Reset Blocking Monitor elements
            const blockCard = document.getElementById('blocking-card-container');
            if (blockCard) blockCard.className = 'card blocking-card';
            const blockIndicator = document.getElementById('blocking-light-indicator');
            if (blockIndicator) {
                blockIndicator.style.backgroundColor = '#10b981';
                blockIndicator.style.boxShadow = '0 0 8px rgba(16, 185, 129, 0.4)';
            }
            const blockSummary = document.getElementById('blocking-summary-text');
            if (blockSummary) blockSummary.innerText = 'Checking locks...';
            const blockBadge = document.getElementById('blocking-status-badge');
            if (blockBadge) {
                blockBadge.innerText = 'Clean';
                blockBadge.className = 'pill-badge green';
            }
            const blockList = document.getElementById('blocking-details-list');
            if (blockList) {
                blockList.innerHTML = '<div style="color: #64748b; text-align: center; margin-top: 1.5rem; font-style: italic;">Checking...</div>';
            }

            // Reload sidebar to reflect active state, which will then refresh the dashboard
            loadDatabases();
            addToDbHistory(db_id);
        }
    })
    .catch(err => console.error("Error setting database:", err));
}

function refreshDashboard(isManual = false) {
    checkDB();
    checkListener();
    checkStandbyStatus();
    checkReportingStatus();
    checkTablespace();
    checkSessions();
    checkBlockingSessions();
    checkBackup();
    checkDbGrowth();
    fetchMemoryInfo();
    checkSessionMemory();
    fetchArchiveLogInfo();
    fetchArchiveCleanupInfo();
    checkOsInfo();
    fetchMountPoints();
    fetchDashboardServerProcesses();

    // Only update CPU sessions and Oracle Processes if triggered manually by user action
    if (isManual) {
        checkSessionCPU();
        checkSessionCPU24h();
        fetchOracleDatabaseProcesses();
    }
}


// Global status state variables
let currentDbStatus = "Checking...";
let currentListenerStatus = "Checking...";

function updateCombinedStatusCard() {
    const card = document.getElementById("status-card-container");
    const dbRow = document.getElementById("db-status-row");
    const dbArrow = document.getElementById("db-arrow");
    const dbText = document.getElementById("db-status-text");
    const lisRow = document.getElementById("listener-status-row");
    const lisArrow = document.getElementById("listener-arrow");
    const lisText = document.getElementById("listener-status-text");

    if (!card || !dbArrow || !dbText || !lisArrow || !lisText) return;

    const isDbUp = (currentDbStatus === "Connected");
    const isLisUp = (currentListenerStatus === "Running");

    // Update Text & Arrows
    if (currentDbStatus === "Checking...") {
        dbText.innerText = "Checking Database...";
        dbArrow.innerText = "•";
    } else if (isDbUp) {
        dbArrow.innerText = "↑";
        dbText.innerText = "Database Up";
    } else {
        dbArrow.innerText = "↓";
        dbText.innerText = "Database Down";
    }

    if (currentListenerStatus === "Checking...") {
        lisText.innerText = "Checking Listener...";
        lisArrow.innerText = "•";
    } else if (isLisUp) {
        lisArrow.innerText = "↑";
        lisText.innerText = "Listener Running";
    } else {
        lisArrow.innerText = "↓";
        lisText.innerText = "Listener Stopped";
    }

    // Update Card Class & Styles based on combined state
    if (currentDbStatus === "Checking..." || currentListenerStatus === "Checking...") {
        card.className = "status-card";
        if (dbRow) dbRow.className = "";
        if (lisRow) lisRow.className = "";
    } else if (isDbUp && isLisUp) {
        card.className = "status-card all-up";
        if (dbRow) dbRow.className = "";
        if (lisRow) lisRow.className = "";
    } else if (!isDbUp && !isLisUp) {
        card.className = "status-card all-down";
        if (dbRow) dbRow.className = "";
        if (lisRow) lisRow.className = "";
    } else {
        card.className = "status-card mixed-status";
        if (dbRow) dbRow.className = isDbUp ? "status-up" : "status-down";
        if (lisRow) lisRow.className = isLisUp ? "status-up" : "status-down";
    }
}

// ✅ DATABASE STATUS
function checkDB() {
    fetch('/db-status')
        .then(res => res.json())
        .then(data => {
            console.log("DB:", data);
            currentDbStatus = data.status;
            updateCombinedStatusCard();
        })
        .catch(err => {
            console.error("DB Error:", err);
            currentDbStatus = "Not Connected";
            updateCombinedStatusCard();
        });
}

// ✅ LISTENER STATUS
function checkListener() {
    fetch('/listener-status')
        .then(res => res.json())
        .then(data => {
            console.log("Listener:", data);
            currentListenerStatus = data.status;
            updateCombinedStatusCard();
        })
        .catch(err => {
            console.error("Listener Error:", err);
            currentListenerStatus = "Stopped";
            updateCombinedStatusCard();
        });
}

// ✅ STANDBY REPLICATION SYNC STATUS DETAILED
function checkStandbyStatus() {
    if (!globalActiveDbId) return;
    
    const activeDb = globalDatabases.find(d => d.db_id === globalActiveDbId);
    
    fetch(`/api/db-summary?db_id=${encodeURIComponent(globalActiveDbId)}`)
        .then(res => res.json())
        .then(summary => {
            const container = document.getElementById('standby-details-card-container');
            if (!container) return;
            
            const status = summary.standby_status || 'Not Configured';
            if (status === 'Not Configured') {
                container.style.display = 'none';
                return;
            }
            
            container.style.display = 'flex';
            
            const badge = document.getElementById('dashboard-standby-badge');
            const label = document.getElementById('dashboard-standby-sync-label');
            const hostLabel = document.getElementById('dashboard-standby-host-label');
            const icon = document.getElementById('dashboard-standby-status-icon');
            
            const priSeq = document.getElementById('dashboard-standby-primary-seq');
            const appSeq = document.getElementById('dashboard-standby-applied-seq');
            const gapSeq = document.getElementById('dashboard-standby-gap-seq');
            
            const errorBox = document.getElementById('dashboard-standby-error-box');
            const errorText = document.getElementById('dashboard-standby-error-text');
            
            if (hostLabel) {
                const standbyHost = activeDb ? (activeDb.stby_host || 'Configured') : 'Configured';
                const standbySid = activeDb ? (activeDb.stby_service_name || '') : '';
                hostLabel.innerText = `Standby Host: ${standbyHost}${standbySid ? ' (' + standbySid + ')' : ''}`;
            }
            
            if (priSeq) priSeq.innerText = (summary.standby_primary_seq !== undefined && summary.standby_primary_seq !== null) ? summary.standby_primary_seq : '-';
            if (appSeq) appSeq.innerText = (summary.standby_applied_seq !== undefined && summary.standby_applied_seq !== null) ? summary.standby_applied_seq : '-';
            if (gapSeq) {
                const gap = (summary.archive_gap !== undefined && summary.archive_gap !== null) ? summary.archive_gap : '-';
                gapSeq.innerText = gap;
            }

            // Per-thread archive log breakdown (V$ARCHIVED_LOG, grouped by THREAD#)
            const threadsBody = document.getElementById('dashboard-standby-threads-body');
            if (threadsBody) {
                const threads = summary.standby_threads || [];
                if (threads.length === 0) {
                    threadsBody.innerHTML = `<tr><td colspan="6" style="padding: 0.75rem; text-align: center; color: #94a3b8; font-style: italic;">No archive log threads reported.</td></tr>`;
                } else {
                    threadsBody.innerHTML = threads.map(t => {
                        const isWarning = !!t.warning;
                        const conditionBadge = isWarning
                            ? `<span style="display: inline-flex; align-items: center; gap: 0.3rem; font-size: 0.72rem; font-weight: 700; padding: 2px 8px; border-radius: 10px; background: #fffbeb; color: #b45309;">⚠️ Warning</span>`
                            : `<span style="display: inline-flex; align-items: center; gap: 0.3rem; font-size: 0.72rem; font-weight: 700; padding: 2px 8px; border-radius: 10px; background: #f0fdf4; color: #16a34a;">✅ Healthy</span>`;
                        return `
                            <tr style="border-bottom: 1px solid #f1f5f9;">
                                <td style="padding: 0.5rem 0.7rem; font-weight: 700; color: #1e293b;">${escapeHtml(String(t.thread !== undefined && t.thread !== null ? t.thread : '-'))}</td>
                                <td style="padding: 0.5rem 0.7rem; color: #475569;">${escapeHtml(String(t.primary_last_generated !== undefined && t.primary_last_generated !== null ? t.primary_last_generated : '-'))}</td>
                                <td style="padding: 0.5rem 0.7rem; color: #475569;">${escapeHtml(String(t.last_received !== undefined && t.last_received !== null ? t.last_received : '-'))}</td>
                                <td style="padding: 0.5rem 0.7rem; color: #475569;">${escapeHtml(String(t.last_applied !== undefined && t.last_applied !== null ? t.last_applied : '-'))}</td>
                                <td style="padding: 0.5rem 0.7rem; color: ${isWarning ? '#b45309' : '#475569'}; font-weight: ${isWarning ? '700' : '400'};">${escapeHtml(String(t.log_gap !== undefined && t.log_gap !== null ? t.log_gap : '-'))}</td>
                                <td style="padding: 0.5rem 0.7rem;">${conditionBadge}</td>
                            </tr>
                        `;
                    }).join('');
                }
            }

            // Archive destination status (V$ARCHIVE_DEST_STATUS, TARGET = 'STANDBY')
            const destSection = document.getElementById('dashboard-standby-dest-section');
            const destBody = document.getElementById('dashboard-standby-dest-body');
            if (destSection && destBody) {
                const destRows = summary.standby_dest_status || [];
                if (destRows.length === 0) {
                    destSection.style.display = 'none';
                } else {
                    destSection.style.display = 'block';
                    destBody.innerHTML = destRows.map(d => {
                        const hasError = !!(d.error && String(d.error).trim());
                        return `
                            <tr style="border-bottom: 1px solid #f1f5f9;">
                                <td style="padding: 0.5rem 0.7rem; font-weight: 700; color: #1e293b;">${escapeHtml(String(d.dest_id !== undefined && d.dest_id !== null ? d.dest_id : '-'))}</td>
                                <td style="padding: 0.5rem 0.7rem; color: #475569;">${escapeHtml(d.status || '-')}</td>
                                <td style="padding: 0.5rem 0.7rem; color: #475569;">${escapeHtml(d.target || '-')}</td>
                                <td style="padding: 0.5rem 0.7rem; color: #475569;">${escapeHtml(d.db_unique_name || '-')}</td>
                                <td style="padding: 0.5rem 0.7rem; color: ${hasError ? '#b91c1c' : '#94a3b8'}; font-weight: ${hasError ? '700' : '400'};">${escapeHtml(hasError ? d.error : '-')}</td>
                            </tr>
                        `;
                    }).join('');
                }
            }

            // Standby Destination Status (raw V$ARCHIVE_DEST_STATUS, DEST_ID IN (1, 2))
            // The card itself (standby-destination-status-card-container) is always
            // visible - only its inner content toggles between the table and the
            // "unavailable" message, so a failed SSH/sqlplus/query never hides the card.
            const destStatusBody = document.getElementById('dashboard-standby-destination-status-body');
            const destStatusTableWrap = document.getElementById('standby-destination-status-table-wrap');
            const destStatusUnavailable = document.getElementById('standby-destination-status-unavailable');
            if (destStatusBody && destStatusTableWrap && destStatusUnavailable) {
                const destStatusRows = summary.standby_destination_status || [];
                if (destStatusRows.length === 0) {
                    destStatusTableWrap.style.display = 'none';
                    destStatusUnavailable.style.display = 'block';
                    const unavailableText = document.getElementById('standby-destination-status-unavailable-text');
                    if (unavailableText) {
                        unavailableText.innerText = summary.standby_error
                            ? `Production/SSH connection is currently unavailable: ${summary.standby_error}`
                            : 'Production/SSH connection is currently unavailable.';
                    }
                } else {
                    destStatusUnavailable.style.display = 'none';
                    destStatusTableWrap.style.display = 'block';
                    destStatusBody.innerHTML = destStatusRows.map(d => {
                        const hasError = !!(d.error && String(d.error).trim());
                        return `
                            <tr style="border-bottom: 1px solid #f1f5f9;">
                                <td style="padding: 0.5rem 0.7rem; font-weight: 700; color: #1e293b;">${escapeHtml(d.dest_id !== undefined && d.dest_id !== null && String(d.dest_id).trim() !== '' ? String(d.dest_id) : '-')}</td>
                                <td style="padding: 0.5rem 0.7rem; color: #475569;">${escapeHtml(d.dest_name || '-')}</td>
                                <td style="padding: 0.5rem 0.7rem; color: #475569;">${escapeHtml(d.db_unique_name || '-')}</td>
                                <td style="padding: 0.5rem 0.7rem; color: #475569;">${escapeHtml(d.status || '-')}</td>
                                <td style="padding: 0.5rem 0.7rem; color: ${hasError ? '#b91c1c' : '#94a3b8'}; font-weight: ${hasError ? '700' : '400'};">${escapeHtml(hasError ? d.error : '-')}</td>
                                <td style="padding: 0.5rem 0.7rem; color: #475569;">${escapeHtml(d.synchronization_status || '-')}</td>
                            </tr>
                        `;
                    }).join('');
                }
            }

            if (status === 'Synced') {
                if (badge) {
                    badge.innerText = 'Synced';
                    badge.className = 'pill-badge green';
                }
                if (label) label.innerText = 'Standby is fully synchronized';
                if (icon) {
                    icon.innerText = '✅';
                    icon.style.background = '#ecfdf5';
                    icon.style.borderColor = '#10b981';
                }
                if (errorBox) errorBox.style.display = 'none';
            } else if (status === 'Not Synced') {
                if (badge) {
                    badge.innerText = 'Not Synced';
                    badge.className = 'pill-badge orange';
                }
                if (label) label.innerText = `Out of sync (Gap: ${summary.archive_gap} logs)`;
                if (icon) {
                    icon.innerText = '⚠️';
                    icon.style.background = '#fffbeb';
                    icon.style.borderColor = '#f59e0b';
                }
                if (errorBox) {
                    errorBox.style.display = 'block';
                    if (errorText) {
                        errorText.innerText = summary.standby_error || `Standby database is lagging behind primary by ${summary.archive_gap} logs. Please check recovery status or archive transport.`;
                    }
                }
            } else {
                if (badge) {
                    badge.innerText = 'Connection Error';
                    badge.className = 'pill-badge red';
                }
                if (label) label.innerText = 'Standby Database offline';
                if (icon) {
                    icon.innerText = '❌';
                    icon.style.background = '#fef2f2';
                    icon.style.borderColor = '#ef4444';
                }
                if (errorBox) {
                    errorBox.style.display = 'block';
                    if (errorText) {
                        errorText.innerText = summary.standby_error || 'Unable to establish a connection to the standby database instance. Please check listener and login credentials.';
                    }
                }
            }
        })
        .catch(err => {
            console.error("Error checking standby status:", err);
            const container = document.getElementById('standby-details-card-container');
            if (container) container.style.display = 'none';
        });
}

// ✅ REPORTING DATABASE STATUS DETAILED
function checkReportingStatus() {
    if (!globalActiveDbId) return;

    const activeDb = globalDatabases.find(d => d.db_id === globalActiveDbId);

    fetch(`/api/db-summary?db_id=${encodeURIComponent(globalActiveDbId)}`)
        .then(res => res.json())
        .then(summary => {
            const container = document.getElementById('reporting-details-card-container');
            if (!container) return;

            const status = summary.reporting_status || 'Not Configured';
            if (status === 'Not Configured') {
                container.style.display = 'none';
                return;
            }

            container.style.display = 'flex';

            const badge = document.getElementById('dashboard-reporting-badge');
            const label = document.getElementById('dashboard-reporting-status-label');
            const hostLabel = document.getElementById('dashboard-reporting-host-label');
            const icon = document.getElementById('dashboard-reporting-status-icon');

            const dbIdEl = document.getElementById('dashboard-reporting-db-id');
            const serviceEl = document.getElementById('dashboard-reporting-service');
            const usernameEl = document.getElementById('dashboard-reporting-username');

            const errorBox = document.getElementById('dashboard-reporting-error-box');
            const errorText = document.getElementById('dashboard-reporting-error-text');

            const repHost = activeDb ? (activeDb.rep_host || '-') : '-';
            const repPort = activeDb ? (activeDb.rep_port || '') : '';
            if (hostLabel) hostLabel.innerText = `Host: ${repHost}${repPort ? ':' + repPort : ''}`;
            if (dbIdEl) dbIdEl.innerText = activeDb ? (activeDb.rep_db_id || '-') : '-';
            if (serviceEl) serviceEl.innerText = activeDb ? (activeDb.rep_service_name || '-') : '-';
            if (usernameEl) usernameEl.innerText = activeDb ? (activeDb.rep_username || '-') : '-';

            if (status === 'UP') {
                if (badge) {
                    badge.innerText = 'Connected';
                    badge.className = 'pill-badge green';
                }
                if (label) label.innerText = 'Reporting database is reachable';
                if (icon) {
                    icon.innerText = '✅';
                    icon.style.background = '#ecfdf5';
                    icon.style.borderColor = '#10b981';
                }
                if (errorBox) errorBox.style.display = 'none';
            } else {
                if (badge) {
                    badge.innerText = 'Connection Error';
                    badge.className = 'pill-badge red';
                }
                if (label) label.innerText = 'Reporting Database offline';
                if (icon) {
                    icon.innerText = '❌';
                    icon.style.background = '#fef2f2';
                    icon.style.borderColor = '#ef4444';
                }
                if (errorBox) {
                    errorBox.style.display = 'block';
                    if (errorText) {
                        errorText.innerText = summary.reporting_error || 'Unable to establish a connection to the reporting database instance. Please check listener and login credentials.';
                    }
                }
            }
        })
        .catch(err => {
            console.error("Error checking reporting status:", err);
            const container = document.getElementById('reporting-details-card-container');
            if (container) container.style.display = 'none';
        });
}


// ✅ TABLESPACE STATUS (REDESIGNED PROGRESS BARS & ALL TABLESPACES MODAL)
let allTablespacesData = [];

function checkTablespace() {
    fetch('/tablespace-used')
        .then(res => res.json())
        .then(data => {
            console.log("Tablespace:", data);
            const listEl = document.getElementById("tablespace-list");
            if (!listEl) return;

            if (data.error) {
                listEl.innerHTML = `<div style="font-size: 0.8rem; color: #ef4444; text-align: center;">Error loading tablespaces</div>`;
                return;
            }

            const tablespaces = data.tablespaces || [];
            allTablespacesData = tablespaces; // store for show all modal

            if (tablespaces.length === 0) {
                listEl.innerHTML = `<div style="font-size: 0.8rem; color: #64748b; text-align: center;">No tablespaces found</div>`;
                return;
            }

            listEl.innerHTML = "";
            // Render only the first 4 tablespaces on the main card
            const visibleTablespaces = tablespaces.slice(0, 4);
            visibleTablespaces.forEach((ts, idx) => {
                const percent = Number(ts.used_percent || 0);
                const usedGb = Number(ts.used_gb || 0).toFixed(2);
                const freeGb = Number(ts.free_gb || 0).toFixed(2);
                const totalGb = Number(ts.total_gb || 0).toFixed(2);
                
                const getThresholdColor = percentValue => {
                    if (percentValue < 40) return "#22c55e"; // green
                    if (percentValue < 80) return "#eab308"; // yellow
                    return "#ef4444"; // red
                };
                const color = getThresholdColor(percent);

                // Add a border separator if it's not the last one
                const borderStyle = idx < visibleTablespaces.length - 1 ? 'border-bottom: 1px solid #f1f5f9; padding-bottom: 0.5rem;' : '';

                const itemHtml = `
                    <div style="${borderStyle} display: flex; flex-direction: column; gap: 0.25rem;">
                        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.78rem; line-height: 1;">
                            <span style="font-weight: 700; color: #0f172a; text-transform: uppercase;">${ts.tablespace_name}</span>
                            <span style="color: #475569; font-weight: 600;">${percent.toFixed(1)}%</span>
                        </div>
                        <div class="progress-track" style="height: 5px;">
                            <div class="progress-fill" style="width: ${percent}%; height: 100%; border-radius: 9999px; background-color: ${color}; transition: width 0.4s ease;"></div>
                        </div>
                        <div style="display: flex; justify-content: space-between; font-size: 0.7rem; color: #1e293b; font-weight: 600; margin-top: 0.1rem;">
                            <span>Used: ${usedGb} GB</span>
                            <span>Free: ${freeGb} GB</span>
                            <span>Total: ${totalGb} GB</span>
                        </div>
                    </div>
                `;
                listEl.insertAdjacentHTML('beforeend', itemHtml);
            });
        })
        .catch(err => {
            console.error("Tablespace Error:", err);
            const listEl = document.getElementById("tablespace-list");
            if (listEl) {
                listEl.innerHTML = `<div style="font-size: 0.8rem; color: #ef4444; text-align: center;">Error fetching data</div>`;
            }
        });
}

function showAllTablespacesModal() {
    const modal = document.getElementById("tablespaces-modal");
    const listEl = document.getElementById("modal-tablespace-list");
    if (!modal || !listEl) return;

    listEl.innerHTML = "";
    if (allTablespacesData.length === 0) {
        listEl.innerHTML = `<div style="color: #64748b; text-align: center; padding: 1rem 0; font-size: 0.9rem;">No tablespace data available</div>`;
    } else {
        allTablespacesData.forEach(ts => {
            const percent = Number(ts.used_percent || 0);
            const usedGb = Number(ts.used_gb || 0).toFixed(2);
            const freeGb = Number(ts.free_gb || 0).toFixed(2);
            const totalGb = Number(ts.total_gb || 0).toFixed(2);

            const getThresholdColor = percentValue => {
                if (percentValue < 40) return "#22c55e";
                if (percentValue < 80) return "#eab308";
                return "#ef4444";
            };
            const color = getThresholdColor(percent);

            const itemHtml = `
                <div style="border-bottom: 1px solid #f1f5f9; padding-bottom: 0.75rem;">
                    <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.88rem; margin-bottom: 0.35rem; line-height: 1;">
                        <span style="font-weight: 700; color: #0f172a; text-transform: uppercase;">${ts.tablespace_name}</span>
                        <span style="color: #475569; font-weight: 600;">${percent.toFixed(1)}%</span>
                    </div>
                    <div class="progress-track" style="height: 6px; margin-bottom: 0.35rem;">
                        <div class="progress-fill" style="width: ${percent}%; height: 100%; border-radius: 9999px; background-color: ${color};"></div>
                    </div>
                    <div style="display: flex; justify-content: space-between; font-size: 0.75rem; color: #1e293b; font-weight: 600;">
                        <span>Used: ${usedGb} GB</span>
                        <span>Free: ${freeGb} GB</span>
                        <span>Total: ${totalGb} GB</span>
                    </div>
                </div>
            `;
            listEl.insertAdjacentHTML('beforeend', itemHtml);
        });
    }

    modal.style.display = "flex";
}

function closeTablespacesModal() {
    const modal = document.getElementById("tablespaces-modal");
    if (modal) modal.style.display = "none";
}

function showAllMountPointsModal() {
    const modal = document.getElementById("mountpoints-modal");
    const listEl = document.getElementById("modal-mountpoint-list");
    if (!modal || !listEl) return;

    listEl.innerHTML = "";
    if (allDisksData.length === 0) {
        listEl.innerHTML = `<div style="color: #64748b; text-align: center; padding: 1rem 0; font-size: 0.9rem;">No mount point data available</div>`;
    } else {
        allDisksData.forEach(disk => {
            const percent = Number(disk.percent || 0);
            const usedGb = Number(disk.used_gb || 0).toFixed(2);
            const freeGb = Number(disk.free_gb || 0).toFixed(2);
            const totalGb = Number(disk.total_gb || 0).toFixed(2);
            const isVirtual = disk.type === 'virtual';

            const getThresholdColor = percentValue => {
                if (isVirtual) return "#94a3b8"; // grey for virtual
                if (percentValue < 75) return "#22c55e"; // green
                if (percentValue < 90) return "#f59e0b"; // orange
                return "#ef4444"; // red
            };
            const color = getThresholdColor(percent);

            const badgeHtml = isVirtual ? 
                `<span class="pill-badge" style="background-color: #f1f5f9; color: #64748b; font-size: 0.65rem; font-weight: 700; padding: 1px 5px; margin-left: 0.5rem; text-transform: uppercase;">Virtual</span>` : 
                `<span class="pill-badge" style="background-color: #ecfdf5; color: #065f46; font-size: 0.65rem; font-weight: 700; padding: 1px 5px; margin-left: 0.5rem; text-transform: uppercase;">Physical</span>`;

            const itemHtml = `
                <div style="border-bottom: 1px solid #f1f5f9; padding-bottom: 0.75rem;">
                    <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.88rem; margin-bottom: 0.35rem; line-height: 1;">
                        <span style="font-weight: 700; color: #0f172a; display: flex; align-items: center;">
                            ${disk.mount}
                            ${badgeHtml}
                        </span>
                        <span style="color: #475569; font-weight: 600;">${percent.toFixed(1)}%</span>
                    </div>
                    <div class="progress-track" style="height: 6px; margin-bottom: 0.35rem; background-color: #e2e8f0; border-radius: 9999px; overflow: hidden;">
                        <div class="progress-fill" style="width: ${percent}%; height: 100%; border-radius: 9999px; background-color: ${color}; transition: width 0.4s ease;"></div>
                    </div>
                    <div style="display: flex; justify-content: space-between; font-size: 0.75rem; color: #475569; font-weight: 600;">
                        <span>Used: ${usedGb} GB</span>
                        <span>Free: ${freeGb} GB</span>
                        <span>Total: ${totalGb} GB</span>
                    </div>
                </div>
            `;
            listEl.insertAdjacentHTML('beforeend', itemHtml);
        });
    }

    modal.style.display = "flex";
}

function closeMountPointsModal() {
    const modal = document.getElementById("mountpoints-modal");
    if (modal) modal.style.display = "none";
}
function checkSessions() {
    fetch('/api/sessions/count')
        .then(res => res.json())
        .then(data => {
            console.log("Sessions:", data);

            const countEl = document.getElementById("session-count-pill");
            const totalCountEl = document.getElementById("total-session-count-pill");

            if (!countEl) return;

            if (data.error) {
                countEl.innerText = "Error";
                if (totalCountEl) {
                    totalCountEl.innerText = "Error";
                }
                updateSessionChart(0, 0);
                return;
            }

            const total = data.total || 0;
            const activeCount = (data.breakdown || []).reduce((sum, item) => {
                return sum + ((String(item.status || '').toUpperCase() === 'ACTIVE') ? (item.count || 0) : 0);
            }, 0);

            countEl.innerText = `${activeCount} Active`;
            if (totalCountEl) {
                totalCountEl.innerText = `${total} Total`;
            }

            updateSessionChart(total, activeCount);
        })
        .catch(err => {
            console.error("Session Error:", err);
            const countEl = document.getElementById("session-count-pill");
            if (countEl) countEl.innerText = "Error";
            const totalCountEl = document.getElementById("total-session-count-pill");
            if (totalCountEl) totalCountEl.innerText = "Error";
            updateSessionChart(0, 0);
        });
}

function updateSessionChart(total, activeCount) {
    const now = new Date();
    let hours = now.getHours();
    let minutes = now.getMinutes();
    const ampm = hours >= 12 ? 'pm' : 'am';
    hours = hours % 12;
    hours = hours ? hours : 12;
    minutes = minutes < 10 ? '0' + minutes : minutes;
    const currentLabel = `${hours}:00 ${ampm}`;

    // If it's a new hour, shift and push to move the 24-hour window forward
    if (sessionHistory.labels.length > 0 && sessionHistory.labels[sessionHistory.labels.length - 1] !== currentLabel) {
        sessionHistory.labels.shift();
        sessionHistory.total.shift();
        sessionHistory.active.shift();
        
        sessionHistory.labels.push(currentLabel);
        sessionHistory.total.push(total);
        sessionHistory.active.push(activeCount);
    } else if (sessionHistory.labels.length > 0) {
        // Otherwise, update the current hour's values
        sessionHistory.total[sessionHistory.total.length - 1] = total;
        sessionHistory.active[sessionHistory.active.length - 1] = activeCount;
    } else {
        sessionHistory.labels.push(currentLabel);
        sessionHistory.total.push(total);
        sessionHistory.active.push(activeCount);
    }

    const chartEl = document.getElementById("sessionChart");
    if (!chartEl) return;

    const chartData = {
        labels: [...sessionHistory.labels], // Keep original time labels for accurate tooltip displays
        datasets: [
            {
                label: 'Active',
                data: [...sessionHistory.active],
                borderColor: '#25a576',
                backgroundColor: 'rgba(37, 165, 118, 0.08)',
                tension: 0.3,
                fill: true,
                pointRadius: 4,
                pointHitRadius: 15, // Increase touch/tap target radius for mobile users
                pointHoverRadius: 6,
                pointBackgroundColor: '#25a576',
                pointBorderColor: '#ffffff',
                pointBorderWidth: 1.5,
                borderWidth: 2.5,
                order: 1
            },
            {
                label: 'Total (ref.)',
                data: [...sessionHistory.total],
                borderColor: '#94a3b8',
                borderWidth: 1.5,
                borderDash: [5, 5],
                fill: false,
                pointRadius: 0,
                pointHitRadius: 15,
                pointHoverRadius: 4,
                order: 2
            }
        ]
    };

    if (sessionChart) {
        sessionChart.data.labels = [...sessionHistory.labels];
        sessionChart.data.datasets[0].data = [...sessionHistory.active];
        sessionChart.data.datasets[1].data = [...sessionHistory.total];
        sessionChart.update();
    } else {
        sessionChart = new Chart(chartEl, {
            type: 'line',
            data: chartData,
            options: {
                responsive: true,
                maintainAspectRatio: false,
                legend: { display: false },
                plugins: {
                    legend: { display: false },
                    title: { display: false },
                    tooltip: {
                        mode: 'index',
                        intersect: false,
                        backgroundColor: 'rgba(15, 23, 42, 0.9)', // Modern premium dark slate style
                        titleColor: '#ffffff',
                        titleFont: { size: 11, weight: 'bold', family: 'system-ui' },
                        bodyColor: '#e2e8f0',
                        bodyFont: { size: 10, family: 'system-ui' },
                        borderColor: '#334155',
                        borderWidth: 1,
                        padding: 8,
                        cornerRadius: 6,
                        displayColors: true,
                        callbacks: {
                            title: function(context) {
                                const index = context[0].dataIndex;
                                return 'Time: ' + (sessionHistory.labels[index] || '');
                            },
                            label: function(context) {
                                let label = context.dataset.label || '';
                                if (label) {
                                    label += ': ';
                                }
                                if (context.parsed.y !== null) {
                                    label += context.parsed.y + ' users';
                                }
                                return label;
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: {
                            color: '#64748b',
                            font: { size: 10, weight: '500' },
                            callback: function(val, index) {
                                // Draw X-axis text selectively at sparse intervals
                                if (index === sessionHistory.labels.length - 1) return 'now';
                                if (index === 0) return '24h ago';
                                if (index === 6) return '18h ago';
                                if (index === 12) return '12h ago';
                                if (index === 18) return '6h ago';
                                return '';
                            }
                        },
                        border: { display: false },
                        title: {
                            display: true,
                            text: 'Time (24 Hours)',
                            color: '#64748b',
                            font: { size: 10, weight: '600' }
                        }
                    },
                    y: {
                        display: true,
                        beginAtZero: true,
                        grid: {
                            color: '#f1f5f9',
                            drawBorder: false,
                            drawTicks: false
                        },
                        border: { display: false },
                        ticks: {
                            display: true,
                            color: '#64748b',
                            precision: 0,
                            font: { size: 10 }
                        },
                        title: {
                            display: true,
                            text: 'Session Count',
                            color: '#64748b',
                            font: { size: 10, weight: '600' }
                        }
                    }
                },
                plugins: [{
                    id: 'dataLabelsPlugin',
                    afterDatasetsDraw(chart) {
                        const {ctx, data} = chart;
                        ctx.save();
                        ctx.font = 'bold 11px sans-serif';
                        ctx.fillStyle = '#25a576';
                        ctx.textAlign = 'center';
                        ctx.textBaseline = 'bottom';
                        
                        chart.getDatasetMeta(0).data.forEach((datapoint, index) => {
                            if (index === 0 || index === 6 || index === 12 || index === 18 || index === 23) {
                                const value = data.datasets[0].data[index];
                                ctx.fillText(value, datapoint.x, datapoint.y - 8);
                            }
                        });
                        ctx.restore();
                    }
                }]
            }
        });
    }
}

function checkDbGrowth() {
    fetch('/api/db-growth')
        .then(res => res.json())
        .then(data => {
            console.log("DB Growth:", data);

            const titleEl = document.getElementById('growth-card-title');
            const usedSpaceEl = document.getElementById('growth-used-space');
            const usedPercentEl = document.getElementById('growth-used-percent-label');
            const progressEl = document.getElementById('growth-progress');
            const totalSpaceEl = document.getElementById('growth-total-space');
            const freeSpaceEl = document.getElementById('growth-free-space');

            const dayValEl = document.getElementById('growth-day-value');
            const weekValEl = document.getElementById('growth-week-value');
            const monthValEl = document.getElementById('growth-month-value');
            const yearValEl = document.getElementById('growth-year-value');

            const barDay = document.getElementById('growth-bar-day');
            const barWeek = document.getElementById('growth-bar-week');
            const barMonth = document.getElementById('growth-bar-month');
            const barYear = document.getElementById('growth-bar-year');

            if (!usedSpaceEl || !usedPercentEl || !progressEl || !totalSpaceEl || !freeSpaceEl) return;

            if (data.error) {
                if (titleEl) titleEl.innerText = 'DATABASE GROWTH';
                usedSpaceEl.innerText = 'N/A';
                usedPercentEl.innerText = '0% used';
                progressEl.style.width = '0%';
                totalSpaceEl.innerText = '0.00 MB';
                freeSpaceEl.innerText = '0.00 MB';

                [dayValEl, weekValEl, monthValEl, yearValEl].forEach(el => {
                    if (el) el.innerText = 'N/A';
                });
                [barDay, barWeek, barMonth, barYear].forEach(el => {
                    if (el) el.style.width = '0%';
                });
                return;
            }

            const activeDbName = data.database_name || 'ORCL';
            if (titleEl) titleEl.innerText = `DATABASE GROWTH · ${activeDbName}`;

            const totalMb = Number(data.total_mb) || 0;
            const usedMb = Number(data.used_mb) || 0;
            const freeMb = Math.max(0, totalMb - usedMb);
            const percent = Math.min(Math.max(Number(data.used_percent) || 0, 0), 100);

            // Format main metrics
            usedSpaceEl.innerText = `${usedMb.toFixed(2)} MB`;
            usedPercentEl.innerText = `${percent.toFixed(1)}% used`;
            progressEl.style.width = `${percent}%`;
            totalSpaceEl.innerText = `${totalMb.toFixed(2)} MB`;
            freeSpaceEl.innerText = `${freeMb.toFixed(2)} MB`;

            // Format period values to match mockup (0, 1, or 2 decimal places based on magnitude)
            const formatVal = (val) => {
                const num = Number(val || 0);
                if (num >= 100) {
                    return `${Math.round(num)} MB`;
                } else if (num >= 10) {
                    return `${num.toFixed(1)} MB`;
                }
                return `${num.toFixed(2)} MB`;
            };

            const dayVal = Number(data.growth_day_mb) || 0;
            const weekVal = Number(data.growth_week_mb) || 0;
            const monthVal = Number(data.growth_month_mb) || 0;
            const yearVal = Number(data.growth_year_mb) || 0;

            if (dayValEl) dayValEl.innerText = formatVal(dayVal);
            if (weekValEl) weekValEl.innerText = formatVal(weekVal);
            if (monthValEl) monthValEl.innerText = formatVal(monthVal);
            if (yearValEl) yearValEl.innerText = formatVal(yearVal);

            // Set row widths dynamically based on the max growth trend value
            const maxGrowth = Math.max(dayVal, weekVal, monthVal, yearVal);
            const calcWidth = (val) => {
                if (maxGrowth <= 0) return '0%';
                // Scale width between 0% and 100% (proportional)
                return `${(val / maxGrowth) * 100}%`;
            };

            if (barDay) barDay.style.width = calcWidth(dayVal);
            if (barWeek) barWeek.style.width = calcWidth(weekVal);
            if (barMonth) barMonth.style.width = calcWidth(monthVal);
            if (barYear) barYear.style.width = calcWidth(yearVal);
        })
        .catch(err => {
            console.error('DB Growth Error:', err);
            const usedSpaceEl = document.getElementById('growth-used-space');
            if (usedSpaceEl) usedSpaceEl.innerText = 'Error';
        });}

function checkBackup() {
    Promise.all([
        fetch('/api/rman/latest').then(res => res.json()),
        fetch('/api/rman/history').then(res => res.json())
    ])
    .then(([latest, historyData]) => {
        console.log("RMAN Latest:", latest);
        console.log("RMAN History:", historyData);

        const cardContainer = document.getElementById("backup-card-container");
        const statusIconContainer = document.getElementById("backup-status-icon-container");
        const statusTitle = document.getElementById("backup-status-title");
        const statusMeta = document.getElementById("backup-status-meta");
        const typeBadge = document.getElementById("backup-type-badge");
        const infoEl = document.getElementById("backup-info");
        const canvasCtx = document.getElementById("backupChart");

        if (!statusTitle || !statusMeta || !statusIconContainer || !canvasCtx) return;

        // Reset info warning box
        if (infoEl) infoEl.style.display = 'none';

        // Months mapping for parsing e.g. "15-MAY 11:49" or "15-MAY-2026"
        const months = {
            'JAN': 0, 'FEB': 1, 'MAR': 2, 'APR': 3, 'MAY': 4, 'JUN': 5,
            'JUL': 6, 'AUG': 7, 'SEP': 8, 'OCT': 9, 'NOV': 10, 'DEC': 11
        };

        const parseBackupDate = (dateStr) => {
            if (!dateStr) return new Date();
            const currentYear = new Date().getFullYear();
            try {
                // Handle formats like "15-MAY 11:49" or "15-MAY-26" or "15-MAY-2026 11:49"
                const cleanStr = dateStr.replace(/-/g, ' '); // "15 MAY 11:49" or "15 MAY 2026"
                const parts = cleanStr.split(' ');
                const day = parseInt(parts[0]);
                const month = months[parts[1].toUpperCase()];

                let year = currentYear;
                if (parts.length > 2 && parts[2].length >= 2 && !parts[2].includes(':')) {
                    const parsedYear = parseInt(parts[2]);
                    year = parsedYear < 100 ? 2000 + parsedYear : parsedYear;
                }

                return new Date(year, month, day);
            } catch (e) {
                return new Date();
            }
        };

        const BACKUP_STALE_AFTER_DAYS = 7;

        // 1. Process latest backup
        if (latest.error) {
            const isNotFound = latest.error === 'No Backup Found';
            if (isNotFound) {
                statusTitle.innerText = 'No Backup Found';
                statusTitle.style.color = '#64748b';
                statusMeta.innerText = 'No recent RMAN backups recorded';
                statusIconContainer.innerHTML = `
                    <div class="backup-status-circle unknown">
                        <span>—</span>
                    </div>
                `;
                if (cardContainer) cardContainer.style.borderTopColor = '#cbd5e1';
            } else {
                statusTitle.innerText = 'Unavailable';
                statusTitle.style.color = '#d97706';
                statusMeta.innerText = 'Backup info temporarily unavailable';
                statusIconContainer.innerHTML = `
                    <div class="backup-status-circle warning">
                        <span>!</span>
                    </div>
                `;
                if (cardContainer) cardContainer.style.borderTopColor = '#f59e0b';
            }
            if (typeBadge) typeBadge.style.display = 'none';
        } else {
            const status = (latest.status || '').toUpperCase();
            let stateClass = 'unknown';
            let stateIcon = '?';
            let titleText = latest.status || 'Unknown';
            let titleColor = '#475569';
            let cardBorderColor = '#cbd5e1';

            const backupDate = latest.start_time ? parseBackupDate(latest.start_time) : null;
            const daysSinceBackup = backupDate ? (Date.now() - backupDate.getTime()) / (1000 * 60 * 60 * 24) : Infinity;
            const isStale = daysSinceBackup > BACKUP_STALE_AFTER_DAYS;

            if ((status === 'COMPLETED' || status === 'SUCCESS') && isStale) {
                stateClass = 'danger';
                stateIcon = '✗';
                titleText = 'Not Completed';
                titleColor = '#b91c1c';
                cardBorderColor = '#ef4444';
            } else if (status === 'COMPLETED' || status === 'SUCCESS') {
                stateClass = 'success';
                stateIcon = '✓';
                titleText = 'Completed';
                titleColor = '#15803d';
                cardBorderColor = '#22c55e';
            } else if (status === 'RUNNING' || status === 'IN PROGRESS' || status === 'STARTED') {
                stateClass = 'warning';
                stateIcon = '⟳';
                titleText = 'Running';
                titleColor = '#b45309';
                cardBorderColor = '#f59e0b';
            } else if (status && status !== 'COMPLETED') {
                stateClass = 'danger';
                stateIcon = '✗';
                titleText = 'Failed';
                titleColor = '#b91c1c';
                cardBorderColor = '#ef4444';
            }

            statusTitle.innerText = titleText;
            statusTitle.style.color = titleColor;

            statusIconContainer.innerHTML = `
                <div class="backup-status-circle ${stateClass}">
                    <span style="font-family: inherit; line-height: 1;">${stateIcon}</span>
                </div>
            `;

            // Format timestamp: "15 May 2026, 11:49 · 1577.09 MB"
            const startTimeStr = latest.start_time || 'N/A';
            const sizeMb = latest.size_mb ? Number(latest.size_mb).toFixed(2) : '0.00';
            statusMeta.innerText = `${startTimeStr} · ${sizeMb} MB`;

            // Badge
            if (typeBadge) {
                if (latest.input_type) {
                    typeBadge.innerText = latest.input_type;
                    typeBadge.style.display = 'block';
                } else {
                    typeBadge.style.display = 'none';
                }
            }
        }

        // 2. Process last 7 days history
        const backups = historyData.backups || [];

        // Determine reference date (end date for the past 7 days)
        let referenceDate = new Date();
        if (latest && latest.start_time) {
            referenceDate = parseBackupDate(latest.start_time);
        }

        // Generate consecutive 7 days ending at referenceDate
        const last7Days = [];
        for (let i = 6; i >= 0; i--) {
            const d = new Date(referenceDate);
            d.setDate(referenceDate.getDate() - i);
            last7Days.push(d);
        }

        // Calculate size for each day
        const chartLabels = [];
        const chartDataValues = [];
        const backgroundColors = [];

        last7Days.forEach((dayDate, idx) => {
            // Label format: "15 May"
            const labelStr = dayDate.toLocaleDateString('en-US', { day: 'numeric', month: 'short' });
            chartLabels.push(labelStr);

            // Find backups on this date
            const sameDayBackups = backups.filter(b => {
                const bDate = parseBackupDate(b.start_time);
                return bDate.getFullYear() === dayDate.getFullYear() &&
                       bDate.getMonth() === dayDate.getMonth() &&
                       bDate.getDate() === dayDate.getDate();
            });

            const totalMbOnDay = sameDayBackups.reduce((sum, b) => sum + (Number(b.size_mb) || 0), 0);
            chartDataValues.push(totalMbOnDay);

            // Highlight the latest/active backup day (the last day index 6) in bright/vibrant green, others in soft mint green
            if (idx === 6) {
                backgroundColors.push('#10b981'); // Vibrant emerald green
            } else {
                backgroundColors.push('#a7f3d0'); // Soft light green
            }
        });

        // 3. Render/Update Chart.js
        if (backupChart) {
            backupChart.destroy();
        }

        backupChart = new Chart(canvasCtx, {
            type: 'bar',
            data: {
                labels: chartLabels,
                datasets: [{
                    label: 'Backup Size (MB)',
                    data: chartDataValues,
                    backgroundColor: backgroundColors,
                    borderRadius: 4,
                    barThickness: 24
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                return `Size: ${context.raw.toFixed(2)} MB`;
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        grid: {
                            color: '#f1f5f9',
                            drawBorder: false
                        },
                        ticks: {
                            color: '#64748b',
                            font: { size: 10 }
                        },
                        title: {
                            display: true,
                            text: 'MB',
                            color: '#64748b',
                            font: { size: 10, weight: '700' }
                        }
                    },
                    x: {
                        grid: { display: false },
                        ticks: {
                            color: '#64748b',
                            font: { size: 10, weight: '600' }
                        },
                        title: {
                            display: true,
                            text: 'Date',
                            color: '#64748b',
                            font: { size: 10, weight: '700' }
                        }
                    }
                }
            }
        });
    })
    .catch(err => {
        console.error("Backup Parallel Fetch Error:", err);
        const statusTitle = document.getElementById("backup-status-title");
        if (statusTitle) {
            statusTitle.innerText = "Error";
            statusTitle.style.color = "#ef4444";
        }
    });
}

function checkBackupHistory() {
    fetch('/api/rman/history')
        .then(res => res.json())
        .then(data => {
            console.log("Backup History:", data);

            const ctx = document.getElementById('backupChart');
            if (!ctx) return;

            if (data.error || !data.backups || data.backups.length === 0) {
                if (backupChart) backupChart.destroy();
                backupChart = new Chart(ctx, {
                    type: 'bar',
                    data: {
                        labels: ['No Backups (Last 14 Days)'],
                        datasets: [{
                            label: 'Backup Size (MB)',
                            data: [0],
                            backgroundColor: '#cbd5e1',
                            borderColor: '#cbd5e1',
                            borderWidth: 1
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: { legend: { display: false } },
                        scales: {
                            y: { beginAtZero: true, title: { display: true, text: 'Size (MB)' } },
                            x: { title: { display: true, text: 'Time' } }
                        }
                    }
                });
                return;
            }

            const labels = data.backups.map(b => b.start_time);
            const sizes = data.backups.map(b => b.size_mb);
            const statuses = data.backups.map(b => b.status);

            const backgroundColors = statuses.map(status => {
                const normalized = (status || '').toUpperCase();
                if (normalized === 'COMPLETED' || normalized === 'SUCCESS') return '#10b981';
                if (normalized === 'FAILED') return '#ef4444';
                return '#f59e0b';
            });

            const chartData = {
                labels: labels,
                datasets: [{
                    label: 'Backup Size (MB)',
                    data: sizes,
                    backgroundColor: backgroundColors,
                    borderRadius: 4,
                    minBarLength: 8
                }]
            };

            const chartOptions = {
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                const index = context.dataIndex;
                                const backup = data.backups[index];
                                return [
                                    `Size: ${backup.size_mb} MB`,
                                    `Status: ${backup.status}`,
                                    `Type: ${backup.input_type || 'N/A'}`,
                                    `Session: ${backup.session_key}`
                                ];
                            }
                        }
                    }
                },
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        title: {
                            display: true,
                            text: 'Size (MB)'
                        }
                    },
                    x: {
                        title: {
                            display: false,
                            text: 'Backup Time'
                        },
                        ticks: {
                            maxRotation: 45,
                            minRotation: 45
                        }
                    }
                }
            };

            if (backupChart) {
                backupChart.destroy();
            }
            backupChart = new Chart(ctx, {
                type: 'bar',
                data: chartData,
                options: chartOptions
            });

            // Populate compact backup history list (top 3 recent)
            const historyEl = document.getElementById('backup-history-list');
            if (historyEl) {
                historyEl.innerHTML = '';
                const recent = data.backups.slice(0, 3);
                recent.forEach(b => {
                    const statusNorm = (b.status || '').toUpperCase();
                    let statusColor = '#f59e0b';
                    if (statusNorm === 'COMPLETED' || statusNorm === 'SUCCESS') statusColor = '#10b981';
                    if (statusNorm === 'FAILED') statusColor = '#ef4444';

                    const item = document.createElement('div');
                    item.className = 'backup-history-item';
                    item.innerHTML = `
                        <div style="display:flex;flex-direction:column;min-width:0;">
                            <div class="backup-history-meta" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${b.start_time || 'Unknown'}</div>
                            <div style="font-size:0.65rem;color:#94a3b8;">${b.input_type || ''} ${b.session_key ? '• ' + b.session_key : ''}</div>
                        </div>
                        <div style="display:flex;flex-direction:column;align-items:flex-end;">
                            <div class="backup-history-status" style="color:${statusColor};">${b.size_mb || 0} MB</div>
                            <div style="font-size:0.66rem;color:#64748b;">${b.status || ''}</div>
                        </div>
                    `;
                    historyEl.appendChild(item);
                });
            }
        })
        .catch(err => {
            console.error('Backup History Error:', err);
            if (backupChart) {
                backupChart.destroy();
                backupChart = null;
            }
        });
}

// ✅ Global state variables for CPU session selection & context menu
let currentSelectedCpuSession = null;
let currentContextMenuSession = null;

// ✅ Helper to update Query Viewer with SID and Active Duration badges
function updateCpuQueryViewer(session, queryBoxId, sidBadgeId, activeBadgeId, statusBadgeId) {
    if (!session) return;
    currentSelectedCpuSession = session;

    const queryBox = document.getElementById(queryBoxId);
    const sidBadge = document.getElementById(sidBadgeId);
    const activeBadge = document.getElementById(activeBadgeId);
    const statusBadge = statusBadgeId ? document.getElementById(statusBadgeId) : null;

    if (queryBox && session.sql_text) {
        queryBox.value = session.sql_text;
    }
    if (sidBadge && session.sid !== undefined) {
        sidBadge.innerText = `SID: ${session.sid}`;
        sidBadge.style.display = 'inline-block';
    }
    if (activeBadge) {
        const timeStr = session.active_time || (session.cpu_usage !== undefined ? `${session.cpu_usage.toFixed(2)}s` : 'N/A');
        activeBadge.innerText = `Active: ${timeStr}`;
        activeBadge.style.display = 'inline-block';
    }
    if (statusBadge) {
        const status = session.status || 'N/A';
        statusBadge.innerText = `Status: ${status}`;
        const isActive = status.toUpperCase() === 'ACTIVE';
        statusBadge.style.background = isActive ? '#dcfce7' : '#e2e8f0';
        statusBadge.style.color = isActive ? '#15803d' : '#475569';
        statusBadge.style.borderColor = isActive ? '#bbf7d0' : '#cbd5e1';
        statusBadge.style.display = 'inline-block';
    }
}

// ✅ Kill Session Custom Modal & API Action
let pendingKillSessionPromiseResolver = null;

function showKillConfirmModal(session) {
    return new Promise((resolve) => {
        pendingKillSessionPromiseResolver = resolve;

        const modal = document.getElementById('kill-session-confirm-modal');
        const sidEl = document.getElementById('kill-modal-sid');
        const serialEl = document.getElementById('kill-modal-serial');
        const userEl = document.getElementById('kill-modal-user');

        if (sidEl) sidEl.innerText = session.sid !== undefined ? session.sid : '-';
        if (serialEl) serialEl.innerText = session.serial !== undefined ? session.serial : '-';
        if (userEl) userEl.innerText = session.username || 'User / Unknown';

        if (modal) modal.style.display = 'flex';
    });
}

function closeKillConfirmModal(isConfirmed) {
    const modal = document.getElementById('kill-session-confirm-modal');
    if (modal) modal.style.display = 'none';

    if (pendingKillSessionPromiseResolver) {
        pendingKillSessionPromiseResolver(isConfirmed);
        pendingKillSessionPromiseResolver = null;
    }
}

// ✅ Custom Styled Notification Result Modal
function showCustomNotificationModal(message, isSuccess = true) {
    const modal = document.getElementById('kill-session-result-modal');
    const header = document.getElementById('result-modal-header');
    const title = document.getElementById('result-modal-title');
    const subtitle = document.getElementById('result-modal-subtitle');
    const messageEl = document.getElementById('result-modal-message');
    const card = document.getElementById('result-modal-card');
    const iconSuccess = document.getElementById('result-modal-icon-success');
    const iconError = document.getElementById('result-modal-icon-error');
    const btn = document.getElementById('result-modal-ok-btn');

    if (messageEl) messageEl.innerText = message;

    if (isSuccess) {
        if (header) header.style.background = 'linear-gradient(135deg, #10b981 0%, #047857 100%)';
        if (title) title.innerText = 'Session Killed Successfully';
        if (subtitle) subtitle.innerText = 'Oracle Database Execution Success';
        if (card) {
            card.style.background = '#ecfdf5';
            card.style.borderColor = '#a7f3d0';
            card.style.color = '#065f46';
        }
        if (iconSuccess) iconSuccess.style.display = 'block';
        if (iconError) iconError.style.display = 'none';
        if (btn) {
            btn.style.background = 'linear-gradient(135deg, #10b981 0%, #059669 100%)';
            btn.style.boxShadow = '0 4px 10px rgba(16, 185, 129, 0.25)';
            btn.innerText = 'OK, Done';
        }
    } else {
        if (header) header.style.background = 'linear-gradient(135deg, #ef4444 0%, #b91c1c 100%)';
        if (title) title.innerText = 'Failed to Kill Session';
        if (subtitle) subtitle.innerText = 'Oracle Database Execution Error';
        if (card) {
            card.style.background = '#fef2f2';
            card.style.borderColor = '#fca5a5';
            card.style.color = '#991b1b';
        }
        if (iconSuccess) iconSuccess.style.display = 'none';
        if (iconError) iconError.style.display = 'block';
        if (btn) {
            btn.style.background = 'linear-gradient(135deg, #dc2626 0%, #b91c1c 100%)';
            btn.style.boxShadow = '0 4px 10px rgba(220, 38, 38, 0.25)';
            btn.innerText = 'Close';
        }
    }

    if (modal) modal.style.display = 'flex';
}

function closeCustomNotificationModal() {
    const modal = document.getElementById('kill-session-result-modal');
    if (modal) modal.style.display = 'none';
}

async function killCurrentSelectedSession(sessionOverride = null) {
    const targetSession = sessionOverride || currentSelectedCpuSession;
    if (!targetSession || targetSession.sid === undefined || targetSession.serial === undefined) {
        showCustomNotificationModal("No active session selected to kill.", false);
        return;
    }

    const isConfirmed = await showKillConfirmModal(targetSession);
    if (!isConfirmed) {
        return;
    }

    const sid = targetSession.sid;
    const serial = targetSession.serial;

    fetch('/api/sessions/kill', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sid: sid, serial: serial })
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success') {
            showCustomNotificationModal(data.message || `Session (SID: ${sid}, Serial#: ${serial}) killed successfully.`, true);
            checkSessionCPU();
            checkSessionCPU24h();
            if (typeof fetchOracleDatabaseProcesses === 'function') fetchOracleDatabaseProcesses();
        } else {
            showCustomNotificationModal(data.message || "Failed to kill session.", false);
        }
    })
    .catch(err => {
        console.error("Kill Session Error:", err);
        showCustomNotificationModal(`Failed to send kill command: ${err.message || err}`, false);
    });
}

// ✅ Session Context Menu Helpers
function showSessionContextMenu(x, y, session) {
    currentContextMenuSession = session;
    const menu = document.getElementById('session-context-menu');
    const header = document.getElementById('session-context-header');
    const killLabel = document.getElementById('session-context-kill-label');
    if (!menu) return;

    if (header) {
        header.innerText = `SID: ${session.sid} (Serial #${session.serial}) · ${session.username || ''}`;
    }
    if (killLabel) {
        killLabel.innerText = `Kill Session (SID: ${session.sid})`;
    }

    menu.style.display = 'block';
    const menuWidth = menu.offsetWidth || 230;
    const menuHeight = menu.offsetHeight || 120;
    
    let posX = x;
    let posY = y;
    
    if (posX + menuWidth > window.innerWidth) {
        posX = window.innerWidth - menuWidth - 10;
    }
    if (posY + menuHeight > window.innerHeight) {
        posY = window.innerHeight - menuHeight - 10;
    }

    menu.style.left = `${posX}px`;
    menu.style.top = `${posY}px`;
}

function hideSessionContextMenu() {
    const menu = document.getElementById('session-context-menu');
    if (menu) menu.style.display = 'none';
}

function executeKillFromContextMenu() {
    hideSessionContextMenu();
    if (currentContextMenuSession) {
        killCurrentSelectedSession(currentContextMenuSession);
    }
}

function executeCopyFromContextMenu() {
    hideSessionContextMenu();
    if (currentContextMenuSession && currentContextMenuSession.sql_text) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(currentContextMenuSession.sql_text).then(() => {
                alert("SQL query copied to clipboard.");
            }).catch(() => {
                copyActiveQuery();
            });
        } else {
            copyActiveQuery();
        }
    }
}

document.addEventListener('click', (e) => {
    const menu = document.getElementById('session-context-menu');
    if (menu && menu.style.display === 'block') {
        if (!menu.contains(e.target)) {
            hideSessionContextMenu();
        }
    }
});

// ✅ SESSION CPU CHART
function checkSessionCPU() {
    fetch('/api/sessions/cpu-usage')
        .then(res => res.json())
        .then(data => {
            console.log("Session CPU:", data);

            const chartEl = document.getElementById("sessionCpuChart");
            if (!chartEl) return;

            if (data.error || !data.sessions || data.sessions.length === 0) {
                if (sessionCpuChart) sessionCpuChart.destroy();
                sessionCpuChart = new Chart(chartEl, {
                    type: 'bar',
                    data: {
                        labels: ['No CPU Sessions'],
                        datasets: [{
                            label: 'CPU Usage (s)',
                            data: [0],
                            backgroundColor: '#cbd5e1'
                        }]
                    },
                    options: {
                        indexAxis: 'y',
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: { legend: { display: false } },
                        scales: { x: { beginAtZero: true } }
                    }
                });
                return;
            }

            const labels = data.sessions.map(s => `${s.sid} · ${s.username} · ${s.status || 'N/A'}`);
            const cpuData = data.sessions.map(s => s.cpu_usage);

            const bgColors = data.sessions.map((session) => {
                if (session.is_deadlock) return '#ef4444'; // Red for deadlocks
                if (session.cpu_usage >= 0.10) return '#eea638'; // Yellow/orange for high CPU usage (>= 0.10s)
                return '#25a576'; // Green for less CPU usage (< 0.10s)
            });

            const chartData = {
                labels: labels,
                datasets: [{
                    label: 'CPU Usage (seconds)',
                    data: cpuData,
                    backgroundColor: bgColors,
                    borderRadius: 4,
                    barPercentage: 0.5,
                    categoryPercentage: 0.8
                }]
            };

            if (data.sessions && data.sessions.length > 0) {
                updateCpuQueryViewer(data.sessions[0], 'cpu-query-box', 'cpu-query-sid-badge', 'cpu-query-active-badge', 'cpu-query-status-badge');
            }

            const chartOptions = {
                indexAxis: 'y', // Horizontal bar chart
                onHover: (event, chartElements) => {
                    if (chartElements && chartElements.length > 0) {
                        const index = chartElements[0].index;
                        const session = data.sessions[index];
                        if (session) {
                            updateCpuQueryViewer(session, 'cpu-query-box', 'cpu-query-sid-badge', 'cpu-query-active-badge', 'cpu-query-status-badge');
                        }
                    }
                },
                plugins: {
                    legend: { display: false },
                    tooltip: { enabled: false }
                },
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: {
                        display: true, // Need true to show grid lines and ticks
                        beginAtZero: true,
                        grid: {
                            display: true,
                            color: '#f1f5f9', // Faint grid lines from image
                            drawBorder: false,
                            drawTicks: false
                        },
                        border: {
                            display: false
                        },
                        ticks: {
                            display: true, // Show numbers on bottom
                            color: '#8f9bb3'
                        },
                        title: {
                            display: true,
                            text: 'CPU Usage (seconds)',
                            color: '#8f9bb3'
                        }
                    },
                    y: {
                        grid: {
                            display: false,
                            drawBorder: false,
                            drawTicks: false
                        },
                        border: {
                            display: false
                        },
                        ticks: {
                            autoSkip: false,
                            color: '#8f9bb3', // Match grey text
                            font: {
                                size: 11
                            },
                            padding: 6
                        },
                        title: {
                            display: true,
                            text: 'Sessions (SID · User)',
                            color: '#64748b',
                            font: { size: 10, weight: '600' }
                        }
                    }
                }
            };

            if (sessionCpuChart) {
                sessionCpuChart.destroy();
            }
            sessionCpuChart = new Chart(chartEl, {
                type: 'bar',
                data: chartData,
                options: chartOptions
            });

            // ✅ Bind Right-Click (contextmenu) on canvas for instant Session Kill Context Menu
            chartEl.oncontextmenu = (e) => {
                e.preventDefault();
                if (!sessionCpuChart) return;
                const points = sessionCpuChart.getElementsAtEventForMode(e, 'nearest', { intersect: false }, false);
                if (points && points.length > 0) {
                    const index = points[0].index;
                    const session = data.sessions[index];
                    if (session) {
                        updateCpuQueryViewer(session, 'cpu-query-box', 'cpu-query-sid-badge', 'cpu-query-active-badge');
                        showSessionContextMenu(e.clientX, e.clientY, session);
                    }
                }
            };
        })
        .catch(err => {
            console.error("Session CPU Error:", err);
            if (sessionCpuChart) {
                sessionCpuChart.destroy();
                sessionCpuChart = null;
            }
        });
}

// ✅ Copy Active SQL Query Function
function copyActiveQuery() {
    const textarea = document.getElementById('cpu-query-box');
    if (textarea && textarea.value) {
        textarea.select();
        document.execCommand('copy');
        
        // Flash button text
        const btn = document.querySelector('.copy-query-btn');
        if (btn) {
            const oldText = btn.innerText;
            btn.innerText = 'Copied!';
            btn.style.backgroundColor = '#d1fae5';
            btn.style.color = '#065f46';
            btn.style.borderColor = '#a7f3d0';
            setTimeout(() => {
                btn.innerText = oldText;
                btn.style.backgroundColor = '#eff6ff';
                btn.style.color = '#2563eb';
                btn.style.borderColor = '#bfdbfe';
            }, 1500);
        }
    }
}

// ✅ TOP CPU SESSIONS IN 24 HOURS CHART
function checkSessionCPU24h() {
    fetch('/api/sessions/cpu-usage-24h')
        .then(res => res.json())
        .then(data => {
            console.log("Session CPU 24h:", data);

            const chartEl = document.getElementById("sessionCpu24hChart");
            if (!chartEl) return;

            if (data.error || !data.sessions || data.sessions.length === 0) {
                if (sessionCpu24hChart) sessionCpu24hChart.destroy();
                sessionCpu24hChart = new Chart(chartEl, {
                    type: 'bar',
                    data: {
                        labels: ['No CPU Sessions in 24h'],
                        datasets: [{
                            label: 'CPU Usage (s)',
                            data: [0],
                            backgroundColor: '#cbd5e1'
                        }]
                    },
                    options: {
                        indexAxis: 'y',
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: { legend: { display: false } },
                        scales: { x: { beginAtZero: true } }
                    }
                });
                return;
            }

            const labels = data.sessions.map(s => `${s.sid} · ${s.username} · ${s.status || 'N/A'}`);
            const cpuData = data.sessions.map(s => s.cpu_usage);

            const bgColors = data.sessions.map((session) => {
                if (session.is_deadlock) return '#ef4444';
                if (session.cpu_usage >= 0.10) return '#eea638';
                return '#25a576';
            });

            const chartData = {
                labels: labels,
                datasets: [{
                    label: 'CPU Usage (seconds)',
                    data: cpuData,
                    backgroundColor: bgColors,
                    borderRadius: 4,
                    barPercentage: 0.5,
                    categoryPercentage: 0.8
                }]
            };

            if (data.sessions && data.sessions.length > 0) {
                updateCpuQueryViewer(data.sessions[0], 'cpu-query-box-24h', 'cpu-query-24h-sid-badge', 'cpu-query-24h-active-badge', 'cpu-query-24h-status-badge');
            }

            const chartOptions = {
                indexAxis: 'y',
                onHover: (event, chartElements) => {
                    if (chartElements && chartElements.length > 0) {
                        const index = chartElements[0].index;
                        const session = data.sessions[index];
                        if (session) {
                            updateCpuQueryViewer(session, 'cpu-query-box-24h', 'cpu-query-24h-sid-badge', 'cpu-query-24h-active-badge', 'cpu-query-24h-status-badge');
                        }
                    }
                },
                plugins: {
                    legend: { display: false },
                    tooltip: { enabled: false }
                },
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: {
                        display: true,
                        beginAtZero: true,
                        grid: {
                            display: true,
                            color: '#f1f5f9',
                            drawBorder: false,
                            drawTicks: false
                        },
                        border: {
                            display: false
                        },
                        ticks: {
                            display: true,
                            color: '#8f9bb3'
                        },
                        title: {
                            display: true,
                            text: 'CPU Usage (seconds)',
                            color: '#8f9bb3'
                        }
                    },
                    y: {
                        grid: {
                            display: false,
                            drawBorder: false,
                            drawTicks: false
                        },
                        border: {
                            display: false
                        },
                        ticks: {
                            autoSkip: false,
                            color: '#8f9bb3',
                            font: {
                                size: 11
                            },
                            padding: 6
                        },
                        title: {
                            display: true,
                            text: 'Sessions (SID · User)',
                            color: '#64748b',
                            font: { size: 10, weight: '600' }
                        }
                    }
                }
            };

            if (sessionCpu24hChart) {
                sessionCpu24hChart.destroy();
            }
            sessionCpu24hChart = new Chart(chartEl, {
                type: 'bar',
                data: chartData,
                options: chartOptions
            });

            // ✅ Bind Right-Click (contextmenu) on canvas for 24h Session Kill Context Menu
            chartEl.oncontextmenu = (e) => {
                e.preventDefault();
                if (!sessionCpu24hChart) return;
                const points = sessionCpu24hChart.getElementsAtEventForMode(e, 'nearest', { intersect: false }, false);
                if (points && points.length > 0) {
                    const index = points[0].index;
                    const session = data.sessions[index];
                    if (session) {
                        updateCpuQueryViewer(session, 'cpu-query-box-24h', 'cpu-query-24h-sid-badge', 'cpu-query-24h-active-badge');
                        showSessionContextMenu(e.clientX, e.clientY, session);
                    }
                }
            };
        })
        .catch(err => {
            console.error("Session CPU 24h Error:", err);
            if (sessionCpu24hChart) {
                sessionCpu24hChart.destroy();
                sessionCpu24hChart = null;
            }
        });
}

function copyActiveQuery24h() {
    const textarea = document.getElementById('cpu-query-box-24h');
    if (textarea && textarea.value) {
        textarea.select();
        document.execCommand('copy');
        
        const btn = document.querySelector('.copy-query-btn-24h');
        if (btn) {
            const oldText = btn.innerText;
            btn.innerText = 'Copied!';
            btn.style.backgroundColor = '#d1fae5';
            btn.style.color = '#065f46';
            btn.style.borderColor = '#a7f3d0';
            setTimeout(() => {
                btn.innerText = oldText;
                btn.style.backgroundColor = '#eff6ff';
                btn.style.color = '#2563eb';
                btn.style.borderColor = '#bfdbfe';
            }, 1500);
        }
    }
}

// ✅ TOP SESSIONS BY SGA & PGA MONITOR
function checkSessionMemory() {
    function escapeHtmlAttr(str) {
        if (!str) return '';
        return str
            .replace(/&/g, '&amp;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    fetch('/api/sessions/memory-usage')
        .then(res => res.json())
        .then(data => {
            console.log("Session Memory:", data);

            const sgaContainer = document.getElementById("top-sga-list");
            const pgaContainer = document.getElementById("top-pga-list");

            if (!sgaContainer && !pgaContainer) return;

            if (data.status === 'no_data' || data.error) {
                const msg = data.message || data.error || 'No session memory statistics available.';
                if (sgaContainer) sgaContainer.innerHTML = `<div style="font-size: 0.7rem; color: #94a3b8; font-style: italic; padding: 0.25rem 0;">${escapeHtml(msg)}</div>`;
                if (pgaContainer) pgaContainer.innerHTML = `<div style="font-size: 0.7rem; color: #94a3b8; font-style: italic; padding: 0.25rem 0;">${escapeHtml(msg)}</div>`;
                return;
            }

            // 1. Render Top Sessions by SGA (UGA)
            if (sgaContainer) {
                const sgaList = data.top_sga || [];
                if (sgaList.length === 0) {
                    sgaContainer.innerHTML = '<div style="font-size: 0.7rem; color: #94a3b8; font-style: italic;">No active SGA session metrics</div>';
                } else {
                    const maxSga = Math.max(...sgaList.map(item => item.memory_mb || 0), 1.0);
                    sgaContainer.innerHTML = sgaList.map((item) => {
                        const pct = Math.min(100, Math.max(12, ((item.memory_mb || 0) / maxSga) * 100));
                        const barColor = '#3b82f6';
                        const sqlText = item.sql_text || 'No query associated or system session';
                        const status = item.status || 'N/A';
                        const dotColor = status.toUpperCase() === 'ACTIVE' ? '#25a576' : '#94a3b8';
                        return `
                            <div class="custom-query-tooltip-row" style="display: flex; align-items: center; justify-content: space-between; gap: 0.6rem; width: 100%; cursor: help;">
                                <div style="font-size: 0.72rem; font-weight: 600; color: #475569; width: 80px; flex-shrink: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="Status: ${escapeHtml(status)}"><span style="display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: ${dotColor}; margin-right: 4px;"></span>${escapeHtml(item.label)}</div>
                                <div style="flex: 1; height: 7px; background-color: #e5e7eb; border-radius: 999px; overflow: hidden; position: relative;">
                                    <div style="width: ${pct.toFixed(1)}%; height: 100%; background-color: ${barColor}; border-radius: 999px; transition: width 0.4s ease;"></div>
                                </div>
                                <div style="font-size: 0.74rem; font-weight: 700; color: #0f172a; width: 52px; text-align: right; flex-shrink: 0;">${item.memory_mb.toFixed(0)} MB</div>
                                <div class="custom-query-tooltip-box">
                                    <div style="font-weight: 700; color: #38bdf8; margin-bottom: 0.3rem; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em;">SQL Query Details</div>
                                    <div style="font-size: 0.85rem; color: #f8fafc; font-weight: 500; font-family: 'Courier New', Courier, monospace; max-height: 120px; overflow-y: auto; padding-right: 4px;">${escapeHtml(sqlText)}</div>
                                </div>
                            </div>
                        `;
                    }).join('');
                }
            }

            // 2. Render Top Sessions by PGA
            if (pgaContainer) {
                const pgaList = data.top_pga || [];
                if (pgaList.length === 0) {
                    pgaContainer.innerHTML = '<div style="font-size: 0.7rem; color: #94a3b8; font-style: italic;">No active PGA session metrics</div>';
                } else {
                    const maxPga = Math.max(...pgaList.map(item => item.memory_mb || 0), 1.0);
                    pgaContainer.innerHTML = pgaList.map((item) => {
                        const pct = Math.min(100, Math.max(12, ((item.memory_mb || 0) / maxPga) * 100));
                        const barColor = '#34d399';
                        const sqlText = item.sql_text || 'No query associated or system session';
                        const status = item.status || 'N/A';
                        const dotColor = status.toUpperCase() === 'ACTIVE' ? '#25a576' : '#94a3b8';
                        return `
                            <div class="custom-query-tooltip-row" style="display: flex; align-items: center; justify-content: space-between; gap: 0.6rem; width: 100%; cursor: help;">
                                <div style="font-size: 0.72rem; font-weight: 600; color: #475569; width: 80px; flex-shrink: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="Status: ${escapeHtml(status)}"><span style="display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: ${dotColor}; margin-right: 4px;"></span>${escapeHtml(item.label)}</div>
                                <div style="flex: 1; height: 7px; background-color: #e5e7eb; border-radius: 999px; overflow: hidden; position: relative;">
                                    <div style="width: ${pct.toFixed(1)}%; height: 100%; background-color: ${barColor}; border-radius: 999px; transition: width 0.4s ease;"></div>
                                </div>
                                <div style="font-size: 0.74rem; font-weight: 700; color: #0f172a; width: 52px; text-align: right; flex-shrink: 0;">${item.memory_mb.toFixed(0)} MB</div>
                                <div class="custom-query-tooltip-box">
                                    <div style="font-weight: 700; color: #38bdf8; margin-bottom: 0.3rem; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em;">SQL Query Details</div>
                                    <div style="font-size: 0.85rem; color: #f8fafc; font-weight: 500; font-family: 'Courier New', Courier, monospace; max-height: 120px; overflow-y: auto; padding-right: 4px;">${escapeHtml(sqlText)}</div>
                                </div>
                            </div>
                        `;
                    }).join('');
                }
            }
        })
        .catch(err => {
            console.error("Session Memory Error:", err);
            const sgaContainer = document.getElementById("top-sga-list");
            const pgaContainer = document.getElementById("top-pga-list");
            if (sgaContainer) sgaContainer.innerHTML = '<div style="font-size: 0.7rem; color: #ef4444;">Unable to fetch SGA metrics</div>';
            if (pgaContainer) pgaContainer.innerHTML = '<div style="font-size: 0.7rem; color: #ef4444;">Unable to fetch PGA metrics</div>';
        });
}

// ✅ BLOCKING & DEADLOCK SESSIONS MONITOR
function setBlockingErrorState() {
    const card = document.getElementById('blocking-card-container');
    if (card) {
        card.className = 'card blocking-card red-alert';
    }
    const indicator = document.getElementById('blocking-light-indicator');
    if (indicator) {
        indicator.style.backgroundColor = '#ef4444';
        indicator.style.boxShadow = '0 0 8px rgba(239, 68, 68, 0.4)';
    }
    const summary = document.getElementById('blocking-summary-text');
    if (summary) {
        summary.innerText = 'Lock Monitor Error';
    }
    const badge = document.getElementById('blocking-status-badge');
    if (badge) {
        badge.innerText = 'Error';
        badge.className = 'pill-badge red';
    }
    const header = document.getElementById('blocking-header-icon-label');
    if (header) {
        header.style.color = '#ef4444';
    }
    const list = document.getElementById('blocking-details-list');
    if (list) {
        list.innerHTML = '<div style="color: #ef4444; text-align: center; margin-top: 1.5rem; font-weight: 500;">Failed to retrieve lock status.</div>';
    }
}

function checkBlockingSessions() {
    fetch('/api/sessions/blocking')
        .then(res => res.json())
        .then(data => {
            if (data.error) {
                console.error("Blocking Sessions Error:", data.error);
                setBlockingErrorState();
                return;
            }

            const card = document.getElementById('blocking-card-container');
            const indicator = document.getElementById('blocking-light-indicator');
            const summary = document.getElementById('blocking-summary-text');
            const badge = document.getElementById('blocking-status-badge');
            const header = document.getElementById('blocking-header-icon-label');
            const list = document.getElementById('blocking-details-list');

            const blocks = data.blocks || [];

            if (blocks.length > 0) {
                // RED state: blocking detected!
                if (card) card.className = 'card blocking-card red-alert';
                if (indicator) {
                    indicator.style.backgroundColor = '#ef4444';
                    indicator.style.boxShadow = '0 0 8px rgba(239, 68, 68, 0.5)';
                }
                if (summary) summary.innerText = `${blocks.length} Blocking Lock(s) Detected`;
                if (badge) {
                    badge.innerText = 'Alert';
                    badge.className = 'pill-badge red';
                }
                if (header) header.style.color = '#ef4444';

                if (list) {
                    list.innerHTML = '';
                    blocks.forEach(block => {
                        const itemHtml = `
                            <div style="background: rgba(239,68,68,0.06); border-left: 3px solid #ef4444; padding: 0.4rem; border-radius: 4px; display: flex; flex-direction: column; gap: 0.15rem; margin-bottom: 0.25rem;">
                                <div style="display: flex; justify-content: space-between; font-weight: 700; color: #b91c1c; font-size: 0.72rem;">
                                    <span>Waiting: ${block.waiting_username} (SID=${block.waiting_sid})</span>
                                    <span>${block.seconds_in_wait}s wait</span>
                                </div>
                                <div style="color: #475569; font-weight: 600; font-size: 0.68rem;">
                                    Blocked by: <span style="color: #0f172a; font-weight: 700;">${block.blocking_username} (SID=${block.blocking_sid})</span>
                                </div>
                            </div>
                        `;
                        list.insertAdjacentHTML('beforeend', itemHtml);
                    });
                }
            } else {
                // GREEN state: all clean!
                if (card) card.className = 'card blocking-card';
                if (indicator) {
                    indicator.style.backgroundColor = '#10b981';
                    indicator.style.boxShadow = '0 0 8px rgba(16, 185, 129, 0.4)';
                }
                if (summary) summary.innerText = 'No Blocking Sessions';
                if (badge) {
                    badge.innerText = 'Clean';
                    badge.className = 'pill-badge green';
                }
                if (header) header.style.color = '#10b981';

                if (list) {
                    list.innerHTML = '<div style="color: #64748b; text-align: center; margin-top: 1.5rem; font-style: italic;">No database locks detected.</div>';
                }
            }
        })
        .catch(err => {
            console.error("Fetch Blocking Sessions Error:", err);
            setBlockingErrorState();
        });
}

// ✅ MEMORY INFO (SGA & PGA)
function formatBytesNumber(bytes, targetUnit) {
    if (!+bytes) return 0;
    const k = 1024;
    if (targetUnit === 'GB') return (bytes / Math.pow(k, 3)).toFixed(1);
    if (targetUnit === 'MB') return (bytes / Math.pow(k, 2)).toFixed(1);
    return bytes;
}

function setMemoryErrorState() {
    ['sga-large-value', 'pga-large-value'].forEach(id => {
        let el = document.getElementById(id);
        if (el) el.innerText = 'Error';
    });
    
    ['sga-pill-badge', 'pga-pill-badge'].forEach(id => {
        let el = document.getElementById(id);
        if (el) {
            el.innerText = '0%';
            el.className = 'pill-badge red';
        }
    });

    ['sga-progress-fill', 'pga-progress-fill'].forEach(id => {
        let el = document.getElementById(id);
        if (el) {
            el.style.width = '0%';
            el.className = 'progress-fill red';
        }
    });

    ['sga-subtext', 'pga-subtext'].forEach(id => {
        let el = document.getElementById(id);
        if (el) el.innerText = 'Memory data unavailable';
    });

    ['sga-segmented-bar', 'pga-segmented-bar'].forEach(id => {
        let el = document.getElementById(id);
        if (el) el.innerHTML = '<div class="segment-bar-fill" style="width: 100%; background-color: #f1f5f9;"></div>';
    });

    ['sga-segmented-label', 'pga-segmented-label'].forEach(id => {
        let el = document.getElementById(id);
        if (el) el.innerText = 'Error';
    });
}

function fetchMemoryInfo() {
    fetch('/api/memory-info')
        .then(res => res.json())
        .then(data => {
            if (data.error) {
                console.error("Memory Info Error:", data.error);
                setMemoryErrorState();
                return;
            }

            // --- SGA Update ---
            const sga = data.sga;
            if (sga) {
                const totalGB = Number(formatBytesNumber(sga.total_bytes, 'GB'));
                const usedGB = Number(formatBytesNumber(sga.used_bytes, 'GB'));
                const freeGB = Number(formatBytesNumber(sga.free_bytes, 'GB'));
                const usedPercent = sga.total_bytes > 0 ? ((sga.used_bytes / sga.total_bytes) * 100) : 0;
                const formattedPercent = usedPercent.toFixed(1);

                // Update UI elements
                const sgaLargeVal = document.getElementById('sga-large-value');
                if (sgaLargeVal) sgaLargeVal.innerText = `${usedGB.toFixed(1)} GB`;

                const sgaPill = document.getElementById('sga-pill-badge');
                if (sgaPill) {
                    sgaPill.innerText = `${formattedPercent}%`;
                    sgaPill.className = 'pill-badge';
                    if (usedPercent >= 90) {
                        sgaPill.classList.add('red');
                    } else if (usedPercent >= 75) {
                        sgaPill.classList.add('orange');
                    } else {
                        sgaPill.classList.add('green');
                    }
                }

                const sgaProgressFill = document.getElementById('sga-progress-fill');
                if (sgaProgressFill) {
                    sgaProgressFill.style.width = `${formattedPercent}%`;
                    sgaProgressFill.className = 'progress-fill';
                    if (usedPercent >= 90) {
                        sgaProgressFill.classList.add('red');
                    } else if (usedPercent >= 75) {
                        sgaProgressFill.classList.add('orange');
                    } else {
                        sgaProgressFill.classList.add('green');
                    }
                }

                const sgaSubtext = document.getElementById('sga-subtext');
                if (sgaSubtext) {
                    sgaSubtext.innerText = `${freeGB.toFixed(1)} GB free of ${totalGB.toFixed(1)} GB total`;
                }

                // Process SGA components for segmented progress bar
                const sgaComponents = [];
                for (const [key, val] of Object.entries(sga.components || {})) {
                    if (val > 0) {
                        sgaComponents.push({ name: key, bytes: val });
                    }
                }
                sgaComponents.sort((a, b) => b.bytes - a.bytes);

                const totalComponentBytes = sgaComponents.reduce((sum, item) => sum + item.bytes, 0);
                const segmentedBar = document.getElementById('sga-segmented-bar');
                if (segmentedBar && totalComponentBytes > 0) {
                    segmentedBar.innerHTML = '';
                    const sgaColors = ['#1d4ed8', '#2563eb', '#3b82f6', '#60a5fa', '#93c5fd'];
                    const top5 = sgaComponents.slice(0, 5);
                    top5.forEach((item, idx) => {
                        const pct = (item.bytes / totalComponentBytes * 100);
                        const segmentColor = sgaColors[idx] || '#cbd5e1';
                        const segHtml = `<div class="segment-bar-fill" style="width: ${pct}%; background-color: ${segmentColor};" title="${item.name}: ${pct.toFixed(1)}%"></div>`;
                        segmentedBar.insertAdjacentHTML('beforeend', segHtml);
                    });
                }

                const sgaSegmentedLabel = document.getElementById('sga-segmented-label');
                if (sgaSegmentedLabel && sgaComponents.length > 0) {
                    const largest = sgaComponents[0];
                    const pct = totalComponentBytes > 0 ? (largest.bytes / totalComponentBytes * 100) : 0;
                    sgaSegmentedLabel.innerText = `${largest.name} largest at ${pct.toFixed(0)}%`;
                }
            }

            // --- PGA Update ---
            const pga = data.pga;
            if (pga) {
                const totalMB = Number(formatBytesNumber(pga.total_bytes, 'MB'));
                const usedMB = Number(formatBytesNumber(pga.used_bytes, 'MB'));
                const freeMB = Number(formatBytesNumber(pga.free_bytes, 'MB'));
                const usedPercent = pga.total_bytes > 0 ? ((pga.used_bytes / pga.total_bytes) * 100) : 0;
                const formattedPercent = usedPercent.toFixed(1);

                // Update UI elements
                const pgaLargeVal = document.getElementById('pga-large-value');
                if (pgaLargeVal) pgaLargeVal.innerText = `${usedMB.toFixed(0)} MB`;

                const pgaPill = document.getElementById('pga-pill-badge');
                if (pgaPill) {
                    pgaPill.innerText = `${formattedPercent}%`;
                    pgaPill.className = 'pill-badge';
                    if (usedPercent >= 90) {
                        pgaPill.classList.add('red');
                    } else if (usedPercent >= 75) {
                        pgaPill.classList.add('orange');
                    } else {
                        pgaPill.classList.add('green');
                    }
                }

                const pgaProgressFill = document.getElementById('pga-progress-fill');
                if (pgaProgressFill) {
                    pgaProgressFill.style.width = `${formattedPercent}%`;
                    pgaProgressFill.className = 'progress-fill';
                    if (usedPercent >= 90) {
                        pgaProgressFill.classList.add('red');
                    } else if (usedPercent >= 75) {
                        pgaProgressFill.classList.add('orange');
                    } else {
                        pgaProgressFill.classList.add('green');
                    }
                }

                const pgaSubtext = document.getElementById('pga-subtext');
                if (pgaSubtext) {
                    pgaSubtext.innerText = `${freeMB.toFixed(1)} MB free of ${totalMB.toFixed(1)} MB`;
                }

                // Process PGA components for segmented progress bar
                const pgaComponents = [];
                for (const [key, val] of Object.entries(pga.components || {})) {
                    if (val > 0) {
                        pgaComponents.push({ name: key, bytes: val });
                    }
                }
                pgaComponents.sort((a, b) => b.bytes - a.bytes);

                const totalPgaComponentBytes = pgaComponents.reduce((sum, item) => sum + item.bytes, 0);
                const segmentedBar = document.getElementById('pga-segmented-bar');
                if (segmentedBar && totalPgaComponentBytes > 0) {
                    segmentedBar.innerHTML = '';
                    const pgaColors = ['#4c1d95', '#6d28d9', '#8b5cf6', '#a78bfa'];
                    const top4 = pgaComponents.slice(0, 4);
                    top4.forEach((item, idx) => {
                        const pct = (item.bytes / totalPgaComponentBytes * 100);
                        const segmentColor = pgaColors[idx] || '#cbd5e1';
                        const segHtml = `<div class="segment-bar-fill" style="width: ${pct}%; background-color: ${segmentColor};" title="${item.name}: ${pct.toFixed(1)}%"></div>`;
                        segmentedBar.insertAdjacentHTML('beforeend', segHtml);
                    });
                }

                const pgaSegmentedLabel = document.getElementById('pga-segmented-label');
                if (pgaSegmentedLabel && pgaComponents.length > 0) {
                    const largest = pgaComponents[0];
                    const pct = totalPgaComponentBytes > 0 ? (largest.bytes / totalPgaComponentBytes * 100) : 0;
                    pgaSegmentedLabel.innerText = `${largest.name} largest at ${pct.toFixed(0)}%`;
                }
            }
        })
        .catch(err => {
            console.error("Fetch Memory Info Error:", err);
            setMemoryErrorState();
        });
}

// ✅ ARCHIVE LOG INFO
function setArchiveLogErrorState() {
    const sizeVal = document.getElementById('archive-size-val');
    if (sizeVal) sizeVal.innerText = 'Error';
    
    const countBadge = document.getElementById('archive-count-badge');
    if (countBadge) {
        countBadge.innerText = 'Error';
        countBadge.className = 'pill-badge red';
    }

    const percentLabel = document.getElementById('archive-fra-percent-label');
    if (percentLabel) {
        percentLabel.innerText = 'Error';
        percentLabel.style.color = '#ef4444';
    }

    const progressBar = document.getElementById('archive-fra-progress');
    if (progressBar) {
        progressBar.style.width = '0%';
        progressBar.style.backgroundColor = '#ef4444';
    }

    const destMeta = document.getElementById('archive-fra-meta');
    if (destMeta) {
        destMeta.innerText = 'Data unavailable';
        destMeta.title = 'Data unavailable';
    }

    const locDisplay = document.getElementById('archive-loc-display');
    if (locDisplay) {
        locDisplay.innerText = 'Loc: N/A';
        locDisplay.title = 'Location unavailable';
    }
}

function fetchArchiveLogInfo() {
    fetch('/api/archive-log-info')
        .then(res => res.json())
        .then(data => {
            if (data.error) {
                console.error("Archive Log Info Error:", data.error);
                setArchiveLogErrorState();
                return;
            }

            // Update UI elements
            const sizeVal = document.getElementById('archive-size-val');
            if (sizeVal) {
                const displayGb = (data.size_gb !== undefined && data.size_gb !== null) ? data.size_gb : (data.total_gb !== undefined ? data.total_gb : data.last_24h_gb);
                sizeVal.innerText = `${Number(displayGb || 0).toFixed(2)} GB`;
            }

            const countBadge = document.getElementById('archive-count-badge');
            if (countBadge) {
                if (data.log_mode === 'NOARCHIVELOG') {
                    countBadge.innerText = 'Disabled';
                    countBadge.style.backgroundColor = '#94a3b8';
                    countBadge.style.color = '#ffffff';
                } else {
                    const displayCount = (data.archive_logs !== undefined && data.archive_logs !== null) ? data.archive_logs : (data.total_count !== undefined ? data.total_count : data.last_24h_count);
                    countBadge.innerText = `${displayCount || 0} logs`;
                    countBadge.className = 'pill-badge green';
                    countBadge.style.backgroundColor = '';
                    countBadge.style.color = '';
                }
            }

            const percentLabel = document.getElementById('archive-fra-percent-label');
            const progressBar = document.getElementById('archive-fra-progress');
            const destMeta = document.getElementById('archive-fra-meta');

            // Handle when FRA is not set (will be N/A or empty or parameter is N/A or empty)
            const hasFRA = data.fra_dest_param && data.fra_dest_param !== 'N/A' && data.fra_dest_param.trim() !== '' && data.fra_limit_gb > 0;

            if (data.log_mode === 'NOARCHIVELOG') {
                if (percentLabel) {
                    percentLabel.innerText = 'Disabled';
                    percentLabel.style.color = '#94a3b8';
                }
                if (progressBar) {
                    progressBar.style.width = '0%';
                    progressBar.style.backgroundColor = '#cbd5e1';
                }
                if (destMeta) {
                    destMeta.innerText = 'Archiving Disabled (NOARCHIVELOG)';
                    destMeta.title = 'Database is in NOARCHIVELOG mode';
                }
                const locDisplay = document.getElementById('archive-loc-display');
                if (locDisplay) {
                    locDisplay.innerText = 'Loc: Disabled';
                    locDisplay.title = 'Archiving is disabled';
                }
            } else if (hasFRA) {
                const limitGB = data.fra_limit_gb;
                const usedGB = data.fra_used_gb;
                const percentUsed = data.fra_percent;

                if (percentLabel) {
                    percentLabel.innerText = `${percentUsed.toFixed(1)}% used`;
                    if (percentUsed >= 90) {
                        percentLabel.style.color = '#ef4444';
                    } else if (percentUsed >= 75) {
                        percentLabel.style.color = '#f59e0b';
                    } else {
                        percentLabel.style.color = '#059669';
                    }
                }

                if (progressBar) {
                    progressBar.style.width = `${percentUsed}%`;
                    if (percentUsed >= 90) {
                        progressBar.style.backgroundColor = '#ef4444';
                    } else if (percentUsed >= 75) {
                        progressBar.style.backgroundColor = '#f59e0b';
                    } else {
                        progressBar.style.backgroundColor = '#10b981';
                    }
                }

                if (destMeta) {
                    destMeta.innerHTML = `Used: ${usedGB.toFixed(1)} GB | Free: <strong style="font-weight: 800; color: #0f172a;">${data.fra_free_gb.toFixed(1)} GB</strong>`;
                    destMeta.title = `Total Storage: ${limitGB.toFixed(1)} GB | Path: ${data.archive_location}`;
                }
                const locDisplay = document.getElementById('archive-loc-display');
                if (locDisplay) {
                    const loc = data.archive_location || 'N/A';
                    locDisplay.innerText = `Loc: ${loc}`;
                    locDisplay.title = `Archive Location: ${loc}`;
                }
            } else {
                // If FRA is N/A but archiving is enabled
                if (percentLabel) {
                    percentLabel.innerText = 'N/A';
                    percentLabel.style.color = '#64748b';
                }
                if (progressBar) {
                    progressBar.style.width = '0%';
                    progressBar.style.backgroundColor = '#cbd5e1';
                }
                if (destMeta) {
                    destMeta.innerText = 'Storage Limits Not Configured';
                    destMeta.title = 'No recovery area storage limits configured';
                }
                const locDisplay = document.getElementById('archive-loc-display');
                if (locDisplay) {
                    const loc = data.archive_location || 'N/A';
                    locDisplay.innerText = `Loc: ${loc}`;
                    locDisplay.title = `Archive Location: ${loc}`;
                }
            }
        })
        .catch(err => {
            console.error("Fetch Archive Log Info Error:", err);
            setArchiveLogErrorState();
        });
}

function checkOsInfo() {
    fetch('/api/os-info')
        .then(res => res.json())
        .then(data => {
            if (data.error) {
                console.error("OS Info Error:", data.error);
                return;
            }

            // Populate system info
            const nameEl = document.getElementById('os-name');
            const uptimeEl = document.getElementById('os-uptime');
            const cpuValEl = document.getElementById('os-cpu-val');
            const cpuBarEl = document.getElementById('os-cpu-bar');
            const memValEl = document.getElementById('os-mem-val');
            const memBarEl = document.getElementById('os-mem-bar');
            const badgeEl = document.getElementById('os-platform-badge');

            if (nameEl) nameEl.innerText = data.os_name || 'Unknown';
            if (uptimeEl) uptimeEl.innerText = data.uptime || 'N/A';
            
            if (cpuValEl) cpuValEl.innerText = `${data.cpu_percent.toFixed(1)}%`;
            if (cpuBarEl) cpuBarEl.style.width = `${data.cpu_percent}%`;
            
            if (data.memory) {
                if (memValEl) memValEl.innerText = `${data.memory.percent.toFixed(1)}%`;
                if (memBarEl) memBarEl.style.width = `${data.memory.percent}%`;
            }

            if (badgeEl) {
                badgeEl.innerText = data.is_linux ? 'Linux' : 'Windows';
                if (data.is_linux) {
                    badgeEl.className = 'pill-badge orange';
                } else {
                    badgeEl.className = 'pill-badge blue';
                }
            }

            // Populate disks/volumes
            const disksContainer = document.getElementById('os-disks-container');
            if (disksContainer && data.disks) {
                disksContainer.innerHTML = '';
                
                if (data.disks.status === 'error') {
                    disksContainer.innerHTML = `<div style="font-size: 0.72rem; color: #ef4444; font-weight: 500; padding: 0.5rem; text-align: center;">${escapeHtml(data.disks.message || 'Unable to retrieve operating system disk information.')}</div>`;
                    const showAllLink = document.getElementById('os-show-all-link');
                    if (showAllLink) showAllLink.style.display = 'none';
                    return;
                }
                
                allDisksData = data.disks || [];

                // Show/hide "Show all" link
                const showAllLink = document.getElementById('os-show-all-link');
                if (showAllLink) {
                    if (allDisksData.length > 2) {
                        showAllLink.style.display = 'inline';
                    } else {
                        showAllLink.style.display = 'none';
                    }
                }

                if (data.is_linux) {
                    // Linux disks layout:
                    // 1. DISK VOLUMES label
                    const volumesLabel = document.createElement('div');
                    volumesLabel.style = 'font-size: 0.75rem; color: #64748b; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 0.25rem; margin-top: 0.15rem;';
                    volumesLabel.innerText = 'DISK VOLUMES';
                    disksContainer.appendChild(volumesLabel);

                    // 2. Physical Disks side-by-side flex container
                    const physicalContainer = document.createElement('div');
                    physicalContainer.style = 'display: flex; flex-wrap: wrap; gap: 0.6rem; margin-bottom: 0.4rem;';
                    disksContainer.appendChild(physicalContainer);

                    // Display only up to 2 physical disks on the main card
                    let physicalCount = 0;
                    data.disks.forEach(disk => {
                        const isVirtual = disk.type === 'virtual';
                        if (!isVirtual) {
                            physicalCount++;
                            if (physicalCount <= 2) {
                                const ringColor = disk.percent > 50 ? '#f59e0b' : '#10b981';
                                const pct = disk.percent;
                                const sizeText = disk.total_gb >= 1.0 ? 
                                    `Used: ${disk.used_gb.toFixed(2)} GB / ${disk.total_gb.toFixed(2)} GB` : 
                                    `Used: ${(disk.used_gb * 1024).toFixed(0)} MB / ${(disk.total_gb * 1024).toFixed(0)} MB`;
                                
                                const card = document.createElement('div');
                                card.style = 'flex: 1; min-width: 130px; display: flex; align-items: center; gap: 0.5rem; background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 0.4rem 0.6rem;';
                                card.innerHTML = `
                                    <div style="position: relative; width: 34px; height: 34px; flex-shrink: 0;">
                                        <svg width="34" height="34" viewBox="0 0 36 36">
                                            <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="#e2e8f0" stroke-width="3.5"/>
                                            <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="${ringColor}" stroke-dasharray="${pct.toFixed(0)}, 100" stroke-width="3.5" stroke-linecap="round"/>
                                            <text x="18" y="21.5" font-size="9.5" font-weight="700" text-anchor="middle" fill="#0f172a">${pct.toFixed(0)}%</text>
                                        </svg>
                                    </div>
                                    <div style="display: flex; flex-direction: column; overflow: hidden;">
                                        <span style="font-size: 0.76rem; font-weight: 700; color: #0f172a; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${disk.label}</span>
                                        <span style="font-size: 0.66rem; color: #64748b; white-space: nowrap;">${sizeText}</span>
                                    </div>
                                `;
                                physicalContainer.appendChild(card);
                            }
                        }
                    });
                } else {
                    // Windows layout (single C:\ disk)
                    const volumesLabel = document.createElement('div');
                    volumesLabel.style = 'font-size: 0.75rem; color: #64748b; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 0.25rem;';
                    volumesLabel.innerText = 'DISK STORAGE';
                    disksContainer.appendChild(volumesLabel);

                    const winDisk = data.disks[0] || { label: 'C:\\', used_gb: 0, total_gb: 100, percent: 0 };
                    const pct = winDisk.percent;

                    const card = document.createElement('div');
                    card.style = 'display: flex; align-items: center; gap: 1rem; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 0.6rem 0.8rem;';
                    card.innerHTML = `
                        <div style="position: relative; width: 40px; height: 40px; flex-shrink: 0;">
                            <svg width="40" height="40" viewBox="0 0 36 36">
                                <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="#e2e8f0" stroke-width="3"/>
                                <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="#2563eb" stroke-dasharray="${pct.toFixed(0)}, 100" stroke-width="3" stroke-linecap="round"/>
                                <text x="18" y="21.5" font-size="8.5" font-weight="700" text-anchor="middle" fill="#0f172a">${pct.toFixed(0)}%</text>
                            </svg>
                        </div>
                        <div style="display: flex; flex-direction: column;">
                            <span style="font-size: 0.8rem; font-weight: 700; color: #0f172a;">${winDisk.label}</span>
                            <span style="font-size: 0.7rem; color: #64748b;">Used: ${winDisk.used_gb.toFixed(2)} GB / ${winDisk.total_gb.toFixed(2)} GB</span>
                        </div>
                    `;
                    disksContainer.appendChild(card);
                }
            }
        })
        .catch(err => console.error("OS Info Fetch Error:", err));
}


// =============== MULTI-DB QUICK CONNECT CARDS ===============
let dbStatuses = {};
let statusPollInterval = null;
let fastPollTimeout = null;
let consecutiveCheckingPolls = 0;
// Only the very first database status fetch shows the Processing/Fetched
// toasts - the recurring 30s background poll (and any fast-poll retries
// that follow it) stays silent so the popup doesn't reappear constantly.
let initialDbStatusFetchDone = false;

function initQuickConnectCards() {
    if (statusPollInterval) {
        clearInterval(statusPollInterval);
    }
    statusPollInterval = setInterval(() => {
        // Poll database statuses in the background for both sidebar and quick connect cards
        pollDatabaseStatuses(globalDatabases);
    }, 30000);
}

function pollDatabaseStatuses(databases, isFastPoll = false) {
    if (!databases || databases.length === 0) {
        updateStatsCounters(0, 0, 0);
        return;
    }

    if (!isFastPoll) {
        consecutiveCheckingPolls = 0;
        if (!initialDbStatusFetchDone) {
            // Persistent (no auto-close) - stays up for the whole fetch cycle,
            // including any fast-poll retries below, until it's replaced by
            // the "Fetched successfully" toast once every database responds.
            showUploadStatus('Processing database connections...', 'info', 0);
        }
    }

    if (fastPollTimeout) {
        clearTimeout(fastPollTimeout);
        fastPollTimeout = null;
    }
    
    const total = databases.length;
    let connected = 0;
    let disconnected = 0;
    let pendingCount = total;
    let hasChecking = false;
    
    // Set default view metrics
    updateStatsCounters(total, 0, total);
    
    databases.forEach(db => {
        const dbKey = db.db_id.toLowerCase().replace(/[^a-z0-9]/g, '_');
        
        fetch(`/api/db-summary?db_id=${encodeURIComponent(db.db_id)}`)
            .then(res => res.json())
            .then(summary => {
                dbStatuses[dbKey] = summary;
                
                if (summary.db_status === 'Checking...' || summary.db_status === 'Fetching...') {
                    hasChecking = true;
                }
                
                let colorClass = 'grey';
                if (summary.db_status === 'Connected') {
                    const isBackupCompleted = summary.last_backup_status && (
                        summary.last_backup_status.toUpperCase() === 'COMPLETED' || 
                        summary.last_backup_status.toUpperCase() === 'SUCCESS'
                    );
                    const backupYesterday = summary.backup_yesterday === true;
                    const backupIsHealthy = isBackupCompleted && backupYesterday;
                    
                    if (!backupIsHealthy) {
                        colorClass = 'orange';
                    } else {
                        colorClass = 'green';
                    }
                    connected++;
                } else if (summary.db_status === 'Checking...' || summary.db_status === 'Fetching...') {
                    colorClass = 'grey';
                } else if (summary.listener_status === 'Running') {
                    colorClass = 'orange';
                } else {
                    colorClass = 'red';
                }
                
                // Update card UI
                updateConnectionCardUI(dbKey, summary, colorClass);
                
                // Re-render sidebar to group databases
                renderSidebarList();
                
                // Re-order quick connect cards so warnings/problems appear first
                reorderQuickConnectCards();
                
                pendingCount--;
                if (pendingCount === 0) {
                    const finalDisconnected = total - connected;
                    updateStatsCounters(total, connected, finalDisconnected);
                    updateFilterCounts();

                    // If any DB is still in "Checking..." state, schedule a fast poll (up to 12 attempts = ~24s max)
                    if (hasChecking && consecutiveCheckingPolls < 12) {
                        consecutiveCheckingPolls++;
                        fastPollTimeout = setTimeout(() => {
                            pollDatabaseStatuses(databases, true);
                        }, 4000);
                    } else {
                        if (!initialDbStatusFetchDone) {
                            showUploadStatus(`✅ Fetched successfully: ${total} database(s) checked.`, 'success', 2500);
                            initialDbStatusFetchDone = true;
                        }
                    }
                }
                renderDbHistoryDropdown();
            })
            .catch(err => {
                console.error(`Error polling status for ${db.db_id}:`, err);
                const mockSummary = {
                    db_status: "Not Connected",
                    listener_status: "Stopped",
                    uptime: "N/A",
                    active_sessions: 0
                };
                dbStatuses[dbKey] = mockSummary;
                
                updateConnectionCardUI(dbKey, mockSummary, 'red');
                
                // Re-render sidebar to group databases
                renderSidebarList();
                
                // Re-order quick connect cards so warnings/problems appear first
                reorderQuickConnectCards();
                
                pendingCount--;
                if (pendingCount === 0) {
                    const finalDisconnected = total - connected;
                    updateStatsCounters(total, connected, finalDisconnected);
                    updateFilterCounts();

                    if (hasChecking && consecutiveCheckingPolls < 12) {
                        consecutiveCheckingPolls++;
                        fastPollTimeout = setTimeout(() => {
                            pollDatabaseStatuses(databases, true);
                        }, 4000);
                    } else {
                        if (!initialDbStatusFetchDone) {
                            showUploadStatus(`✅ Fetched successfully: ${total} database(s) checked.`, 'success', 2500);
                            initialDbStatusFetchDone = true;
                        }
                    }
                }
                renderDbHistoryDropdown();
            });
    });
}

function updateStatsCounters(total, connected, disconnected) {
    const totalEl = document.getElementById('stat-total-dbs');
    const connectedEl = document.getElementById('stat-connected-dbs');
    const disconnectedEl = document.getElementById('stat-disconnected-dbs');
    
    if (totalEl) totalEl.innerText = total;
    if (connectedEl) connectedEl.innerText = connected;
    if (disconnectedEl) disconnectedEl.innerText = disconnected;
}

function getDbWarningScore(db) {
    const dbKey = db.db_id.toLowerCase().replace(/[^a-z0-9]/g, '_');
    const summary = dbStatuses[dbKey];
    
    if (!summary) {
        return 3; // Unchecked / loading
    }
    
    const isConnected = (summary.db_status === 'Connected');
    const isListenerRunning = (summary.listener_status === 'Running');
    const criticalTSStrings = summary.full_tablespaces ? summary.full_tablespaces.filter(item => {
        const match = item.match(/\((\d+(\.\d+)?)\%\)/);
        return match && parseFloat(match[1]) >= 90;
    }) : [];
    const hasTablespaceWarning = criticalTSStrings.length > 0;
    const hasLockWarning = Boolean(isConnected && summary.blocking_sessions_count > 0);
    const hasMountWarning = Boolean(isConnected && summary.has_critical_mount === true);
    const hasError = Boolean(summary.db_error);

    // Score 0: Offline or DB Down or Connection Error (Highest priority problem)
    if (!isConnected || !isListenerRunning || hasError) {
        return 0;
    }
    
    const isBackupCompleted = summary.last_backup_status && (
        summary.last_backup_status.toUpperCase() === 'COMPLETED' || 
        summary.last_backup_status.toUpperCase() === 'SUCCESS'
    );
    const backupYesterday = summary.backup_yesterday === true;
    const backupIsHealthy = isBackupCompleted && backupYesterday;
    
    // Score 1: Connected, but has Tablespace Alert / Warning, backup not completed yesterday, Lock Alert, or Mount Alert
    if (hasTablespaceWarning || !backupIsHealthy || hasLockWarning || hasMountWarning) {
        return 1;
    }
    
    // Score 2: Connected and fully healthy
    return 2;
}

function reorderQuickConnectCards() {
    const grid = document.getElementById('quick-connect-grid');
    if (!grid) return;
    if (!globalDatabases || globalDatabases.length === 0) return;
    
    const sortedDbs = [...globalDatabases].sort((a, b) => {
        const scoreA = getDbWarningScore(a);
        const scoreB = getDbWarningScore(b);
        if (scoreA !== scoreB) {
            return scoreA - scoreB;
        }
        return a.db_id.localeCompare(b.db_id);
    });

    sortedDbs.forEach(db => {
        const dbKey = db.db_id.toLowerCase().replace(/[^a-z0-9]/g, '_');
        const cardEl = document.getElementById(`quick-card-${dbKey}`);
        if (cardEl && cardEl.parentNode === grid) {
            grid.appendChild(cardEl);
            
            const summary = dbStatuses[dbKey];
            let colorClass = 'grey';
            if (summary) {
                if (summary.db_status === 'Connected') {
                    const isBackupCompleted = summary.last_backup_status && (
                        summary.last_backup_status.toUpperCase() === 'COMPLETED' || 
                        summary.last_backup_status.toUpperCase() === 'SUCCESS'
                    );
                    const backupYesterday = summary.backup_yesterday === true;
                    const backupIsHealthy = isBackupCompleted && backupYesterday;
                    const hasTablespaceWarning = Boolean(
                        summary.has_full_tablespace || 
                        (summary.above_90_tablespaces && summary.above_90_tablespaces.length > 0) || 
                        (summary.full_tablespaces && summary.full_tablespaces.length > 0)
                    );
                    const hasLockWarning = Boolean(summary.blocking_sessions_count > 0);
                    const hasMountWarning = Boolean(summary.has_critical_mount === true);
                    
                    if (hasTablespaceWarning || hasLockWarning || hasMountWarning || !backupIsHealthy) {
                        colorClass = 'orange';
                    } else {
                        colorClass = 'green';
                    }
                } else if (summary.listener_status === 'Running') {
                    colorClass = 'orange';
                } else {
                    colorClass = 'red';
                }
            }
            
            let targetGroup = 'critical';
            if (colorClass === 'green') targetGroup = 'healthy';
            if (colorClass === 'orange') targetGroup = 'warning';
            
            if (typeof globalFilters !== 'undefined' && globalFilters.has(targetGroup)) {
                cardEl.style.display = 'block';
            } else if (typeof globalFilters !== 'undefined') {
                cardEl.style.display = 'none';
            } else {
                cardEl.style.display = 'block';
            }
        }
    });
}

// Maps a card's status colorClass ('green'/'orange'/'red'/'grey' - the same
// classification already used for the status dot and card sorting) to a
// subtle border + background tint for the whole card box.
function getCardStatusColors(colorClass) {
    if (colorClass === 'green') {
        return { border: '1px solid #86efac', background: '#f0fdf4' };   // Healthy / Connected
    } else if (colorClass === 'orange') {
        return { border: '1px solid #fcd34d', background: '#fffbeb' };  // Warning / Pending
    } else if (colorClass === 'red') {
        return { border: '1px solid #fca5a5', background: '#fef2f2' };  // Down / Error
    }
    return { border: '1px solid #e2e8f0', background: '#ffffff' };      // Not Configured / Unknown
}

function updateConnectionCardUI(dbKey, summary, colorClass) {
    const cardDot = document.getElementById(`card-dot-${dbKey}`);
    if (cardDot) {
        cardDot.className = `sidebar-status-dot ${colorClass}`;
    }

    const isBackupCompleted = summary && summary.db_status === 'Connected' && summary.last_backup_status && (
        summary.last_backup_status.toUpperCase() === 'COMPLETED' || 
        summary.last_backup_status.toUpperCase() === 'SUCCESS'
    );
    const backupYesterday = summary && summary.backup_yesterday === true;
    const backupIsHealthy = isBackupCompleted && backupYesterday;

    const criticalTSStrings = summary && summary.full_tablespaces ? summary.full_tablespaces.filter(item => {
        const match = item.match(/\((\d+(\.\d+)?)\%\)/);
        return match && parseFloat(match[1]) >= 90;
    }) : [];
    const hasTablespaceWarning = criticalTSStrings.length > 0;
    const hasMountWarning = summary && summary.db_status === 'Connected' && summary.has_critical_mount === true;
    const isCritical = hasTablespaceWarning || hasMountWarning;

    // Reflect the DB's overall status across the whole card box (subtle
    // border + background tint), not just the small status dot.
    const cardEl = document.getElementById(`quick-card-${dbKey}`);
    if (cardEl) {
        const statusColors = getCardStatusColors(colorClass);
        cardEl.style.border = statusColors.border;
        cardEl.style.background = statusColors.background;
        cardEl.style.boxShadow = 'none';
    }

    // Check if there is any full tablespace or lock alert
    const nameContainer = document.getElementById(`card-name-container-${dbKey}`);
    if (nameContainer) {
        const existingAlert = document.getElementById(`card-alert-${dbKey}`);
        if (existingAlert) {
            existingAlert.remove();
        }
        
        if (hasTablespaceWarning) {
            const alertBadge = document.createElement('span');
            alertBadge.id = `card-alert-${dbKey}`;
            alertBadge.className = 'tablespace-alert-badge';
            alertBadge.title = `Critical Tablespace (>=90%):\n${criticalTSStrings.join('\n')}`;
            alertBadge.style.cssText = 'display: inline-flex; align-items: center; justify-content: center; background-color: #fef2f2; color: #ef4444; border: 1px solid #fee2e2; border-radius: 6px; padding: 2.5px 7px; font-size: 0.65rem; font-weight: 700; margin-left: 0.5rem;';
            alertBadge.innerHTML = `
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="margin-right: 3px; flex-shrink: 0;">
                    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
                    <line x1="12" y1="9" x2="12" y2="13"></line>
                    <line x1="12" y1="17" x2="12.01" y2="17"></line>
                </svg>
                Alert: ${criticalTSStrings.join(', ')}
            `;
            nameContainer.appendChild(alertBadge);
        } else if (summary.db_status === 'Connected' && summary.has_critical_mount) {
            const alertBadge = document.createElement('span');
            alertBadge.id = `card-alert-${dbKey}`;
            alertBadge.className = 'mount-alert-badge';
            alertBadge.title = `Warning: Mount point(s) critical (>90%):\n${summary.critical_mount_points.join('\n')}`;
            alertBadge.style.cssText = 'display: inline-flex; align-items: center; justify-content: center; background-color: #fef2f2; color: #dc2626; border: 1px solid #fecaca; border-radius: 6px; padding: 2.5px 7px; font-size: 0.65rem; font-weight: 700; margin-left: 0.5rem;';
            alertBadge.innerHTML = `
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="margin-right: 3px; flex-shrink: 0;">
                    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
                    <line x1="12" y1="9" x2="12" y2="13"></line>
                    <line x1="12" y1="17" x2="12.01" y2="17"></line>
                </svg>
                Disk Alert: ${summary.critical_mount_points.join(', ')}
            `;
            nameContainer.appendChild(alertBadge);
        } else if (summary.db_status === 'Connected' && summary.blocking_sessions_count > 0) {
            const alertBadge = document.createElement('span');
            alertBadge.id = `card-alert-${dbKey}`;
            alertBadge.className = 'lock-alert-badge';
            alertBadge.title = `Warning: Deadlock/Blocking session detected:\n${summary.blocking_sessions_count} blocked session(s) active.`;
            alertBadge.style.cssText = 'display: inline-flex; align-items: center; justify-content: center; background-color: #fef2f2; color: #dc2626; border: 1px solid #fecaca; border-radius: 6px; padding: 2.5px 7px; font-size: 0.65rem; font-weight: 700; margin-left: 0.5rem;';
            alertBadge.innerHTML = `
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="margin-right: 3px; flex-shrink: 0;">
                    <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
                    <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
                </svg>
                Deadlock: ${summary.blocking_sessions_count} Blocked
            `;
            nameContainer.appendChild(alertBadge);
        }
    }
    
    // Update Standby Status row on connection card
    const stbyVal = document.getElementById(`card-stby-val-${dbKey}`);
    const stbyRow = document.getElementById(`card-stby-row-${dbKey}`);
    if (stbyVal && stbyRow) {
        // Default single-line sizing/alignment - only the "Not Synced" case
        // below (whose "(Gap: N)" suffix can wrap to a second line) overrides
        // these, so it doesn't collide with the "Standby" label to its left.
        stbyVal.style.fontSize = '';
        stbyVal.style.textAlign = '';
        stbyRow.style.alignItems = 'center';

        if (!summary || !summary.standby_status || summary.standby_status === 'Not Configured') {
            stbyVal.innerText = 'Not Configured';
            stbyVal.style.color = '#64748b';
            stbyVal.style.fontWeight = '500';
            stbyVal.title = '';
        } else if (summary.standby_status === 'Synced') {
            stbyVal.innerText = '✅ Synced';
            stbyVal.style.color = '#10b981';
            stbyVal.style.fontWeight = '700';
            stbyVal.title = `Primary: ${summary.standby_primary_seq} · Standby: ${summary.standby_applied_seq}`;
        } else if (summary.standby_status === 'Not Synced') {
            stbyVal.innerText = `⚠️ Not Synced (Gap: ${summary.archive_gap})`;
            stbyVal.style.color = '#f59e0b';
            stbyVal.style.fontWeight = '700';
            stbyVal.style.fontSize = '0.72rem';
            stbyVal.style.textAlign = 'right';
            stbyRow.style.alignItems = 'flex-start';
            stbyVal.title = `Primary: ${summary.standby_primary_seq} · Standby: ${summary.standby_applied_seq}\nError: ${summary.standby_error || ''}`;
        } else if (summary.standby_status === 'Not Available') {
            stbyVal.innerText = '🔴 Not Available';
            stbyVal.style.color = '#ef4444';
            stbyVal.style.fontWeight = '700';
            stbyVal.title = summary.standby_error || 'Standby database is unreachable';
        } else {
            stbyVal.innerText = '🔴 DOWN';
            stbyVal.style.color = '#ef4444';
            stbyVal.style.fontWeight = '700';
            stbyVal.title = summary.standby_error || 'Connection failed';
        }
    }

    // Update Reporting Status row on connection card
    const repVal = document.getElementById(`card-rep-val-${dbKey}`);
    const repRow = document.getElementById(`card-rep-row-${dbKey}`);
    if (repVal && repRow) {
        if (!summary || !summary.reporting_status || summary.reporting_status === 'Not Configured') {
            repVal.innerText = 'Not Configured';
            repVal.style.color = '#64748b';
            repVal.style.fontWeight = '500';
        } else if (summary.reporting_status === 'UP') {
            repVal.innerText = '🟢 UP';
            repVal.style.color = '#10b981';
            repVal.style.fontWeight = '700';
        } else {
            repVal.innerText = '🔴 DOWN';
            repVal.style.color = '#ef4444';
            repVal.style.fontWeight = '700';
        }
    }
    
    const dbStatusVal = document.getElementById(`card-db-status-val-${dbKey}`);
    if (dbStatusVal) {
        if (summary.db_status === 'Connected') {
            dbStatusVal.innerText = '↑';
            dbStatusVal.style.color = '#10b981';
        } else if (summary.db_status === 'Checking...' || summary.db_status === 'Fetching...') {
            dbStatusVal.innerText = '•';
            dbStatusVal.style.color = '#64748b';
        } else {
            dbStatusVal.innerText = '↓';
            dbStatusVal.style.color = '#ef4444';
        }
    }
    
    const lisStatusVal = document.getElementById(`card-lis-status-val-${dbKey}`);
    if (lisStatusVal) {
        if (summary.listener_status === 'Running') {
            lisStatusVal.innerText = '↑';
            lisStatusVal.style.color = '#10b981';
        } else if (summary.listener_status === 'Checking...' || !summary.listener_status) {
            if (summary.db_status === 'Checking...' || summary.db_status === 'Fetching...') {
                lisStatusVal.innerText = '•';
                lisStatusVal.style.color = '#64748b';
            } else {
                lisStatusVal.innerText = '↓';
                lisStatusVal.style.color = '#ef4444';
            }
        } else {
            lisStatusVal.innerText = '↓';
            lisStatusVal.style.color = '#ef4444';
        }
    }
    
    const uptimeVal = document.getElementById(`card-uptime-val-${dbKey}`);
    if (uptimeVal) {
        uptimeVal.innerText = summary.uptime || 'N/A';
    }

    const activeUsersVal = document.getElementById(`card-active-users-val-${dbKey}`);
    if (activeUsersVal) {
        activeUsersVal.innerText = (summary.db_status === 'Connected') ? (summary.active_sessions !== undefined && summary.active_sessions !== null ? summary.active_sessions : 0) : 'N/A';
    }

    const backupVal = document.getElementById(`card-backup-val-${dbKey}`);
    if (backupVal) {
        if (summary.db_status === 'Connected') {
            const status = summary.last_backup_status || 'Unknown';
            const time = summary.last_backup_time || 'N/A';
            
            let displayText = status;
            if (!backupIsHealthy) {
                displayText = 'Pending';
            }
            backupVal.innerText = displayText;
            
            if (backupIsHealthy) {
                backupVal.style.color = '#10b981';
                backupVal.style.fontWeight = '600';
            } else {
                backupVal.style.color = '#f59e0b';
                backupVal.style.fontWeight = '700';
            }
            if (time && time !== 'N/A') {
                const titleStr = backupIsHealthy 
                    ? `Last Backup Date/Time: ${time}` 
                    : `Last Backup Date/Time: ${time} (Warning: Backup not executed yesterday or today!)`;
                backupVal.title = titleStr;
            } else {
                backupVal.title = 'Backup time details unavailable';
            }
        } else {
            backupVal.innerText = 'N/A';
            backupVal.style.color = '';
            backupVal.style.fontWeight = '';
            backupVal.title = '';
        }
    }

    const tbVal = document.getElementById(`card-tablespace-val-${dbKey}`);
    const tbBarContainer = document.getElementById(`card-tb-bar-container-${dbKey}`);
    const tbBarFill = document.getElementById(`card-tb-bar-fill-${dbKey}`);
    const dbErrorBox = document.getElementById(`card-db-error-box-${dbKey}`);
    
    if (tbVal) {
        if (summary.db_status === 'Connected') {
            if (dbErrorBox) {
                dbErrorBox.style.display = 'none';
                dbErrorBox.innerText = '';
            }
            const above90 = summary.above_90_tablespaces || [];
            if (above90.length > 0) {
                tbVal.innerText = above90.join(', ');
                tbVal.style.color = '#ef4444';
                tbVal.style.fontWeight = '700';
                tbVal.style.fontSize = '0.78rem';
                tbVal.style.maxWidth = 'none';
                tbVal.style.whiteSpace = 'normal';
                tbVal.style.overflow = 'visible';
                tbVal.title = `Critical Tablespace(s) > 90%: ${above90.join(', ')}`;
                if (tbBarContainer) {
                    tbBarContainer.style.display = 'none';
                }
            } else {
                tbVal.innerText = '';
                tbVal.style.color = '';
                tbVal.style.fontWeight = '';
                tbVal.style.fontSize = '';
                tbVal.style.maxWidth = '';
                tbVal.style.whiteSpace = '';
                tbVal.style.overflow = '';
                tbVal.title = '';
                if (tbBarContainer) {
                    tbBarContainer.style.display = 'none';
                }
            }
        } else {
            tbVal.innerText = 'N/A';
            tbVal.style.color = '';
            tbVal.style.fontWeight = '';
            tbVal.style.fontSize = '';
            tbVal.style.maxWidth = '';
            tbVal.style.whiteSpace = '';
            tbVal.style.overflow = '';
            tbVal.title = '';
            if (tbBarContainer) {
                tbBarContainer.style.display = 'none';
            }
            if (dbErrorBox) {
                if (summary.db_status === 'Checking...' || summary.db_status === 'Fetching...') {
                    dbErrorBox.innerText = 'Checking connection status...';
                    dbErrorBox.style.color = '#64748b';
                    dbErrorBox.style.display = 'block';
                } else {
                    dbErrorBox.innerText = summary.db_error || 'Connection failed';
                    dbErrorBox.style.color = '#ef4444';
                    dbErrorBox.style.display = 'block';
                }
            }
        }
    }

    const tbWarnBox = document.getElementById(`card-tb-warn-box-${dbKey}`);
    const tbWarnList = document.getElementById(`card-tb-warn-list-${dbKey}`);
    
    if (tbWarnBox && tbWarnList) {
        if (summary && summary.db_status === 'Connected' && summary.has_full_tablespace && summary.full_tablespaces && summary.full_tablespaces.length > 0) {
            tbWarnBox.style.display = 'flex';
            tbWarnList.innerText = summary.full_tablespaces.join(', ');
        } else {
            tbWarnBox.style.display = 'none';
            tbWarnList.innerText = '';
        }
    }
    
    const mountWarnBox = document.getElementById(`card-mount-warn-box-${dbKey}`);
    const mountWarnList = document.getElementById(`card-mount-warn-list-${dbKey}`);
    if (mountWarnBox && mountWarnList) {
        if (summary && summary.db_status === 'Connected' && summary.has_critical_mount && summary.critical_mount_points && summary.critical_mount_points.length > 0) {
            mountWarnBox.style.display = 'flex';
            mountWarnList.innerText = summary.critical_mount_points.join(', ');
        } else {
            mountWarnBox.style.display = 'none';
            mountWarnList.innerText = '';
        }
    }
    
    const backupWarnBox = document.getElementById(`card-backup-warn-box-${dbKey}`);
    if (backupWarnBox) {
        if (summary && summary.db_status === 'Connected' && !backupIsHealthy) {
            backupWarnBox.style.display = 'flex';
        } else {
            backupWarnBox.style.display = 'none';
        }
    }
    
    const lockWarnBox = document.getElementById(`card-lock-warn-box-${dbKey}`);
    const lockWarnMsg = document.getElementById(`card-lock-warn-msg-${dbKey}`);
    if (lockWarnBox && lockWarnMsg) {
        if (summary && summary.db_status === 'Connected' && summary.blocking_sessions_count > 0) {
            lockWarnBox.style.display = 'flex';
            lockWarnMsg.innerText = `Deadlock/Blocking session detected: ${summary.blocking_sessions_count} blocked session(s) active.`;
        } else {
            lockWarnBox.style.display = 'none';
            lockWarnMsg.innerText = '';
        }
    }
}

function renderQuickConnectCards(databases, autoConnectIds = []) {
    const grid = document.getElementById('quick-connect-grid');
    if (!grid) return;
    
    grid.innerHTML = '';
    
    if (!databases || databases.length === 0) {
        grid.innerHTML = '<p style="grid-column: 1/-1; text-align: center; color: #64748b; font-size: 0.9rem;">No databases available. Please import a database configuration file above.</p>';
        updateStatsCounters(0, 0, 0);
        const filterCountAll = document.getElementById('filter-count-all');
        if (filterCountAll) filterCountAll.innerText = '0';
        return;
    }

    const filteredDbs = databases.filter(db => {
        if (!dbSearchQuery) return true;
        const q = dbSearchQuery.toLowerCase();
        return (db.db_id && db.db_id.toLowerCase().includes(q)) ||
               (db.host && db.host.toLowerCase().includes(q)) ||
               (db.service_name && db.service_name.toLowerCase().includes(q));
    });

    const filterCountAll = document.getElementById('filter-count-all');
    if (filterCountAll) filterCountAll.innerText = filteredDbs.length;

    if (filteredDbs.length === 0) {
        grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 2.2rem 1.5rem; color: #64748b; background: #ffffff; border: 1px dashed #cbd5e1; border-radius: 12px; margin: 0.5rem 0 1.5rem 0; width: 100%; box-sizing: border-box;">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" style="margin: 0 auto 0.6rem auto; display: block;">
                <circle cx="11" cy="11" r="8"></circle>
                <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
            </svg>
            <p style="margin: 0; font-size: 0.98rem; font-weight: 700; color: #334155;">No databases matching "${escapeHtml(dbSearchQuery)}"</p>
            <p style="margin: 0.3rem 0 0 0; font-size: 0.8rem; color: #94a3b8;">Check the database name or clear the search filter to view all connections.</p>
        </div>`;
        return;
    }
    
    filteredDbs.forEach((db, idx) => {
        const dbKey = db.db_id.toLowerCase().replace(/[^a-z0-9]/g, '_');
        
        const card = document.createElement('div');
        card.className = 'card db-quick-card';
        card.id = `quick-card-${dbKey}`;
        card.style.minHeight = 'auto';
        card.style.width = '100%';
        card.style.cursor = 'pointer';
        
        card.onclick = () => {
            setActiveDatabase(db.db_id);
        };
        
        card.innerHTML = `
            <div class="quick-card-header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;">
                <div id="card-name-container-${dbKey}" style="display: flex; align-items: center; gap: 0.5rem;">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#475569" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <ellipse cx="12" cy="5" rx="9" ry="3"></ellipse>
                        <path d="M3 5V19A9 3 0 0 0 21 19V5"></path>
                        <path d="M3 12A9 3 0 0 0 21 12"></path>
                    </svg>
                    <h3 style="font-size: 1.05rem; font-weight: 600; color: #1e293b; margin: 0;">${db.db_id}</h3>
                </div>
                <div style="display: flex; align-items: center; gap: 0.6rem;">
                    <span class="sidebar-status-dot grey" id="card-dot-${dbKey}"></span>
                </div>
            </div>
            
            <div class="quick-card-details" style="margin-bottom: 0;">
                <div class="quick-card-detail-row">
                    <span class="quick-card-detail-label">DB Status</span>
                    <span class="quick-card-detail-value" id="card-db-status-val-${dbKey}" style="font-weight: 800; font-size: 1.15rem; line-height: 1; color: #64748b;">•</span>
                </div>
                <div class="quick-card-detail-row">
                    <span class="quick-card-detail-label">Listener Status</span>
                    <span class="quick-card-detail-value" id="card-lis-status-val-${dbKey}" style="font-weight: 800; font-size: 1.15rem; line-height: 1; color: #64748b;">•</span>
                </div>
                <div class="quick-card-detail-row">
                    <span class="quick-card-detail-label">Uptime</span>
                    <span class="quick-card-detail-value" id="card-uptime-val-${dbKey}">Checking...</span>
                </div>
                <div class="quick-card-detail-row">
                    <span class="quick-card-detail-label">Active Users</span>
                    <span class="quick-card-detail-value" id="card-active-users-val-${dbKey}">Checking...</span>
                </div>
                <div class="quick-card-detail-row">
                    <span class="quick-card-detail-label">Backup</span>
                    <span class="quick-card-detail-value" id="card-backup-val-${dbKey}">Checking...</span>
                </div>
                <div class="quick-card-detail-row" style="margin-top: 0.25rem;">
                    <span class="quick-card-detail-label">Tablespace</span>
                    <div style="display: flex; align-items: center; gap: 0.4rem; width: 60%; justify-content: flex-end;">
                        <div style="background-color: #f1f5f9; height: 6px; border-radius: 9999px; overflow: hidden; flex: 1; display: none;" id="card-tb-bar-container-${dbKey}">
                            <div style="height: 100%; border-radius: 9999px; width: 0%; transition: width 0.4s ease;" id="card-tb-bar-fill-${dbKey}"></div>
                        </div>
                        <span class="quick-card-detail-value" id="card-tablespace-val-${dbKey}">Checking...</span>
                    </div>
                </div>
                <div class="quick-card-detail-row" id="card-stby-row-${dbKey}">
                    <span class="quick-card-detail-label">Standby</span>
                    <span class="quick-card-detail-value" id="card-stby-val-${dbKey}">Checking...</span>
                </div>
                <div class="quick-card-detail-row" id="card-rep-row-${dbKey}">
                    <span class="quick-card-detail-label">Reporting</span>
                    <span class="quick-card-detail-value" id="card-rep-val-${dbKey}">Checking...</span>
                </div>
                ${db.rep_db_id ? `
                <div class="quick-card-rep-details" style="font-size: 0.72rem; color: #64748b; margin-top: -2px; margin-bottom: 4px; text-align: right; width: 100%; word-break: break-all; opacity: 0.85; font-family: monospace;">
                    ${escapeHtml(db.rep_db_id)} | ${escapeHtml(db.rep_host)} | ${escapeHtml(db.rep_port)} | ${escapeHtml(db.rep_service_name)} | ${escapeHtml(db.rep_username)} | ${escapeHtml(db.rep_password)}
                </div>
                ` : ''}
                <!-- Connection Error Box -->
                <div id="card-db-error-box-${dbKey}" style="display: none; margin-top: 0.25rem; font-size: 0.7rem; color: #ef4444; font-weight: 600; text-align: right; width: 100%; word-break: break-word; line-height: 1.2;"></div>
                <!-- Tablespace Critical Warning Box -->
                <div id="card-tb-warn-box-${dbKey}" style="display: none; margin-top: 0.4rem; flex-direction: column; background: #fff5f5; border: 1px solid #feb2b2; border-radius: 4px; padding: 0.4rem; font-size: 0.72rem; color: #c53030;">
                    <div style="font-weight: 700; display: flex; align-items: center; gap: 0.25rem; margin-bottom: 0.15rem;">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink: 0;">
                            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
                            <line x1="12" y1="9" x2="12" y2="13"></line>
                            <line x1="12" y1="17" x2="12.01" y2="17"></line>
                        </svg>
                        Critical Tablespace:
                    </div>
                    <div id="card-tb-warn-list-${dbKey}" style="font-weight: 600; padding-left: 0.95rem;"></div>
                </div>
                <!-- Mount Space Warning Box -->
                <div id="card-mount-warn-box-${dbKey}" style="display: none; margin-top: 0.4rem; flex-direction: column; background: #fff5f5; border: 1px solid #feb2b2; border-radius: 4px; padding: 0.4rem; font-size: 0.72rem; color: #c53030;">
                    <div style="font-weight: 700; display: flex; align-items: center; gap: 0.25rem; margin-bottom: 0.15rem;">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink: 0;">
                            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
                            <line x1="12" y1="9" x2="12" y2="13"></line>
                            <line x1="12" y1="17" x2="12.01" y2="17"></line>
                        </svg>
                        Critical Mount Point(s) (>90%):
                    </div>
                    <div id="card-mount-warn-list-${dbKey}" style="font-weight: 600; padding-left: 0.95rem;"></div>
                </div>
                <!-- Lock Deadlock Warning Box -->
                <div id="card-lock-warn-box-${dbKey}" style="display: none; margin-top: 0.4rem; flex-direction: column; background: #fef2f2; border: 1px solid #fca5a5; border-radius: 4px; padding: 0.4rem; font-size: 0.72rem; color: #b91c1c; text-align: left; width: 100%;">
                    <div style="font-weight: 700; display: flex; align-items: center; gap: 0.25rem; margin-bottom: 0.15rem;">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink: 0;">
                            <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
                            <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
                        </svg>
                        Lock/Deadlock Warning:
                    </div>
                    <div id="card-lock-warn-msg-${dbKey}" style="font-weight: 600; padding-left: 0.95rem;"></div>
                </div>
                <!-- Backup Pending Warning Box -->
                <div id="card-backup-warn-box-${dbKey}" style="display: none; margin-top: 0.4rem; flex-direction: column; background: #fffbeb; border: 1px solid #fde68a; border-radius: 4px; padding: 0.4rem; font-size: 0.70rem; color: #b45309; text-align: left; width: 100%;">
                    <div style="font-weight: 700; display: flex; align-items: center; gap: 0.25rem; margin-bottom: 0.15rem;">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink: 0;">
                            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
                            <line x1="12" y1="9" x2="12" y2="13"></line>
                            <line x1="12" y1="17" x2="12.01" y2="17"></line>
                        </svg>
                        Backup Warning:
                    </div>
                    <div id="card-backup-warn-desc-${dbKey}" style="font-weight: 600; padding-left: 0.95rem;">Backup is Pending / Outdated</div>
                </div>
            </div>
        `;
        grid.appendChild(card);
    });
    
    pollDatabaseStatuses(databases);
}

// File Selection Handlers
function handleFileSelect(event) {
    const file = event.target.files[0];
    if (file) {
        uploadDbFile(file);
    }
}

function handleFileDrop(event) {
    event.preventDefault();
    const file = event.dataTransfer.files[0];
    if (file) {
        uploadDbFile(file);
    }
}

// Configuration status indicator ("Uploaded" right after a file is
// processed, "Refreshed" whenever the page loads and the config is
// automatically re-read server-side). Reflects the backend's single shared
// state - see set_config_status() in app.py - so it stays correct even if
// the page was never manually reloaded since the last upload.
function setConfigStatusBadge(state, updatedAt) {
    const badge = document.getElementById('config-status-badge');
    const dot = document.getElementById('config-status-dot');
    const text = document.getElementById('config-status-text');
    if (!badge || !dot || !text) return;

    const isRefreshed = state === 'Refreshed';
    badge.dataset.state = state;
    badge.style.background = isRefreshed ? '#eff6ff' : '#f0fdf4';
    badge.style.color = isRefreshed ? '#1d4ed8' : '#15803d';
    dot.style.background = isRefreshed ? '#2563eb' : '#16a34a';
    text.textContent = state;
    if (updatedAt) {
        badge.title = `Configuration last updated: ${updatedAt}`;
    }
}

let uploadStatusTimeout = null;

// Config upload/refresh/restore feedback, rendered as the same floating
// popup style as showGlobalToast (rather than an inline banner in the page
// flow). Keeps a single, stable toast element so a fast sequence of calls
// (e.g. "Uploading..." immediately followed by "Uploaded successfully!")
// updates that one popup in place instead of stacking duplicates.
function showUploadStatus(message, type = 'success', autoCloseMs = 7000) {
    if (uploadStatusTimeout) {
        clearTimeout(uploadStatusTimeout);
        uploadStatusTimeout = null;
    }

    const container = _getGlobalToastContainer();
    const { color, icon } = _TOAST_STYLES[type] || _TOAST_STYLES.success;

    let toast = document.getElementById('upload-status-toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'upload-status-toast';
        toast.className = 'global-toast';
        container.appendChild(toast);
    }
    toast.style.opacity = '1';
    toast.style.color = color;
    toast.innerHTML = `
        ${icon}
        <span style="flex: 1;">${message}</span>
        <button onclick="hideUploadStatus()" title="Dismiss notification" style="background: none; border: none; color: ${color}; font-size: 1.1rem; cursor: pointer; padding: 0 0 0 0.3rem; line-height: 1; opacity: 0.7; transition: opacity 0.15s;" onmouseover="this.style.opacity='1'" onmouseout="this.style.opacity='0.7'">&times;</button>
    `;

    if (autoCloseMs > 0) {
        uploadStatusTimeout = setTimeout(() => {
            hideUploadStatus();
        }, autoCloseMs);
    }
}

function hideUploadStatus() {
    const toast = document.getElementById('upload-status-toast');
    if (toast) {
        toast.style.transition = 'opacity 0.25s ease';
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 250);
    }
    if (uploadStatusTimeout) {
        clearTimeout(uploadStatusTimeout);
        uploadStatusTimeout = null;
    }
}

function uploadDbFile(file) {
    const reader = new FileReader();
    reader.onload = function(e) {
        const content = e.target.result;
        performUpload(file, content);
    };
    reader.readAsText(file);
}

function performUpload(file, content) {
    showUploadStatus(`Uploading "${file.name}"...`, 'info', 0);

    const formData = new FormData();
    formData.append('file', file);

    fetch('/api/upload-databases', {
        method: 'POST',
        body: formData
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            showUploadStatus(`✅ File "${file.name}" uploaded successfully! ${data.message || ''}`, 'success', 7000);
            if (data.config_status) {
                setConfigStatusBadge(data.config_status.state, data.config_status.updated_at);
            }
            loadDatabases(true, data.db_ids);
            loadServersOverview();
            loadImportHistory();
            // Refresh the homepage Oracle Server Processes summary immediately
            // instead of waiting for the 20s auto-refresh interval, so newly
            // uploaded databases/servers show up right away. The backend
            // cache was also invalidated server-side by this same upload, so
            // this fetch won't return stale pre-upload data.
            loadHomeOracleProcesses();
        } else {
            showUploadStatus(`❌ Upload Error: ${data.error || 'Failed to process file'}`, 'error', 10000);
        }
    })
    .catch(err => {
        console.error("Upload failed:", err);
        showUploadStatus(`❌ Upload failed for "${file.name}": network error or server down.`, 'error', 10000);
    });
}

function deleteDatabasePrompt(dbId) {
    showCustomConfirm({
        title: 'Delete Database?',
        message: `Are you sure you want to delete database "${dbId}"? This will permanently remove its configuration.`,
        type: 'danger',
        confirmText: 'Delete',
        cancelText: 'Cancel'
    }).then(confirmed => {
        if (!confirmed) return;

        fetch('/api/delete-database', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ db_id: dbId })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                loadDatabases(true);
            } else {
                showCustomAlert({
                    title: 'Delete Failed',
                    message: `Error deleting database: ${data.error}`,
                    type: 'danger'
                });
            }
        })
        .catch(err => {
            console.error("Delete failed:", err);
            showCustomAlert({
                title: 'Network Error',
                message: 'Failed to delete database: network error.',
                type: 'danger'
            });
        });
    });
}

// Close sidebar when clicking outside on mobile
document.addEventListener('click', (e) => {
    if (window.innerWidth <= 768) {
        const sidebar = document.querySelector('.sidebar');
        const toggleBtn = document.getElementById('sidebar-toggle');
        if (sidebar && !sidebar.classList.contains('hidden')) {
            if (!sidebar.contains(e.target) && (!toggleBtn || !toggleBtn.contains(e.target))) {
                sidebar.classList.add('hidden');
            }
        }
    }
});

function loadImportHistory() {
    fetch('/api/upload-history')
        .then(res => res.json())
        .then(data => {
            if (data.success && Array.isArray(data.history)) {
                renderImportHistory(data.history);
            }
        })
        .catch(err => {
            console.error("Failed to load upload history:", err);
        });
}

function renderImportHistory(history) {
    const listEl = document.getElementById('import-history-list');
    if (!listEl) return;

    if (!history || history.length === 0) {
        listEl.innerHTML = '<li style="color: #94a3b8; font-style: italic; font-size: 0.75rem; padding: 0.5rem 0; text-align: center;">No database files uploaded yet</li>';
        return;
    }

    listEl.innerHTML = history.map(item => {
        const isActive = item.status === 'ACTIVE';
        const bg = isActive ? '#f0fdf4' : '#f8fafc';
        const border = isActive ? '#a7f3d0' : '#e2e8f0';
        const badgeBg = isActive ? '#10b981' : '#94a3b8';
        const badgeText = isActive ? 'ACTIVE' : 'INACTIVE';
        const dbsSummary = Array.isArray(item.db_ids) && item.db_ids.length > 0 
            ? item.db_ids.join(', ') 
            : `${item.db_count || 0} DBs`;

        return `
            <li class="import-history-item" style="display: flex; flex-direction: column; gap: 0.35rem; padding: 0.55rem 0.65rem; background-color: ${bg}; border: 1px solid ${border}; border-radius: 8px; transition: all 0.2s; box-shadow: 0 1px 2px rgba(0,0,0,0.03);">
                <div style="display: flex; align-items: center; justify-content: space-between; gap: 0.4rem;">
                    <div style="display: flex; align-items: center; gap: 0.35rem; overflow: hidden; flex: 1;">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="${isActive ? '#10b981' : '#64748b'}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink: 0;">
                            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                            <polyline points="14 2 14 8 20 8"></polyline>
                        </svg>
                        <span class="import-history-name" style="font-weight: 700; color: ${isActive ? '#065f46' : '#334155'}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 0.76rem;" title="${item.filename}">${item.filename}</span>
                    </div>
                    <span style="background-color: ${badgeBg}; color: #ffffff; font-size: 0.58rem; font-weight: 800; padding: 2px 6px; border-radius: 10px; letter-spacing: 0.05em; flex-shrink: 0;">${badgeText}</span>
                </div>
                
                <div style="display: flex; align-items: center; justify-content: space-between; font-size: 0.65rem; color: #64748b; margin-top: 0.1rem;">
                    <span title="Uploaded DBs: ${dbsSummary}" style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 130px; font-weight: 500;">
                        📁 ${item.db_count || 0} DBs (${dbsSummary})
                    </span>
                    <span style="color: #94a3b8; font-size: 0.62rem;">${item.upload_time || ''}</span>
                </div>

                <div style="display: flex; align-items: center; justify-content: flex-end; gap: 0.35rem; margin-top: 0.2rem; border-top: 1px dashed ${isActive ? '#bbf7d0' : '#e2e8f0'}; padding-top: 0.25rem;">
                    ${!isActive ? `
                        <button onclick="restoreHistoryUpload('${item.upload_id}', event)" title="Make this file the active database configuration" style="display: flex; align-items: center; gap: 0.25rem; background: #ffffff; border: 1px solid #10b981; color: #059669; font-size: 0.68rem; font-weight: 700; padding: 2px 7px; border-radius: 4px; cursor: pointer; transition: all 0.15s;" onmouseover="this.style.background='#10b981'; this.style.color='#ffffff';" onmouseout="this.style.background='#ffffff'; this.style.color='#059669';">
                            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                <polyline points="23 4 23 10 17 10"></polyline>
                                <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
                            </svg>
                            Restore
                        </button>
                    ` : `
                        <span style="font-size: 0.65rem; color: #059669; font-weight: 700; display: flex; align-items: center; gap: 0.2rem;">
                            ✓ Currently Active
                        </span>
                    `}
                    <button onclick="deleteHistoryUpload('${item.upload_id}', event)" title="Delete from history" style="background: none; border: none; padding: 2px 4px; cursor: pointer; color: #94a3b8; display: flex; align-items: center; justify-content: center; transition: color 0.15s; border-radius: 4px;" onmouseover="this.style.color='#ef4444'; this.style.backgroundColor='#fef2f2';" onmouseout="this.style.color='#94a3b8'; this.style.backgroundColor='transparent';">
                        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                            <polyline points="3 6 5 6 21 6"></polyline>
                            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                        </svg>
                    </button>
                </div>
            </li>
        `;
    }).join('');
}

function restoreHistoryUpload(uploadId, event) {
    if (event) event.stopPropagation();
    showUploadStatus("Restoring database configuration...", "info", 1800);

    fetch('/api/upload-history/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ upload_id: uploadId })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            showUploadStatus(`✅ ${data.message}`, 'success', 6000);
            loadDatabases(true);
            loadServersOverview();
            loadImportHistory();
        } else {
            showUploadStatus(`❌ Restore Error: ${data.error}`, 'error', 8000);
        }
    })
    .catch(err => {
        console.error("Restore failed:", err);
        showUploadStatus("❌ Failed to restore configuration: network error.", 'error', 8000);
    });
}

function deleteHistoryUpload(uploadId, event) {
    if (event) event.stopPropagation();
    showCustomConfirm({
        title: 'Delete Upload History Entry?',
        message: 'Are you sure you want to delete this configuration file from history?',
        type: 'danger',
        confirmText: 'Delete',
        cancelText: 'Cancel'
    }).then(confirmed => {
        if (!confirmed) return;

        fetch(`/api/upload-history/${uploadId}`, {
            method: 'DELETE'
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                showUploadStatus(`✅ ${data.message}`, 'info', 4000);
                loadDatabases(true);
                loadServersOverview();
                loadImportHistory();
            } else {
                showCustomAlert({
                    title: 'Delete Failed',
                    message: data.error,
                    type: 'danger'
                });
            }
        })
        .catch(err => {
            console.error("Delete history failed:", err);
        });
    });
}

// Custom DB Access History functions
function addToDbHistory(dbId) {
    let history = JSON.parse(localStorage.getItem('dbAccessHistory') || '[]');
    history = history.filter(id => id !== dbId);
    history.unshift(dbId);
    if (history.length > 5) history.pop();
    localStorage.setItem('dbAccessHistory', JSON.stringify(history));
    renderDbHistoryDropdown();
}

function clearDbHistory() {
    localStorage.removeItem('dbAccessHistory');
    renderDbHistoryDropdown();
}

function renderDbHistoryDropdown() {
    const listEl = document.getElementById('history-dropdown-content');
    if (!listEl) return;
    
    const history = JSON.parse(localStorage.getItem('dbAccessHistory') || '[]');
    if (history.length === 0) {
        listEl.innerHTML = '<div class="history-empty">No recently viewed databases</div>';
        return;
    }
    
    let html = '';
    history.forEach(dbId => {
        const dbConfig = (typeof globalDatabases !== 'undefined' ? globalDatabases : []).find(d => d.db_id === dbId);
        const hostSub = dbConfig ? `${dbConfig.host}:${dbConfig.port}` : 'Oracle Database';
        
        const dbKey = dbId.toLowerCase().replace(/[^a-z0-9]/g, '_');
        const cachedStatus = (typeof dbStatuses !== 'undefined' ? dbStatuses : {})[dbKey];
        let statusDotColor = '#cbd5e1'; // grey
        if (cachedStatus) {
            if (cachedStatus.db_status === 'Connected') {
                statusDotColor = '#22c55e'; // green
            } else if (cachedStatus.listener_status === 'Running') {
                statusDotColor = '#f59e0b'; // orange
            } else {
                statusDotColor = '#ef4444'; // red
            }
        }
        
        html += `
            <div class="history-item" onclick="setActiveDatabase('${dbId.replace(/'/g, "\\'")}')">
                <div class="history-item-left">
                    <span class="history-item-name">${dbId}</span>
                    <span class="history-item-sub">${hostSub}</span>
                </div>
                <span style="width: 8px; height: 8px; border-radius: 50%; background-color: ${statusDotColor}; display: inline-block; flex-shrink: 0;" title="Status Indicator"></span>
            </div>
        `;
    });
    
    html += `
        <div class="history-divider"></div>
        <button class="history-clear-btn" onclick="event.stopPropagation(); clearDbHistory();">Clear History</button>
    `;
    
    listEl.innerHTML = html;
}

function getCircularProgressSVG(percentage, label) {
    const radius = 24;
    const circumference = 2 * Math.PI * radius;
    
    const isNa = percentage === null || percentage === undefined || percentage === -1 || isNaN(percentage);
    const pctVal = isNa ? 0 : Math.min(100, Math.max(0, percentage));
    const offset = circumference - (pctVal / 100) * circumference;
    
    let color = '#22c55e'; // green
    if (isNa) {
        color = '#94a3b8'; // grey
    } else if (pctVal >= 80) {
        color = '#ef4444'; // red
    } else if (pctVal >= 50) {
        color = '#f59e0b'; // orange
    }
    
    const displayVal = isNa ? 'N/A' : `${Math.round(pctVal)}%`;
    
    return `
        <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 0.35rem;">
            <div style="position: relative; width: 62px; height: 62px; display: flex; align-items: center; justify-content: center;">
                <svg width="62" height="62" viewBox="0 0 62 62" style="transform: rotate(-90deg);">
                    <circle cx="31" cy="31" r="${radius}" stroke="#f1f5f9" stroke-width="5" fill="transparent" />
                    <circle cx="31" cy="31" r="${radius}" stroke="${color}" stroke-width="5" fill="transparent" 
                            stroke-dasharray="${circumference}" stroke-dashoffset="${offset}" 
                            stroke-linecap="round" style="transition: stroke-dashoffset 0.5s ease-in-out;" />
                </svg>
                <span style="position: absolute; font-size: 0.8rem; font-weight: 700; color: #1e293b;">${displayVal}</span>
            </div>
            <span style="font-size: 0.65rem; font-weight: 700; color: #475569; letter-spacing: 0.05em; text-transform: uppercase;">${label}</span>
        </div>
    `;
}

let currentProcessTab = 'both'; // 'both', 'cpu', or 'memory'
let cachedProcessData = null;
let processSearchQuery = '';
let processSortOption = 'default';

// Formats a raw MB figure for display, auto-scaling to GB once it's large
// enough that GB reads more naturally.
function formatMemoryMb(memMb) {
    const val = Number(memMb || 0);
    return val >= 1024 ? `${(val / 1024).toFixed(2)} GB` : `${val.toFixed(1)} MB`;
}

function filterAndSortProcesses(procs) {
    if (!procs || !Array.isArray(procs)) return [];

    let list = [...procs];

    if (processSearchQuery && processSearchQuery.trim()) {
        const query = processSearchQuery.trim().toLowerCase();
        list = list.filter(p => {
            const pidStr = String(p.pid || '');
            const nameStr = String(p.process || p.name || '').toLowerCase();
            const userStr = String(p.user || p.username || '').toLowerCase();
            return pidStr.includes(query) || nameStr.includes(query) || userStr.includes(query);
        });
    }

    if (processSortOption === 'cpu_desc') {
        list.sort((a, b) => (b.cpu_seconds || 0) - (a.cpu_seconds || 0));
    } else if (processSortOption === 'mem_desc') {
        list.sort((a, b) => (b.memory_mb || 0) - (a.memory_mb || 0));
    } else if (processSortOption === 'pid_asc') {
        list.sort((a, b) => (a.pid || 0) - (b.pid || 0));
    } else if (processSortOption === 'name_asc') {
        list.sort((a, b) => String(a.process || a.name || '').localeCompare(String(b.process || b.name || '')));
    }

    return list;
}

function handleProcessSearch() {
    const input = document.getElementById('process-search-input');
    processSearchQuery = input ? input.value : '';
    if (cachedProcessData) {
        renderTopProcessesTable(cachedProcessData);
    }
}

function handleProcessSort() {
    const select = document.getElementById('process-sort-select');
    processSortOption = select ? select.value : 'default';
    if (cachedProcessData) {
        renderTopProcessesTable(cachedProcessData);
    }
}

function setSortOption(option) {
    processSortOption = option;
    const select = document.getElementById('process-sort-select');
    if (select) select.value = option;
    if (cachedProcessData) {
        renderTopProcessesTable(cachedProcessData);
    }
}

function switchProcessTab(tabType) {
    currentProcessTab = tabType;
    const btnBoth = document.getElementById('btn-top-both-proc');
    const btnMem = document.getElementById('btn-top-mem-proc');
    const btnCpu = document.getElementById('btn-top-cpu-proc');

    [btnBoth, btnMem, btnCpu].forEach(b => b && b.classList.remove('active'));
    if (tabType === 'both' && btnBoth) btnBoth.classList.add('active');
    else if (tabType === 'memory' && btnMem) btnMem.classList.add('active');
    else if (tabType === 'cpu' && btnCpu) btnCpu.classList.add('active');

    if (cachedProcessData) {
        renderTopProcessesTable(cachedProcessData);
    }
}

function renderSingleProcessTable(title, procs, isCpuPrimary) {
    if (!procs || procs.length === 0) {
        return `
            <div class="card shadow-sm border-0 mb-3" style="border-radius: 10px; overflow: hidden; background: #ffffff; border: 1px solid #e2e8f0;">
                <div style="padding: 0.75rem 1.25rem; background: #f8fafc; border-bottom: 1px solid #e2e8f0; font-size: 0.82rem; font-weight: 700; color: #334155; text-transform: uppercase; letter-spacing: 0.04em;">
                    ${escapeHtml(title)}
                </div>
                <div style="padding: 1.5rem; text-align: center; color: #64748b; font-size: 0.82rem; font-style: italic;">
                    No matching processes found.
                </div>
            </div>
        `;
    }

    let rowsHtml = '';
    procs.forEach((p) => {
        const cpuVal = Number(p.cpu_seconds || 0);
        const memVal = Number(p.memory_mb || 0);
        const procName = String(p.process || p.name || '');
        const userName = String(p.user || p.username || '');

        const cpuTd = `
            <td style="font-size: 0.8rem; font-weight: 700; text-align: right; padding: 0.55rem 1rem; color: #334155;">
                ${cpuVal.toFixed(1)}s
            </td>
        `;

        const memTd = `
            <td style="font-size: 0.8rem; font-weight: 700; text-align: right; padding: 0.55rem 1rem; color: #334155;">
                ${escapeHtml(formatMemoryMb(memVal))}
            </td>
        `;

        rowsHtml += `
            <tr style="transition: background-color 0.15s ease;">
                <td style="font-size: 0.78rem; font-weight: 700; color: #475569; width: 75px; padding: 0.55rem 1rem;">#${p.pid}</td>
                <td style="font-size: 0.82rem; font-weight: 600; color: #0f172a; padding: 0.55rem 1rem;">
                    <div style="display: flex; align-items: center; gap: 0.45rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 380px;" title="${escapeHtml(procName)}">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#64748b" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink: 0;">
                            <rect x="4" y="4" width="16" height="16" rx="2" ry="2"></rect>
                            <rect x="9" y="9" width="6" height="6"></rect>
                            <line x1="9" y1="1" x2="9" y2="4"></line>
                            <line x1="15" y1="1" x2="15" y2="4"></line>
                            <line x1="9" y1="20" x2="9" y2="23"></line>
                            <line x1="15" y1="20" x2="15" y2="23"></line>
                            <line x1="20" y1="9" x2="23" y2="9"></line>
                            <line x1="20" y1="15" x2="23" y2="15"></line>
                            <line x1="1" y1="9" x2="4" y2="9"></line>
                            <line x1="1" y1="15" x2="4" y2="15"></line>
                        </svg>
                        <span style="overflow: hidden; text-overflow: ellipsis;">${escapeHtml(procName)}</span>
                    </div>
                </td>
                <td style="font-size: 0.75rem; color: #64748b; padding: 0.55rem 1rem;">
                    <span style="background-color: #f1f5f9; padding: 2px 7px; border-radius: 4px; font-weight: 500; border: 1px solid #e2e8f0; max-width: 120px; display: inline-block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; vertical-align: middle;">
                        ${escapeHtml(userName)}
                    </span>
                </td>
                ${isCpuPrimary ? cpuTd + memTd : memTd + cpuTd}
            </tr>
        `;
    });

    const col4Header = isCpuPrimary ? 'CPU Usage' : 'Memory Usage';
    const col5Header = isCpuPrimary ? 'Memory Usage' : 'CPU Usage';

    return `
        <div class="card shadow-sm border-0 mb-3" style="border-radius: 10px; overflow: hidden; background: #ffffff; border: 1px solid #e2e8f0;">
            <div style="padding: 0.7rem 1.1rem; background: #f8fafc; border-bottom: 1px solid #e2e8f0; display: flex; justify-content: space-between; align-items: center;">
                <span style="font-size: 0.82rem; font-weight: 800; color: #1e293b; text-transform: uppercase; letter-spacing: 0.04em;">
                    ${escapeHtml(title)}
                </span>
                <span style="font-size: 0.7rem; color: #64748b; font-weight: 600; background: #f1f5f9; padding: 2px 8px; border-radius: 12px; border: 1px solid #cbd5e1;">Top 10</span>
            </div>
            <div class="table-responsive" style="max-height: 380px; overflow-y: auto;">
                <table class="table table-hover align-middle mb-0" style="width: 100%;">
                    <thead style="background-color: #f1f5f9; position: sticky; top: 0; z-index: 2;">
                        <tr>
                            <th style="font-size: 0.72rem; font-weight: 700; color: #475569; text-transform: uppercase; padding: 0.65rem 1rem; border-bottom: 1px solid #e2e8f0; cursor: pointer;" onclick="setSortOption('pid_asc')">PID</th>
                            <th style="font-size: 0.72rem; font-weight: 700; color: #475569; text-transform: uppercase; padding: 0.65rem 1rem; border-bottom: 1px solid #e2e8f0; cursor: pointer;" onclick="setSortOption('name_asc')">Process</th>
                            <th style="font-size: 0.72rem; font-weight: 700; color: #475569; text-transform: uppercase; padding: 0.65rem 1rem; border-bottom: 1px solid #e2e8f0;">User</th>
                            <th style="font-size: 0.72rem; font-weight: 700; color: #475569; text-transform: uppercase; padding: 0.65rem 1rem; text-align: right; border-bottom: 1px solid #e2e8f0; cursor: pointer;" onclick="setSortOption('${isCpuPrimary ? 'cpu_desc' : 'mem_desc'}')">${col4Header}</th>
                            <th style="font-size: 0.72rem; font-weight: 700; color: #475569; text-transform: uppercase; padding: 0.65rem 1rem; text-align: right; border-bottom: 1px solid #e2e8f0; cursor: pointer;" onclick="setSortOption('${isCpuPrimary ? 'mem_desc' : 'cpu_desc'}')">${col5Header}</th>
                        </tr>
                    </thead>
                    <tbody style="background-color: #ffffff;">
                        ${rowsHtml}
                    </tbody>
                </table>
            </div>
        </div>
    `;
}

const collapsedServerGroups = new Set();

function toggleServerProcessGroup(id, btn) {
    const el = document.getElementById(id);
    if (!el) return;
    const span = btn.querySelector('span');
    const svg = btn.querySelector('.toggle-icon');
    if (el.style.display === 'none') {
        el.style.display = '';
        if (span) span.innerText = 'Hide';
        if (svg) svg.style.transform = 'none';
        collapsedServerGroups.delete(id);
    } else {
        el.style.display = 'none';
        if (span) span.innerText = 'Show';
        if (svg) svg.style.transform = 'rotate(180deg)';
        collapsedServerGroups.add(id);
    }
}

function renderTopProcessesTable(data) {
    const container = document.getElementById('servers-overview-container');
    if (!container) return;

    if (!data || data.status === 'error' || (!data.databases && !data.cpu_processes)) {
        const errorMsg = data ? (data.message || data.error || "Unable to load server process information.") : "Unable to load server process information.";
        container.innerHTML = `
            <div style="color: #ef4444; font-size: 0.88rem; font-weight: 600; text-align: center; padding: 1.5rem; background: #ffffff; border-radius: 10px; border: 1px solid #fee2e2;">
                ${escapeHtml(errorMsg)}
            </div>
        `;
        return;
    }

    // Support single database fallback key or multi-db keys mapping
    let databasesMap = {};
    if (data.databases) {
        databasesMap = data.databases;
    } else {
        // Fallback backward compatibility representation
        databasesMap['default_db'] = {
            "status": "success",
            "db_name": globalActiveDbId || "active_database",
            "server": "kasorcl",
            "host": "localhost",
            "cpu_processes": data.cpu_processes || [],
            "memory_processes": data.memory_processes || []
        };
    }

    const dbKeys = Object.keys(databasesMap);
    if (dbKeys.length === 0) {
        container.innerHTML = `
            <div style="color: #64748b; font-size: 0.88rem; font-weight: 500; text-align: center; padding: 1.5rem; background: #ffffff; border-radius: 10px; border: 1px solid #e2e8f0;">
                No configured databases found.
            </div>
        `;
        return;
    }

    // Group databases by server to avoid duplicating processes for the same host
    const groupedServers = {};
    dbKeys.forEach(dbId => {
        const dbInfo = databasesMap[dbId];
        const dbLabel = dbInfo.db_name || dbId;
        const serverLabel = (dbInfo.server || dbInfo.host || 'Unknown Server').trim();

        if (!groupedServers[serverLabel]) {
            groupedServers[serverLabel] = {
                dbLabels: [],
                host: dbInfo.host || 'N/A',
                status: 'failed',
                message: '',
                cpu_processes: [],
                memory_processes: []
            };
        }

        if (!groupedServers[serverLabel].dbLabels.includes(dbLabel)) {
            groupedServers[serverLabel].dbLabels.push(dbLabel);
        }

        if (dbInfo.status === 'success') {
            groupedServers[serverLabel].status = 'success';
            if (groupedServers[serverLabel].cpu_processes.length === 0) {
                groupedServers[serverLabel].cpu_processes = dbInfo.cpu_processes || [];
                groupedServers[serverLabel].memory_processes = dbInfo.memory_processes || [];
            }
        } else {
            if (groupedServers[serverLabel].status !== 'success') {
                groupedServers[serverLabel].message = dbInfo.message || 'Unable to retrieve process details.';
            }
        }
    });

    let html = '';
    Object.keys(groupedServers).forEach(serverLabel => {
        const serverData = groupedServers[serverLabel];
        const dbNamesJoined = serverData.dbLabels.join(', ');
        const isSuccess = serverData.status === 'success';
        const safeServerId = 'server-group-' + serverLabel.replace(/[^a-zA-Z0-9]/g, '-');
        const isCollapsed = collapsedServerGroups.has(safeServerId);

        html += `
            <div class="db-process-group" style="margin-bottom: 2rem; padding-bottom: 1.5rem; border-bottom: 1px solid #edf2f7;">
                <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.85rem; border-left: 4px solid ${isSuccess ? '#2563eb' : '#dc2626'}; padding-left: 0.75rem;">
                    <div>
                        <h4 style="margin: 0; font-size: 0.95rem; font-weight: 700; color: #1e293b; display: flex; align-items: center; gap: 0.4rem;">
                            Databases: <span style="color: #2563eb;">${escapeHtml(dbNamesJoined)}</span>
                        </h4>
                        <span style="font-size: 0.75rem; color: #64748b; font-weight: 500;">
                            Server: ${escapeHtml(serverLabel)} [Host/IP: ${escapeHtml(serverData.host)}]
                        </span>
                    </div>
                    <div style="display: flex; align-items: center; gap: 0.5rem;">
                        ${isSuccess 
                            ? `<span class="badge bg-success-light" style="font-size: 0.7rem; font-weight: 600; padding: 4px 8px; border-radius: 6px; background-color: #ecfdf5; color: #065f46; border: 1px solid #a7f3d0;">SSH Connected</span>`
                            : `<span class="badge bg-danger-light" style="font-size: 0.7rem; font-weight: 600; padding: 4px 8px; border-radius: 6px; background-color: #fef2f2; color: #991b1b; border: 1px solid #fecaca;">Connection Failed</span>`
                        }
                        <button onclick="toggleServerProcessGroup('${safeServerId}', this)" class="btn btn-sm" style="font-size: 0.73rem; font-weight: 600; padding: 4px 10px; border-radius: 6px; border: 1px solid #cbd5e1; background: #ffffff; color: #475569; display: inline-flex; align-items: center; gap: 0.35rem; cursor: pointer; transition: all 0.2s;" onmouseover="this.style.background='#f1f5f9';" onmouseout="this.style.background='#ffffff';">
                            <span>${isCollapsed ? 'Show' : 'Hide'}</span>
                            <svg class="toggle-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="transition: transform 0.2s; ${isCollapsed ? 'transform: rotate(180deg);' : ''}">
                                <polyline points="18 15 12 9 6 15"></polyline>
                            </svg>
                        </button>
                    </div>
                </div>
                <div id="${safeServerId}" style="${isCollapsed ? 'display: none;' : ''}">
        `;

        if (!isSuccess) {
            html += `
                <div style="color: #ef4444; font-size: 0.82rem; font-weight: 600; padding: 1rem 1.25rem; background: #fef2f2; border-radius: 8px; border: 1px solid #fecaca; margin-bottom: 0.5rem; display: flex; align-items: center; gap: 0.5rem;">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="12" cy="12" r="10"></circle>
                        <line x1="12" y1="8" x2="12" y2="12"></line>
                        <line x1="12" y1="16" x2="12.01" y2="16"></line>
                    </svg>
                    Error: ${escapeHtml(serverData.message || 'Unable to retrieve process details.')}
                </div>
            </div>
            </div>
            `;
            return;
        }

        const cpuProcsRaw = serverData.cpu_processes || [];
        const memProcsRaw = serverData.memory_processes || [];

        const filteredCpuProcs = filterAndSortProcesses(cpuProcsRaw).slice(0, 10);
        const filteredMemProcs = filterAndSortProcesses(memProcsRaw).slice(0, 10);

        if (currentProcessTab === 'cpu') {
            html += renderSingleProcessTable('Top CPU Consuming Processes', filteredCpuProcs, true);
        } else if (currentProcessTab === 'memory') {
            html += renderSingleProcessTable('Top Memory Consuming Processes', filteredMemProcs, false);
        } else {
            const cpuTable = renderSingleProcessTable('Top CPU Consuming Processes', filteredCpuProcs, true);
            const memTable = renderSingleProcessTable('Top Memory Consuming Processes', filteredMemProcs, false);

            html += `
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 1rem; width: 100%;">
                    <div>${cpuTable}</div>
                    <div>${memTable}</div>
                </div>
            `;
        }

        html += `</div>`; // Close collapsible container
        html += `</div>`; // Close db-process-group
    });

    container.innerHTML = html;
}

function loadServersOverview() {
    const container = document.getElementById('servers-overview-container');
    if (!container) return;

    if (container.children.length === 0 || container.querySelector('.servers-loading')) {
        container.innerHTML = `
            <div class="servers-loading" style="display: flex; justify-content: center; align-items: center; padding: 2rem; color: #64748b; font-size: 0.9rem; background: #ffffff; border-radius: 10px; border: 1px solid #e2e8f0;">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#2563eb" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="refresh-icon" style="animation: spin 1s linear infinite; margin-right: 0.6rem;">
                    <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"></path>
                </svg>
                Loading remote server processes via SSH...
            </div>
        `;
    }

    fetch('/api/server-processes')
        .then(res => res.json())
        .then(data => {
            if (data && data.status === 'checking') {
                setTimeout(loadServersOverview, 2000);
                return;
            }
            cachedProcessData = data;
            renderTopProcessesTable(data);
        })
        .catch(err => {
            console.error("Error loading remote server processes:", err);
            container.innerHTML = `
                <div style="color: #ef4444; font-size: 0.88rem; font-weight: 600; text-align: center; padding: 1.5rem; background: #ffffff; border-radius: 10px; border: 1px solid #fee2e2;">
                    Unable to load remote server process information via SSH.
                </div>
            `;
        });
}

// --- DASHBOARD DATABASE-WISE HOST PROCESSES ---
let dashboardCachedProcessData = null;
let currentDashboardProcessTab = 'both';
let dashboardProcessSearchQuery = '';
let dashboardProcessSortVal = 'default';

function fetchDashboardServerProcesses() {
    if (!globalActiveDbId) return;

    const container = document.getElementById('dashboard-servers-overview-container');
    if (!container) return;

    // Set loading indicator
    if (container.children.length === 0 || container.querySelector('.servers-loading')) {
        container.innerHTML = `
            <div class="servers-loading" style="display: flex; justify-content: center; align-items: center; padding: 2rem; color: #64748b; font-size: 0.9rem; background: #ffffff; border-radius: 10px; border: 1px solid #e2e8f0;">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#2563eb" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="refresh-icon" style="animation: spin 1s linear infinite; margin-right: 0.6rem;">
                    <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"></path>
                </svg>
                Loading remote host OS processes via SSH...
            </div>
        `;
    }

    fetch(`/api/server-processes?db_id=${encodeURIComponent(globalActiveDbId)}`)
        .then(res => res.json())
        .then(data => {
            if (data && data.status === 'checking') {
                setTimeout(fetchDashboardServerProcesses, 2000);
                return;
            }
            dashboardCachedProcessData = data;

            // Set Host Server Label
            let dbInfo = null;
            if (data.databases && data.databases[globalActiveDbId]) {
                dbInfo = data.databases[globalActiveDbId];
            } else if (data.cpu_processes) {
                dbInfo = data;
            }

            const serverNameEl = document.getElementById('dashboard-proc-server-name');
            if (serverNameEl && dbInfo) {
                serverNameEl.innerText = dbInfo.server || dbInfo.host || globalActiveDbId;
            }

            renderDashboardTopProcessesTable(data);
        })
        .catch(err => {
            console.error("Error loading dashboard server processes:", err);
            container.innerHTML = `
                <div style="color: #ef4444; font-size: 0.88rem; font-weight: 600; text-align: center; padding: 1.5rem; background: #ffffff; border-radius: 10px; border: 1px solid #fee2e2;">
                    Unable to load host server process information via SSH.
                </div>
            `;
        });
}

function filterAndSortDashboardProcesses(procs) {
    if (!procs || !Array.isArray(procs)) return [];

    let list = [...procs];

    if (dashboardProcessSearchQuery && dashboardProcessSearchQuery.trim()) {
        const query = dashboardProcessSearchQuery.trim().toLowerCase();
        list = list.filter(p => {
            const pidStr = String(p.pid || '');
            const nameStr = String(p.process || p.name || '').toLowerCase();
            const userStr = String(p.user || p.username || '').toLowerCase();
            return pidStr.includes(query) || nameStr.includes(query) || userStr.includes(query);
        });
    }

    if (dashboardProcessSortVal === 'cpu_desc') {
        list.sort((a, b) => (b.cpu_seconds || 0) - (a.cpu_seconds || 0));
    } else if (dashboardProcessSortVal === 'mem_desc') {
        list.sort((a, b) => (b.memory_mb || 0) - (a.memory_mb || 0));
    } else if (dashboardProcessSortVal === 'pid_asc') {
        list.sort((a, b) => (a.pid || 0) - (b.pid || 0));
    } else if (dashboardProcessSortVal === 'name_asc') {
        list.sort((a, b) => String(a.process || a.name || '').localeCompare(String(b.process || b.name || '')));
    }

    return list;
}

function handleDashboardProcessSearch() {
    const input = document.getElementById('dashboard-process-search-input');
    dashboardProcessSearchQuery = input ? input.value : '';
    if (dashboardCachedProcessData) {
        renderDashboardTopProcessesTable(dashboardCachedProcessData);
    }
}

function handleDashboardProcessSort() {
    const select = document.getElementById('dashboard-process-sort-select');
    dashboardProcessSortVal = select ? select.value : 'default';
    if (dashboardCachedProcessData) {
        renderDashboardTopProcessesTable(dashboardCachedProcessData);
    }
}

function switchDashboardProcessTab(tabType) {
    currentDashboardProcessTab = tabType;
    const btnBoth = document.getElementById('btn-dashboard-both-proc');
    const btnMem = document.getElementById('btn-dashboard-mem-proc');
    const btnCpu = document.getElementById('btn-dashboard-cpu-proc');

    if (btnBoth) btnBoth.classList.toggle('active', tabType === 'both');
    if (btnMem) btnMem.classList.toggle('active', tabType === 'memory');
    if (btnCpu) btnCpu.classList.toggle('active', tabType === 'cpu');

    if (dashboardCachedProcessData) {
        renderDashboardTopProcessesTable(dashboardCachedProcessData);
    }
}

function renderDashboardTopProcessesTable(data) {
    const container = document.getElementById('dashboard-servers-overview-container');
    if (!container) return;

    if (!data || data.status === 'error' || (!data.databases && !data.cpu_processes)) {
        const errorMsg = data ? (data.message || data.error || "Unable to load server process information.") : "Unable to load server process information.";
        container.innerHTML = `
            <div style="color: #ef4444; font-size: 0.88rem; font-weight: 600; text-align: center; padding: 1.5rem; background: #ffffff; border-radius: 10px; border: 1px solid #fee2e2;">
                ${escapeHtml(errorMsg)}
            </div>
        `;
        return;
    }

    // Get config for globalActiveDbId
    let dbInfo = null;
    if (data.databases && globalActiveDbId && data.databases[globalActiveDbId]) {
        dbInfo = data.databases[globalActiveDbId];
    } else if (data.cpu_processes) { // backward fallback
        dbInfo = {
            "status": "success",
            "db_name": globalActiveDbId || "active_database",
            "server": "kasorcl",
            "host": "localhost",
            "cpu_processes": data.cpu_processes || [],
            "memory_processes": data.memory_processes || []
        };
    }

    if (!dbInfo) {
        container.innerHTML = `
            <div style="color: #64748b; font-size: 0.88rem; font-weight: 500; text-align: center; padding: 1.5rem; background: #ffffff; border-radius: 10px; border: 1px solid #e2e8f0;">
                No process monitoring configured for this database connection.
            </div>
        `;
        return;
    }

    const isSuccess = dbInfo.status === 'success';

    // Set connection status badge
    const statusContainer = document.getElementById('dashboard-server-processes-ssh-status');
    if (statusContainer) {
        statusContainer.innerHTML = isSuccess 
            ? `<span class="badge bg-success-light" style="font-size: 0.7rem; font-weight: 600; padding: 4px 8px; border-radius: 6px; background-color: #ecfdf5; color: #065f46; border: 1px solid #a7f3d0;">SSH Connected</span>`
            : `<span class="badge bg-danger-light" style="font-size: 0.7rem; font-weight: 600; padding: 4px 8px; border-radius: 6px; background-color: #fef2f2; color: #991b1b; border: 1px solid #fecaca;">Connection Failed</span>`;
    }

    if (!isSuccess) {
        container.innerHTML = `
            <div style="color: #ef4444; font-size: 0.82rem; font-weight: 600; padding: 1rem 1.25rem; background: #fef2f2; border-radius: 8px; border: 1px solid #fecaca; margin-bottom: 0.5rem; display: flex; align-items: center; gap: 0.5rem;">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="12" cy="12" r="10"></circle>
                    <line x1="12" y1="8" x2="12" y2="12"></line>
                    <line x1="12" y1="16" x2="12.01" y2="16"></line>
                </svg>
                Error: ${escapeHtml(dbInfo.message || 'Unable to retrieve process details.')}
            </div>
        `;
        return;
    }

    const cpuProcsRaw = dbInfo.cpu_processes || [];
    const memProcsRaw = dbInfo.memory_processes || [];

    const filteredCpuProcs = filterAndSortDashboardProcesses(cpuProcsRaw).slice(0, 10);
    const filteredMemProcs = filterAndSortDashboardProcesses(memProcsRaw).slice(0, 10);

    let html = '';
    if (currentDashboardProcessTab === 'cpu') {
        html += renderSingleProcessTable('Top Host CPU Consuming Processes', filteredCpuProcs, true);
    } else if (currentDashboardProcessTab === 'memory') {
        html += renderSingleProcessTable('Top Host Memory Consuming Processes', filteredMemProcs, false);
    } else {
        const cpuTable = renderSingleProcessTable('Top Host CPU Consuming Processes', filteredCpuProcs, true);
        const memTable = renderSingleProcessTable('Top Host Memory Consuming Processes', filteredMemProcs, false);

        html += `
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 1rem; width: 100%;">
                <div>${cpuTable}</div>
                <div>${memTable}</div>
            </div>
        `;
    }

    container.innerHTML = html;
}




// --- HOME NAVIGATION CONTROLLER ---
function goToHome() {
    const homeView = document.getElementById('home-view');
    const dashboardView = document.getElementById('dashboard-view');
    const emailView = document.getElementById('email-settings-view');
    
    if (dashboardView) dashboardView.style.display = 'none';
    if (emailView) emailView.style.display = 'none';
    if (homeView) homeView.style.display = 'block';
    updateDisconnectButtonVisibility();

    const backBtn = document.getElementById('header-back-btn');
    if (backBtn) backBtn.style.display = 'none';

    const activeLis = document.querySelectorAll('.db-list li');
    activeLis.forEach(li => li.classList.remove('active'));
    
    const emailSidebarSec = document.getElementById('email-settings-sidebar-section');
    if (emailSidebarSec) {
        emailSidebarSec.style.backgroundColor = '';
    }
    
    const subtitle = document.getElementById('dashboard-subtitle');
    if (subtitle) {
        subtitle.innerText = 'Database system overview';
    }
    
    const statusCard = document.getElementById('status-card-container');
    if (statusCard) {
        statusCard.style.display = 'none';
    }
    
    loadServersOverview();
}

// --- EMAIL ALERT SETTINGS VIEW CONTROLLER ---

// Clears SMTP form fields only — recipients and custom rules are NOT cleared (they persist)
function clearEmailSettingsFields() {
    // Auth type - reset to default
    const authSelect = document.getElementById('smtp-auth-type');
    if (authSelect) authSelect.value = 'OAuth2';
    toggleAuthTypeFields();

    // SMTP credential fields - clear to empty
    const fieldsToEmpty = [
        'smtp-host', 'smtp-port',
        'oauth-tenant-id', 'oauth-client-id', 'oauth-client-secret',
        'smtp-username', 'smtp-password',
        'sender-name', 'sender-email',
        'smtp-timeout', 'smtp-retry-count', 'smtp-retry-interval',
        'email-subject', 'email-message'
    ];
    fieldsToEmpty.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });

    // Checkboxes - uncheck all
    const checkboxIds = [
        'smtp-tls-validation', 'smtp-debug-logging',
        'alert-db-down', 'alert-listener-down', 'alert-tablespace-90',
        'alert-disk-90', 'alert-cpu-90', 'alert-mem-90',
        'alert-rman-failed', 'alert-archive-full',
        'alert-blocking-sessions', 'alert-db-startup', 'alert-db-shutdown',
        'alert-standby-down', 'alert-standby-not-synced', 'alert-standby-log-gap',
        'alert-standby-destination-error', 'alert-reporting-db-down', 'alert-mount-point-critical'
    ];
    checkboxIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.checked = false;
    });

    // NOTE: Recipients and Custom Alert Rules are NOT cleared here.
    // They persist until the user manually deletes them.

    // Connection status - reset
    updateConnectionStatusUI('Not Connected', '');
}

// Load ONLY recipients and custom alert rules from saved backend config (persists across sessions)
function loadPersistedRecipientsAndRules() {
    fetch('/api/email/config')
        .then(res => res.json())
        .then(resData => {
            const settings = resData.config || resData.settings || resData || {};

            // Restore recipients
            currentRecipients = settings.recipients || [];
            renderRecipientsTable();

            // Restore custom alert rules
            if (typeof currentCustomAlertRules !== 'undefined') {
                currentCustomAlertRules = settings.custom_alert_rules || [];
                if (typeof renderCustomAlertRulesTable === 'function') renderCustomAlertRulesTable();
            }
        })
        .catch(err => console.error('Error loading persisted recipients/rules:', err));
}

let currentRecipients = [];

function goToEmailSettings() {
    document.getElementById('home-view').style.display = 'none';
    document.getElementById('dashboard-view').style.display = 'none';
    document.getElementById('email-settings-view').style.display = 'flex';
    updateDisconnectButtonVisibility();

    const backBtn = document.getElementById('header-back-btn');
    if (backBtn) backBtn.style.display = 'none';
    
    const activeLis = document.querySelectorAll('.db-list li');
    activeLis.forEach(li => li.classList.remove('active'));
    
    const emailSidebarSec = document.getElementById('email-settings-sidebar-section');
    if (emailSidebarSec) {
        emailSidebarSec.style.backgroundColor = '#f1f5f9';
    }
    
    const subtitle = document.getElementById('dashboard-subtitle');
    if (subtitle) {
        subtitle.innerText = 'Microsoft Office 365 OAuth2 Authentication & Email Alert Manager';
    }
    
    clearEmailSettingsFields();
    loadPersistedRecipientsAndRules();
    loadEmailHistory();
}

function toggleAuthTypeFields() {
    const authType = document.getElementById('smtp-auth-type')?.value || 'OAuth2';
    const oauthContainer = document.getElementById('oauth2-fields-container');
    const basicContainer = document.getElementById('basic-auth-fields-container');
    
    if (authType === 'OAuth2') {
        if (oauthContainer) oauthContainer.style.display = 'flex';
        if (basicContainer) basicContainer.style.display = 'none';
    } else {
        if (oauthContainer) oauthContainer.style.display = 'none';
        if (basicContainer) basicContainer.style.display = 'flex';
    }
}

function toggleClientSecretVisibility() {
    const secretInput = document.getElementById('oauth-client-secret');
    if (secretInput) secretInput.type = (secretInput.type === 'password') ? 'text' : 'password';
}

function toggleSmtpPasswordVisibility() {
    const pwdInput = document.getElementById('smtp-password');
    if (pwdInput) pwdInput.type = (pwdInput.type === 'password') ? 'text' : 'password';
}

function toggleAdvancedSettingsAccordion() {
    const content = document.getElementById('advanced-settings-content');
    const arrow = document.getElementById('accordion-arrow');
    if (content) {
        const isHidden = content.style.display === 'none' || content.style.display === '';
        content.style.display = isHidden ? 'flex' : 'none';
        if (arrow) arrow.style.transform = isHidden ? 'rotate(180deg)' : 'rotate(0deg)';
    }
}

function updateConnectionStatusUI(status, lastError) {
    const badgeDot = document.getElementById('email-status-dot');
    const badgeText = document.getElementById('email-status-text');
    const alertContainer = document.getElementById('email-status-alert-container');
    const successBanner = document.getElementById('email-status-success');
    const errorBanner = document.getElementById('email-status-error');
    const errorMsg = document.getElementById('email-error-msg');

    if (!badgeDot || !badgeText) return;

    if (status === 'Connected') {
        badgeDot.style.background = '#10b981';
        badgeText.innerText = 'Connected';
        if (alertContainer) alertContainer.style.display = 'block';
        if (successBanner) successBanner.style.display = 'flex';
        if (errorBanner) errorBanner.style.display = 'none';
    } else if (status === 'Connecting...') {
        badgeDot.style.background = '#0078d4';
        badgeText.innerText = 'Connecting...';
        if (alertContainer) alertContainer.style.display = 'none';
    } else if (status === 'Connection Failed') {
        badgeDot.style.background = '#ef4444';
        badgeText.innerText = 'Connection Failed';
        if (alertContainer) alertContainer.style.display = 'block';
        if (successBanner) successBanner.style.display = 'none';
        if (errorBanner) {
            errorBanner.style.display = 'flex';
            if (errorMsg) errorMsg.innerText = lastError || 'Failed to connect to Microsoft 365 OAuth2 service.';
        }
    } else {
        badgeDot.style.background = '#fbbf24';
        badgeText.innerText = 'Not Connected';
        if (alertContainer) alertContainer.style.display = 'none';
    }
}

let currentCustomAlertRules = [];

// Render the custom alert rules table
function renderCustomAlertRulesTable() {
    const container = document.getElementById('custom-alerts-container');
    if (!container) return;
    
    container.innerHTML = '';
    if (currentCustomAlertRules.length === 0) {
        return;
    }
    
    const typeLabels = {
        cpu_usage: 'CPU Usage (%)',
        mem_usage: 'Memory Usage (%)',
        tablespace_usage: 'Tablespace Usage (%)',
        disk_usage: 'Disk Usage (%)',
        blocking_sessions: 'Blocking Sessions (Count)',
        active_sessions: 'Active Sessions (Count)'
    };
    
    currentCustomAlertRules.forEach((rule, idx) => {
        const itemDiv = document.createElement('div');
        itemDiv.style.display = 'flex';
        itemDiv.style.alignItems = 'center';
        itemDiv.style.justifyContent = 'space-between';
        itemDiv.style.fontSize = '0.78rem';
        itemDiv.style.color = '#334155';
        itemDiv.style.padding = '0.35rem 0';
        itemDiv.style.borderTop = '1px dashed #e2e8f0';
        
        const label = typeLabels[rule.type] || rule.type;
        const threshold = rule.threshold !== null && rule.threshold !== undefined ? rule.threshold : 'N/A';
        const isChecked = rule.enabled !== false;
        
        itemDiv.innerHTML = `
            <label style="display: flex; align-items: center; gap: 0.45rem; cursor: pointer; margin: 0; user-select: none; font-weight: 500;">
                <input type="checkbox" onchange="toggleCustomAlertRuleEnabled(${idx}, this.checked)" ${isChecked ? 'checked' : ''} style="cursor: pointer; width: 14px; height: 14px; margin: 0;">
                <span>${label} &ge; ${threshold}</span>
            </label>
            <button onclick="deleteCustomAlertRule(${idx})" title="Delete Rule" style="background: none; border: none; cursor: pointer; color: #ef4444; font-size: 0.85rem; padding: 2px 4px; outline: none; transition: transform 0.15s;" onmouseover="this.style.transform='scale(1.2)'" onmouseout="this.style.transform='scale(1)'">
                🗑
            </button>
        `;
        container.appendChild(itemDiv);
    });
}

function toggleCustomAlertRuleEnabled(index, checked) {
    if (currentCustomAlertRules[index]) {
        currentCustomAlertRules[index].enabled = checked;
    }
}

function deleteCustomAlertRule(index) {
    currentCustomAlertRules.splice(index, 1);
    renderCustomAlertRulesTable();
}

function openAddCustomAlertModal() {
    if (document.getElementById('custom-alert-type-input')) {
        document.getElementById('custom-alert-type-input').value = 'cpu_usage';
    }
    if (document.getElementById('custom-alert-threshold-input')) {
        document.getElementById('custom-alert-threshold-input').value = '85';
    }
    if (document.getElementById('add-custom-alert-modal')) {
        document.getElementById('add-custom-alert-modal').style.display = 'flex';
    }
}

function closeAddCustomAlertModal() {
    if (document.getElementById('add-custom-alert-modal')) {
        document.getElementById('add-custom-alert-modal').style.display = 'none';
    }
}

function submitAddCustomAlertRule(silent = false) {
    const typeInput = document.getElementById('custom-alert-type-input');
    const thresholdInput = document.getElementById('custom-alert-threshold-input');
    
    if (!typeInput || !thresholdInput) return null;
    
    const type = typeInput.value.trim();
    if (!type) {
        if (!silent) alert('Please enter or select a metric type.');
        return null;
    }
    
    const threshVal = thresholdInput.value.trim();
    if (threshVal === '' || isNaN(threshVal)) {
        if (!silent) alert('Please enter a valid numeric threshold.');
        return null;
    }
    
    const threshold = parseFloat(threshVal);
    
    // Check for duplicates
    const isDuplicate = currentCustomAlertRules.some(r => r.type.toLowerCase() === type.toLowerCase() && r.threshold === threshold);
    if (isDuplicate) {
        if (!silent) alert('This exact alert rule already exists.');
        return null;
    }
    
    const newRule = { type, threshold, enabled: true };
    currentCustomAlertRules.push(newRule);
    renderCustomAlertRulesTable();
    closeAddCustomAlertModal();
    return newRule;
}

function submitAddAndTestCustomAlertRule() {
    const addedRule = submitAddCustomAlertRule(true);
    if (!addedRule) {
        alert('Please fill in a valid metric type and numeric threshold before sending a test email.');
        return;
    }
    
    const typeLabels = {
        cpu_usage: 'CPU Usage (%)',
        mem_usage: 'Memory Usage (%)',
        tablespace_usage: 'Tablespace Usage (%)',
        disk_usage: 'Disk Usage (%)',
        blocking_sessions: 'Blocking Sessions (Count)',
        active_sessions: 'Active Sessions (Count)'
    };
    const label = typeLabels[addedRule.type] || addedRule.type;
    
    let targetEmails = currentRecipients.map(r => r.email).join(', ');
    if (!targetEmails) {
        const senderEm = document.getElementById('sender-email')?.value.trim();
        if (senderEm) {
            targetEmails = senderEm;
        }
    }
    
    if (!targetEmails) {
        alert('No recipient email addresses available to send test email.');
        return;
    }
    
    const config = buildSettingsObject();
    const testSubject = `[CRITICAL] Custom Alert Rule Test: ${label}`;
    const testMessage = `Hello,\n\nThis is a custom alert test notification from GreenWorld Monitor.\n\nCustom Rule Details:\n- Metric Type: ${label}\n- Threshold: >= ${addedRule.threshold}\n- Status: Simulated Trigger\n\nRegards,\nGreenWorld Database Monitoring Team`;
    
    const testHtmlBody = `
    <div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 600px; padding: 24px; border: 1px solid #e2e8f0; border-radius: 12px; background: #ffffff; margin: 0 auto;">
        <div style="background: linear-gradient(135deg, #ef4444 0%, #f97316 100%); padding: 18px 24px; border-radius: 8px; color: #ffffff; margin-bottom: 24px;">
            <h2 style="margin: 0; font-size: 1.3rem; font-weight: 700; letter-spacing: 0.02em;">[TEST ALERT] GreenWorld Custom Rule</h2>
            <p style="margin: 4px 0 0 0; font-size: 0.88rem; opacity: 0.95;">Simulated Custom Metric Alert Threshold Exceeded</p>
        </div>
        <p style="color: #1e293b; font-size: 1rem; line-height: 1.5; margin-bottom: 16px;">Hello,</p>
        <p style="color: #334155; font-size: 0.95rem; line-height: 1.6; margin-bottom: 20px;">
            This test notification validates that your custom alert rule monitoring is configured correctly.
        </p>
        <div style="background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 8px; padding: 14px 18px; margin-bottom: 24px;">
            <ul style="margin: 0; padding-left: 18px; color: #475569; font-size: 0.88rem; line-height: 1.7;">
                <li><strong>Metric Type:</strong> ${label}</li>
                <li><strong>Configured Threshold:</strong> &ge; ${addedRule.threshold}</li>
                <li><strong>Trigger Status:</strong> Simulated Event</li>
            </ul>
        </div>
        <p style="color: #64748b; font-size: 0.85rem; margin-top: 24px; border-top: 1px solid #e2e8f0; padding-top: 14px;">
            GreenWorld Enterprise Database Monitoring System
        </p>
    </div>
    `;
    
    alert(`Sending test email for custom rule "${label} >= ${addedRule.threshold}"...\n\nPlease wait...`);
    
    fetch('/api/email/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            config: config,
            test_recipient: targetEmails,
            subject: testSubject,
            message: testMessage,
            html_body: testHtmlBody
        })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            alert(`✓ Success! Test email sent successfully to:\n${targetEmails}`);
            loadEmailHistory();
        } else {
            alert('Failed to send test email: ' + (data.message || data.error));
            loadEmailHistory();
        }
    })
    .catch(err => {
        console.error('Error sending custom rule test email:', err);
        alert('Network error while sending test email.');
    });
}

function loadGlobalEmailSettingsConfig() {
    fetch('/api/email/config')
        .then(res => res.json())
        .then(resData => {
            const settings = resData.config || resData.settings || resData || {};
            
            // Set Auth type
            const authSelect = document.getElementById('smtp-auth-type');
            if (authSelect) authSelect.value = settings.auth_type || 'OAuth2';
            toggleAuthTypeFields();

            // Host & Port
            if (document.getElementById('smtp-host')) document.getElementById('smtp-host').value = settings.smtp_host || 'smtp.office365.com';
            if (document.getElementById('smtp-port')) document.getElementById('smtp-port').value = settings.smtp_port || 587;

            // OAuth Fields
            if (document.getElementById('oauth-tenant-id')) document.getElementById('oauth-tenant-id').value = settings.tenant_id || '';
            if (document.getElementById('oauth-client-id')) document.getElementById('oauth-client-id').value = settings.client_id || '';
            if (document.getElementById('oauth-client-secret')) document.getElementById('oauth-client-secret').value = settings.client_secret || '';

            // Basic Auth Fields
            if (document.getElementById('smtp-username')) document.getElementById('smtp-username').value = settings.smtp_username || '';
            if (document.getElementById('smtp-password')) document.getElementById('smtp-password').value = settings.smtp_password || '';

            // Sender
            if (document.getElementById('sender-name')) document.getElementById('sender-name').value = settings.sender_name || 'GreenWorld Monitor';
            if (document.getElementById('sender-email')) document.getElementById('sender-email').value = settings.sender_email || '';

            // Advanced settings
            if (document.getElementById('smtp-timeout')) document.getElementById('smtp-timeout').value = settings.timeout || 60;
            if (document.getElementById('smtp-retry-count')) document.getElementById('smtp-retry-count').value = settings.retry_count || 3;
            if (document.getElementById('smtp-retry-interval')) document.getElementById('smtp-retry-interval').value = settings.retry_interval || 5;
            if (document.getElementById('smtp-tls-validation')) document.getElementById('smtp-tls-validation').checked = settings.tls_validation !== false;
            if (document.getElementById('smtp-debug-logging')) document.getElementById('smtp-debug-logging').checked = settings.debug_logging === true;

            // Alert Checkboxes
            const alerts = settings.alert_types || {};
            if (document.getElementById('alert-db-down')) document.getElementById('alert-db-down').checked = alerts.db_down !== false;
            if (document.getElementById('alert-listener-down')) document.getElementById('alert-listener-down').checked = alerts.listener_down !== false;
            if (document.getElementById('alert-tablespace-90')) document.getElementById('alert-tablespace-90').checked = alerts.tablespace_90 !== false;
            if (document.getElementById('alert-disk-90')) document.getElementById('alert-disk-90').checked = alerts.disk_90 !== false;
            if (document.getElementById('alert-cpu-90')) document.getElementById('alert-cpu-90').checked = alerts.cpu_90 !== false;
            if (document.getElementById('alert-mem-90')) document.getElementById('alert-mem-90').checked = alerts.mem_90 !== false;
            if (document.getElementById('alert-rman-failed')) document.getElementById('alert-rman-failed').checked = alerts.rman_failed !== false;
            if (document.getElementById('alert-archive-full')) document.getElementById('alert-archive-full').checked = alerts.archive_full !== false;
            if (document.getElementById('alert-blocking-sessions')) document.getElementById('alert-blocking-sessions').checked = alerts.blocking_sessions !== false;
            if (document.getElementById('alert-db-startup')) document.getElementById('alert-db-startup').checked = alerts.db_startup !== false;
            if (document.getElementById('alert-db-shutdown')) document.getElementById('alert-db-shutdown').checked = alerts.db_shutdown !== false;
            if (document.getElementById('alert-standby-down')) document.getElementById('alert-standby-down').checked = alerts.standby_down !== false;
            if (document.getElementById('alert-standby-not-synced')) document.getElementById('alert-standby-not-synced').checked = alerts.standby_not_synced !== false;
            if (document.getElementById('alert-standby-log-gap')) document.getElementById('alert-standby-log-gap').checked = alerts.standby_log_gap !== false;
            if (document.getElementById('alert-standby-destination-error')) document.getElementById('alert-standby-destination-error').checked = alerts.standby_destination_error !== false;
            if (document.getElementById('alert-reporting-db-down')) document.getElementById('alert-reporting-db-down').checked = alerts.reporting_db_down !== false;
            if (document.getElementById('alert-mount-point-critical')) document.getElementById('alert-mount-point-critical').checked = alerts.mount_point_critical !== false;

            // Frequency
            const freq = settings.frequency || 'once';
            const freqRadio = document.querySelector(`input[name="email-frequency"][value="${freq}"]`);
            if (freqRadio) freqRadio.checked = true;

            // Template
            if (document.getElementById('email-subject')) document.getElementById('email-subject').value = settings.template_subject || '[CRITICAL] Database Alert: {DATABASE_NAME} ({STATUS})';
            if (document.getElementById('email-message')) document.getElementById('email-message').value = settings.template_message || 'Alert for {DATABASE_NAME} on {HOST}.\nStatus: {STATUS}\nTime: {DATE_TIME}';

            // Recipients
            currentRecipients = settings.recipients || [];
            renderRecipientsTable();

            // Custom Alert Rules
            currentCustomAlertRules = settings.custom_alert_rules || [];
            renderCustomAlertRulesTable();

            // Status UI
            updateConnectionStatusUI(settings.connection_status || 'Not Connected', settings.last_error || '');
        })
        .catch(err => console.error("Error loading email config:", err));
}

function clearValidationErrors() {
    const errs = document.querySelectorAll('.validation-error');
    errs.forEach(el => el.style.display = 'none');
}

function buildSettingsObject() {
    const authType = document.getElementById('smtp-auth-type')?.value || 'OAuth2';
    
    return {
        auth_type: authType,
        smtp_host: document.getElementById('smtp-host')?.value || 'smtp.office365.com',
        smtp_port: parseInt(document.getElementById('smtp-port')?.value || '587', 10),
        tenant_id: document.getElementById('oauth-tenant-id')?.value.trim() || '',
        client_id: document.getElementById('oauth-client-id')?.value.trim() || '',
        client_secret: document.getElementById('oauth-client-secret')?.value || '',
        smtp_username: document.getElementById('smtp-username')?.value.trim() || '',
        smtp_password: document.getElementById('smtp-password')?.value || '',
        sender_name: document.getElementById('sender-name')?.value.trim() || 'GreenWorld Monitor',
        sender_email: document.getElementById('sender-email')?.value.trim() || '',
        timeout: parseInt(document.getElementById('smtp-timeout')?.value || '60', 10),
        retry_count: parseInt(document.getElementById('smtp-retry-count')?.value || '3', 10),
        retry_interval: parseInt(document.getElementById('smtp-retry-interval')?.value || '5', 10),
        tls_validation: document.getElementById('smtp-tls-validation')?.checked !== false,
        debug_logging: document.getElementById('smtp-debug-logging')?.checked === true,
        recipients: currentRecipients,
        alert_types: {
            db_down: document.getElementById('alert-db-down')?.checked !== false,
            listener_down: document.getElementById('alert-listener-down')?.checked !== false,
            tablespace_90: document.getElementById('alert-tablespace-90')?.checked !== false,
            disk_90: document.getElementById('alert-disk-90')?.checked !== false,
            cpu_90: document.getElementById('alert-cpu-90')?.checked !== false,
            mem_90: document.getElementById('alert-mem-90')?.checked !== false,
            rman_failed: document.getElementById('alert-rman-failed')?.checked !== false,
            archive_full: document.getElementById('alert-archive-full')?.checked !== false,
            blocking_sessions: document.getElementById('alert-blocking-sessions')?.checked !== false,
            db_startup: document.getElementById('alert-db-startup')?.checked !== false,
            db_shutdown: document.getElementById('alert-db-shutdown')?.checked !== false,
            standby_down: document.getElementById('alert-standby-down')?.checked !== false,
            standby_not_synced: document.getElementById('alert-standby-not-synced')?.checked !== false,
            standby_log_gap: document.getElementById('alert-standby-log-gap')?.checked !== false,
            standby_destination_error: document.getElementById('alert-standby-destination-error')?.checked !== false,
            reporting_db_down: document.getElementById('alert-reporting-db-down')?.checked !== false,
            mount_point_critical: document.getElementById('alert-mount-point-critical')?.checked !== false
        },
        frequency: document.querySelector('input[name="email-frequency"]:checked')?.value || 'once',
        template_subject: document.getElementById('email-subject')?.value.trim() || '',
        template_message: document.getElementById('email-message')?.value || '',
        custom_alert_rules: currentCustomAlertRules
    };
}

function connectMicrosoft365() {
    clearValidationErrors();
    const config = buildSettingsObject();

    if (config.auth_type === 'OAuth2') {
        let hasError = false;
        if (!config.tenant_id) {
            document.getElementById('val-err-tenant').style.display = 'block';
            hasError = true;
        }
        if (!config.client_id) {
            document.getElementById('val-err-client').style.display = 'block';
            hasError = true;
        }
        if (!config.client_secret) {
            document.getElementById('val-err-secret').style.display = 'block';
            hasError = true;
        }
        if (!config.sender_email || !config.sender_email.includes('@')) {
            document.getElementById('val-err-sender').style.display = 'block';
            hasError = true;
        }
        if (hasError) return;
    }

    updateConnectionStatusUI('Connecting...', '');

    fetch('/api/email/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            updateConnectionStatusUI('Connected', '');
        } else {
            updateConnectionStatusUI('Connection Failed', data.error || data.message || 'Connection failed.');
        }
    })
    .catch(err => {
        console.error('Error connecting to email service:', err);
        updateConnectionStatusUI('Connection Failed', 'Failed to communicate with server.');
    });
}

function testOAuthConnection() {
    const config = buildSettingsObject();

    fetch('/api/email/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            alert('✓ OAuth Test Successful! Test email sent.');
            loadEmailHistory();
        } else {
            alert('OAuth Test Failed: ' + (data.message || data.error));
            loadEmailHistory();
        }
    })
    .catch(err => {
        console.error('Error testing OAuth connection:', err);
        alert('Network error testing OAuth connection.');
    });
}

function disconnectEmail() {
    if (!confirm('Are you sure you want to disconnect Microsoft 365 OAuth2 authentication?')) return;

    fetch('/api/email/disconnect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            updateConnectionStatusUI('Not Connected', '');
            alert('Disconnected from Microsoft 365.');
        }
    })
    .catch(err => console.error('Error disconnecting:', err));
}

function saveEmailSettings() {
    clearValidationErrors();
    const config = buildSettingsObject();

    fetch('/api/email/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            alert('Email Alert Settings saved successfully!');
            loadGlobalEmailSettingsConfig();
        } else {
            alert('Error saving settings: ' + (data.error || 'Unknown error'));
        }
    })
    .catch(err => {
        console.error('Error saving settings:', err);
        alert('Failed to connect to the server to save settings.');
    });
}

// Render the recipients table
function renderRecipientsTable() {
    const tbody = document.getElementById('recipients-body');
    if (!tbody) return;
    
    tbody.innerHTML = '';
    if (currentRecipients.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="4" style="padding: 0.75rem; text-align: center; color: #94a3b8; font-style: italic;">
                    No recipients added yet
                </td>
            </tr>
        `;
        return;
    }
    
    currentRecipients.forEach((rec, idx) => {
        const tr = document.createElement('tr');
        tr.style.borderBottom = '1px solid #f1f5f9';
        tr.innerHTML = `
            <td style="padding: 0.5rem 0.6rem; color: #1e293b; font-weight: 500;">${rec.name}</td>
            <td style="padding: 0.5rem 0.6rem; color: #475569;">${rec.email}</td>
            <td style="padding: 0.5rem 0.6rem;">
                <span class="pill-badge blue" style="font-size: 0.65rem; padding: 1px 6px; font-weight: 700;">${rec.role || 'DBA'}</span>
            </td>
            <td style="padding: 0.5rem 0.6rem; text-align: center;">
                <div style="display: flex; gap: 0.4rem; justify-content: center; align-items: center;">
                    <button onclick="sendTestEmailWithRecipients('${rec.email}')" title="Send Test Email to ${rec.name} (${rec.email})" style="background: none; border: none; cursor: pointer; color: #059669; font-size: 0.95rem; padding: 2px; outline: none; transition: transform 0.15s;" onmouseover="this.style.transform='scale(1.2)'" onmouseout="this.style.transform='scale(1)'">
                        ✉
                    </button>
                    <button onclick="deleteRecipient(${idx})" title="Delete Recipient" style="background: none; border: none; cursor: pointer; color: #ef4444; font-size: 0.95rem; padding: 2px; outline: none; transition: transform 0.15s;" onmouseover="this.style.transform='scale(1.2)'" onmouseout="this.style.transform='scale(1)'">
                        🗑
                    </button>
                </div>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

function deleteRecipient(index) {
    currentRecipients.splice(index, 1);
    renderRecipientsTable();
}

function openAddRecipientModal() {
    if (document.getElementById('recipient-name-input')) document.getElementById('recipient-name-input').value = '';
    if (document.getElementById('recipient-email-input')) document.getElementById('recipient-email-input').value = '';
    if (document.getElementById('recipient-role-select')) document.getElementById('recipient-role-select').value = 'DBA';
    if (document.getElementById('add-recipient-modal')) document.getElementById('add-recipient-modal').style.display = 'flex';
}

function closeAddRecipientModal() {
    if (document.getElementById('add-recipient-modal')) document.getElementById('add-recipient-modal').style.display = 'none';
}

function submitAddRecipient(silent = false) {
    const nameRaw = document.getElementById('recipient-name-input')?.value.trim() || '';
    const emailRaw = document.getElementById('recipient-email-input')?.value.trim() || '';
    const role = document.getElementById('recipient-role-select')?.value || 'DBA';
    
    if (!emailRaw) {
        if (!silent) alert('Please enter at least one email address.');
        return [];
    }

    const rawEmails = emailRaw.split(',').map(e => e.strip ? e.strip() : e.trim()).filter(e => e.length > 0);
    const rawNames = nameRaw ? nameRaw.split(',').map(n => n.strip ? n.strip() : n.trim()).filter(n => n.length > 0) : [];

    if (rawEmails.length === 0) {
        if (!silent) alert('Please enter valid email address(es).');
        return [];
    }

    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    const addedItems = [];

    rawEmails.forEach((em, idx) => {
        if (emailRegex.test(em)) {
            let recipientName = rawNames[idx] || rawNames[0] || em.split('@')[0];
            // Capitalize default name nicely
            if (!rawNames[idx] && !rawNames[0]) {
                recipientName = recipientName.charAt(0).toUpperCase() + recipientName.slice(1);
            }
            const recObj = { name: recipientName, email: em, role: role };
            currentRecipients.push(recObj);
            addedItems.push(recObj);
        }
    });

    if (addedItems.length === 0) {
        if (!silent) alert('No valid email addresses format recognized (e.g. name@domain.com).');
        return [];
    }

    renderRecipientsTable();
    closeAddRecipientModal();

    if (!silent) {
        const count = addedItems.length;
        console.log(`Added ${count} recipient(s) with role ${role}.`);
    }

    return addedItems;
}

function submitAddAndTestRecipient() {
    const added = submitAddRecipient(true);
    if (!added || added.length === 0) {
        alert('Please fill in valid name(s) and email address(es) before sending test email.');
        return;
    }
    const emailList = added.map(r => r.email).join(', ');
    sendTestEmailWithRecipients(emailList);
}

function openTestEmailModal() {
    if (document.getElementById('test-email-recipient-input')) {
        const defaultEmails = currentRecipients.map(r => r.email).join(', ');
        document.getElementById('test-email-recipient-input').value = defaultEmails;
    }
    if (document.getElementById('test-email-modal')) document.getElementById('test-email-modal').style.display = 'flex';
}

function closeTestEmailModal() {
    if (document.getElementById('test-email-modal')) document.getElementById('test-email-modal').style.display = 'none';
}

function sendTestEmailWithRecipients(targetEmails) {
    let emails = targetEmails;
    if (!emails || !emails.trim()) {
        if (currentRecipients.length > 0) {
            emails = currentRecipients.map(r => r.email).join(', ');
        } else {
            const senderEm = document.getElementById('sender-email')?.value.trim();
            if (senderEm) emails = senderEm;
        }
    }

    if (!emails) {
        alert('No recipient email address available to send test email.');
        return;
    }

    const config = buildSettingsObject();
    
    alert(`Sending test email via Microsoft 365 OAuth2 to:\n${emails}\n\nPlease wait...`);
    
    fetch('/api/email/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            config: config,
            test_recipient: emails
        })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            alert(`✓ Success! Test email sent successfully to:\n${emails}`);
            loadEmailHistory();
        } else {
            alert('Failed to send test email: ' + (data.message || data.error));
            loadEmailHistory();
        }
    })
    .catch(err => {
        console.error('Error sending test email:', err);
        alert('Network error while sending test email.');
    });
}

function submitSendTestEmail() {
    const recipientInput = document.getElementById('test-email-recipient-input')?.value.trim();
    closeTestEmailModal();
    sendTestEmailWithRecipients(recipientInput);
}

function loadEmailHistory() {
    const tbody = document.getElementById('email-history-body');
    if (!tbody) return;
    
    fetch('/api/email-history')
        .then(res => res.json())
        .then(data => {
            if (!data.success || !data.history || data.history.length === 0) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="5" style="padding: 1rem; text-align: center; color: #94a3b8; font-style: italic;">
                            No email alerts sent yet
                        </td>
                    </tr>
                `;
                return;
            }
            
            tbody.innerHTML = '';
            data.history.forEach(item => {
                const tr = document.createElement('tr');
                tr.style.borderBottom = '1px solid #f1f5f9';
                
                const statusClass = item.status === 'Success' ? 'green' : 'red';
                const badgeTitle = item.error ? item.error.replace(/"/g, '&quot;') : '';
                const recs = Array.isArray(item.recipients) ? item.recipients.join(', ') : (item.recipients || 'N/A');
                const dbName = item.db_id || 'System';
                
                tr.innerHTML = `
                    <td style="padding: 0.5rem 0.6rem; color: #475569; font-weight: 500; white-space: nowrap;">${item.timestamp}</td>
                    <td style="padding: 0.5rem 0.6rem; color: #1e293b; font-weight: 700;">${dbName}</td>
                    <td style="padding: 0.5rem 0.6rem; color: #1e293b; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${recs}">${recs}</td>
                    <td style="padding: 0.5rem 0.6rem; color: #1e293b; max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${item.subject}">${item.subject}</td>
                    <td style="padding: 0.5rem 0.6rem; text-align: center;">
                        <span class="pill-badge ${statusClass}" style="font-size: 0.65rem; padding: 1px 6px; cursor: help;" title="${badgeTitle}">
                            ${item.status}
                        </span>
                    </td>
                `;
                tbody.appendChild(tr);
            });
        })
        .catch(err => {
            console.error("Error loading email history:", err);
            tbody.innerHTML = `
                <tr>
                    <td colspan="5" style="padding: 1rem; text-align: center; color: #ef4444; font-weight: 500;">
                        Failed to load email send history
                    </td>
                </tr>
            `;
        });
}

function clearEmailHistory() {
    if (!confirm('Are you sure you want to clear the email send history?')) {
        return;
    }
    
    fetch('/api/clear-email-history', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({})
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            loadEmailHistory();
        } else {
            alert('Error clearing history: ' + (data.error || 'Unknown error'));
        }
    })
    .catch(err => {
        console.error('Error clearing email history:', err);
        alert('Failed to connect to the server to clear history.');
    });
}

// Auto-initialize Top Running Processes Monitoring & 10s AJAX polling on DOM load
document.addEventListener('DOMContentLoaded', function() {
    loadServersOverview();
    loadHomeMountPoints();
    loadHomeAsmDiskGroups();
    loadHomeOracleProcesses();
    setInterval(loadServersOverview, 10000);
    // Auto-refresh HomePage Mount Points every 20 seconds
    setInterval(function() {
        const homeView = document.getElementById('home-view');
        if (homeView && homeView.style.display !== 'none') {
            loadHomeMountPoints(true);
        }
    }, 20000);
    // Auto-refresh HomePage ASM Disk Groups every 20 seconds
    setInterval(function() {
        const homeView = document.getElementById('home-view');
        if (homeView && homeView.style.display !== 'none') {
            loadHomeAsmDiskGroups(true);
        }
    }, 20000);
    // Auto-refresh HomePage Oracle Server Processes every 20 seconds
    setInterval(function() {
        const homeView = document.getElementById('home-view');
        if (homeView && homeView.style.display !== 'none') {
            loadHomeOracleProcesses(true);
        }
    }, 20000);

    // Auto-refresh Mount Points every 30 seconds
    setInterval(function() {
        const dashboardView = document.getElementById('dashboard-view');
        if (dashboardView && dashboardView.style.display !== 'none') {
            fetchMountPoints();
        }
    }, 30000);

    // Auto-refresh Dashboard Host OS Processes every 30 seconds
    setInterval(function() {
        const dashboardView = document.getElementById('dashboard-view');
        if (dashboardView && dashboardView.style.display !== 'none') {
            fetchDashboardServerProcesses();
        }
    }, 30000);
});

// ✅ SERVER MOUNT POINTS MODULE LOGIC
let globalMountPointsData = [];
let currentMountSortColumn = 'mount_point';
let currentMountSortAscending = true;

function getStatusBadgeHtml(percent) {
    if (percent >= 90) {
        return `<span class="badge bg-danger" style="font-weight: 600; padding: 0.35rem 0.65rem; border-radius: 4px;">Critical</span>`;
    } else if (percent >= 71) {
        return `<span class="badge bg-warning text-dark" style="font-weight: 600; padding: 0.35rem 0.65rem; border-radius: 4px;">Warning</span>`;
    } else {
        return `<span class="badge bg-success" style="font-weight: 600; padding: 0.35rem 0.65rem; border-radius: 4px;">Healthy</span>`;
    }
}

function getProgressFillColor(percent) {
    if (percent >= 90) return '#dc2626'; // Red
    if (percent >= 71) return '#d97706'; // Yellow/Orange
    return '#16a34a'; // Green
}

function fetchMountPoints() {
    const errorBanner = document.getElementById('mountpoints-error-banner');
    const errorMsg = document.getElementById('mountpoints-error-msg');
    const spinner = document.getElementById('mount-table-spinner');
    const serverNameEl = document.getElementById('mount-server-name');

    if (errorBanner) errorBanner.style.display = 'none';
    if (spinner) spinner.style.display = 'block';

    const url = globalActiveDbId ? `/api/server/mount-points?db_id=${encodeURIComponent(globalActiveDbId)}` : '/api/server/mount-points';

    fetch(url)
        .then(res => res.json())
        .then(data => {
            if (data && data.status === 'checking') {
                if (spinner) spinner.style.display = 'block';
                if (serverNameEl) serverNameEl.innerText = data.server || 'Checking...';
                setTimeout(fetchMountPoints, 2000);
                return;
            }

            if (spinner) spinner.style.display = 'none';

            if (data.status === 'error' || !data.mount_points) {
                if (errorBanner) errorBanner.style.display = 'flex';
                if (errorMsg) errorMsg.innerText = data.message || 'Unable to retrieve mount point information from the server.';
                if (serverNameEl) serverNameEl.innerText = data.server || 'unknown';
                globalMountPointsData = [];
                renderMountPointsUI();
                return;
            }

            if (serverNameEl) serverNameEl.innerText = data.server || 'kasorcl';
            globalMountPointsData = data.mount_points || [];
            renderMountPointsUI();
        })
        .catch(err => {
            console.error('Fetch Mount Points Error:', err);
            if (spinner) spinner.style.display = 'none';
            if (errorBanner) errorBanner.style.display = 'flex';
            if (errorMsg) errorMsg.innerText = 'Unable to retrieve mount point information from the server.';
            globalMountPointsData = [];
            renderMountPointsUI();
        });
}

function renderMountPointsUI() {
    // 1. Calculate summary counts
    let total = globalMountPointsData.length;
    let healthy = 0;
    let warning = 0;
    let critical = 0;

    globalMountPointsData.forEach(item => {
        const pct = item.usage_percent || 0;
        if (pct >= 90) critical++;
        else if (pct >= 71) warning++;
        else healthy++;
    });

    const statTotal = document.getElementById('mount-stat-total');
    const statHealthy = document.getElementById('mount-stat-healthy');
    const statWarning = document.getElementById('mount-stat-warning');
    const statCritical = document.getElementById('mount-stat-critical');

    if (statTotal) statTotal.innerText = total;
    if (statHealthy) statHealthy.innerText = healthy;
    if (statWarning) statWarning.innerText = warning;
    if (statCritical) statCritical.innerText = critical;

    // 2. Filter & Sort Table
    filterMountPointsTable();
}

function filterMountPointsTable() {
    const searchInput = document.getElementById('mount-search-input');
    const query = searchInput ? searchInput.value.toLowerCase().trim() : '';

    let filtered = globalMountPointsData.filter(item => {
        if (!query) return true;
        return (item.device || '').toLowerCase().includes(query) ||
               (item.mount_point || '').toLowerCase().includes(query) ||
               (item.filesystem || '').toLowerCase().includes(query);
    });

    // Sort filtered items
    filtered.sort((a, b) => {
        let valA = a[currentMountSortColumn];
        let valB = b[currentMountSortColumn];

        if (currentMountSortColumn === 'status') {
            valA = a.usage_percent >= 90 ? 3 : (a.usage_percent >= 71 ? 2 : 1);
            valB = b.usage_percent >= 90 ? 3 : (b.usage_percent >= 71 ? 2 : 1);
        } else if (typeof valA === 'string') {
            valA = valA.toLowerCase();
            valB = (valB || '').toLowerCase();
        }

        if (valA < valB) return currentMountSortAscending ? -1 : 1;
        if (valA > valB) return currentMountSortAscending ? 1 : -1;
        return 0;
    });

    const showingCount = document.getElementById('mount-showing-count');
    if (showingCount) showingCount.innerText = filtered.length;

    // Update sort icons in table header
    const cols = ['device', 'mount_point', 'filesystem', 'total_size', 'used_size', 'available_size', 'usage_percent', 'status'];
    cols.forEach(col => {
        const iconEl = document.getElementById(`sort-icon-${col}`);
        if (iconEl) {
            if (col === currentMountSortColumn) {
                iconEl.innerText = currentMountSortAscending ? '▲' : '▼';
                iconEl.style.color = '#2563eb';
            } else {
                iconEl.innerText = '↕';
                iconEl.style.color = '#94a3b8';
            }
        }
    });

    // Populate Table Body
    const tbody = document.getElementById('mount-points-tbody');
    if (!tbody) return;

    if (filtered.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" style="text-align: center; padding: 2rem; color: #94a3b8; font-style: italic;">
                    No mount points match your search criteria.
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = '';
    filtered.forEach(item => {
        const pct = item.usage_percent || 0;
        const barColor = getProgressFillColor(pct);
        const badgeHtml = getStatusBadgeHtml(pct);

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td style="padding: 0.65rem 0.85rem; font-weight: 600; color: #0f172a;">${item.device}</td>
            <td style="padding: 0.65rem 0.85rem; font-weight: 700; color: #1e293b;">${item.mount_point}</td>
            <td style="padding: 0.65rem 0.85rem; color: #64748b;"><span class="badge bg-light text-dark border">${item.filesystem}</span></td>
            <td style="padding: 0.65rem 0.85rem; font-weight: 600; color: #334155;">${item.total_size}</td>
            <td style="padding: 0.65rem 0.85rem; font-weight: 600; color: #334155;">${item.used_size}</td>
            <td style="padding: 0.65rem 0.85rem; font-weight: 600; color: #334155;">${item.available_size}</td>
            <td style="padding: 0.65rem 0.85rem; min-width: 140px;">
                <div style="display: flex; align-items: center; gap: 0.5rem;">
                    <div style="flex: 1; height: 7px; background: #e2e8f0; border-radius: 999px; overflow: hidden;">
                        <div style="width: ${Math.min(100, Math.max(0, pct))}%; height: 100%; background: ${barColor}; border-radius: 999px;"></div>
                    </div>
                    <span style="font-weight: 700; font-size: 0.78rem; color: ${barColor}; min-width: 32px; text-align: right;">${pct}%</span>
                </div>
            </td>
            <td style="padding: 0.65rem 0.85rem;">${badgeHtml}</td>
        `;
        tbody.appendChild(tr);
    });
}

function sortMountPointsTable(column) {
    if (currentMountSortColumn === column) {
        currentMountSortAscending = !currentMountSortAscending;
    } else {
        currentMountSortColumn = column;
        currentMountSortAscending = true;
    }
    filterMountPointsTable();
}

// ✅ ORACLE DATABASE PROCESSES MODULE LOGIC
let oracleProcessesData = [];
let oracleProcSearchQuery = "";
let oracleProcSortOption = "pid_asc";
let oracleProcStatusFilter = "all";

function fetchOracleDatabaseProcesses() {
    const container = document.getElementById('oracle-processes-container');
    if (!container) return;

    fetch('/api/oracle-processes')
        .then(res => res.json())
        .then(data => {
            if (data.status === 'error' || !data.processes) {
                container.innerHTML = `
                    <div style="padding: 1.5rem; text-align: center; color: #ef4444; font-weight: 600; font-size: 0.85rem; background: #fef2f2; border: 1px solid #fecaca; border-radius: 6px;">
                        Unable to retrieve Oracle Database Processes.
                    </div>`;
                oracleProcessesData = [];
                return;
            }

            oracleProcessesData = data.processes || [];
            renderOracleProcessesTable();
        })
        .catch(err => {
            console.error('Error fetching Oracle database processes:', err);
            container.innerHTML = `
                <div style="padding: 1.5rem; text-align: center; color: #ef4444; font-weight: 600; font-size: 0.85rem; background: #fef2f2; border: 1px solid #fecaca; border-radius: 6px;">
                    Unable to retrieve Oracle Database Processes.
                </div>`;
            oracleProcessesData = [];
        });
}

function renderOracleProcessesTable() {
    const container = document.getElementById('oracle-processes-container');
    if (!container) return;

    if (!oracleProcessesData || oracleProcessesData.length === 0) {
        container.innerHTML = `
            <div style="padding: 1.5rem; text-align: center; color: #64748b; font-weight: 600; font-size: 0.85rem; background: #f8fafc; border: 1px dashed #cbd5e1; border-radius: 6px;">
                No active Oracle user processes found.
            </div>`;
        return;
    }

    // 1. Filter
    let list = oracleProcessesData.filter(proc => {
        // Exclude actual SYS/SYSTEM-family sessions only. A missing username
        // (backend sends "N/A" for those, never blank) is intentionally kept -
        // see get_oracle_database_processes().
        const usernameUpper = (proc.username || '').toUpperCase().trim();
        if (usernameUpper && ['SYS', 'SYSTEM', 'DBSNMP', 'SYSMAN', 'SYSDG', 'SYSBACKUP', 'SYSKM', 'SYSRAC'].includes(usernameUpper)) {
            return false;
        }

        // Status filter
        if (oracleProcStatusFilter === 'active' && proc.status !== 'ACTIVE') return false;
        if (oracleProcStatusFilter === 'inactive' && proc.status !== 'INACTIVE') return false;

        // Search query filter (Username, Machine, Program, Oracle PID)
        if (oracleProcSearchQuery && oracleProcSearchQuery.trim() !== '') {
            const q = oracleProcSearchQuery.toLowerCase();
            const username = (proc.username || '').toLowerCase();
            const machine = (proc.machine || '').toLowerCase();
            const program = (proc.program || '').toLowerCase();
            const pid = String(proc.oracle_pid || '').trim().toLowerCase();
            const osUser = (proc.os_user || '').toLowerCase();
            const module = (proc.module || '').toLowerCase();
            const sid = String(proc.sid || '').toLowerCase();
            const serial = String(proc.serial || '').toLowerCase();

            return username.includes(q) || machine.includes(q) || program.includes(q) || pid.includes(q) || osUser.includes(q) || module.includes(q) || sid.includes(q) || serial.includes(q);
        }
        return true;
    });

    if (list.length === 0) {
        container.innerHTML = `
            <div style="padding: 1.5rem; text-align: center; color: #64748b; font-weight: 600; font-size: 0.85rem; background: #f8fafc; border: 1px dashed #cbd5e1; border-radius: 6px;">
                No active Oracle user processes found.
            </div>`;
        return;
    }

    // 2. Sort
    list.sort((a, b) => {
        if (oracleProcSortOption === 'pid_asc') {
            return (parseInt(a.oracle_pid) || 0) - (parseInt(b.oracle_pid) || 0);
        } else if (oracleProcSortOption === 'pid_desc') {
            return (parseInt(b.oracle_pid) || 0) - (parseInt(a.oracle_pid) || 0);
        } else if (oracleProcSortOption === 'username_asc') {
            return (a.username || '').localeCompare(b.username || '');
        } else if (oracleProcSortOption === 'status_asc') {
            return (a.status || '').localeCompare(b.status || '');
        } else if (oracleProcSortOption === 'machine_asc') {
            return (a.machine || '').localeCompare(b.machine || '');
        } else if (oracleProcSortOption === 'program_asc') {
            return (a.program || '').localeCompare(b.program || '');
        }
        return 0;
    });

    // Slice to top 10 if filter is 'top10'
    if (oracleProcStatusFilter === 'top10') {
        list = list.slice(0, 10);
    }

    // 3. Build Table
    let tableHtml = `
        <table class="table table-hover align-middle mb-0" style="width: 100%; font-size: 0.8rem; border-collapse: separate; border-spacing: 0;">
            <thead style="background: #f1f5f9;">
                <tr>
                    <th scope="col" style="padding: 0.65rem 0.85rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1;">Oracle PID</th>
                    <th scope="col" style="padding: 0.65rem 0.85rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1;">SID</th>
                    <th scope="col" style="padding: 0.65rem 0.85rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1;">Serial#</th>
                    <th scope="col" style="padding: 0.65rem 0.85rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1;">Logon Time</th>
                    <th scope="col" style="padding: 0.65rem 0.85rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1;">Username</th>
                    <th scope="col" style="padding: 0.65rem 0.85rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1;">OS User</th>
                    <th scope="col" style="padding: 0.65rem 0.85rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1;">Machine</th>
                    <th scope="col" style="padding: 0.65rem 0.85rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1;">Program</th>
                    <th scope="col" style="padding: 0.65rem 0.85rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1;">Module</th>
                    <th scope="col" style="padding: 0.65rem 0.85rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1;">Status</th>
                </tr>
            </thead>
            <tbody>
    `;

    // Store filtered & sorted list for row event index lookup
    currentFilteredProcList = list;

    list.forEach((proc, idx) => {
        const isStatusActive = (proc.status === 'ACTIVE');
        const badgeStyle = isStatusActive 
            ? 'background: #10b981; color: #ffffff;' 
            : 'background: #64748b; color: #ffffff;';

        tableHtml += `
            <tr style="border-bottom: 1px solid #f1f5f9; cursor: pointer; transition: background 0.15s;" 
                onmouseover="this.style.background='#f8fafc';" 
                onmouseout="this.style.background='transparent';"
                oncontextmenu="handleOraProcRowRightClick(event, ${idx})"
                onclick="handleOraProcRowClick(event, ${idx})"
                title="Click or right-click row to view running SQL query / kill session (SID: ${proc.sid})">
                <td style="padding: 0.55rem 0.85rem; font-weight: 700; color: #1e293b;">${proc.oracle_pid}</td>
                <td style="padding: 0.55rem 0.85rem; font-weight: 600; color: #475569;">${proc.sid}</td>
                <td style="padding: 0.55rem 0.85rem; color: #64748b;">${proc.serial}</td>
                <td style="padding: 0.55rem 0.85rem; color: #64748b; white-space: nowrap;">${proc.logon_time}</td>
                <td style="padding: 0.55rem 0.85rem; font-weight: 700; color: #2563eb;">${proc.username}</td>
                <td style="padding: 0.55rem 0.85rem; color: #475569;">${proc.os_user}</td>
                <td style="padding: 0.55rem 0.85rem; color: #475569;">${proc.machine}</td>
                <td style="padding: 0.55rem 0.85rem; color: #334155; font-weight: 500;">${proc.program}</td>
                <td style="padding: 0.55rem 0.85rem; color: #64748b;">${proc.module}</td>
                <td style="padding: 0.55rem 0.85rem;">
                    <span class="badge" style="${badgeStyle} padding: 4px 8px; font-size: 0.7rem; font-weight: 700; border-radius: 4px; text-transform: uppercase;">
                        ${proc.status}
                    </span>
                </td>
            </tr>
        `;
    });

    tableHtml += `
            </tbody>
        </table>
    `;

    container.innerHTML = tableHtml;
}

// ✅ Oracle Process SQL Modal & Right-Click Handlers
let currentFilteredProcList = [];
let currentProcModalSession = null;

function handleOraProcRowRightClick(e, index) {
    e.preventDefault();
    if (!currentFilteredProcList || !currentFilteredProcList[index]) return;
    const proc = currentFilteredProcList[index];
    currentProcModalSession = proc;
    showSessionContextMenu(e.clientX, e.clientY, proc);
}

function handleOraProcRowClick(e, index) {
    if (e.button === 0 && currentFilteredProcList && currentFilteredProcList[index]) {
        openOraProcSqlModal(currentFilteredProcList[index]);
    }
}

function openOraProcSqlModal(proc) {
    if (!proc) proc = currentProcModalSession || currentContextMenuSession;
    if (!proc) return;

    currentProcModalSession = proc;
    const modal = document.getElementById('oracle-proc-sql-modal');
    const title = document.getElementById('ora-proc-modal-title');
    const subtitle = document.getElementById('ora-proc-modal-subtitle');
    const badgesContainer = document.getElementById('ora-proc-modal-badges');
    const textarea = document.getElementById('ora-proc-modal-sqltext');

    if (title) title.innerText = `Running SQL Query — Process PID: ${proc.oracle_pid || 'N/A'}`;
    if (subtitle) subtitle.innerText = `User: ${proc.username || 'N/A'} · Machine: ${proc.machine || 'N/A'} · Logon: ${proc.logon_time || 'N/A'}`;
    if (textarea) textarea.value = proc.sql_text || '-- No SQL query available for this session.';

    if (badgesContainer) {
        badgesContainer.innerHTML = `
            <span style="font-size: 0.68rem; font-weight: 700; background: #e0e7ff; color: #4338ca; padding: 3px 8px; border-radius: 4px; border: 1px solid #c7d2fe;">SID: ${proc.sid}</span>
            <span style="font-size: 0.68rem; font-weight: 700; background: #f1f5f9; color: #475569; padding: 3px 8px; border-radius: 4px; border: 1px solid #cbd5e1;">Serial#: ${proc.serial}</span>
            <span style="font-size: 0.68rem; font-weight: 700; background: #dcfce7; color: #15803d; padding: 3px 8px; border-radius: 4px; border: 1px solid #86efac;">User: ${proc.username}</span>
            <span style="font-size: 0.68rem; font-weight: 700; background: ${proc.status === 'ACTIVE' ? '#10b981' : '#64748b'}; color: #ffffff; padding: 3px 8px; border-radius: 4px;">Status: ${proc.status}</span>
            <span style="font-size: 0.68rem; font-weight: 600; background: #f8fafc; color: #64748b; padding: 3px 8px; border-radius: 4px; border: 1px solid #e2e8f0;">OS User: ${proc.os_user}</span>
            <span style="font-size: 0.68rem; font-weight: 600; background: #f8fafc; color: #64748b; padding: 3px 8px; border-radius: 4px; border: 1px solid #e2e8f0;">Program: ${proc.program}</span>
        `;
    }

    if (modal) modal.style.display = 'flex';
}

function closeOraProcSqlModal() {
    const modal = document.getElementById('oracle-proc-sql-modal');
    if (modal) modal.style.display = 'none';
}

function copyOraProcSql() {
    const textarea = document.getElementById('ora-proc-modal-sqltext');
    if (textarea && textarea.value) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(textarea.value).then(() => alert("SQL query copied to clipboard."));
        } else {
            textarea.select();
            document.execCommand('copy');
            alert("SQL query copied to clipboard.");
        }
    }
}

function executeKillFromProcModal() {
    if (currentProcModalSession) {
        closeOraProcSqlModal();
        killCurrentSelectedSession(currentProcModalSession);
    }
}

function executeViewQueryFromContextMenu() {
    hideSessionContextMenu();
    if (currentContextMenuSession) {
        openOraProcSqlModal(currentContextMenuSession);
    }
}

function handleOraProcSearch() {
    const input = document.getElementById('ora-proc-search-input');
    if (input) {
        oracleProcSearchQuery = input.value;
        renderOracleProcessesTable();
    }
}

function handleOraProcSort() {
    const select = document.getElementById('ora-proc-sort-select');
    if (select) {
        oracleProcSortOption = select.value;
        renderOracleProcessesTable();
    }
}

function filterOraProcStatus(status) {
    oracleProcStatusFilter = status;

    const btnAll = document.getElementById('btn-ora-filter-all');
    const btnActive = document.getElementById('btn-ora-filter-active');
    const btnInactive = document.getElementById('btn-ora-filter-inactive');
    const btnTop10 = document.getElementById('btn-ora-filter-top10');

    if (btnAll) btnAll.classList.remove('active');
    if (btnActive) btnActive.classList.remove('active');
    if (btnInactive) btnInactive.classList.remove('active');
    if (btnTop10) btnTop10.classList.remove('active');

    if (status === 'all' && btnAll) btnAll.classList.add('active');
    if (status === 'active' && btnActive) btnActive.classList.add('active');
    if (status === 'inactive' && btnInactive) btnInactive.classList.add('active');
    if (status === 'top10' && btnTop10) btnTop10.classList.add('active');

    renderOracleProcessesTable();
}

// ✅ HOME PAGE SERVER MOUNT POINTS WIDGET LOGIC
function loadHomeMountPoints(isSilent) {
    const container = document.getElementById('home-mountpoints-container');
    if (!container) return;

    // Show loading spinner only if we don't have existing content and isSilent is falsy
    if (!isSilent && (container.children.length === 0 || container.querySelector('.mounts-loading'))) {
        container.innerHTML = `
            <div class="mounts-loading" style="display: flex; justify-content: center; align-items: center; padding: 2rem; color: #64748b; font-size: 0.9rem;">
                <svg class="animate-spin" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="margin-right: 0.5rem; animation: spin 1.0s linear infinite;">
                    <circle cx="12" cy="12" r="10" opacity="0.25"></circle>
                    <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83 shadow-sm-2.83M16.24 7.76l2.83 shadow-sm-2.83"></path>
                </svg>
                Loading remote mount points...
            </div>
        `;
    }

    fetch('/api/server/all-mount-points')
        .then(res => res.json())
        .then(data => {
            if (data && data.status === 'checking') {
                if (!isSilent) {
                    container.innerHTML = `
                        <div class="mounts-loading" style="display: flex; justify-content: center; align-items: center; padding: 2rem; color: #64748b; font-size: 0.9rem;">
                            <svg class="animate-spin" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="margin-right: 0.5rem; animation: spin 1.0s linear infinite;">
                                <circle cx="12" cy="12" r="10" opacity="0.25"></circle>
                                <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83 shadow-sm-2.83M16.24 7.76l2.83 shadow-sm-2.83"></path>
                            </svg>
                            Checking remote mount points...
                        </div>
                    `;
                }
                setTimeout(() => loadHomeMountPoints(true), 2000);
                return;
            }

            if (!data || data.status === 'error' || !data.servers) {
                const errMsg = data ? (data.message || "Failed to load partitions data.") : "Failed to load partitions data.";
                container.innerHTML = `
                    <div style="color: #ef4444; font-size: 0.88rem; font-weight: 600; text-align: center; padding: 1.5rem; background: #ffffff; border-radius: 10px; border: 1px solid #fee2e2;">
                        Error: ${escapeHtml(errMsg)}
                    </div>
                `;
                return;
            }

            lastHomeMountServersData = data.servers;
            renderHomeMountPointsTable(data.servers);
        })
        .catch(err => {
            console.error("Error loading home mount points:", err);
            container.innerHTML = `
                <div style="color: #ef4444; font-size: 0.88rem; font-weight: 600; text-align: center; padding: 1.5rem; background: #ffffff; border-radius: 10px; border: 1px solid #fee2e2;">
                    Error: Connection or parsing exception occurred while loading telemetry.
                </div>
            `;
        });
}

// `servers` is keyed by unique host - the backend already fetches and groups
// by host, so each entry is one real server (never one row per database).
// Sort state is kept per-host so sorting one server's columns doesn't disturb
// the others, and the last-fetched data is cached so a header click can
// re-render instantly without another round-trip.
let lastHomeMountServersData = null;
let homeMountSortState = {};

const HOME_MOUNT_COLUMNS = [
    { key: 'device', label: 'Device' },
    { key: 'mount_point', label: 'Mount Point' },
    { key: 'filesystem', label: 'Filesystem' },
    { key: 'total_size', label: 'Total Size' },
    { key: 'used_size', label: 'Used Size' },
    { key: 'available_size', label: 'Available Size' },
    { key: 'usage_percent', label: 'Usage %' },
    { key: 'status', label: 'Status' }
];

function renderHomeMountPointsTable(servers) {
    const container = document.getElementById('home-mountpoints-container');
    if (!container) return;

    const hostKeys = Object.keys(servers);
    if (hostKeys.length === 0) {
        container.innerHTML = `
            <div style="color: #64748b; font-size: 0.88rem; text-align: center; padding: 1.5rem;">
                No configured databases or mount point configurations found.
            </div>
        `;
        return;
    }

    let groupsHtml = '';
    hostKeys.forEach(host => {
        const info = servers[host];
        if (!info) return;

        // Always label the group by its host (the actual grouping key), not by
        // one arbitrary member database's service_name (e.g. "kasorcl") -- with
        // several databases sharing a server, no single service_name represents
        // the group correctly.
        const serverLabel = (host || info.server || 'Unknown').trim();
        const dbNames = info.db_names || [];
        const dbCountLabel = `${dbNames.length} database${dbNames.length === 1 ? '' : 's'} on this server`;
        const dbTitle = dbNames.join(', ');

        if (info.status !== 'success') {
            groupsHtml += `
                <div style="margin-bottom: 1.25rem; border: 1px solid #fecaca; border-radius: 8px; overflow: hidden;">
                    <div style="background: #fef2f2; padding: 0.65rem 1rem; display: flex; justify-content: space-between; align-items: center; gap: 0.75rem; flex-wrap: wrap;">
                        <span style="font-weight: 700; color: #991b1b; font-size: 0.85rem;" title="${escapeHtml(dbTitle)}">${escapeHtml(serverLabel)}</span>
                        <span style="font-size: 0.75rem; color: #7f1d1d; font-weight: 600;">${dbCountLabel}</span>
                    </div>
                    <div style="padding: 0.85rem 1rem; font-size: 0.82rem; color: #b91c1c; font-weight: 600;">
                        ⚠️ Connection Failed: ${escapeHtml(info.message || 'SSH Authentication Error or Unreachable.')}
                    </div>
                </div>
            `;
            return;
        }

        const mounts = (info.mount_points || []).filter(mount => {
            const fs = (mount.filesystem || '').toLowerCase().trim();
            const dev = (mount.device || '').toLowerCase().trim();
            return fs !== 'tmpfs' && fs !== 'devtmpfs' && dev !== 'tmpfs' && dev !== 'devtmpfs';
        });

        const sortState = homeMountSortState[host] || { column: null, ascending: true };
        const sortedMounts = mounts.slice();
        if (sortState.column) {
            sortedMounts.sort((a, b) => {
                let valA, valB;
                if (sortState.column === 'status') {
                    valA = (a.usage_percent || 0) >= 90 ? 3 : ((a.usage_percent || 0) >= 71 ? 2 : 1);
                    valB = (b.usage_percent || 0) >= 90 ? 3 : ((b.usage_percent || 0) >= 71 ? 2 : 1);
                } else {
                    valA = a[sortState.column];
                    valB = b[sortState.column];
                    if (typeof valA === 'string') { valA = valA.toLowerCase(); valB = (valB || '').toLowerCase(); }
                }
                if (valA < valB) return sortState.ascending ? -1 : 1;
                if (valA > valB) return sortState.ascending ? 1 : -1;
                return 0;
            });
        }

        const headerHtml = HOME_MOUNT_COLUMNS.map(col => {
            const active = sortState.column === col.key;
            const icon = active ? (sortState.ascending ? '▲' : '▼') : '↕';
            const color = active ? '#2563eb' : '#94a3b8';
            const widthStyle = col.key === 'usage_percent' ? 'width: 140px;' : '';
            return `
                <th scope="col" onclick="sortHomeMountGroup('${host.replace(/'/g, "\\'")}', '${col.key}')" style="cursor: pointer; padding: 0.55rem 0.75rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1; user-select: none; white-space: nowrap; ${widthStyle}">
                    ${col.label} <span style="color: ${color};">${icon}</span>
                </th>
            `;
        }).join('');

        let rowsHtml;
        if (sortedMounts.length === 0) {
            rowsHtml = `
                <tr>
                    <td colspan="8" style="text-align: center; padding: 1.25rem; color: #94a3b8; font-style: italic;">
                        No partition entries returned by server.
                    </td>
                </tr>
            `;
        } else {
            rowsHtml = sortedMounts.map(mount => {
                const pct = mount.usage_percent || 0;
                const barColor = getProgressFillColor(pct);
                const badgeHtml = getStatusBadgeHtml(pct);
                return `
                    <tr style="border-bottom: 1px solid #f1f5f9;">
                        <td style="padding: 0.6rem 0.75rem; font-weight: 600; color: #0f172a;">${escapeHtml(mount.device || '')}</td>
                        <td style="padding: 0.6rem 0.75rem; font-weight: 700; color: #1e293b;">${escapeHtml(mount.mount_point || '')}</td>
                        <td style="padding: 0.6rem 0.75rem;"><span class="badge bg-light text-dark border">${escapeHtml(mount.filesystem || 'N/A')}</span></td>
                        <td style="padding: 0.6rem 0.75rem; font-weight: 600; color: #334155;">${escapeHtml(mount.total_size || '')}</td>
                        <td style="padding: 0.6rem 0.75rem; font-weight: 600; color: #334155;">${escapeHtml(mount.used_size || '')}</td>
                        <td style="padding: 0.6rem 0.75rem; font-weight: 600; color: #334155;">${escapeHtml(mount.available_size || '')}</td>
                        <td style="padding: 0.6rem 0.75rem; min-width: 140px;">
                            <div style="display: flex; align-items: center; gap: 0.5rem;">
                                <div style="flex: 1; height: 7px; background: #e2e8f0; border-radius: 999px; overflow: hidden;">
                                    <div style="width: ${Math.min(100, Math.max(0, pct))}%; height: 100%; background: ${barColor}; border-radius: 999px;"></div>
                                </div>
                                <span style="font-weight: 700; font-size: 0.78rem; color: ${barColor}; min-width: 32px; text-align: right;">${pct}%</span>
                            </div>
                        </td>
                        <td style="padding: 0.6rem 0.75rem;">${badgeHtml}</td>
                    </tr>
                `;
            }).join('');
        }

        groupsHtml += `
            <div style="margin-bottom: 1.25rem; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden;">
                <div style="background: #f1f5f9; padding: 0.65rem 1rem; display: flex; justify-content: space-between; align-items: center; gap: 0.75rem; flex-wrap: wrap;">
                    <span style="font-weight: 700; color: #1e293b; font-size: 0.85rem;" title="${escapeHtml(dbTitle)}">🖥️ ${escapeHtml(serverLabel)}</span>
                    <span style="font-size: 0.75rem; color: #475569; font-weight: 600;">${dbCountLabel}</span>
                </div>
                <div style="overflow-x: auto;">
                    <table style="width: 100%; min-width: 820px; font-size: 0.82rem; border-collapse: separate; border-spacing: 0;">
                        <thead style="background: #f8fafc;"><tr>${headerHtml}</tr></thead>
                        <tbody>${rowsHtml}</tbody>
                    </table>
                </div>
            </div>
        `;
    });

    container.innerHTML = groupsHtml;
}

function sortHomeMountGroup(host, column) {
    const state = homeMountSortState[host] || { column: null, ascending: true };
    if (state.column === column) {
        state.ascending = !state.ascending;
    } else {
        state.column = column;
        state.ascending = true;
    }
    homeMountSortState[host] = state;
    if (lastHomeMountServersData) {
        renderHomeMountPointsTable(lastHomeMountServersData);
    }
}

// ✅ HOMEPAGE ASM DISK GROUPS (Grid Infrastructure, grouped by unique host)
// Entirely separate data source/SSH path from Server Mount Points and Oracle
// Server Processes above - see /api/server/all-asm-diskgroups and
// services/asm_service.py. There is no separate Grid configuration: hosts
// come straight from the uploaded DB configuration's Production entries. A
// host where Grid SSH/ASM discovery or the query itself fails shows
// "Not Available" with the specific reason, without implying the Oracle
// database on that host is down.
function loadHomeAsmDiskGroups(isSilent) {
    const container = document.getElementById('home-asm-diskgroups-container');
    if (!container) return;

    if (!isSilent && (container.children.length === 0 || container.querySelector('.asm-loading'))) {
        container.innerHTML = `
            <div class="asm-loading" style="display: flex; justify-content: center; align-items: center; padding: 2rem; color: #64748b; font-size: 0.9rem;">
                <svg class="animate-spin" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="margin-right: 0.5rem; animation: spin 1.0s linear infinite;">
                    <circle cx="12" cy="12" r="10" opacity="0.25"></circle>
                    <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83 2.83M16.24 7.76l2.83-2.83"></path>
                </svg>
                Loading ASM disk groups...
            </div>
        `;
    }

    fetch('/api/server/all-asm-diskgroups')
        .then(res => res.json())
        .then(data => {
            if (!data || data.status === 'error' || !data.servers) {
                const errMsg = data ? (data.message || "Failed to load ASM disk group data.") : "Failed to load ASM disk group data.";
                container.innerHTML = `
                    <div style="color: #ef4444; font-size: 0.88rem; font-weight: 600; text-align: center; padding: 1.5rem; background: #ffffff; border-radius: 10px; border: 1px solid #fee2e2;">
                        Error: ${escapeHtml(errMsg)}
                    </div>
                `;
                return;
            }
            renderHomeAsmDiskGroupsTable(data.servers);
        })
        .catch(err => {
            console.error("Error loading home ASM disk groups:", err);
            container.innerHTML = `
                <div style="color: #ef4444; font-size: 0.88rem; font-weight: 600; text-align: center; padding: 1.5rem; background: #ffffff; border-radius: 10px; border: 1px solid #fee2e2;">
                    Error: Connection or parsing exception occurred while loading ASM telemetry.
                </div>
            `;
        });
}

function formatAsmGb(gbValue) {
    if (gbValue === null || gbValue === undefined || isNaN(gbValue)) return 'N/A';
    return gbValue.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' GB';
}

function renderHomeAsmDiskGroupsTable(servers) {
    const container = document.getElementById('home-asm-diskgroups-container');
    if (!container) return;

    const hostKeys = Object.keys(servers || {});
    if (hostKeys.length === 0) {
        container.innerHTML = `
            <div style="color: #64748b; font-size: 0.88rem; text-align: center; padding: 1.5rem;">
                No Production database servers are configured. Grid / ASM: Not Available.
            </div>
        `;
        return;
    }

    let groupsHtml = '';
    hostKeys.forEach(host => {
        const info = servers[host];
        if (!info) return;

        const serverLabel = (info.server || host || 'Unknown').trim();

        // ASM instance up but no disk group mounted yet is a normal, valid
        // ASM state - never render it as "Not Available"/a connection
        // failure like the generic error branch below.
        if (info.status === 'not_mounted') {
            const instanceStatus = (info.instance_status || '').trim();
            groupsHtml += `
                <div style="margin-bottom: 1.25rem; border: 1px solid #fde68a; border-radius: 8px; overflow: hidden;">
                    <div style="background: #fffbeb; padding: 0.65rem 1rem; display: flex; justify-content: space-between; align-items: center; gap: 0.75rem; flex-wrap: wrap;">
                        <span style="font-weight: 700; color: #92400e; font-size: 0.85rem;">🖥️ ${escapeHtml(serverLabel)}</span>
                        <span class="badge" style="font-weight: 600; background: #f59e0b; color: #fff;">ASM Instance: AVAILABLE</span>
                    </div>
                    <div style="padding: 0.6rem 1rem; background: #fffdf5; color: #92400e; font-size: 0.8rem; font-weight: 600;">
                        ASM Disk Groups: NOT MOUNTED${instanceStatus ? ` (instance status: ${escapeHtml(instanceStatus)})` : ''}
                    </div>
                </div>
            `;
            return;
        }

        if (info.status !== 'success') {
            const errText = (info.message || 'Not Available').trim();
            groupsHtml += `
                <div style="margin-bottom: 1.25rem; border: 1px solid #fecaca; border-radius: 8px; overflow: hidden;">
                    <div style="background: #fef2f2; padding: 0.65rem 1rem; display: flex; justify-content: space-between; align-items: center; gap: 0.75rem; flex-wrap: wrap;">
                        <span style="font-weight: 700; color: #991b1b; font-size: 0.85rem;">${escapeHtml(serverLabel)}</span>
                        <span class="badge bg-danger" style="font-weight: 600;">Not Available</span>
                    </div>
                    <div style="padding: 0.6rem 1rem; background: #fff7f7; color: #b91c1c; font-size: 0.8rem; font-weight: 500; word-break: break-word;">
                        ${escapeHtml(errText)}
                    </div>
                </div>
            `;
            return;
        }

        const diskgroups = info.diskgroups || [];
        let rowsHtml;
        if (diskgroups.length === 0) {
            rowsHtml = `
                <tr>
                    <td colspan="5" style="text-align: center; padding: 1.25rem; color: #94a3b8; font-style: italic;">
                        No ASM disk groups returned by server.
                    </td>
                </tr>
            `;
        } else {
            rowsHtml = diskgroups.map(dg => {
                const pct = dg.used_pct || 0;
                const barColor = getProgressFillColor(pct);
                const totalGb = (dg.total_mb || 0) / 1024;
                return `
                    <tr style="border-bottom: 1px solid #f1f5f9;">
                        <td style="padding: 0.6rem 0.75rem; font-weight: 700; color: #1e293b;">${escapeHtml(dg.name || '')}</td>
                        <td style="padding: 0.6rem 0.75rem; font-weight: 600; color: #334155;">${formatAsmGb(totalGb)}</td>
                        <td style="padding: 0.6rem 0.75rem; font-weight: 600; color: #334155;">${formatAsmGb(dg.used_gb)}</td>
                        <td style="padding: 0.6rem 0.75rem; font-weight: 600; color: #334155;">${formatAsmGb(dg.free_gb)}</td>
                        <td style="padding: 0.6rem 0.75rem; min-width: 140px;">
                            <div style="display: flex; align-items: center; gap: 0.5rem;">
                                <div style="flex: 1; height: 7px; background: #e2e8f0; border-radius: 999px; overflow: hidden;">
                                    <div style="width: ${Math.min(100, Math.max(0, pct))}%; height: 100%; background: ${barColor}; border-radius: 999px;"></div>
                                </div>
                                <span style="font-weight: 700; font-size: 0.78rem; color: ${barColor}; min-width: 40px; text-align: right;">${pct}%</span>
                            </div>
                        </td>
                    </tr>
                `;
            }).join('');
        }

        groupsHtml += `
            <div style="margin-bottom: 1.25rem; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden;">
                <div style="background: #f1f5f9; padding: 0.65rem 1rem; display: flex; justify-content: space-between; align-items: center; gap: 0.75rem; flex-wrap: wrap;">
                    <span style="font-weight: 700; color: #1e293b; font-size: 0.85rem;">🖥️ ${escapeHtml(serverLabel)}</span>
                    <span class="badge bg-success" style="font-weight: 600;">Available</span>
                </div>
                <div style="overflow-x: auto;">
                    <table style="width: 100%; min-width: 600px; font-size: 0.82rem; border-collapse: separate; border-spacing: 0;">
                        <thead style="background: #f8fafc;">
                            <tr>
                                <th scope="col" style="padding: 0.55rem 0.75rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1; white-space: nowrap;">Name</th>
                                <th scope="col" style="padding: 0.55rem 0.75rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1; white-space: nowrap;">Total</th>
                                <th scope="col" style="padding: 0.55rem 0.75rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1; white-space: nowrap;">Used</th>
                                <th scope="col" style="padding: 0.55rem 0.75rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1; white-space: nowrap;">Free</th>
                                <th scope="col" style="padding: 0.55rem 0.75rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1; white-space: nowrap; width: 140px;">Used %</th>
                            </tr>
                        </thead>
                        <tbody>${rowsHtml}</tbody>
                    </table>
                </div>
            </div>
        `;
    });

    container.innerHTML = groupsHtml;
}

// ✅ HOMEPAGE ORACLE SERVER PROCESSES (grouped by unique host)
// Separate from the dashboard's fetchOracleDatabaseProcesses()/
// renderOracleProcessesTable() (oracle-processes-container), which is
// untouched. This reuses the same underlying V$SESSION/V$PROCESS query via
// /api/server/all-oracle-processes, just aggregated per server for a compact
// homepage summary (no full process list here).
function loadHomeOracleProcesses(isSilent) {
    const container = document.getElementById('home-oracle-processes-container');
    if (!container) return;

    if (!isSilent && (container.children.length === 0 || container.querySelector('.oracle-proc-loading'))) {
        container.innerHTML = `
            <div class="oracle-proc-loading" style="display: flex; justify-content: center; align-items: center; padding: 2rem; color: #64748b; font-size: 0.9rem;">
                <svg class="animate-spin" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="margin-right: 0.5rem; animation: spin 1.0s linear infinite;">
                    <circle cx="12" cy="12" r="10" opacity="0.25"></circle>
                    <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83 2.83M16.24 7.76l2.83-2.83"></path>
                </svg>
                Loading Oracle server processes...
            </div>
        `;
    }

    fetch('/api/server/all-oracle-processes')
        .then(res => res.json())
        .then(data => {
            if (!data || data.status === 'error' || !data.servers) {
                const errMsg = data ? (data.message || "Failed to load Oracle server processes.") : "Failed to load Oracle server processes.";
                container.innerHTML = `
                    <div style="color: #ef4444; font-size: 0.88rem; font-weight: 600; text-align: center; padding: 1.5rem; background: #ffffff; border-radius: 10px; border: 1px solid #fee2e2;">
                        Error: ${escapeHtml(errMsg)}
                    </div>
                `;
                return;
            }
            lastHomeOracleServersData = data.servers;
            renderHomeOracleProcessesTable(data.servers);
        })
        .catch(err => {
            console.error("Error loading home Oracle server processes:", err);
            container.innerHTML = `
                <div style="color: #ef4444; font-size: 0.88rem; font-weight: 600; text-align: center; padding: 1.5rem; background: #ffffff; border-radius: 10px; border: 1px solid #fee2e2;">
                    Error: Connection or parsing exception occurred while loading telemetry.
                </div>
            `;
        });
}

// `servers` is keyed by unique host. Each entry's `processes` is already the
// top 10 Oracle OS processes for that host, ranked by %CPU descending -
// fetched via SSH with EXACTLY:
//   ps -eo pid,user,ni,vsz,rss,state,pcpu,pmem,time,comm --sort=-pcpu |
//   awk 'NR==1 {print; next} $2=="oracle" {print; count++; if(count==10) exit}'
// The backend already caps this at 10 rows per host, so there's no
// client-side Top N slicing here.
let lastHomeOracleServersData = null;

function renderHomeOracleProcessesTable(servers) {
    const container = document.getElementById('home-oracle-processes-container');
    if (!container) return;

    const hostKeys = Object.keys(servers);
    if (hostKeys.length === 0) {
        container.innerHTML = `
            <div style="color: #64748b; font-size: 0.88rem; text-align: center; padding: 1.5rem;">
                No configured databases found.
            </div>
        `;
        return;
    }

    let groupsHtml = '';
    hostKeys.forEach(host => {
        const info = servers[host];
        if (!info) return;

        const serverLabel = (host || info.server || 'Unknown').trim();
        const dbNames = info.db_names || [];
        const dbCountLabel = `${dbNames.length} database${dbNames.length === 1 ? '' : 's'} on this server`;
        const isRunning = info.status === 'Running';
        const statusColor = isRunning ? '#16a34a' : '#ef4444';
        const statusBg = isRunning ? '#f0fdf4' : '#fef2f2';

        const shown = info.processes || [];
        const totalCount = info.total_process_count ?? shown.length;

        let bodyHtml;
        if (!isRunning && shown.length === 0) {
            bodyHtml = `
                <div style="padding: 0.85rem 1rem; font-size: 0.82rem; color: #b91c1c; font-weight: 600;">
                    ⚠️ ${escapeHtml(info.message || 'Unable to retrieve Oracle server processes for this host.')}
                </div>
            `;
        } else if (shown.length === 0) {
            bodyHtml = `
                <div style="padding: 1.25rem; text-align: center; color: #94a3b8; font-style: italic; font-size: 0.82rem;">
                    No active Oracle OS processes found on this server.
                </div>
            `;
        } else {
            const rows = shown.map(p => {
                return `
                    <tr style="border-bottom: 1px solid #f1f5f9;">
                        <td style="padding: 0.55rem 0.75rem; font-weight: 700; color: #1e293b;">${escapeHtml(String(p.oracle_pid || ''))}</td>
                        <td style="padding: 0.55rem 0.75rem; color: #475569;">${escapeHtml(p.os_user || '')}</td>
                        <td style="padding: 0.55rem 0.75rem; color: #475569;">${escapeHtml(String(p.niceness || ''))}</td>
                        <td style="padding: 0.55rem 0.75rem; color: #475569; white-space: nowrap;">${escapeHtml(String(p.vsz_kb || ''))}</td>
                        <td style="padding: 0.55rem 0.75rem; color: #475569; white-space: nowrap;">${escapeHtml(String(p.rss_kb || ''))}</td>
                        <td style="padding: 0.55rem 0.75rem; color: #475569;">${escapeHtml(p.state || '')}</td>
                        <td style="padding: 0.55rem 0.75rem; color: #475569; white-space: nowrap;">${escapeHtml((Number(p.cpu_percent) || 0).toFixed(1))}%</td>
                        <td style="padding: 0.55rem 0.75rem; color: #475569; white-space: nowrap;">${escapeHtml((Number(p.memory_percent) || 0).toFixed(1))}%</td>
                        <td style="padding: 0.55rem 0.75rem; color: #475569; white-space: nowrap;">${escapeHtml(p.cpu_time || '')}</td>
                        <td style="padding: 0.55rem 0.75rem; color: #334155;">${escapeHtml(p.program || '')}</td>
                    </tr>
                `;
            }).join('');

            bodyHtml = `
                <div style="overflow-x: auto;">
                    <table style="width: 100%; min-width: 640px; font-size: 0.8rem; border-collapse: separate; border-spacing: 0;">
                        <thead style="background: #f8fafc;">
                            <tr>
                                <th style="padding: 0.55rem 0.75rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1;">PID</th>
                                <th style="padding: 0.55rem 0.75rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1;">USER</th>
                                <th style="padding: 0.55rem 0.75rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1;">NI</th>
                                <th style="padding: 0.55rem 0.75rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1;">VSZ</th>
                                <th style="padding: 0.55rem 0.75rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1;">RSS</th>
                                <th style="padding: 0.55rem 0.75rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1;">S</th>
                                <th style="padding: 0.55rem 0.75rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1;">%CPU</th>
                                <th style="padding: 0.55rem 0.75rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1;">%MEM</th>
                                <th style="padding: 0.55rem 0.75rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1;">TIME</th>
                                <th style="padding: 0.55rem 0.75rem; font-weight: 700; color: #334155; border-bottom: 2px solid #cbd5e1;">COMMAND</th>
                            </tr>
                        </thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>
            `;
        }

        groupsHtml += `
            <div style="margin-bottom: 1.25rem; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden;">
                <div style="background: #f1f5f9; padding: 0.65rem 1rem; display: flex; justify-content: space-between; align-items: center; gap: 0.75rem; flex-wrap: wrap;">
                    <div style="display: flex; align-items: center; gap: 0.65rem; flex-wrap: wrap;">
                        <span style="font-weight: 700; color: #1e293b; font-size: 0.85rem;" title="${escapeHtml(dbNames.join(', '))}">🖥️ ${escapeHtml(serverLabel)}</span>
                        <span style="font-size: 0.75rem; color: #475569; font-weight: 600;">${dbCountLabel}</span>
                        <span style="display: inline-flex; align-items: center; gap: 0.35rem; font-size: 0.72rem; font-weight: 700; padding: 2px 9px; border-radius: 12px; background: ${statusBg}; color: ${statusColor};">
                            <span style="width: 6px; height: 6px; border-radius: 50%; background: ${statusColor};"></span>
                            ${isRunning ? 'Running' : 'Down'}
                        </span>
                    </div>
                    <div style="display: flex; align-items: center; gap: 0.4rem;">
                        <span style="font-size: 0.72rem; color: #64748b; font-weight: 600;">Top ${totalCount} Oracle process${totalCount === 1 ? '' : 'es'} by CPU usage</span>
                    </div>
                </div>
                ${bodyHtml}
            </div>
        `;
    });

    container.innerHTML = groupsHtml;
}

// Dropdown Status Filtering System
let globalFilters = new Set(['healthy', 'warning', 'critical']);

function initStatusFilterDropdown() {
    const btn = document.getElementById('filter-dropdown-btn');
    const content = document.getElementById('filter-dropdown-content');
    if (!btn || !content) return;
    
    btn.onclick = (e) => {
        e.stopPropagation();
        const isOpen = content.style.display === 'block';
        content.style.display = isOpen ? 'none' : 'block';
    };
    
    // Hide dropdown when clicking elsewhere
    document.addEventListener('click', (e) => {
        if (!content.contains(e.target) && e.target !== btn) {
            content.style.display = 'none';
        }
    });

    // All Databases button quick reset
    const allBtn = document.getElementById('filter-all-btn');
    if (allBtn) {
        allBtn.onclick = (e) => {
            e.stopPropagation();
            document.getElementById('filter-chk-healthy').checked = true;
            document.getElementById('filter-chk-warning').checked = true;
            document.getElementById('filter-chk-critical').checked = true;
            updateFilterOptionStyles();
            
            globalFilters.clear();
            globalFilters.add('healthy');
            globalFilters.add('warning');
            globalFilters.add('critical');
            reorderQuickConnectCards();
        };
    }
    
    // Clear button
    const clearBtn = document.getElementById('filter-clear-btn');
    if (clearBtn) {
        clearBtn.onclick = (e) => {
            e.stopPropagation();
            document.getElementById('filter-chk-healthy').checked = false;
            document.getElementById('filter-chk-warning').checked = false;
            document.getElementById('filter-chk-critical').checked = false;
            updateFilterOptionStyles();
        };
    }
    
    // Checkboxes change styles immediately
    const chks = ['healthy', 'warning', 'critical'];
    chks.forEach(type => {
        const el = document.getElementById(`filter-chk-${type}`);
        if (el) {
            el.addEventListener('change', updateFilterOptionStyles);
        }
    });
    
    // Apply button
    const applyBtn = document.getElementById('filter-apply-btn');
    if (applyBtn) {
        applyBtn.onclick = (e) => {
            e.stopPropagation();
            globalFilters.clear();
            chks.forEach(type => {
                const el = document.getElementById(`filter-chk-${type}`);
                if (el && el.checked) {
                    globalFilters.add(type);
                }
            });
            reorderQuickConnectCards();
            content.style.display = 'none';
        };
    }
    
    updateFilterOptionStyles();
}

function updateFilterOptionStyles() {
    const chks = ['healthy', 'warning', 'critical'];
    chks.forEach(type => {
        const el = document.getElementById(`filter-chk-${type}`);
        if (el) {
            // Find parent label
            let parent = el.parentElement;
            while (parent && parent.tagName !== 'LABEL') {
                parent = parent.parentElement;
            }
            if (parent) {
                if (el.checked) {
                    if (type === 'healthy') {
                        parent.style.backgroundColor = 'rgba(16, 185, 129, 0.08)';
                    } else if (type === 'warning') {
                        parent.style.backgroundColor = '#fef3c7'; // Light yellow/amber
                    } else if (type === 'critical') {
                        parent.style.backgroundColor = '#fee2e2'; // Light red/pink
                    }
                } else {
                    parent.style.backgroundColor = '';
                }
            }
        }
    });
}

function updateFilterCounts() {
    let healthy = 0;
    let warning = 0;
    let critical = 0;
    
    if (globalDatabases && globalDatabases.length > 0) {
        globalDatabases.forEach(db => {
            const dbKey = db.db_id.toLowerCase().replace(/[^a-z0-9]/g, '_');
            const summary = dbStatuses[dbKey];
            if (summary) {
                if (summary.db_status === 'Connected') {
                    const isBackupCompleted = summary.last_backup_status && (
                        summary.last_backup_status.toUpperCase() === 'COMPLETED' || 
                        summary.last_backup_status.toUpperCase() === 'SUCCESS'
                    );
                    const backupYesterday = summary.backup_yesterday === true;
                    const backupIsHealthy = isBackupCompleted && backupYesterday;
                    if (!backupIsHealthy) {
                        warning++;
                    } else {
                        healthy++;
                    }
                } else if (summary.listener_status === 'Running') {
                    warning++;
                } else {
                    critical++;
                }
            } else {
                critical++; // Loading/Offline count
            }
        });
    }
    
    const countHealthyEl = document.getElementById('filter-count-healthy');
    const countWarningEl = document.getElementById('filter-count-warning');
    const countCriticalEl = document.getElementById('filter-count-critical');
    const countAllEl = document.getElementById('filter-count-all');
    
    if (countHealthyEl) countHealthyEl.innerText = healthy;
    if (countWarningEl) countWarningEl.innerText = warning;
    if (countCriticalEl) countCriticalEl.innerText = critical;
    if (countAllEl) countAllEl.innerText = (globalDatabases ? globalDatabases.length : 0);
}

// ✅ ARCHIVE LOG CLEANUP INTEGRATION
let isCleanupInProgress = false;

function fetchArchiveCleanupInfo() {
    // Only fetch if dashboard view is active
    const dbView = document.getElementById('dashboard-view');
    if (!dbView || dbView.style.display === 'none') return;
    
    // Don't overwrite state if execution is running
    if (isCleanupInProgress) return;

    const retentionInput = document.getElementById('cleanup-retention-input');
    const retentionDays = retentionInput ? retentionInput.value : 10;

    fetch(`/api/archive-cleanup/info?retention_days=${retentionDays}`)
        .then(res => res.json())
        .then(data => {
            if (data.error) {
                console.error("Archive Cleanup Info Error:", data.error);
                setArchiveCleanupErrorState();
                return;
            }

            // Update Badge Mode
            const modeBadge = document.getElementById('cleanup-mode-badge');
            if (modeBadge) {
                if (data.log_mode === 'ARCHIVELOG') {
                    modeBadge.innerText = 'Enabled';
                    modeBadge.className = 'pill-badge green';
                    modeBadge.style.backgroundColor = '';
                    modeBadge.style.color = '';
                } else {
                    modeBadge.innerText = 'Disabled';
                    modeBadge.className = 'pill-badge gray';
                    modeBadge.style.backgroundColor = '#94a3b8';
                    modeBadge.style.color = '#ffffff';
                }
            }

            // Update Info Labels
            const destLabel = document.getElementById('cleanup-display-dest');
            if (destLabel) {
                destLabel.innerText = data.archive_destination || 'N/A';
                destLabel.title = data.archive_destination || 'N/A';
            }

            const typeLabel = document.getElementById('cleanup-display-type');
            if (typeLabel) {
                typeLabel.innerText = data.archive_type || 'N/A';
            }

            const eligibleLabel = document.getElementById('cleanup-display-eligible');
            if (eligibleLabel) {
                eligibleLabel.innerText = `${data.eligible_log_count || 0} logs`;
                if (data.eligible_log_count > 0) {
                    eligibleLabel.style.color = '#dc2626';
                } else {
                    eligibleLabel.style.color = '#1e3a8a';
                }
            }

            // Update Last Run Stats
            const statusLabel = document.getElementById('cleanup-last-status');
            if (statusLabel) {
                statusLabel.innerText = data.last_cleanup_status || 'Never Run';
                if (data.last_cleanup_status === 'SUCCESS') {
                    statusLabel.style.color = '#10b981';
                } else if (data.last_cleanup_status === 'FAILED') {
                    statusLabel.style.color = '#ef4444';
                } else {
                    statusLabel.style.color = '#94a3b8';
                }
            }

            const timeLabel = document.getElementById('cleanup-last-time');
            if (timeLabel) {
                timeLabel.innerText = data.last_cleanup_time || 'N/A';
            }

            // Disable delete button if NOT in archivelog mode or if eligible count is 0
            const deleteBtn = document.getElementById('archive-delete-btn');
            if (deleteBtn) {
                if (data.log_mode !== 'ARCHIVELOG') {
                    deleteBtn.disabled = true;
                    deleteBtn.style.opacity = '0.5';
                    deleteBtn.style.cursor = 'not-allowed';
                    deleteBtn.title = 'Database is not running in ARCHIVELOG mode.';
                } else {
                    deleteBtn.disabled = false;
                    deleteBtn.style.opacity = '1';
                    deleteBtn.style.cursor = 'pointer';
                    deleteBtn.title = `Delete archive logs older than ${retentionDays} days.`;
                }
            }
        })
        .catch(err => {
            console.error("Fetch Archive Cleanup Info error:", err);
            setArchiveCleanupErrorState();
        });
}

function setArchiveCleanupErrorState() {
    const badge = document.getElementById('cleanup-mode-badge');
    if (badge) {
        badge.innerText = 'Error';
        badge.className = 'pill-badge red';
        badge.style.backgroundColor = '#ef4444';
        badge.style.color = '#ffffff';
    }
    const dest = document.getElementById('cleanup-display-dest');
    if (dest) dest.innerText = 'Error';
    const type = document.getElementById('cleanup-display-type');
    if (type) type.innerText = 'Error';
    const eligible = document.getElementById('cleanup-display-eligible');
    if (eligible) {
        eligible.innerText = 'N/A';
        eligible.style.color = '#94a3b8';
    }
    const delBtn = document.getElementById('archive-delete-btn');
    if (delBtn) {
        delBtn.disabled = true;
        delBtn.style.opacity = '0.5';
        delBtn.style.cursor = 'not-allowed';
    }
}

function confirmArchiveCleanup() {
    // Collect UI details and open confirm modal
    const dest = document.getElementById('cleanup-display-dest')?.innerText || 'N/A';
    const type = document.getElementById('cleanup-display-type')?.innerText || 'N/A';
    const eligible = document.getElementById('cleanup-display-eligible')?.innerText || '0 logs';

    const retentionInput = document.getElementById('cleanup-retention-input');
    const retentionDays = retentionInput ? retentionInput.value : 10;

    document.getElementById('cleanup-modal-dest').innerText = dest;
    document.getElementById('cleanup-modal-type').innerText = type;
    document.getElementById('cleanup-modal-eligible').innerText = eligible;

    const confirmRetention = document.getElementById('cleanup-confirm-retention');
    if (confirmRetention) {
        confirmRetention.innerText = `${retentionDays} Days`;
    }
    const confirmWarningRetention = document.getElementById('cleanup-confirm-warning-retention');
    if (confirmWarningRetention) {
        confirmWarningRetention.innerText = retentionDays;
    }

    const modal = document.getElementById('archive-cleanup-confirm-modal');
    if (modal) {
        modal.style.display = 'flex';
    }
}

function closeArchiveCleanupModal(isApproved = false) {
    const modal = document.getElementById('archive-cleanup-confirm-modal');
    if (modal) {
        modal.style.display = 'none';
    }
    if (isApproved) {
        executeArchiveCleanup();
    }
}

function executeArchiveCleanup() {
    closeArchiveCleanupControlModal();
    const confirmModal = document.getElementById('archive-cleanup-confirm-modal');
    if (confirmModal) confirmModal.style.display = 'none';

    isCleanupInProgress = true;
    
    // Disable UI Button and show loading
    const deleteBtn = document.getElementById('archive-delete-btn');
    let originalHtml = "";
    if (deleteBtn) {
        originalHtml = deleteBtn.innerHTML;
        deleteBtn.disabled = true;
        deleteBtn.style.opacity = '0.7';
        deleteBtn.style.cursor = 'wait';
        deleteBtn.innerHTML = `
            <svg class="animate-spin" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" style="animation: spin 1s linear infinite; margin-right: 0.25rem;">
                <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-opacity="0.25"></circle>
                <path fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            Cleaning Up...
        `;
    }

    const retentionInput = document.getElementById('cleanup-retention-input');
    const retentionDays = retentionInput ? parseInt(retentionInput.value) : 10;

    // Trigger POST
    fetch('/api/archive-cleanup/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ retention_days: retentionDays })
    })
    .then(res => res.json())
    .then(data => {
        isCleanupInProgress = false;
        
        // Restore Button
        if (deleteBtn) {
            deleteBtn.disabled = false;
            deleteBtn.style.opacity = '1';
            deleteBtn.style.cursor = 'pointer';
            deleteBtn.innerHTML = originalHtml;
        }

        // Check if data is success or failed
        const status = data.status || "FAILED";
        
        // Show Results Modal
        const header = document.getElementById('cleanup-result-header');
        const iconBg = document.getElementById('cleanup-result-icon-bg');
        const title = document.getElementById('cleanup-result-title');
        
        if (status === 'SUCCESS') {
            if (header) header.style.background = 'linear-gradient(135deg, #10b981 0%, #047857 100%)';
            if (iconBg) iconBg.style.background = 'rgba(255, 255, 255, 0.2)';
            if (title) title.innerText = 'Archive Cleanup Successful';
            document.getElementById('cleanup-result-status-text').innerText = 'SUCCESS';
            document.getElementById('cleanup-result-status-text').style.color = '#059669';
        } else {
            if (header) header.style.background = 'linear-gradient(135deg, #f43f5e 0%, #be123c 100%)';
            if (iconBg) iconBg.style.background = 'rgba(255, 255, 255, 0.2)';
            if (title) title.innerText = 'Archive Cleanup Failed';
            document.getElementById('cleanup-result-status-text').innerText = 'FAILED';
            document.getElementById('cleanup-result-status-text').style.color = '#e11d48';
        }

        document.getElementById('cleanup-result-deleted-count').innerText = `${data.deleted_count || 0} logs`;
        document.getElementById('cleanup-result-start-time').innerText = data.start_time || '-';
        document.getElementById('cleanup-result-end-time').innerText = data.end_time || '-';
        document.getElementById('cleanup-result-duration').innerText = `${Number(data.duration_seconds || 0).toFixed(2)} seconds`;
        
        const outputVal = data.rman_output || data.errors || "No rman logs produced.";
        document.getElementById('cleanup-result-rman-output').value = outputVal;

        const resultsModal = document.getElementById('archive-cleanup-result-modal');
        if (resultsModal) {
            resultsModal.style.display = 'flex';
        }
    })
    .catch(err => {
        isCleanupInProgress = false;
        if (deleteBtn) {
            deleteBtn.disabled = false;
            deleteBtn.style.opacity = '1';
            deleteBtn.style.cursor = 'pointer';
            deleteBtn.innerHTML = originalHtml;
        }
        console.error("Cleanup Execution API Error:", err);
        
        // Show Failure Results modal
        const header = document.getElementById('cleanup-result-header');
        if (header) header.style.background = 'linear-gradient(135deg, #f43f5e 0%, #be123c 100%)';
        document.getElementById('cleanup-result-title').innerText = 'Archive Cleanup Error';
        document.getElementById('cleanup-result-status-text').innerText = 'EXCEPTION';
        document.getElementById('cleanup-result-status-text').style.color = '#e11d48';
        document.getElementById('cleanup-result-deleted-count').innerText = '0 logs';
        document.getElementById('cleanup-result-rman-output').value = `Network or browser error: ${err.message}`;
        
        const resultsModal = document.getElementById('archive-cleanup-result-modal');
        if (resultsModal) {
            resultsModal.style.display = 'flex';
        }
    });
}

function closeCleanupResultModal() {
    const modal = document.getElementById('archive-cleanup-result-modal');
    if (modal) {
        modal.style.display = 'none';
    }
    // Refresh the cleanup info to update UI
    fetchArchiveCleanupInfo();
    if (typeof refreshDashboard === 'function') {
        refreshDashboard(true);
    }
}

function onCleanupRetentionChange() {
    fetchArchiveCleanupInfo();
}

function toggleArchiveCleanupPanel() {
    const modal = document.getElementById('archive-cleanup-modal');
    if (modal) {
        modal.style.display = 'flex';
        fetchArchiveCleanupInfo();
    }
}

function closeArchiveCleanupControlModal() {
    const modal = document.getElementById('archive-cleanup-modal');
    if (modal) {
        modal.style.display = 'none';
    }
}





