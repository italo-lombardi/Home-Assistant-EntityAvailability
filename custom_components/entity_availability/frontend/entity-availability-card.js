/**
 * Entity Availability Card v0.3.7
 * Custom Lovelace card for the Home Assistant Entity Availability integration.
 */

const CARD_VERSION = "0.3.10";

console.info(
  `%c ENTITY-AVAILABILITY-CARD %c v${CARD_VERSION} %c — github.com/italo-lombardi `,
  "color: white; background: #4caf50; font-weight: bold; padding: 2px 6px; border-radius: 3px 0 0 3px;",
  "color: #4caf50; background: #e8f5e9; font-weight: bold; padding: 2px 6px;",
  "color: #9e9e9e; background: #e8f5e9; padding: 2px 6px; border-radius: 0 3px 3px 0;"
);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "entity-availability-card",
  name: "Entity Availability Card",
  description: "Dashboard-style entity health monitoring with availability bars and entity list.",
  preview: true,
  documentationURL: "https://github.com/italo-lombardi/Home-Assistant-EntityAvailability",
});

// Canonical no-build LitElement bootstrap — matches thomasloven/lovelace-card-tools pattern.
// home-assistant-main and hui-view are in HA's initial bundle; always defined before card JS runs.
// Synchronous get() avoids the iOS WKWebView timing issues caused by whenDefined("ha-panel-lovelace")
// (ha-panel-lovelace is lazy-loaded and may resolve late or not at all on the Companion App).
const LitElement = Object.getPrototypeOf(
  customElements.get("home-assistant-main") || customElements.get("hui-view")
);
const html = LitElement.prototype.html;
const nothing = LitElement.prototype.nothing ?? "";
const css = LitElement.prototype.css || (() => {
  class CSSResult {
    constructor(cssText) {
      this.cssText = cssText;
      this._styleSheet = null;
    }
    get styleSheet() {
      if (this._styleSheet === null && window.CSSStyleSheet) {
        try {
          this._styleSheet = new CSSStyleSheet();
          this._styleSheet.replaceSync(this.cssText);
        } catch (e) {
          this._styleSheet = null;
        }
      }
      return this._styleSheet;
    }
    toString() { return this.cssText; }
  }
  return (strings, ...values) => new CSSResult(
    strings.reduce((acc, str, i) => acc + str + (values[i] != null ? String(values[i]) : ""), "")
  );
})();

const AVAILABILITY_WINDOWS = [
  { key: "today", label: "Today" },
  { key: "3d", label: "3 Days" },
  { key: "5d", label: "5 Days" },
  { key: "7d", label: "7 Days" },
];

const STATUS_ICONS = {
  green: "mdi:check-circle",
  yellow: "mdi:alert-circle",
  red: "mdi:close-circle",
};

const STATUS_COLORS = {
  green: "#4caf50",
  yellow: "#ff9800",
  red: "#f44336",
};

