/**
 * Crypto Price LTP Dashboard - Frontend Application
 * Glassmorphism UI with bulk controls, search, and differential updates.
 */

// ==================== Configuration ====================

const CONFIG = {
    API_BASE_URL: '',
    REFRESH_INTERVAL: 2000,
    REQUEST_TIMEOUT: 5000,
    TOAST_DURATION: 3000,
    SEARCH_DEBOUNCE: 300,
};

const DATA_TYPE_CONFIG = {
    ltp:       { label: 'LTP',     cssClass: 'badge-ltp' },
    orderbook: { label: 'OB',      cssClass: 'badge-ob' },
    trades:    { label: 'TRADES',  cssClass: 'badge-trades' },
    funding:   { label: 'FUNDING', cssClass: 'badge-funding' },
};

// ==================== State ====================

const state = {
    services: [],
    exchanges: {},
    lastUpdate: null,
    refreshTimer: null,
    countdownTimer: null,
    isLoading: false,
    isFirstRender: true,
    previousServiceCount: 0,
    searchQuery: '',
};

// ==================== API Client ====================

class APIClient {
    async _request(url, method = 'GET', body = null) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), CONFIG.REQUEST_TIMEOUT);

        try {
            const options = { method, signal: controller.signal };
            if (body !== null) {
                options.headers = { 'Content-Type': 'application/json' };
                options.body = JSON.stringify(body);
            }

            const response = await fetch(`${CONFIG.API_BASE_URL}${url}`, options);
            clearTimeout(timeout);

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            return await response.json();
        } catch (error) {
            clearTimeout(timeout);
            throw error;
        }
    }

    fetchStatus() {
        return this._request('/api/status');
    }

    startService(serviceId) {
        return this._request(`/api/service/${serviceId}/start`, 'POST');
    }

    stopService(serviceId) {
        return this._request(`/api/service/${serviceId}/stop`, 'POST');
    }

    setAutoStart(serviceId, enabled) {
        return this._request(`/api/service/${serviceId}/auto-start`, 'POST', { enabled });
    }

    startAll() {
        return this._request('/api/services/start-all', 'POST');
    }

    stopAll() {
        return this._request('/api/services/stop-all', 'POST');
    }

    startExchange(exchangeId) {
        return this._request(`/api/exchange/${exchangeId}/start`, 'POST');
    }

    stopExchange(exchangeId) {
        return this._request(`/api/exchange/${exchangeId}/stop`, 'POST');
    }
}

const api = new APIClient();

// ==================== Toast Notifications ====================

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('toast-removing');
        setTimeout(() => toast.remove(), 300);
    }, CONFIG.TOAST_DURATION);
}

// ==================== UI Renderer ====================

class UIRenderer {

    // ---------- Full Render ----------

    renderExchanges(exchanges) {
        const container = document.getElementById('exchanges-container');
        container.innerHTML = '';

        const sorted = Object.entries(exchanges).sort((a, b) =>
            a[1].name.localeCompare(b[1].name)
        );

        for (const [exchangeId, exchangeData] of sorted) {
            container.appendChild(this.createExchangeCard(exchangeId, exchangeData));
        }
    }

    createExchangeCard(exchangeId, exchangeData) {
        const card = document.createElement('div');
        card.className = 'exchange-card';
        card.id = `exchange-${exchangeId}`;
        card.dataset.exchange = exchangeId;

        // Header
        const header = document.createElement('div');
        header.className = 'exchange-header';

        const headerLeft = document.createElement('div');
        headerLeft.className = 'exchange-header-left';

        const name = document.createElement('div');
        name.className = 'exchange-name';
        name.textContent = exchangeData.name;

        const runningCount = document.createElement('div');
        runningCount.className = 'exchange-running-count';
        const running = exchangeData.services.filter(s => s.status === 'running').length;
        const total = exchangeData.services.length;
        runningCount.textContent = `${running}/${total} running`;
        runningCount.id = `exchange-running-${exchangeId}`;

        headerLeft.appendChild(name);
        headerLeft.appendChild(runningCount);

        const headerRight = document.createElement('div');
        headerRight.className = 'exchange-header-right';

        const dataCount = document.createElement('div');
        dataCount.className = 'data-count';
        dataCount.id = `exchange-data-${exchangeId}`;
        dataCount.innerHTML = `Data: <span class="count-value">${exchangeData.total_data_points || 0}</span>`;

        const startBtn = document.createElement('button');
        startBtn.className = 'btn btn-exchange-start';
        startBtn.textContent = 'Start All';
        startBtn.onclick = () => this.handleStartExchange(exchangeId);

        const stopBtn = document.createElement('button');
        stopBtn.className = 'btn btn-exchange-stop';
        stopBtn.textContent = 'Stop All';
        stopBtn.onclick = () => this.handleStopExchange(exchangeId);

        headerRight.appendChild(dataCount);
        headerRight.appendChild(startBtn);
        headerRight.appendChild(stopBtn);

        header.appendChild(headerLeft);
        header.appendChild(headerRight);

        // Services grid
        const grid = document.createElement('div');
        grid.className = 'services-grid';

        for (const service of exchangeData.services) {
            grid.appendChild(this.createServiceCard(service));
        }

        card.appendChild(header);
        card.appendChild(grid);

        return card;
    }

