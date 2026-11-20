/* @module static/js/settings_accounts.js
 * @purpose Accounts, API Keys & Library Paths configuration card in Settings with ?reveal=1 unmasking and service tests.
 * @inputs GET/PUT /api/config/accounts (?reveal=1) and POST /api/config/accounts/test endpoints.
 * @outputs Rendered Accounts & API Keys form HTML and interactive reveal/test/save event bindings. */

import { state, esc, showToast, refreshCurrentView } from "./state.js";
import { apiFetch } from "./api.js";
import { refreshTooltips } from "./tooltips.js";

function readAccountField(data, section, key, fallback = "") {
  if (!data || typeof data !== "object") return fallback;
  const src = data.accounts && typeof data.accounts === "object" ? data.accounts : data;
  const dotted = `${section}.${key}`;
  if (src[dotted] !== undefined && src[dotted] !== null) {
    return Array.isArray(src[dotted]) ? src[dotted].join(", ") : String(src[dotted]);
  }
  const secObj = src[section] || data[section];
  if (secObj && typeof secObj === "object" && secObj[key] !== undefined && secObj[key] !== null) {
    return Array.isArray(secObj[key]) ? secObj[key].join(", ") : String(secObj[key]);
  }
  if (src[key] !== undefined && src[key] !== null) {
    return Array.isArray(src[key]) ? src[key].join(", ") : String(src[key]);
  }
  return fallback;
}

function splitCsvList(raw) {
  if (!raw) return [];
  return String(raw).split(/[\n,]+/).map((s) => s.trim()).filter(Boolean);
}