const cardStyles = css`
  :host {
    --eac-green: #4caf50;
    --eac-yellow: #ff9800;
    --eac-red: #f44336;
    --eac-text-primary: var(--primary-text-color, #212121);
    --eac-text-secondary: var(--secondary-text-color, #727272);
    --eac-divider: var(--divider-color, rgba(0, 0, 0, 0.12));
    --eac-bar-bg: var(--disabled-color, #bdbdbd);
  }

  ha-card {
    overflow: visible;
  }

  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 16px 12px;
  }

  .header-left {
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
  }

  .header-icon {
    --mdc-icon-size: 24px;
    flex-shrink: 0;
  }

  .group-title {
    font-size: 16px;
    font-weight: 500;
    color: var(--eac-text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .header-right {
    font-size: 14px;
    font-weight: 500;
    color: var(--eac-text-secondary);
    white-space: nowrap;
    margin-left: 12px;
  }

  .divider {
    height: 1px;
    background-color: var(--eac-divider);
    margin: 0 16px;
  }

  /* Stats Row */
  .stats-row {
    display: flex;
    align-items: center;
    padding: 10px 16px;
  }

  .stat-item {
    flex: 1;
    text-align: center;
    font-size: 13px;
    font-weight: 500;
  }

  .stat-item.online {
    color: var(--eac-green);
  }

  .stat-item.offline {
    color: var(--eac-red);
  }

  .stat-item.battery {
    color: var(--eac-yellow);
  }

  .stat-item.neutral {
    color: var(--eac-text-secondary);
  }

  .suppressed-banner {
    font-size: 12px;
    color: var(--eac-text-secondary);
    font-style: italic;
    text-align: center;
    padding: 4px 16px 8px;
  }

  /* Availability Section */
  .availability-section {
    padding: 10px 16px;
  }

  .availability-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
  }

  .availability-row:last-child {
    margin-bottom: 0;
  }

  .availability-label {
    font-size: 13px;
    color: var(--eac-text-secondary);
    min-width: 50px;
  }

  .availability-bar {
    flex: 1;
    height: 8px;
    border-radius: 4px;
    background-color: var(--eac-bar-bg);
    overflow: hidden;
  }

  .availability-fill {
    height: 100%;
    border-radius: 4px;
    transition: width 0.5s ease;
  }

  .availability-value {
    font-size: 13px;
    font-weight: 500;
    color: var(--eac-text-primary);
    min-width: 45px;
    text-align: right;
  }

  /* Entity List */
  .entity-section-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 16px;
    cursor: pointer;
    user-select: none;
  }

  .entity-section-header:hover {
    opacity: 0.8;
  }

  .entity-section-title {
    font-size: 14px;
    font-weight: 500;
    color: var(--eac-text-secondary);
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .chevron {
    transition: transform 0.3s ease;
    --mdc-icon-size: 18px;
  }

  .chevron.expanded {
    transform: rotate(180deg);
  }

  .entity-list {
    padding: 0 16px 12px;
    overflow: visible;
    transition: max-height 0.3s ease, opacity 0.3s ease;
  }

  .entity-list.collapsed {
    max-height: 0;
    opacity: 0;
    padding: 0 16px;
    overflow: hidden;
  }

  .entity-list.expanded {
    max-height: 2000px;
    opacity: 1;
  }

  /* Combined group breakdown */
  .group-breakdown {
    padding: 0 16px 12px;
    overflow: hidden;
    transition: max-height 0.3s ease, opacity 0.3s ease;
  }

  .group-breakdown.collapsed {
    max-height: 0;
    opacity: 0;
    padding: 0 16px;
  }

  .group-breakdown.expanded {
    max-height: 2000px;
    opacity: 1;
  }

  .group-breakdown-row {
    display: grid;
    grid-template-columns: 1fr repeat(3, 56px);
    align-items: center;
    padding: 5px 0;
    border-bottom: 1px solid var(--eac-divider);
    font-size: 13px;
  }

  .group-breakdown-row:last-child {
    border-bottom: none;
  }

  .group-breakdown-header {
    font-weight: 500;
    color: var(--eac-text-secondary);
    font-size: 12px;
  }

  .group-breakdown-name {
    color: var(--eac-text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    padding-right: 8px;
  }

  .group-breakdown-count {
    text-align: center;
    font-weight: 500;
  }

  .group-breakdown-count.online { color: var(--eac-green); }
  .group-breakdown-count.offline { color: var(--eac-red); }
  .group-breakdown-count.battery { color: var(--eac-yellow); }

  .entity-legend {
    display: flex;
    align-items: center;
    padding: 0 0 6px;
    gap: 10px;
    border-bottom: 1px solid var(--eac-divider);
    margin-bottom: 4px;
  }

  .entity-legend-dot {
    width: 10px;
    flex-shrink: 0;
  }

  .entity-legend-name {
    font-size: 11px;
    font-weight: 600;
    color: var(--eac-text-secondary);
    flex: 1;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .entity-legend-status {
    font-size: 11px;
    font-weight: 600;
    color: var(--eac-text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    white-space: nowrap;
  }

  .entity-legend-battery {
    font-size: 11px;
    font-weight: 600;
    color: var(--eac-text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    min-width: 35px;
    text-align: right;
  }

  .entity-item {
    display: flex;
    align-items: center;
    padding: 5px 0;
    gap: 10px;
    position: relative;
    flex-wrap: wrap;
  }

  .entity-detail-inline {
    width: 100%;
    padding: 2px 0 6px 20px;
    font-size: 12px;
    border-bottom: 1px solid var(--eac-divider);
    margin-bottom: 2px;
  }

  .entity-detail-inline .entity-tooltip-row {
    padding: 1px 0;
  }

  .entity-tooltip {
    display: none;
    position: absolute;
    left: 0;
    top: calc(100% + 4px);
    z-index: 10;
    background: var(--card-background-color, #fff);
    border: 1px solid var(--divider-color, rgba(0,0,0,0.12));
    border-radius: 6px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.15);
    padding: 8px 10px;
    font-size: 12px;
    color: var(--primary-text-color, #212121);
    white-space: nowrap;
    pointer-events: none;
    min-width: 200px;
  }

  .entity-item:hover .entity-tooltip {
    display: block;
  }

  .entity-tooltip-row {
    display: flex;
    gap: 6px;
    padding: 1px 0;
  }

  .entity-tooltip-label {
    color: var(--secondary-text-color, #727272);
    min-width: 80px;
    flex-shrink: 0;
  }

  .entity-tooltip-value {
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .entity-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  .entity-dot.green { background-color: var(--eac-green); }
  .entity-dot.red { background-color: var(--eac-red); }
  .entity-dot.yellow { background-color: var(--eac-yellow); }
  .entity-dot.grey { background-color: var(--eac-bar-bg); }

  .entity-name {
    font-size: 13px;
    color: var(--eac-text-primary);
    flex: 1;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .entity-status {
    font-size: 12px;
    color: var(--eac-text-secondary);
    white-space: nowrap;
  }

  .entity-battery {
    font-size: 12px;
    color: var(--eac-text-secondary);
    white-space: nowrap;
    min-width: 35px;
    text-align: right;
  }

  /* Actions */
  .actions-section {
    padding: 8px 16px 12px;
    display: flex;
    justify-content: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .action-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 14px;
    border: none;
    border-radius: 4px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: opacity 0.2s;
  }

  .action-btn:hover { opacity: 0.85; }
  .action-btn:active { opacity: 0.7; }

  .action-btn.suppress {
    background-color: var(--primary-color, #03a9f4);
    color: var(--text-primary-color, #fff);
  }

  .action-btn.unsuppress {
    background-color: var(--secondary-background-color, #e0e0e0);
    color: var(--primary-text-color, #212121);
  }

  .error-message {
    padding: 16px;
    color: var(--error-color, #db4437);
    font-size: 14px;
  }

  .compact .card-header { padding: 12px 16px 8px; }
  .compact .stats-row { padding: 6px 16px; }
  .compact .availability-section { padding: 6px 16px; }
  .compact .entity-section-header { padding: 6px 16px; }
  .compact .actions-section { padding: 4px 16px 8px; }
`;