    createServiceCard(service) {
        const card = document.createElement('div');
        card.className = `service-card ${service.status}`;
        card.id = `service-${service.id}`;
        card.dataset.serviceId = service.id;
        card.dataset.exchange = service.exchange;
        card.dataset.name = service.name.toLowerCase();
        card.dataset.type = service.type;

        // Header row
        const header = document.createElement('div');
        header.className = 'service-header';

        const info = document.createElement('div');
        info.className = 'service-info';

        const nameEl = document.createElement('h3');
        nameEl.textContent = service.name;

        const typeEl = document.createElement('div');
        typeEl.className = `service-type ${service.type}`;
        typeEl.textContent = service.type;

        // Data type badges
        const badgesContainer = document.createElement('div');
        badgesContainer.className = 'data-badges';

        const dataTypes = service.data_types || [];
        const dataCounts = service.data_counts || {};

        for (const dt of dataTypes) {
            const cfg = DATA_TYPE_CONFIG[dt];
            if (!cfg) continue;

            const badge = document.createElement('span');
            const count = dataCounts[dt] || 0;
            const isActive = dt === 'funding' ? (dataCounts.ltp || 0) > 0 : count > 0;

            badge.className = `data-badge ${cfg.cssClass} ${isActive ? 'active' : 'dimmed'}`;
            badge.textContent = cfg.label;
            badge.dataset.dataType = dt;
            badgesContainer.appendChild(badge);
        }

        info.appendChild(nameEl);
        info.appendChild(typeEl);
        info.appendChild(badgesContainer);

        card.dataset.dataTypes = dataTypes.join(' ');

        // Status indicator
        const statusIndicator = document.createElement('div');
        statusIndicator.className = 'status-indicator';

        const statusDot = document.createElement('div');
        statusDot.className = `status-dot ${service.status}`;

        const statusText = document.createElement('div');
        statusText.className = 'status-text';
        statusText.textContent = service.status;

        statusIndicator.appendChild(statusDot);
        statusIndicator.appendChild(statusText);

        header.appendChild(info);
        header.appendChild(statusIndicator);

        // Details
        const details = document.createElement('div');
        details.className = 'service-details';
        details.appendChild(this.createDetailRow('Data', this.formatDataBreakdown(dataTypes, dataCounts)));
        if (service.last_update) {
            details.appendChild(this.createDetailRow('Updated', this.formatTime(service.last_update)));
        }

        // Auto-start toggle (persists to exchanges.yaml + starts/stops now)
        const autoStartRow = document.createElement('div');
        autoStartRow.className = 'auto-start-row';

        const toggleLabel = document.createElement('label');
        toggleLabel.className = 'auto-start-toggle';

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.className = 'auto-start-checkbox';
        checkbox.checked = !!service.auto_start;
        checkbox.onchange = (e) => this.handleToggleAutoStart(service.id, e.target.checked);

        const slider = document.createElement('span');
        slider.className = 'toggle-slider';

        const labelText = document.createElement('span');
        labelText.className = 'auto-start-label';
        labelText.textContent = 'Auto-start';

        toggleLabel.appendChild(checkbox);
        toggleLabel.appendChild(slider);
        toggleLabel.appendChild(labelText);
        autoStartRow.appendChild(toggleLabel);

        // Actions
        const actions = document.createElement('div');
        actions.className = 'service-actions';

        const startBtn = document.createElement('button');
        startBtn.className = 'btn btn-start';
        startBtn.textContent = 'Start';
        startBtn.onclick = () => this.handleStartService(service.id);
        startBtn.disabled = service.status === 'running' || service.status === 'starting';

        const stopBtn = document.createElement('button');
        stopBtn.className = 'btn btn-stop';
        stopBtn.textContent = 'Stop';
        stopBtn.onclick = () => this.handleStopService(service.id);
        stopBtn.disabled = service.status === 'stopped' || service.status === 'stopping' || service.status === 'unknown';

        actions.appendChild(startBtn);
        actions.appendChild(stopBtn);

        card.appendChild(header);
        card.appendChild(details);
        card.appendChild(autoStartRow);
        card.appendChild(actions);

        return card;
    }