export function renderAccountsSettingsCardHtml() {
  const acc = state.settings?.accounts || {};
  const takeoutDir = readAccountField(acc, "paths", "takeout_dir", "drops/takeout");
  const romSets = readAccountField(acc, "paths", "rom_sets", "");
  const emudrive = readAccountField(acc, "paths", "emudrive", "");
  const plexBaseUrl = readAccountField(acc, "paths", "plex_base_url", "http://127.0.0.1:32400");
  const plexToken = readAccountField(acc, "paths", "plex_token", "");
  const calibreDb = readAccountField(acc, "paths", "calibre_db", "");

  const pcApiKey = readAccountField(acc, "pricecharting", "api_key", "");
  const steamApiKey = readAccountField(acc, "steam", "api_key", "");
  const steamId = readAccountField(acc, "steam", "steam_id", "");
  const raUser = readAccountField(acc, "retroachievements", "username", "");
  const raKey = readAccountField(acc, "retroachievements", "api_key", "");
  const hardcoverToken = readAccountField(acc, "hardcover", "api_token", "");

  const ytKey = readAccountField(acc, "youtube", "api_key", "");
  const ytBackupKey = readAccountField(acc, "youtube", "backup_api_key", "");
  const ytChannels = readAccountField(acc, "youtube", "channels", "");
  const twitchChannel = readAccountField(acc, "twitch", "channel", "");
  const twitchClientId = readAccountField(acc, "twitch", "client_id", "");
  const twitchSecret = readAccountField(acc, "twitch", "client_secret", "");
  const bskyHandle = readAccountField(acc, "bluesky", "handle", "");
  const bskyPass = readAccountField(acc, "bluesky", "app_password", "");
  const bufferToken = readAccountField(acc, "buffer", "access_token", "");
  const discordWebhook = readAccountField(acc, "discord", "webhook_url", "");

  return `
    <section class="card contain-panel" id="settingsAccountsCard">
      <div class="card-head">
        <div>
          <h2 class="card-title">🔑 Accounts, API Keys &amp; Library Paths</h2>
          <p class="card-subtitle">All integrations are optional. Secrets remain masked until you click <strong>Reveal / Hide Keys</strong>.</p>
        </div>
        <div class="toolbar">
          <button type="button" class="ghost-btn" id="toggleRevealSecretsBtn" title="Reveal Or Hide API Keys|Fetch and toggle secret masking on all API keys and tokens">👁 Reveal / Hide Keys</button>
          <button type="button" class="chip-btn" data-test-account="plex" id="testPlexAccountBtn" title="Test Plex Server|Verify connectivity to your local Plex Media Server">🔌 Plex</button>
          <button type="button" class="chip-btn" data-test-account="takeout" id="testTakeoutPathBtn" title="Test Takeout Path|Verify Takeout directory exists on disk">🔌 Takeout</button>
          <button type="button" class="chip-btn" data-test-account="rom_sets" title="Test ROM Sets|Verify personal ROM and ISO backup directories exist">🔌 ROMs</button>
          <button type="button" class="chip-btn" data-test-account="pricecharting" id="testPricechartingBtn" title="Test PriceCharting|Verify PriceCharting API key or local guide cache">🔌 PriceCharting</button>
          <button type="button" class="chip-btn" data-test-account="steam" title="Test Steam|Verify Steam Web API key and 64-bit Steam ID">🔌 Steam</button>
          <button type="button" class="chip-btn" data-test-account="retroachievements" title="Test RetroAchievements|Verify RetroAchievements username and Web API key">🔌 RA</button>
          <button type="button" class="chip-btn" data-test-account="hardcover" title="Test Hardcover|Verify Hardcover API bearer token">🔌 Hardcover</button>
          <button type="button" class="chip-btn" data-test-account="youtube" title="Test YouTube|Verify YouTube Data API v3 key and channel IDs">🔌 YouTube</button>
          <button type="button" class="chip-btn" data-test-account="twitch" title="Test Twitch|Verify Twitch Client ID and Client Secret">🔌 Twitch</button>
          <button type="button" class="chip-btn" data-test-account="bluesky" title="Test Bluesky|Verify Bluesky handle and app password">🔌 Bluesky</button>
          <button type="button" class="chip-btn" data-test-account="buffer" title="Test Buffer|Verify Buffer access token">🔌 Buffer</button>
          <button type="button" class="chip-btn" data-test-account="discord" title="Test Discord|Verify Discord webhook URL">🔌 Discord</button>
        </div>
      </div>

      <form id="accountsConfigForm" class="stack-list">
        <div class="card" style="background:var(--bg-card-alt);">
          <div class="kpi-label">📂 1. Library Paths &amp; Local Plex Server</div>
          <div class="form-grid">
            <div class="form-field"><label>Takeout Root Directory</label><input name="takeout_dir" class="field-input mono" placeholder="drops/takeout" value="${esc(takeoutDir)}"></div>
            <div class="form-field"><label>ROM Sets Directories (comma-separated)</label><input name="rom_sets" class="field-input mono" placeholder="D:\\ROMs\\Verified" value="${esc(romSets)}"></div>
            <div class="form-field"><label>Emulation Drive Root</label><input name="emudrive" class="field-input mono" placeholder="D:\\Emulation" value="${esc(emudrive)}"></div>
            <div class="form-field"><label>Calibre Library Database</label><input name="calibre_db" class="field-input mono" placeholder="D:\\Books\\Calibre\\metadata.db" value="${esc(calibreDb)}"></div>
            <div class="form-field"><label>Plex Server Base URL</label><input name="plex_base_url" class="field-input mono" placeholder="http://127.0.0.1:32400" value="${esc(plexBaseUrl)}"></div>
            <div class="form-field"><label>Plex Access Token (blank uses local registry)</label><input type="password" data-secret-input="1" name="plex_token" class="field-input mono" placeholder="Auto-discovered if blank" value="${esc(plexToken)}"></div>
          </div>
        </div>

        <div class="card" style="background:var(--bg-card-alt);">
          <div class="kpi-label">🎮 2. Gaming &amp; Catalog APIs</div>
          <div class="form-grid">
            <div class="form-field"><label>PriceCharting API Key</label><input type="password" data-secret-input="1" name="pricecharting.api_key" class="field-input mono" placeholder="Optional API token" value="${esc(pcApiKey)}"></div>
            <div class="form-field"><label>Steam Web API Key</label><input type="password" data-secret-input="1" name="steam.api_key" class="field-input mono" placeholder="Steam Web API Key" value="${esc(steamApiKey)}"></div>
            <div class="form-field"><label>Steam 64-Bit ID</label><input name="steam.steam_id" class="field-input mono" placeholder="7656119..." value="${esc(steamId)}"></div>
            <div class="form-field"><label>RetroAchievements Username</label><input name="retroachievements.username" class="field-input mono" placeholder="your_ra_username" value="${esc(raUser)}"></div>
            <div class="form-field"><label>RetroAchievements Web API Key</label><input type="password" data-secret-input="1" name="retroachievements.api_key" class="field-input mono" placeholder="RA Web API Key" value="${esc(raKey)}"></div>
            <div class="form-field"><label>Hardcover API Token</label><input type="password" data-secret-input="1" name="hardcover.api_token" class="field-input mono" placeholder="Bearer token" value="${esc(hardcoverToken)}"></div>
          </div>
        </div>

        <div class="card" style="background:var(--bg-card-alt);">
          <div class="kpi-label">📺 3. Creator Studio &amp; Social Accounts</div>
          <div class="form-grid">
            <div class="form-field"><label>YouTube Data API v3 Key</label><input type="password" data-secret-input="1" name="youtube.api_key" class="field-input mono" placeholder="AIza..." value="${esc(ytKey)}"></div>
            <div class="form-field"><label>YouTube Backup API Key</label><input type="password" data-secret-input="1" name="youtube.backup_api_key" class="field-input mono" placeholder="Optional fallback key" value="${esc(ytBackupKey)}"></div>
            <div class="form-field span-full"><label>YouTube Channel IDs (comma-separated)</label><input name="youtube.channels" class="field-input mono" placeholder="UCxxxxxxxxxxxxxxxxxxxxxx, UCxxxxxxxxxxxxxxxxxxxx02" value="${esc(ytChannels)}"></div>
            <div class="form-field"><label>Twitch Channel</label><input name="twitch.channel" class="field-input mono" placeholder="your_twitch_channel" value="${esc(twitchChannel)}"></div>
            <div class="form-field"><label>Twitch Client ID</label><input name="twitch.client_id" class="field-input mono" placeholder="Twitch App Client ID" value="${esc(twitchClientId)}"></div>
            <div class="form-field"><label>Twitch Client Secret</label><input type="password" data-secret-input="1" name="twitch.client_secret" class="field-input mono" placeholder="Twitch App Secret" value="${esc(twitchSecret)}"></div>
            <div class="form-field"><label>Bluesky Handle</label><input name="bluesky.handle" class="field-input mono" placeholder="yourhandle.bsky.social" value="${esc(bskyHandle)}"></div>
            <div class="form-field"><label>Bluesky App Password</label><input type="password" data-secret-input="1" name="bluesky.app_password" class="field-input mono" placeholder="xxxx-xxxx-xxxx-xxxx" value="${esc(bskyPass)}"></div>
            <div class="form-field"><label>Buffer Access Token</label><input type="password" data-secret-input="1" name="buffer.access_token" class="field-input mono" placeholder="Buffer API token" value="${esc(bufferToken)}"></div>
            <div class="form-field"><label>Discord Webhook URL</label><input type="password" data-secret-input="1" name="discord.webhook_url" class="field-input mono" placeholder="https://discord.com/api/webhooks/..." value="${esc(discordWebhook)}"></div>
          </div>
        </div>

        <div class="toolbar" style="justify-content:flex-end;">
          <button type="submit" class="primary-btn" id="saveAccountsBtn">💾 Save Accounts, API Keys &amp; Paths</button>
        </div>
      </form>
    </section>`;
}