class EntityAvailabilityCard extends LitElement {
  static get properties() {
    return {
      hass: { attribute: false },
      _config: { state: true },
      _entitiesExpanded: { state: true },
    };
  }

  static get styles() {
    return cardStyles;
  }

  static getConfigElement() {
    return document.createElement("entity-availability-card-editor");
  }

  static getStubConfig(hass) {
    const match = Object.keys(hass.states).find(
      (id) =>
        id.startsWith("sensor.entity_availability_") &&
        id.endsWith("_offline_count")
    );
    const group = match
      ? match.replace("sensor.entity_availability_", "").replace("_offline_count", "")
      : "my_devices";
    return {
      group,
      show_availability: true,
      show_entities: true,
      entities_expanded: false,
      show_actions: false,
      compact: false,
    };
  }

  constructor() {
    super();
    this._config = {};
    this._entitiesExpanded = false;
  }

  setConfig(config) {
    if (!config.group) {
      throw new Error("You must define a 'group' in the card configuration.");
    }
    this._config = {
      show_availability: true,
      show_entities: true,
      entities_expanded: false,
      show_actions: false,
      compact: false,
      sort_by: "status",
      entity_detail: "off",
      entity_filter: "all",
      ...config,
    };
    // backwards compat: show_entity_tooltips: true → entity_detail: "tooltip"
    if (!config.entity_detail && config.show_entity_tooltips) {
      this._config.entity_detail = "tooltip";
    }
    this._entitiesExpanded = this._config.entities_expanded;
  }

  getCardSize() {
    return this._config.compact ? 3 : 5;
  }

  shouldUpdate(changedProps) {
    if (changedProps.has("_config") || changedProps.has("_entitiesExpanded")) return true;
    if (!this.hass) return false;

    const oldHass = changedProps.get("hass");
    if (!oldHass) return true;

    const ids = this._getAllEntityIds();
    return ids.some((id) => oldHass.states[id] !== this.hass.states[id]);
  }

  render() {
    if (!this._config || !this.hass) {
      return html`<ha-card><div class="error-message">Card not configured.</div></ha-card>`;
    }

    if (!this._config.group) {
      return html`<ha-card><div class="error-message">No group configured.</div></ha-card>`;
    }

    const isCombined = this._isCombinedGroup();

    if (isCombined) {
      const prefix = `entity_availability_combined_${this._config.group}`;
      const summary = this._getEntity(`sensor.${prefix}_combined_summary`);
      if (!summary) {
        return html`<ha-card>
          <div class="error-message">
            No entities found for combined group "${this._config.group}".
            Expected: sensor.${prefix}_combined_summary
          </div>
        </ha-card>`;
      }
      const attrs = summary.attributes || {};
      const total = attrs.total_entities || 0;
      const online = attrs.online || 0;
      const offline = attrs.offline || 0;
      const lowBattery = attrs.low_battery || 0;
      const suppressed = attrs.suppressed || 0;
      const groups = attrs.groups || {};

      const statusColor = offline > 0 ? "red" : lowBattery > 0 ? "yellow" : "green";
      const title = this._config.title || this._formatGroupName(this._config.group);
      const compactClass = this._config.compact ? "compact" : "";
      const statusText = offline > 0 ? `${offline} Offline` : lowBattery > 0 ? "Degraded" : "All OK";

      return html`
        <ha-card class="${compactClass}">
          ${this._renderHeader(title, statusColor, statusText)}
          <div class="divider"></div>
          ${this._renderStats(online, offline, lowBattery, suppressed)}
          ${suppressed > 0 ? html`<div class="suppressed-banner">${suppressed} ${suppressed > 1 ? "entities" : "entity"} suppressed</div>` : nothing}
          ${this._config.show_entities ? this._renderCombinedGroupBreakdown(groups) : nothing}
          ${this._config.show_actions ? this._renderActions(prefix) : nothing}
        </ha-card>
      `;
    }

    const prefix = `entity_availability_${this._config.group}`;
    const summary = this._getEntity(`sensor.${prefix}_group_summary`);
    const offlineCountEntity = this._getEntity(`sensor.${prefix}_offline_count`);

    if (!summary && !offlineCountEntity) {
      return html`<ha-card>
        <div class="error-message">
          No entities found for group "${this._config.group}".
          Expected: sensor.${prefix}_offline_count
        </div>
      </ha-card>`;
    }

    const attrs = summary?.attributes || {};
    const total = attrs.total_entities || 0;
    const online = attrs.online || 0;
    const offline = attrs.offline || 0;
    const lowBattery = attrs.low_battery || 0;
    const suppressed = attrs.suppressed || 0;
    const entities = attrs.entities || [];
    const batteryLevels = attrs.battery_levels || {};
    const suppressedUntil = attrs.suppressed_until || {};
    const staleEntities = attrs.stale_entities || [];
    const offlineSince = attrs.offline_since || {};
    const lowBatteryEntities = attrs.low_battery_entities || [];
    const displayNames = attrs.display_names || {};

    const statusColor = offline > 0 ? "red" : lowBattery > 0 ? "yellow" : "green";
    const title = this._config.title || this._formatGroupName(this._config.group);
    const compactClass = this._config.compact ? "compact" : "";

    const statusText = offline > 0
      ? `${offline} Offline`
      : lowBattery > 0
      ? "Degraded"
      : "All OK";

    return html`
      <ha-card class="${compactClass}">
        ${this._renderHeader(title, statusColor, statusText)}
        <div class="divider"></div>
        ${this._renderStats(online, offline, lowBattery, suppressed)}
        ${suppressed > 0 ? html`<div class="suppressed-banner">${suppressed} ${suppressed > 1 ? "entities" : "entity"} suppressed</div>` : nothing}
        ${this._config.show_availability ? this._renderAvailability(prefix) : nothing}
        ${this._config.show_entities ? this._renderEntityList(entities, batteryLevels, suppressedUntil, staleEntities, offlineSince, total, lowBatteryEntities, displayNames) : nothing}
        ${this._config.show_actions ? this._renderActions(prefix) : nothing}
      </ha-card>
    `;
  }