    createDetailRow(label, value) {
        const row = document.createElement('div');
        row.className = 'detail-row';

        const labelEl = document.createElement('div');
        labelEl.className = 'detail-label';
        labelEl.textContent = label;

        const valueEl = document.createElement('div');
        valueEl.className = 'detail-value';
        valueEl.textContent = value;

        row.appendChild(labelEl);
        row.appendChild(valueEl);

        return row;
    }

    // ---------- Differential Updates ----------

    updateExchanges(exchanges) {
        for (const [exchangeId, exchangeData] of Object.entries(exchanges)) {
            // Update exchange-level stats
            const runningEl = document.getElementById(`exchange-running-${exchangeId}`);
            if (runningEl) {
                const running = exchangeData.services.filter(s => s.status === 'running').length;
                const total = exchangeData.services.length;
                runningEl.textContent = `${running}/${total} running`;
            }

            const dataEl = document.getElementById(`exchange-data-${exchangeId}`);
            if (dataEl) {
                dataEl.innerHTML = `Data: <span class="count-value">${exchangeData.total_data_points || 0}</span>`;
            }

            // Update each service card
            for (const service of exchangeData.services) {
                this.updateServiceCard(service);
            }
        }
    }

    updateServiceCard(service) {
        const card = document.getElementById(`service-${service.id}`);
        if (!card) return;

        // Update card class for border color
        card.className = `service-card ${service.status}`;
        if (card.classList.contains('hidden')) {
            card.classList.add('hidden');
        }

        // Update status dot
        const dot = card.querySelector('.status-dot');
        if (dot) {
            dot.className = `status-dot ${service.status}`;
        }

        // Update status text
        const text = card.querySelector('.status-text');
        if (text) {
            text.textContent = service.status;
        }

        // Update data badges
        const badges = card.querySelectorAll('.data-badge');
        const dataCounts = service.data_counts || {};
        for (const badge of badges) {
            const dt = badge.dataset.dataType;
            const count = dataCounts[dt] || 0;
            const isActive = dt === 'funding' ? (dataCounts.ltp || 0) > 0 : count > 0;
            badge.classList.toggle('active', isActive);
            badge.classList.toggle('dimmed', !isActive);
        }

        // Update data breakdown
        const detailValues = card.querySelectorAll('.detail-value');
        const dataTypes = service.data_types || [];
        if (detailValues.length > 0) {
            detailValues[0].textContent = this.formatDataBreakdown(dataTypes, dataCounts);
        }
        if (detailValues.length > 1 && service.last_update) {
            detailValues[1].textContent = this.formatTime(service.last_update);
        }

        // Update auto-start toggle (skip while a toggle request is in flight)
        const autoStartCheckbox = card.querySelector('.auto-start-checkbox');
        if (autoStartCheckbox && !autoStartCheckbox.disabled) {
            autoStartCheckbox.checked = !!service.auto_start;
        }

        // Update button states
        const startBtn = card.querySelector('.btn-start');
        const stopBtn = card.querySelector('.btn-stop');
        if (startBtn) {
            startBtn.disabled = service.status === 'running' || service.status === 'starting';
            startBtn.textContent = 'Start';
            startBtn.className = 'btn btn-start';
        }
        if (stopBtn) {
            stopBtn.disabled = service.status === 'stopped' || service.status === 'stopping' || service.status === 'unknown';
            stopBtn.textContent = 'Stop';
            stopBtn.className = 'btn btn-stop';
        }
    }

    // ---------- Header Stats ----------

    updateHeaderStats(totalServices, runningServices) {
        const totalEl = document.getElementById('total-services');
        const runningEl = document.getElementById('running-services');
        const stoppedEl = document.getElementById('stopped-services');

        if (totalEl) totalEl.textContent = totalServices;
        if (runningEl) runningEl.textContent = runningServices;
        if (stoppedEl) stoppedEl.textContent = totalServices - runningServices;
    }

    // ---------- Search / Filter ----------

