/**
 * Crypto Price LTP Dashboard - Frontend Application
 */

// ==================== Configuration ====================

const CONFIG = {
    API_BASE_URL: '',  // Same origin
    REFRESH_INTERVAL: 2000,  // 2 seconds
    REQUEST_TIMEOUT: 5000,   // 5 seconds
};

// ==================== State Management ====================

let state = {
    services: [],
    exchanges: {},
    lastUpdate: null,
    refreshTimer: null,
    countdownTimer: null,
    isLoading: false,
};

// ==================== API Client ====================

class APIClient {
    async fetchStatus() {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), CONFIG.REQUEST_TIMEOUT);

        try {
            const response = await fetch(`${CONFIG.API_BASE_URL}/api/status`, {
                signal: controller.signal,
            });
            clearTimeout(timeout);

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            return await response.json();
        } catch (error) {
            clearTimeout(timeout);
            throw error;
        }
    }

    async startService(serviceId) {
        const response = await fetch(`${CONFIG.API_BASE_URL}/api/service/${serviceId}/start`, {
            method: 'POST',
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        return await response.json();
    }

    async stopService(serviceId) {
        const response = await fetch(`${CONFIG.API_BASE_URL}/api/service/${serviceId}/stop`, {
            method: 'POST',
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        return await response.json();
    }
}

const apiClient = new APIClient();

// ==================== UI Rendering ====================

class UIRenderer {
    renderExchanges(exchanges) {
        const container = document.getElementById('exchanges-container');
        container.innerHTML = '';

        // Sort exchanges by name
        const sortedExchanges = Object.entries(exchanges).sort((a, b) =>
            a[1].name.localeCompare(b[1].name)
        );

        for (const [exchangeId, exchangeData] of sortedExchanges) {
            const exchangeCard = this.createExchangeCard(exchangeId, exchangeData);
            container.appendChild(exchangeCard);
        }
    }

    createExchangeCard(exchangeId, exchangeData) {
        const card = document.createElement('div');
        card.className = 'exchange-card';
        card.id = `exchange-${exchangeId}`;

        const header = document.createElement('div');
        header.className = 'exchange-header';

        const name = document.createElement('div');
        name.className = 'exchange-name';
        name.textContent = exchangeData.name;

        const stats = document.createElement('div');
        stats.className = 'exchange-stats';

        const dataCount = document.createElement('div');
        dataCount.className = 'data-count';
        dataCount.innerHTML = `Data Points: <span class="count-value">${exchangeData.total_data_points || 0}</span>`;

        stats.appendChild(dataCount);
        header.appendChild(name);
        header.appendChild(stats);

        const servicesGrid = document.createElement('div');
        servicesGrid.className = 'services-grid';

        for (const service of exchangeData.services) {
            const serviceCard = this.createServiceCard(service);
            servicesGrid.appendChild(serviceCard);
        }

        card.appendChild(header);
        card.appendChild(servicesGrid);

        return card;
    }

    createServiceCard(service) {
        const card = document.createElement('div');
        card.className = `service-card ${service.status}`;
        card.id = `service-${service.id}`;

        // Service Header
        const header = document.createElement('div');
        header.className = 'service-header';

        const info = document.createElement('div');
        info.className = 'service-info';

        const name = document.createElement('h3');
        name.textContent = service.name;

        const type = document.createElement('div');
        type.className = 'service-type';
        type.textContent = service.type;

        info.appendChild(name);
        info.appendChild(type);

        const statusBadge = document.createElement('div');
        statusBadge.className = `status-badge ${service.status}`;
        statusBadge.textContent = service.status;

        header.appendChild(info);
        header.appendChild(statusBadge);

        // Service Details
        const details = document.createElement('div');
        details.className = 'service-details';

        const dataCountRow = this.createDetailRow('Data Points', service.data_count || 0);
        details.appendChild(dataCountRow);

        if (service.last_update) {
            const lastUpdateRow = this.createDetailRow('Last Update', this.formatTime(service.last_update));
            details.appendChild(lastUpdateRow);
        }

        // Service Actions
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
        stopBtn.disabled = service.status === 'stopped' || service.status === 'stopping';

        actions.appendChild(startBtn);
        actions.appendChild(stopBtn);

        card.appendChild(header);
        card.appendChild(details);
        card.appendChild(actions);

        return card;
    }

    createDetailRow(label, value) {
        const row = document.createElement('div');
        row.className = 'detail-row';

        const labelElem = document.createElement('div');
        labelElem.className = 'detail-label';
        labelElem.textContent = label;

        const valueElem = document.createElement('div');
        valueElem.className = 'detail-value';
        valueElem.textContent = value;

        row.appendChild(labelElem);
        row.appendChild(valueElem);

        return row;
    }

    updateHeaderStats(totalServices, runningServices) {
        document.getElementById('total-services').textContent = totalServices;
        document.getElementById('running-services').textContent = runningServices;
        document.getElementById('last-update').textContent = this.formatTime(new Date().toISOString());
    }

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

    formatTime(isoString) {
        if (!isoString) return 'N/A';
        const date = new Date(isoString);
        return date.toLocaleTimeString('en-US', {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
    }

    async handleStartService(serviceId) {
        const button = document.querySelector(`#service-${serviceId} .btn-start`);
        const originalText = button.textContent;
        button.textContent = 'Starting...';
        button.disabled = true;
        button.className = 'btn btn-loading';

        try {
            await apiClient.startService(serviceId);
            console.log(`Start command sent for ${serviceId}`);
            // Status will update on next refresh
        } catch (error) {
            console.error(`Error starting service ${serviceId}:`, error);
            alert(`Failed to start service: ${error.message}`);
            button.textContent = originalText;
            button.disabled = false;
            button.className = 'btn btn-start';
        }
    }

    async handleStopService(serviceId) {
        const button = document.querySelector(`#service-${serviceId} .btn-stop`);
        const originalText = button.textContent;
        button.textContent = 'Stopping...';
        button.disabled = true;
        button.className = 'btn btn-loading';

        try {
            await apiClient.stopService(serviceId);
            console.log(`Stop command sent for ${serviceId}`);
            // Status will update on next refresh
        } catch (error) {
            console.error(`Error stopping service ${serviceId}:`, error);
            alert(`Failed to stop service: ${error.message}`);
            button.textContent = originalText;
            button.disabled = false;
            button.className = 'btn btn-stop';
        }
    }
}

const uiRenderer = new UIRenderer();

// ==================== Application Controller ====================

class DashboardApp {
    constructor() {
        this.refreshCountdown = CONFIG.REFRESH_INTERVAL / 1000;
    }

    async initialize() {
        console.log('Initializing Crypto Price LTP Dashboard...');
        await this.loadData();
        this.startAutoRefresh();
        this.startCountdown();
    }

    async loadData() {
        if (state.isLoading) return;

        state.isLoading = true;

        try {
            const data = await apiClient.fetchStatus();

            if (data.success) {
                state.services = data.services;
                state.exchanges = data.exchanges;
                state.lastUpdate = new Date();

                uiRenderer.hideLoading();
                uiRenderer.renderExchanges(data.exchanges);
                uiRenderer.updateHeaderStats(data.total_services, data.running_services);
            } else {
                throw new Error('Failed to fetch status');
            }
        } catch (error) {
            console.error('Error loading data:', error);
            uiRenderer.showError(`Failed to load dashboard: ${error.message}`);
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
            document.getElementById('refresh-countdown').textContent = this.refreshCountdown;
        }, 1000);
    }

    cleanup() {
        if (state.refreshTimer) {
            clearInterval(state.refreshTimer);
        }
        if (state.countdownTimer) {
            clearInterval(state.countdownTimer);
        }
    }
}

// ==================== Application Entry Point ====================

const app = new DashboardApp();

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => app.initialize());
} else {
    app.initialize();
}

// Cleanup on page unload
window.addEventListener('beforeunload', () => app.cleanup());