  _renderHeader(title, statusColor, statusText) {
    return html`
      <div class="card-header">
        <div class="header-left">
          <ha-icon
            class="header-icon"
            icon="${STATUS_ICONS[statusColor]}"
            style="color: ${STATUS_COLORS[statusColor]}"
          ></ha-icon>
          <span class="group-title">${title}</span>
        </div>
        <div class="header-right">${statusText}</div>
      </div>
    `;
  }

  _renderStats(online, offline, lowBattery, suppressed) {
    return html`
      <div class="stats-row">
        <span class="stat-item ${online > 0 ? "online" : "neutral"}">Online: ${online}</span>
        <span class="stat-item ${offline > 0 ? "offline" : "neutral"}">Offline: ${offline}</span>
        <span class="stat-item ${lowBattery > 0 ? "battery" : "neutral"}">Low Battery: ${lowBattery}</span>
      </div>
    `;
  }

  _renderAvailability(prefix) {
    const windows = [];
    for (const w of AVAILABILITY_WINDOWS) {
      const entity = this._getEntity(`sensor.${prefix}_availability_${w.key}`);
      if (entity && entity.state !== "unavailable" && entity.state !== "unknown") {
        const pct = parseFloat(entity.state) || 0;
        windows.push({ label: w.label, pct });
      }
    }

    if (windows.length === 0) return nothing;

    return html`
      <div class="divider"></div>
      <div class="availability-section">
        ${windows.map(
          (w) => html`
            <div class="availability-row">
              <span class="availability-label">${w.label}</span>
              <div class="availability-bar">
                <div
                  class="availability-fill"
                  style="width: ${Math.min(100, Math.max(0, w.pct))}%; background-color: ${this._getAvailabilityBarColor(w.pct)}"
                ></div>
              </div>
              <span class="availability-value">${w.pct.toFixed(1)}%</span>
            </div>
          `
        )}
      </div>
    `;
  }

  _renderEntityList(entities, batteryLevels, suppressedUntil, staleEntities, offlineSince, total, lowBatteryEntities, displayNames = {}) {
    if (entities.length === 0 && total === 0) return nothing;

    const allItems = this._buildEntityItems(entities, batteryLevels, staleEntities, offlineSince, suppressedUntil, lowBatteryEntities, displayNames);
    const filter = this._config.entity_filter || "all";
    const items = filter === "offline"
      ? allItems.filter((i) => i.isOffline || i.isStale || i.dotColor === "yellow")
      : filter === "online"
      ? allItems.filter((i) => !i.isOffline && !i.isStale && i.dotColor !== "yellow")
      : allItems;

    const expanded = this._entitiesExpanded;
    const hasBattery = allItems.some((i) => i.battery !== null);

    const sectionTitle = filter === "offline" ? "Problem Entities"
      : filter === "online" ? "Healthy Entities"
      : "Entities";
    const countLabel = filter !== "all"
      ? `${items.length}/${allItems.length}`
      : `${items.length}`;

    return html`
      <div class="divider"></div>
      <div class="entity-section-header" @click=${this._toggleEntities}>
        <span class="entity-section-title">
          ${sectionTitle} (${countLabel})
        </span>
        <ha-icon
          class="chevron ${expanded ? "expanded" : ""}"
          icon="mdi:chevron-down"
        ></ha-icon>
      </div>
      <div class="entity-list ${expanded ? "expanded" : "collapsed"}">
        <div class="entity-legend">
          <span class="entity-legend-dot"></span>
          <span class="entity-legend-name">Entity</span>
          <span class="entity-legend-status">State</span>
          ${hasBattery ? html`<span class="entity-legend-battery">Bat.</span>` : nothing}
        </div>
        ${items.map(
          (item) => html`
            <div class="entity-item">
              <div class="entity-dot ${item.dotColor}"></div>
              <span class="entity-name">${item.name}</span>
              <span class="entity-status">${item.status}</span>
              ${hasBattery
                ? html`<span class="entity-battery">${item.battery !== null ? `${item.battery}%` : ""}</span>`
                : nothing}
              ${this._config.entity_detail === "tooltip"
                ? this._renderTooltip(item, suppressedUntil)
                : nothing}
              ${this._config.entity_detail === "inline"
                ? this._renderDetailInline(item, suppressedUntil)
                : nothing}
            </div>
          `
        )}
      </div>
    `;
  }