    filterServices(query) {
        const q = query.toLowerCase().trim();
        const exchangeCards = document.querySelectorAll('.exchange-card');

        for (const exchangeCard of exchangeCards) {
            const serviceCards = exchangeCard.querySelectorAll('.service-card');
            let visibleCount = 0;

            for (const serviceCard of serviceCards) {
                const name = serviceCard.dataset.name || '';
                const exchange = serviceCard.dataset.exchange || '';
                const type = serviceCard.dataset.type || '';
                const dataTypes = serviceCard.dataset.dataTypes || '';

                const matches = !q || name.includes(q) || exchange.includes(q) || type.includes(q) || dataTypes.includes(q);

                if (matches) {
                    serviceCard.classList.remove('hidden');
                    visibleCount++;
                } else {
                    serviceCard.classList.add('hidden');
                }
            }

            if (visibleCount === 0 && q) {
                exchangeCard.classList.add('hidden');
            } else {
                exchangeCard.classList.remove('hidden');
            }
        }
    }

    // ---------- Loading / Error States ----------

    showLoading() {
        document.getElementById('loading').style.display = 'block';
        document.getElementById('exchanges-container').style.display = 'none';
        document.getElementById('error-message').style.display = 'none';
    }

    hideLoading() {
        document.getElementById('loading').style.display = 'none';
        document.getElementById('exchanges-container').style.display = 'flex';
    }

    showError(message) {
        document.getElementById('error-text').textContent = message;
        document.getElementById('error-message').style.display = 'block';
        document.getElementById('exchanges-container').style.display = 'none';
        document.getElementById('loading').style.display = 'none';
    }

    // ---------- Helpers ----------

    formatTime(isoString) {
        if (!isoString) return 'N/A';
        const date = new Date(isoString);
        return date.toLocaleTimeString('en-US', {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
        });
    }

    formatDataBreakdown(dataTypes, dataCounts) {
        if (!dataTypes || dataTypes.length === 0) return '0';
        const parts = [];
        for (const dt of dataTypes) {
            const cfg = DATA_TYPE_CONFIG[dt];
            if (!cfg) continue;
            const count = dataCounts[dt] || 0;
            parts.push(`${cfg.label}: ${count}`);
        }
        return parts.join(' | ');
    }

    // ---------- Service Action Handlers ----------

    async handleStartService(serviceId) {
        const card = document.getElementById(`service-${serviceId}`);
        const button = card ? card.querySelector('.btn-start') : null;
        if (button) {
            button.textContent = 'Starting...';
            button.disabled = true;
            button.className = 'btn btn-loading';
        }

        try {
            await api.startService(serviceId);
            showToast(`Start command sent for ${serviceId}`, 'success');
        } catch (error) {
            console.error(`Error starting service ${serviceId}:`, error);
            showToast(`Failed to start ${serviceId}: ${error.message}`, 'error');
            if (button) {
                button.textContent = 'Start';
                button.disabled = false;
                button.className = 'btn btn-start';
            }
        }
    }

    async handleStopService(serviceId) {
        const card = document.getElementById(`service-${serviceId}`);
        const button = card ? card.querySelector('.btn-stop') : null;
        if (button) {
            button.textContent = 'Stopping...';
            button.disabled = true;
            button.className = 'btn btn-loading';
        }

        try {
            await api.stopService(serviceId);
            showToast(`Stop command sent for ${serviceId}`, 'success');
        } catch (error) {
            console.error(`Error stopping service ${serviceId}:`, error);
            showToast(`Failed to stop ${serviceId}: ${error.message}`, 'error');
            if (button) {
                button.textContent = 'Stop';
                button.disabled = false;
                button.className = 'btn btn-stop';
            }
        }
    }

    async handleToggleAutoStart(serviceId, enabled) {
        const card = document.getElementById(`service-${serviceId}`);
        const checkbox = card ? card.querySelector('.auto-start-checkbox') : null;
        if (checkbox) checkbox.disabled = true;

        try {
            await api.setAutoStart(serviceId, enabled);
            showToast(
                `Auto-start ${enabled ? 'enabled' : 'disabled'} for ${serviceId}`,
                'success'
            );
        } catch (error) {
            console.error(`Error setting auto-start for ${serviceId}:`, error);
            showToast(`Failed to update auto-start for ${serviceId}: ${error.message}`, 'error');
            if (checkbox) checkbox.checked = !enabled;  // revert on failure
        } finally {
            if (checkbox) checkbox.disabled = false;
        }
    }

    // ---------- Bulk Action Handlers ----------