function collectAccountsFormPayload(form) {
  const fd = new FormData(form);
  const g = (k) => String(fd.get(k) ?? "").trim();
  const romSetsList = splitCsvList(g("rom_sets"));
  const ytChannelsList = splitCsvList(g("youtube.channels"));
  return {
    takeout_dir: g("takeout_dir"), rom_sets: romSetsList, emudrive: g("emudrive"), calibre_db: g("calibre_db"),
    plex_base_url: g("plex_base_url"), plex_url: g("plex_base_url"), plex_token: g("plex_token"), bluesky_handle: g("bluesky.handle"),
    paths: { takeout_dir: g("takeout_dir"), rom_sets: romSetsList, emudrive: g("emudrive"), calibre_db: g("calibre_db"), plex_base_url: g("plex_base_url"), plex_token: g("plex_token") },
    pricecharting: { api_key: g("pricecharting.api_key") },
    steam: { api_key: g("steam.api_key"), steam_id: g("steam.steam_id") },
    retroachievements: { username: g("retroachievements.username"), api_key: g("retroachievements.api_key") },
    hardcover: { api_token: g("hardcover.api_token") },
    youtube: { api_key: g("youtube.api_key"), backup_api_key: g("youtube.backup_api_key"), channels: ytChannelsList },
    twitch: { channel: g("twitch.channel"), client_id: g("twitch.client_id"), client_secret: g("twitch.client_secret") },
    bluesky: { handle: g("bluesky.handle"), app_password: g("bluesky.app_password") },
    buffer: { access_token: g("buffer.access_token") },
    discord: { webhook_url: g("discord.webhook_url") },
  };
}