  _renderCombinedGroupBreakdown(groups) {
    const entries = Object.entries(groups);
    if (entries.length === 0) return nothing;

    const sortBy = this._config.group_sort_by || "name_asc";
    entries.sort(([, gA], [, gB]) => {
      const nameA = gA.name ?? "";
      const nameB = gB.name ?? "";
      if (sortBy === "name_desc") return nameB.localeCompare(nameA);
      if (sortBy === "offline_desc") {
        const diff = (gB.offline ?? 0) - (gA.offline ?? 0);
        return diff !== 0 ? diff : nameA.localeCompare(nameB);
      }
      return nameA.localeCompare(nameB);
    });

    const expanded = this._entitiesExpanded;

    return html`
      <div class="divider"></div>
      <div class="entity-section-header" @click=${this._toggleEntities}>
        <span class="entity-section-title">Groups (${entries.length})</span>
        <ha-icon class="chevron ${expanded ? "expanded" : ""}" icon="mdi:chevron-down"></ha-icon>
      </div>
      <div class="group-breakdown ${expanded ? "expanded" : "collapsed"}">
        <div class="group-breakdown-row group-breakdown-header">
          <span>Group</span>
          <span style="text-align:center">Online</span>
          <span style="text-align:center">Offline</span>
          <span style="text-align:center">Bat.</span>
        </div>
        ${entries.map(([, g]) => html`
          <div class="group-breakdown-row">
            <span class="group-breakdown-name">${g.name ?? ""}</span>
            <span class="group-breakdown-count online">${g.online ?? 0}</span>
            <span class="group-breakdown-count ${g.offline > 0 ? "offline" : "neutral"}">${g.offline ?? 0}</span>
            <span class="group-breakdown-count ${g.low_battery > 0 ? "battery" : "neutral"}">${g.low_battery ?? 0}</span>
          </div>
        `)}
      </div>
    `;
  }

  _renderActions(prefix) {
    return html`
      <div class="divider"></div>
      <div class="actions-section">
        <button class="action-btn suppress" @click=${this._handleSuppressAll}>
          Suppress All
        </button>
        <button class="action-btn unsuppress" @click=${this._handleUnsuppressAll}>
          Unsuppress All
        </button>
      </div>
    `;
  }

  _buildEntityItems(entities, batteryLevels, staleEntities, offlineSince, suppressedUntil, lowBatteryEntities = [], displayNames = {}) {
    const items = entities.map((entityId) => {
      const state = this.hass.states[entityId];
      const friendlyName = displayNames[entityId] || state?.attributes?.friendly_name || entityId.split(".").pop();
      const offlineEntities = this._getOfflineEntityIds();
      const isOffline = offlineEntities.includes(entityId);
      const isStale = staleEntities.includes(entityId);
      const isSuppressed = entityId in suppressedUntil;
      const battery = batteryLevels[entityId] ?? null;
      const isLowBattery = lowBatteryEntities.includes(entityId);

      let dotColor = "green";
      let status = "Online";

      if (isSuppressed) {
        dotColor = "green";
        status = "Suppressed";
      } else if (isOffline) {
        dotColor = "red";
        const since = offlineSince[entityId];
        if (since) {
          const diff = Date.now() - new Date(since).getTime();
          const minutes = Math.floor(diff / 60000);
          if (minutes < 1) status = "just now";
          else if (minutes < 60) status = `${minutes} minute${minutes === 1 ? "" : "s"}`;
          else {
            const hours = Math.floor(minutes / 60);
            if (hours < 24) status = `${hours} hour${hours === 1 ? "" : "s"}`;
            else {
              const days = Math.floor(hours / 24);
              status = `${days} day${days === 1 ? "" : "s"}`;
            }
          }
        } else {
          status = "Offline";
        }
      } else if (isStale) {
        dotColor = "grey";
        status = "Stale";
      } else if (isLowBattery) {
        dotColor = "yellow";
        status = "Low Battery";
      }

      return { entityId, name: friendlyName, dotColor, status, battery, isOffline, isStale, isSuppressed };
    });

    items.sort((a, b) => {
      const sortBy = this._config.sort_by || "status";
      if (sortBy === "name_asc") {
        return a.name.localeCompare(b.name);
      } else if (sortBy === "name_desc") {
        return b.name.localeCompare(a.name);
      } else if (sortBy === "battery_asc") {
        const aBat = a.battery ?? 101;
        const bBat = b.battery ?? 101;
        if (aBat !== bBat) return aBat - bBat;
        return a.name.localeCompare(b.name);
      } else if (sortBy === "battery_desc") {
        const aBat = a.battery ?? -1;
        const bBat = b.battery ?? -1;
        if (aBat !== bBat) return bBat - aBat;
        return a.name.localeCompare(b.name);
      } else {
        if (a.isOffline && !b.isOffline) return -1;
        if (!a.isOffline && b.isOffline) return 1;
        if (a.dotColor === "yellow" && b.dotColor === "green") return -1;
        if (a.dotColor === "green" && b.dotColor === "yellow") return 1;
        if (a.dotColor === "grey" && b.dotColor === "green") return -1;
        if (a.dotColor === "green" && b.dotColor === "grey") return 1;
        return a.name.localeCompare(b.name);
      }
    });

    return items;
  }

  _buildDetailRows(item, suppressedUntilMap) {
    const entityState = this.hass.states[item.entityId];
    const lastChanged = entityState?.last_changed
      ? this._computeDuration(item.entityId)
      : null;

    const areaId = this.hass.entities?.[item.entityId]?.area_id;
    const areaName = areaId ? (this.hass.areas?.[areaId]?.name || null) : null;

    const suppressedUntilIso = suppressedUntilMap[item.entityId];
    const suppressedUntil = suppressedUntilIso
      ? this._formatFutureDate(suppressedUntilIso)
      : null;

    return [
      { label: "Entity ID", value: item.entityId },
      areaName ? { label: "Area", value: areaName } : null,
      { label: "HA State", value: lastChanged ? `${this._formatStateWithUnit(entityState)} · ${lastChanged}` : this._formatStateWithUnit(entityState) },
      { label: "Condition", value: suppressedUntil ? "Suppressed" : item.isOffline ? `Offline for ${item.status}` : item.status },
      item.battery !== null ? { label: "Battery", value: `${item.battery}%` } : null,
      suppressedUntil ? { label: "Suppressed", value: `until ${suppressedUntil}` } : null,
    ].filter(Boolean);
  }