    async handleStartAll() {
        const btn = document.getElementById('start-all-btn');
        if (btn) btn.disabled = true;

        try {
            await api.startAll();
            showToast('Start commands sent for all services', 'success');
        } catch (error) {
            console.error('Error starting all services:', error);
            showToast(`Failed to start all: ${error.message}`, 'error');
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    async handleStopAll() {
        const btn = document.getElementById('stop-all-btn');
        if (btn) btn.disabled = true;

        try {
            await api.stopAll();
            showToast('Stop commands sent for all services', 'success');
        } catch (error) {
            console.error('Error stopping all services:', error);
            showToast(`Failed to stop all: ${error.message}`, 'error');
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    async handleStartExchange(exchangeId) {
        try {
            await api.startExchange(exchangeId);
            showToast(`Start commands sent for ${exchangeId}`, 'success');
        } catch (error) {
            console.error(`Error starting exchange ${exchangeId}:`, error);
            showToast(`Failed to start ${exchangeId}: ${error.message}`, 'error');
        }
    }

    async handleStopExchange(exchangeId) {
        try {
            await api.stopExchange(exchangeId);
            showToast(`Stop commands sent for ${exchangeId}`, 'success');
        } catch (error) {
            console.error(`Error stopping exchange ${exchangeId}:`, error);
            showToast(`Failed to stop ${exchangeId}: ${error.message}`, 'error');
        }
    }
}

const ui = new UIRenderer();

// ==================== Application Controller ====================

class DashboardApp {
    constructor() {
        this.refreshCountdown = CONFIG.REFRESH_INTERVAL / 1000;
        this._searchDebounceTimer = null;
    }

    async initialize() {
        console.log('Initializing Crypto Price LTP Dashboard...');
        this.bindGlobalEvents();
        await this.loadData();
        this.startAutoRefresh();
        this.startCountdown();
    }

    bindGlobalEvents() {
        // Start All
        const startAllBtn = document.getElementById('start-all-btn');
        if (startAllBtn) {
            startAllBtn.addEventListener('click', () => ui.handleStartAll());
        }

        // Stop All
        const stopAllBtn = document.getElementById('stop-all-btn');
        if (stopAllBtn) {
            stopAllBtn.addEventListener('click', () => ui.handleStopAll());
        }

        // Search input with debounce
        const searchInput = document.getElementById('search-input');
        if (searchInput) {
            searchInput.addEventListener('input', (e) => {
                clearTimeout(this._searchDebounceTimer);
                this._searchDebounceTimer = setTimeout(() => {
                    state.searchQuery = e.target.value;
                    ui.filterServices(e.target.value);
                }, CONFIG.SEARCH_DEBOUNCE);
            });
        }
    }

    async loadData() {
        if (state.isLoading) return;
        state.isLoading = true;

        try {
            const data = await api.fetchStatus();

            if (data.success) {
                state.services = data.services;
                state.exchanges = data.exchanges;
                state.lastUpdate = new Date();

                ui.hideLoading();

                const serviceCount = data.services.length;
                if (state.isFirstRender || serviceCount !== state.previousServiceCount) {
                    // Full render on first load or when service count changes
                    ui.renderExchanges(data.exchanges);
                    state.isFirstRender = false;
                    state.previousServiceCount = serviceCount;

                    // Re-apply search filter after full render
                    if (state.searchQuery) {
                        ui.filterServices(state.searchQuery);
                    }
                } else {
                    // Differential update
                    ui.updateExchanges(data.exchanges);
                }

                ui.updateHeaderStats(data.total_services, data.running_services);
            } else {
                throw new Error('Failed to fetch status');
            }
        } catch (error) {
            console.error('Error loading data:', error);
            if (state.isFirstRender) {
                ui.showError(`Failed to load dashboard: ${error.message}`);
            }
        } finally {
            state.isLoading = false;
        }
    }

    startAutoRefresh() {
        if (state.refreshTimer) {
            clearInterval(state.refreshTimer);
        }

        state.refreshTimer = setInterval(() => {
            this.loadData();
            this.refreshCountdown = CONFIG.REFRESH_INTERVAL / 1000;
        }, CONFIG.REFRESH_INTERVAL);
    }

    startCountdown() {
        if (state.countdownTimer) {
            clearInterval(state.countdownTimer);
        }

        state.countdownTimer = setInterval(() => {
            this.refreshCountdown -= 1;
            if (this.refreshCountdown < 0) {
                this.refreshCountdown = CONFIG.REFRESH_INTERVAL / 1000;
            }
            const el = document.getElementById('refresh-countdown');
            if (el) el.textContent = this.refreshCountdown;
        }, 1000);
    }

    cleanup() {
        if (state.refreshTimer) clearInterval(state.refreshTimer);
        if (state.countdownTimer) clearInterval(state.countdownTimer);
    }
}

// ==================== Entry Point ====================

const app = new DashboardApp();

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => app.initialize());
} else {
    app.initialize();
}

window.addEventListener('beforeunload', () => app.cleanup());