function populateAccountsFormInputs(card, data, forceOverwrite = false) {
  if (!card || !data) return;
  const map = {
    takeout_dir: readAccountField(data, "paths", "takeout_dir", ""), rom_sets: readAccountField(data, "paths", "rom_sets", ""),
    emudrive: readAccountField(data, "paths", "emudrive", ""), calibre_db: readAccountField(data, "paths", "calibre_db", ""),
    plex_base_url: readAccountField(data, "paths", "plex_base_url", "http://127.0.0.1:32400"), plex_token: readAccountField(data, "paths", "plex_token", data.plex_token_masked || ""),
    "pricecharting.api_key": readAccountField(data, "pricecharting", "api_key", ""), "steam.api_key": readAccountField(data, "steam", "api_key", ""),
    "steam.steam_id": readAccountField(data, "steam", "steam_id", ""), "retroachievements.username": readAccountField(data, "retroachievements", "username", ""),
    "retroachievements.api_key": readAccountField(data, "retroachievements", "api_key", ""), "hardcover.api_token": readAccountField(data, "hardcover", "api_token", ""),
    "youtube.api_key": readAccountField(data, "youtube", "api_key", ""), "youtube.backup_api_key": readAccountField(data, "youtube", "backup_api_key", ""),
    "youtube.channels": readAccountField(data, "youtube", "channels", ""), "twitch.channel": readAccountField(data, "twitch", "channel", ""),
    "twitch.client_id": readAccountField(data, "twitch", "client_id", ""), "twitch.client_secret": readAccountField(data, "twitch", "client_secret", ""),
    "bluesky.handle": readAccountField(data, "bluesky", "handle", ""), "bluesky.app_password": readAccountField(data, "bluesky", "app_password", ""),
    "buffer.access_token": readAccountField(data, "buffer", "access_token", ""), "discord.webhook_url": readAccountField(data, "discord", "webhook_url", ""),
  };
  Object.entries(map).forEach(([name, val]) => {
    const inp = card.querySelector(`[name="${name}"]`);
    if (inp && val !== undefined && val !== null && (forceOverwrite || document.activeElement !== inp)) inp.value = val;
  });
}

export function bindAccountsSettingsCard(root) {
  const card = root?.querySelector("#settingsAccountsCard");
  if (!card) return;
  apiFetch("/api/config/accounts").then((res) => { state.settings.accounts = res; populateAccountsFormInputs(card, res); }).catch((err) => console.debug("Accounts config fetch skipped:", err));
  card.querySelector("#toggleRevealSecretsBtn")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const inputs = card.querySelectorAll("[data-secret-input]");
    const revealing = inputs[0]?.type === "password";
    if (revealing) {
      try { populateAccountsFormInputs(card, await apiFetch("/api/config/accounts?reveal=1"), true); } catch (err) { console.debug("Reveal secrets skipped:", err); }
    } else if (state.settings.accounts) {
      populateAccountsFormInputs(card, state.settings.accounts, true);
    }
    inputs.forEach((inp) => { inp.type = revealing ? "text" : "password"; });
    btn.textContent = revealing ? "🙈 Hide Keys" : "👁 Reveal / Hide Keys";
  });
  card.querySelectorAll("[data-test-account]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const service = btn.dataset.testAccount;
      const form = card.querySelector("#accountsConfigForm");
      try {
        const res = await apiFetch("/api/config/accounts/test", { method: "POST", body: { service, ...(form ? collectAccountsFormPayload(form) : {}) } });
        showToast(res.message || `${service}: ${res.ok ? "OK" : "Check failed"}`);
      } catch (err) { showToast(err.message || `Failed to test ${service}`); }
    });
  });
  card.querySelector("#accountsConfigForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    state.settings.accounts = await apiFetch("/api/config/accounts", { method: "PUT", body: collectAccountsFormPayload(e.currentTarget) });
    showToast("Saved Accounts, API Keys & Library Paths");
    refreshCurrentView({ background: true });
  });
  refreshTooltips(card);
}