  _renderTooltip(item, suppressedUntilMap) {
    const rows = this._buildDetailRows(item, suppressedUntilMap);
    return html`
      <div class="entity-tooltip">
        ${rows.map((r) => html`
          <div class="entity-tooltip-row">
            <span class="entity-tooltip-label">${r.label}</span>
            <span class="entity-tooltip-value">${r.value}</span>
          </div>
        `)}
      </div>
    `;
  }

  _renderDetailInline(item, suppressedUntilMap) {
    const compact = this._config.compact === true;
    let rows;
    if (compact) {
      const entityState = this.hass.states[item.entityId];
      const lastChanged = entityState?.last_changed
        ? this._computeDuration(item.entityId)
        : null;
      const haStateValue = lastChanged
        ? `${this._formatStateWithUnit(entityState)} · ${lastChanged}`
        : this._formatStateWithUnit(entityState);
      rows = [{ label: "HA State", value: haStateValue }];
    } else {
      rows = this._buildDetailRows(item, suppressedUntilMap);
    }
    return html`
      <div class="entity-detail-inline">
        ${rows.map((r) => html`
          <div class="entity-tooltip-row">
            <span class="entity-tooltip-label">${r.label}</span>
            <span class="entity-tooltip-value">${r.value}</span>
          </div>
        `)}
      </div>
    `;
  }

  _getOfflineEntityIds() {
    const isCombined = this._isCombinedGroup();
    const prefix = isCombined
      ? `entity_availability_combined_${this._config.group}`
      : `entity_availability_${this._config.group}`;
    const entity = this._getEntity(`sensor.${prefix}_offline_entities`);
    if (!entity || !entity.attributes) return [];
    return entity.attributes.entities || [];
  }

  _getEntity(entityId) {
    return this.hass?.states?.[entityId];
  }

  _isCombinedGroup() {
    if (!this.hass || !this._config.group) return false;
    const slug = this._config.group;
    return !!this.hass.states[`sensor.entity_availability_combined_${slug}_combined_summary`];
  }

  _getAllEntityIds() {
    const group = this._config.group;
    if (this._isCombinedGroup()) {
      const prefix = `entity_availability_combined_${group}`;
      return [
        `sensor.${prefix}_combined_summary`,
        `sensor.${prefix}_offline_entities`,
        `sensor.${prefix}_low_battery`,
        `sensor.${prefix}_low_battery_count`,
        `binary_sensor.${prefix}_any_offline`,
      ];
    }
    const prefix = `entity_availability_${group}`;
    return [
      `sensor.${prefix}_group_summary`,
      `sensor.${prefix}_offline_count`,
      `sensor.${prefix}_offline_entities`,
      `sensor.${prefix}_low_battery`,
      `sensor.${prefix}_availability_today`,
      `sensor.${prefix}_availability_3d`,
      `sensor.${prefix}_availability_5d`,
      `sensor.${prefix}_availability_7d`,
      `binary_sensor.${prefix}_any_offline`,
    ];
  }

  _computeDuration(entityId) {
    const state = this.hass?.states?.[entityId];
    if (!state?.last_changed) return null;

    const diff = Date.now() - new Date(state.last_changed).getTime();
    const minutes = Math.floor(diff / 60000);

    if (minutes < 1) return "just now";
    if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
    const days = Math.floor(hours / 24);
    if (days < 7) return `${days} day${days === 1 ? "" : "s"} ago`;
    const weeks = Math.floor(days / 7);
    return `${weeks} week${weeks === 1 ? "" : "s"} ago`;
  }

  _formatFutureDate(isoString) {
    const date = new Date(isoString);
    if (isNaN(date.getTime())) return isoString;
    const now = new Date();
    const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const tomorrowStart = new Date(todayStart.getTime() + 86400000);

    if (date < tomorrowStart && date >= todayStart) {
      return `today at ${date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`;
    }
    const sameYear = date.getFullYear() === now.getFullYear();
    return date.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      ...(sameYear ? {} : { year: "numeric" }),
    });
  }

  _formatStateWithUnit(entityState) {
    if (!entityState) return "unknown";
    const formatted = this._formatIsoState(entityState.state);
    const unit = entityState.attributes?.unit_of_measurement;
    return unit ? `${formatted} ${unit}` : formatted;
  }

  _formatIsoState(stateValue) {
    if (!/^\d{4}-\d{2}-\d{2}T/.test(stateValue)) return stateValue;
    const date = new Date(stateValue);
    if (isNaN(date.getTime())) return stateValue;
    const sameYear = date.getFullYear() === new Date().getFullYear();
    return date.toLocaleString(undefined, {
      month: "short", day: "numeric",
      ...(sameYear ? {} : { year: "numeric" }),
      hour: "2-digit", minute: "2-digit",
    });
  }

  _getAvailabilityColor(pct) {
    const t = this._config.availability_thresholds || { high: 99, mid: 95 };
    if (pct >= t.high) return "green";
    if (pct >= t.mid) return "yellow";
    return "red";
  }

  _getAvailabilityBarColor(pct) {
    const t = this._config.availability_thresholds || { high: 99, mid: 95 };
    const c = this._config.availability_colors || { high: "#4caf50", mid: "#ff9800", low: "#f44336" };
    if (pct >= t.high) return c.high;
    if (pct >= t.mid) return c.mid;
    return c.low;
  }

  _formatGroupName(group) {
    return group.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  _toggleEntities() {
    this._entitiesExpanded = !this._entitiesExpanded;
  }

  async _handleSuppressAll(e) {
    e.stopPropagation();
    const offlineIds = this._getOfflineEntityIds();
    for (const entityId of offlineIds) {
      await this.hass.callService("entity_availability", "suppress", {
        entity_id: entityId,
        duration: 60,
      });
    }
  }

  async _handleUnsuppressAll(e) {
    e.stopPropagation();
    const isCombined = this._isCombinedGroup();
    const prefix = isCombined
      ? `entity_availability_combined_${this._config.group}`
      : `entity_availability_${this._config.group}`;
    const summaryId = isCombined
      ? `sensor.${prefix}_combined_summary`
      : `sensor.${prefix}_group_summary`;
    const summary = this._getEntity(summaryId);
    const entities = summary?.attributes?.entities || [];
    for (const entityId of entities) {
      await this.hass.callService("entity_availability", "unsuppress", {
        entity_id: entityId,
      });
    }
  }
}

customElements.define("entity-availability-card", EntityAvailabilityCard);

// --- Card Editor ---

class EntityAvailabilityCardEditor extends LitElement {
  static get properties() {
    return {
      hass: { attribute: false },
      _config: { state: true },
    };
  }

  static get styles() {
    return css`
      .editor-row {
        margin-bottom: 12px;
      }
      .editor-row label {
        display: block;
        font-weight: 500;
        margin-bottom: 4px;
      }
      .editor-row input[type="text"],
      .editor-row select {
        width: 100%;
        padding: 8px;
        border: 1px solid var(--divider-color, #ccc);
        border-radius: 4px;
        box-sizing: border-box;
        background: var(--card-background-color, #fff);
        color: var(--primary-text-color, #212121);
      }
      .editor-row.checkbox label {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        font-weight: normal;
      }
      .color-row {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 8px;
      }
      .color-row label {
        font-size: 13px;
        min-width: 80px;
      }
      .color-row input[type="color"] {
        width: 36px;
        height: 28px;
        border: 1px solid var(--divider-color, #ccc);
        border-radius: 4px;
        cursor: pointer;
        padding: 2px;
      }
      .color-row input[type="number"] {
        width: 60px;
        padding: 4px 6px;
        border: 1px solid var(--divider-color, #ccc);
        border-radius: 4px;
        background: var(--card-background-color, #fff);
        color: var(--primary-text-color, #212121);
      }
      .threshold-section {
        margin-top: 12px;
        padding-top: 12px;
        border-top: 1px solid var(--divider-color, #e0e0e0);
      }
      .threshold-section > label {
        display: block;
        font-weight: 500;
        margin-bottom: 8px;
      }
    `;
  }

  setConfig(config) {
    this._config = config;
  }

  _getGroupOptions() {
    if (!this.hass) return { regular: [], combined: [] };
    const regular = Object.keys(this.hass.states)
      .filter(
        (id) =>
          id.startsWith("sensor.entity_availability_") &&
          !id.startsWith("sensor.entity_availability_combined_") &&
          id.endsWith("_offline_count")
      )
      .map((id) =>
        id
          .replace("sensor.entity_availability_", "")
          .replace("_offline_count", "")
      )
      .sort();
    const combined = Object.keys(this.hass.states)
      .filter(
        (id) =>
          id.startsWith("sensor.entity_availability_combined_") &&
          id.endsWith("_combined_summary")
      )
      .map((id) =>
        id
          .replace("sensor.entity_availability_combined_", "")
          .replace("_combined_summary", "")
      )
      .sort();
    return { regular, combined };
  }

  _isSelectedGroupCombined() {
    if (!this.hass || !this._config?.group) return false;
    const slug = this._config.group;
    return !!this.hass.states[`sensor.entity_availability_combined_${slug}_combined_summary`];
  }

  render() {
    if (!this._config) return html``;

    return html`
      <div style="padding: 16px;">
        <div class="editor-row">
          <label>Group Slug</label>
          ${(() => {
            const { regular, combined } = this._getGroupOptions();
            const hasOptions = regular.length > 0 || combined.length > 0;
            if (!hasOptions) {
              return html`<input
                type="text"
                .value=${this._config.group || ""}
                @input=${(e) => this._updateConfig("group", e.target.value)}
                placeholder="e.g. security_devices"
              />`;
            }
            return html`<select
              .value=${this._config.group || ""}
              @change=${(e) => this._updateConfig("group", e.target.value)}
            >
              ${regular.length > 0 ? html`
                <optgroup label="Groups">
                  ${regular.map(
                    (slug) => html`<option value=${slug} ?selected=${this._config.group === slug}>${slug}</option>`
                  )}
                </optgroup>` : nothing}
              ${combined.length > 0 ? html`
                <optgroup label="Combined Groups">
                  ${combined.map(
                    (slug) => html`<option value=${slug} ?selected=${this._config.group === slug}>${slug}</option>`
                  )}
                </optgroup>` : nothing}
            </select>`;
          })()}
        </div>
        <div class="editor-row">
          <label>Title (optional)</label>
          <input
            type="text"
            .value=${this._config.title || ""}
            @input=${(e) => this._updateConfig("title", e.target.value || undefined)}
            placeholder="Custom card title"
          />
        </div>
        ${!this._isSelectedGroupCombined() ? html`
        <div class="editor-row checkbox">
          <label>
            <input
              type="checkbox"
              .checked=${this._config.show_availability !== false}
              @change=${(e) => this._updateConfig("show_availability", e.target.checked)}
            />
            Show Availability Bars
          </label>
        </div>
        ` : nothing}
        <div class="editor-row checkbox">
          <label>
            <input
              type="checkbox"
              .checked=${this._config.show_entities !== false}
              @change=${(e) => this._updateConfig("show_entities", e.target.checked)}
            />
            Show Entity List
          </label>
        </div>
        ${!this._isSelectedGroupCombined() ? html`
        <div class="editor-row">
          <label>Filter Entities (requires Show Entity List)</label>
          <select
            .value=${this._config.entity_filter || "all"}
            @change=${(e) => this._updateConfig("entity_filter", e.target.value)}
            ?disabled=${this._config.show_entities === false}
          >
            <option value="all">All entities</option>
            <option value="offline">Problems only (offline, stale, low battery)</option>
            <option value="online">Healthy only (online)</option>
          </select>
        </div>
        ` : nothing}
        <div class="editor-row checkbox">
          <label>
            <input
              type="checkbox"
              .checked=${this._config.entities_expanded === true}
              @change=${(e) => this._updateConfig("entities_expanded", e.target.checked)}
            />
            Entity List Expanded by Default
          </label>
        </div>
        ${!this._isSelectedGroupCombined() ? html`
        <div class="editor-row checkbox">
          <label>
            <input
              type="checkbox"
              .checked=${this._config.show_actions === true}
              @change=${(e) => this._updateConfig("show_actions", e.target.checked)}
            />
            Show Suppress/Unsuppress Buttons
          </label>
        </div>
        ` : nothing}
        <div class="editor-row checkbox">
          <label>
            <input
              type="checkbox"
              .checked=${this._config.compact === true}
              @change=${(e) => this._updateConfig("compact", e.target.checked)}
            />
            Compact Mode
          </label>
        </div>
        ${this._isSelectedGroupCombined() ? html`
        <div class="editor-row">
          <label>Sort Groups By</label>
          <select
            .value=${this._config.group_sort_by || "name_asc"}
            @change=${(e) => this._updateConfig("group_sort_by", e.target.value)}
          >
            <option value="name_asc">Name A → Z (default)</option>
            <option value="name_desc">Name Z → A</option>
            <option value="offline_desc">Offline ↓ (most issues first)</option>
          </select>
        </div>
        ` : nothing}
        ${!this._isSelectedGroupCombined() ? html`
        <div class="editor-row">
          <label>Entity Detail</label>
          <select
            .value=${this._config.entity_detail || "off"}
            @change=${(e) => this._updateConfig("entity_detail", e.target.value)}
          >
            <option value="off">Off</option>
            <option value="tooltip">Tooltip on hover</option>
            <option value="inline">Always visible (inline)</option>
          </select>
        </div>
        <div class="editor-row">
          <label>Sort Entities By</label>
          <select
            .value=${this._config.sort_by || "status"}
            @change=${(e) => this._updateConfig("sort_by", e.target.value)}
          >
            <option value="status">Status (default)</option>
            <option value="name_asc">Name A → Z</option>
            <option value="name_desc">Name Z → A</option>
            <option value="battery_asc">Battery ↑ (weakest first)</option>
            <option value="battery_desc">Battery ↓ (strongest first)</option>
          </select>
        </div>
        ` : nothing}
        ${!this._isSelectedGroupCombined() ? html`
        <div class="threshold-section">
          <label>Availability Bar Colors & Thresholds</label>
          <div class="color-row">
            <label>High ≥</label>
            <input
              type="number"
              min="0" max="100"
              .value=${(this._config.availability_thresholds?.high ?? 99).toString()}
              @input=${(e) => this._updateThreshold("high", e.target.value)}
            />
            <span>%</span>
            <input
              type="color"
              .value=${this._config.availability_colors?.high || "#4caf50"}
              @input=${(e) => this._updateColor("high", e.target.value)}
            />
          </div>
          <div class="color-row">
            <label>Mid ≥</label>
            <input
              type="number"
              min="0" max="100"
              .value=${(this._config.availability_thresholds?.mid ?? 95).toString()}
              @input=${(e) => this._updateThreshold("mid", e.target.value)}
            />
            <span>%</span>
            <input
              type="color"
              .value=${this._config.availability_colors?.mid || "#ff9800"}
              @input=${(e) => this._updateColor("mid", e.target.value)}
            />
          </div>
          <div class="color-row">
            <label>Low below</label>
            <input
              type="color"
              .value=${this._config.availability_colors?.low || "#f44336"}
              @input=${(e) => this._updateColor("low", e.target.value)}
            />
          </div>
        </div>
        ` : nothing}
      </div>
    `;
  }

  _updateThreshold(level, value) {
    const thresholds = { ...(this._config.availability_thresholds || { high: 99, mid: 95 }) };
    thresholds[level] = parseInt(value, 10) || 0;
    this._updateConfig("availability_thresholds", thresholds);
  }

  _updateColor(level, value) {
    const colors = { ...(this._config.availability_colors || { high: "#4caf50", mid: "#ff9800", low: "#f44336" }) };
    colors[level] = value;
    this._updateConfig("availability_colors", colors);
  }

  _updateConfig(key, value) {
    if (!this._config) return;
    const newConfig = { ...this._config, [key]: value };
    Object.keys(newConfig).forEach((k) => {
      if (newConfig[k] === undefined) delete newConfig[k];
    });
    this._config = newConfig;
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: this._config },
        bubbles: true,
        composed: true,
      })
    );
  }
}

customElements.define("entity-availability-card-editor", EntityAvailabilityCardEditor);

