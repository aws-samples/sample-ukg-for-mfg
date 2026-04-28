/**
 * Home page — Universal Knowledge Graph Control Panel
 * Loads systems, graph, vocabulary, and provides inline chat.
 *
 * LocalStorage keys (shared across pages that use the chat API):
 *   - agentcore-selected-model  (shared with discover.js)
 *   - agentcore-session-id
 */

// Model list is hydrated from /api/models into SHARED_MODELS/SHARED_DEFAULT_MODEL_ID
// by utils.js at page load. Keep this alias for the small number of call
// sites below that read the live array.
const HOME_MODELS = SHARED_MODELS;
// LocalStorage keys (stable across deploys; shared with discover.js)
const HOME_MODEL_KEY = 'agentcore-selected-model';
const HOME_AGENT_KEY = undefined;
const HOME_SESSION_KEY = 'agentcore-session-id';

function getHomeModel() {
    var stored = localStorage.getItem(HOME_MODEL_KEY);
    if (stored) {
        var m = SHARED_MODELS.find(function(x) { return x.id === stored; });
        if (m) return m;
    }
    var defaultModel = SHARED_MODELS.find(function(x) { return x.id === SHARED_DEFAULT_MODEL_ID; });
    if (defaultModel) return defaultModel;
    // Hydration hasn't completed yet — return a stub so callers can still
    // read .id. The server will substitute its own default if we send this.
    return SHARED_MODELS[0] || { id: SHARED_DEFAULT_MODEL_ID, name: '' };
}

function setHomeModel(modelId) {
    localStorage.setItem(HOME_MODEL_KEY, modelId);
}

function getHomeAgent() {
    var agents = window.homeAvailableAgents || [];
    var stored = localStorage.getItem(HOME_AGENT_KEY);
    if (stored) {
        var a = agents.find(function(x) { return x.id === stored; });
        if (a) return a;
    }
    return agents[0] || { id: 'explorer', name: 'Data Explorer' };
}

function setHomeAgent(agentId) {
    localStorage.setItem(HOME_AGENT_KEY, agentId);
}

async function initHomeSelectors() {
    // Model list is hydrated asynchronously from /api/models — wait for
    // the hydrator before building the dropdown so the options are real.
    if (window.modelsReady) { try { await window.modelsReady; } catch (_) {} }
    // Populate model selector
    var modelSel = document.getElementById('home-model-select');
    if (modelSel) {
        var currentModel = getHomeModel();
        modelSel.innerHTML = HOME_MODELS.map(function(m) { // nosemgrep: insecure-innerhtml, insecure-document-method
            return '<option value="' + m.id + '"' + (m.id === currentModel.id ? ' selected' : '') + '>' + escHtml(m.name) + '</option>';
        }).join('');
        modelSel.addEventListener('change', function() { setHomeModel(this.value); });
    }
}

// ============================================================================
// Systems Panel (shared SystemExplorer component)
// ============================================================================

var homeExplorer = new SystemExplorer({
    listEl: document.getElementById('home-systems-list'),
    detailEl: document.getElementById('home-sys-detail'),
    detailHeaderEl: document.getElementById('home-sys-detail-header'),
    detailBodyEl: document.getElementById('home-sys-detail-body'),
    hideOnDrill: [
        document.getElementById('home-systems-list-wrap'),
    ],
});

// Legacy alias
async function loadHomeSystems() { return homeExplorer.load(); }
function navigateSystem(systemId) { homeExplorer.openSystem(systemId); }

/** Force-refresh systems from network, bypassing cache */
async function refreshHomeSystems() {
    var btn = document.getElementById('refresh-systems-btn');
    if (btn) { btn.disabled = true; btn.style.opacity = '0.5'; }
    await homeExplorer.load(true);
    if (btn) { btn.disabled = false; btn.style.opacity = '1'; }
}

/** Force-refresh concepts/vocabulary from network, bypassing cache */
async function refreshHomeVocab() {
    var btn = document.getElementById('refresh-vocab-btn');
    if (btn) { btn.disabled = true; btn.style.opacity = '0.5'; }
    await loadHomeVocab(true);
    if (btn) { btn.disabled = false; btn.style.opacity = '1'; }
}

// ============================================================================
// Graph Panel (mini Cytoscape)
// ============================================================================

let homeGraph = null;

async function loadHomeGraph() {
    const container = document.getElementById('home-graph');
    if (!container) return;
    try {
        var data;
        if (typeof DataCache !== 'undefined') {
            data = await DataCache.getOrFetch('graph');
        }
        if (!data) {
            const resp = await fetch('/api/registry/graph?edge_type=concepts');
            data = await resp.json();
        }
        if (!data.configured || !data.elements) {
            container.innerHTML = '<div class="home-loading">No graph data</div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
            return;
        }

        // Collect system metadata for legend
        const systemMeta = {};
        const nodeMap = new Map();
        for (const n of data.elements.nodes) {
            const d = n.data;
            nodeMap.set(d.id, d);
            if (d.type === 'system') {
                systemMeta[d.id] = { label: d.label || d.id, color: d.color || '#484f58' };
            }
        }

        // Build nodes — only table nodes (skip system parent nodes)
        const nodes = [];
        for (const n of data.elements.nodes) {
            const d = n.data;
            if (d.type === 'table') {
                nodes.push({
                    id: d.id,
                    label: d.label || d.id,
                    type: 'table',
                    color: d.color || '#484f58',
                    system_id: d.system_id || d.parent || null,
                    val: 4,
                });
            }
        }

        // Build links — only between table nodes
        const tableIds = new Set(nodes.map(n => n.id));
        const links = [];
        for (const e of data.elements.edges) {
            // Create placeholder table nodes for missing endpoints
            for (const ep of [e.data.source, e.data.target]) {
                if (!tableIds.has(ep)) {
                    const [sysId, tbl] = ep.includes('.') ? ep.split('.', 2) : [ep, ''];
                    const color = systemMeta[sysId] ? systemMeta[sysId].color : '#484f58';
                    nodes.push({ id: ep, label: tbl || ep, type: 'table', color: color, system_id: sysId, val: 4 });
                    tableIds.add(ep);
                }
            }
            if (tableIds.has(e.data.source) && tableIds.has(e.data.target)) {
                links.push({ source: e.data.source, target: e.data.target });
            }
        }

        // Theme colors
        // Theme colors — read live in render callback for theme switching
        function getTC() {
            const s = getComputedStyle(document.documentElement);
            return {
                text: s.getPropertyValue('--text').trim() || '#f0f6fc',
                bg: s.getPropertyValue('--surface').trim() || '#161b22',
                border: s.getPropertyValue('--border').trim() || '#30363d',
                accent: s.getPropertyValue('--accent').trim() || '#00d8ff',
            };
        }

        // Hover highlight state
        let homeHighlightNode = null;
        let homeHighlightLink = null;

        // Clean up previous instance
        if (homeGraph) {
            homeGraph._destructor && homeGraph._destructor();
        }
        container.innerHTML = ''; // nosemgrep: insecure-innerhtml, insecure-document-method

        const rect = container.getBoundingClientRect();

        homeGraph = ForceGraph()(container)
            .width(rect.width)
            .height(rect.height)
            .backgroundColor('rgba(0,0,0,0)')
            .graphData({ nodes, links })
            .nodeId('id')
            .nodeVal('val')
            .nodeLabel(n => {
                const sys = systemMeta[n.system_id];
                return n.label + (sys ? ' (' + sys.label + ')' : '');
            })
            .nodeCanvasObject((node, ctx, globalScale) => {
                const tc = getTC();
                const isLinkEnd = homeHighlightLink && (
                    (typeof homeHighlightLink.source === 'object' ? homeHighlightLink.source : null) === node ||
                    (typeof homeHighlightLink.target === 'object' ? homeHighlightLink.target : null) === node
                );
                const isHighlight = homeHighlightNode === node || isLinkEnd;
                const size = isHighlight ? 9 : 6;
                ctx.beginPath();
                ctx.arc(node.x, node.y, size, 0, 2 * Math.PI);
                ctx.fillStyle = node.color;
                ctx.fill();
                if (isHighlight) {
                    ctx.strokeStyle = tc.accent;
                    ctx.lineWidth = 2.5 / globalScale;
                } else {
                    ctx.strokeStyle = tc.border;
                    ctx.lineWidth = 1 / globalScale;
                }
                ctx.stroke();

                // Label when zoomed in enough
                if (globalScale > 1.2) {
                    const fontSize = Math.max(8 / globalScale, 3);
                    ctx.font = `${fontSize}px IBM Plex Mono, monospace`;
                    ctx.textAlign = 'center';
                    ctx.textBaseline = 'top';
                    const labelY = node.y + size + 2;
                    // Text outline for readability on any background
                    ctx.strokeStyle = tc.bg;
                    ctx.lineWidth = 3 / globalScale;
                    ctx.lineJoin = 'round';
                    ctx.strokeText(node.label, node.x, labelY);
                    ctx.fillStyle = tc.text;
                    ctx.fillText(node.label, node.x, labelY);
                }
            })
            .nodeCanvasObjectMode(() => 'replace')
            .linkColor(l => homeHighlightLink === l ? '#a371f7' : '#a371f730')
            .linkWidth(l => homeHighlightLink === l ? 3 : 1)
            .linkLineDash([4, 2])
            .autoPauseRedraw(false)
            .d3AlphaDecay(0.03)
            .d3VelocityDecay(0.3)
            .cooldownTicks(150)
            .onNodeHover(node => {
                homeHighlightNode = node;
                container.style.cursor = node ? 'pointer' : 'default';
            })
            .onLinkHover(link => {
                homeHighlightLink = link;
                container.style.cursor = link ? 'pointer' : 'default';
            })
            .linkHoverPrecision(8)
            .onNodeClick(node => {
                if (node.system_id && typeof navigateSystem === 'function') {
                    navigateSystem(node.system_id);
                }
            })
            .onEngineStop(() => {
                homeGraph.zoomToFit(300, 20);
            });

        // Cluster same-system nodes closer together
        homeGraph.d3Force('link').distance(link => {
            const src = typeof link.source === 'object' ? link.source : null;
            const tgt = typeof link.target === 'object' ? link.target : null;
            if (src && tgt && src.system_id && src.system_id === tgt.system_id) return 25;
            return 60;
        });
        homeGraph.d3Force('collision', d3.forceCollide(8));

        // Build legend overlay
        const legend = document.createElement('div');
        legend.id = 'home-graph-legend';
        legend.style.cssText = 'position:absolute;bottom:6px;left:6px;right:6px;display:flex;flex-wrap:wrap;gap:0.5rem;padding:0.4rem 0.6rem;font-family:IBM Plex Mono,monospace;font-size:0.7rem;color:var(--text2);pointer-events:none;background:var(--surface);border:1px solid var(--border);border-radius:6px;';
        for (const [sysId, meta] of Object.entries(systemMeta)) {
            const item = document.createElement('span');
            item.style.cssText = 'display:flex;align-items:center;gap:0.25rem;';
            item.innerHTML = `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${meta.color};flex-shrink:0;"></span>${meta.label}`; // nosemgrep: insecure-innerhtml, insecure-document-method
            legend.appendChild(item);
        }
        container.style.position = 'relative';
        container.appendChild(legend);

    } catch (e) {
        console.error('Home graph error:', e);
        container.innerHTML = '<div class="home-loading" style="color:var(--red);">Graph error</div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
    }
}

// ============================================================================
// Vocabulary Panel
// ============================================================================

let homeVocabData = null;

/** Toggle the graph legend visibility */
function toggleGraphLegend() {
    var legend = document.getElementById('home-graph-legend');
    var btn = document.getElementById('home-graph-legend-toggle');
    if (!legend) return;
    if (legend.style.display === 'none') {
        legend.style.display = '';
        if (btn) btn.textContent = 'Hide Legend';
    } else {
        legend.style.display = 'none';
        if (btn) btn.textContent = 'Show Legend';
    }
}

async function loadHomeVocab(forceRefresh) {
    const c = document.getElementById('home-vocab-content');
    const searchInput = document.getElementById('home-vocab-filter');
    if (!c) return;
    try {
        var data;
        if (forceRefresh && typeof DataCache !== 'undefined') {
            data = await DataCache.refresh('vocabulary');
        } else if (typeof DataCache !== 'undefined') {
            data = await DataCache.getOrFetch('vocabulary');
        }
        if (!data) {
            const resp = await fetch('/api/registry/vocabulary');
            data = await resp.json();
        }
        homeVocabData = data;
        const groups = homeVocabData.groups || [];
        const total = homeVocabData.total_concepts || 0;
        if (searchInput) searchInput.placeholder = 'Search ' + total + ' concepts...';
        renderHomeVocab(groups, '');
    } catch (e) {
        c.innerHTML = '<div class="home-loading" style="color:var(--red);">Failed to load</div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
    }
}

function renderHomeVocab(groups, filter) {
    const c = document.getElementById('home-vocab-content');
    if (!c) return;
    const q = (filter || '').toLowerCase().trim();
    let html = '';
    let matchCount = 0;

    groups.forEach(function(group, gi) {
        let groupItems = '';
        let visibleCount = 0;
        (group.subgroups || []).forEach(function(sg) {
            (sg.concepts || []).forEach(function(concept) {
                const text = (concept.id + ' ' + concept.desc).toLowerCase();
                if (q && text.indexOf(q) === -1) return;
                visibleCount++;
                matchCount++;
                groupItems += '<div class="home-vocab-item">'
                    + '<code>' + escHtml(concept.id) + '</code>'
                    + '<span>' + escHtml(concept.desc) + '</span>'
                    + '</div>';
            });
        });
        if (visibleCount === 0) return;
        const expanded = q ? true : false;
        html += '<div class="home-vocab-group">'
            + '<button class="home-vocab-group-btn" onclick="this.nextElementSibling.classList.toggle(\'hidden\');this.querySelector(\'svg\').classList.toggle(\'rotate-180\')">'
            + '<span>' + (group.icon || '📁') + '</span>'
            + '<span style="flex:1;">' + escHtml(group.group) + '</span>'
            + '<span class="home-sys-badge">' + visibleCount + '</span>'
            + '<svg class="w-3 h-3 transition-transform ' + (expanded ? 'rotate-180' : '') + '" style="color:var(--muted);" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" /></svg>'
            + '</button>'
            + '<div class="home-vocab-items ' + (expanded ? '' : 'hidden') + '">' + groupItems + '</div>'
            + '</div>';
    });

    if (!html) {
        html = '<div class="home-loading">No matches</div>';
    }
    c.innerHTML = html; // nosemgrep: insecure-innerhtml, insecure-document-method
}

function filterHomeVocab(term) {
    if (!homeVocabData) return;
    renderHomeVocab(homeVocabData.groups || [], term);
}

// ============================================================================
// Shortcut Cards (prompt templates)
// ============================================================================

async function loadHomeShortcuts() {
    var container = document.getElementById('home-shortcut-cards');
    if (!container) return;
    try {
        // Use cache if available, otherwise fetch
        var templates = (typeof DataCache !== 'undefined') ? await DataCache.getOrFetch('templates') : null;
        if (!templates) {
            var resp = await fetch('/api/templates');
            templates = await resp.json();
        }
        if (!templates || !templates.length) {
            container.innerHTML = '<p style="color:var(--muted);font-size:0.8rem;grid-column:1/-1;text-align:center;">No prompt templates configured yet.</p>'; // nosemgrep: insecure-innerhtml, insecure-document-method
            return;
        }
        // Show up to 10
        var items = templates.slice(0, 10);
        homeShortcutCount = items.length;
        container.innerHTML = items.map(function(t) { // nosemgrep: insecure-innerhtml, insecure-document-method
            return '<button class="home-shortcut-card">'
                + '<div class="sc-body">'
                + '<div class="sc-title">' + escHtml(t.title) + '</div>'
                + '<div class="sc-desc">' + escHtml(t.description) + '</div>'
                + '</div>'
                + '<svg class="sc-arrow" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" /></svg>'
                + '</button>';
        }).join('');
        // Bind clicks via JS to avoid inline quote issues
        container.querySelectorAll('.home-shortcut-card').forEach(function(btn, i) {
            btn.addEventListener('click', function() {
                useShortcut(items[i].prompt_detail || items[i].title);
            });
        });
        // If there are more templates than shown on cards, show the lightning bolt immediately
        if (templates.length > items.length) {
            homeTemplatesCache = templates;
            showHomeTemplatesBtn();
        }
    } catch (e) {
        container.innerHTML = '<p style="color:var(--muted);font-size:0.75rem;grid-column:1/-1;text-align:center;">Could not load suggestions.</p>'; // nosemgrep: insecure-innerhtml, insecure-document-method
    }
}

function useShortcut(promptText) {
    // Fill the input and auto-send
    var input = document.getElementById('home-chat-input');
    if (input) input.value = promptText;
    // Trigger send
    var form = document.getElementById('home-chat-form');
    if (form) form.dispatchEvent(new Event('submit', { cancelable: true }));
}

// ============================================================================
// Chat Panel (inline mini-chat)
// ============================================================================

let homeChatStreaming = false;
let homeLastMetadata = null;

function newHomeChat() {
    // Generate a new session ID
    var newId = homeGenerateSessionId();
    localStorage.setItem(HOME_SESSION_KEY, newId);
    console.log('[home] New chat started, session:', newId);

    // Visual feedback on the New button
    var btn = document.querySelector('[onclick*="newHomeChat"]');
    if (btn) {
        var origHTML = btn.innerHTML; // nosemgrep: insecure-innerhtml, insecure-document-method
        var origColor = btn.style.color;
        var origBorder = btn.style.borderColor;
        btn.innerHTML = '<svg style="width:12px;height:12px;" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" /></svg> Created'; // nosemgrep: insecure-innerhtml, insecure-document-method
        btn.style.color = 'var(--green, #22c55e)';
        btn.style.borderColor = 'var(--green, #22c55e)';
        setTimeout(function() {
            btn.innerHTML = origHTML; // nosemgrep: insecure-innerhtml, insecure-document-method
            btn.style.color = origColor;
            btn.style.borderColor = origBorder;
        }, 1200);
    }

    // Clear messages and restore welcome state
    var messages = document.getElementById('home-chat-messages');
    if (messages) {
        messages.innerHTML = '<div id="home-chat-welcome" class="home-chat-welcome">' // nosemgrep: insecure-innerhtml, insecure-document-method
            + '<p style="color: var(--accent); font-size: 1.25rem; font-weight: 600; font-family: Outfit, sans-serif;">What can I help you explore?</p>'
            + '<div id="home-shortcut-cards" class="home-shortcut-grid"></div>'
            + '</div>';
        loadHomeShortcuts();
    }
    var input = document.getElementById('home-chat-input');
    if (input) { input.value = ''; input.focus(); }
}

async function sendHomeChat(event) {
    event.preventDefault();
    const input = document.getElementById('home-chat-input');
    const msg = (input.value || '').trim();
    if (!msg || homeChatStreaming) return;
    input.value = '';

    const messages = document.getElementById('home-chat-messages');
    // Remove welcome state
    var welcome = document.getElementById('home-chat-welcome');
    if (welcome) welcome.remove();

    // Show the templates lightning bolt button after first message
    showHomeTemplatesBtn();

    // Add user message
    messages.innerHTML += '<div class="home-chat-msg user">' + escHtml(msg) + '</div>'; // nosemgrep: insecure-innerhtml, insecure-document-method

    // Add user message to memory cache for real-time updates
    addHomeMemoryEvent('user', msg);

    // Add assistant message — reasoning is inline inside the response bubble
    const assistantId = 'home-msg-' + Date.now();
    var msgWrap = document.createElement('div');
    msgWrap.id = assistantId + '-wrap';
    // Agent label
    var agentLabel = document.createElement('div');
    agentLabel.className = 'home-agent-label';
    agentLabel.textContent = 'AGENT';
    msgWrap.appendChild(agentLabel);
    // Single assistant bubble containing reasoning + response
    var bubbleEl = document.createElement('div');
    bubbleEl.className = 'home-chat-msg assistant';
    bubbleEl.id = assistantId;
    // Reasoning details (expanded while streaming, collapses when done) — inside the bubble
    var reasoningDetails = document.createElement('details');
    reasoningDetails.className = 'home-reasoning-details';
    var reasoningSummary = document.createElement('summary');
    reasoningSummary.className = 'home-reasoning-summary';
    reasoningSummary.textContent = 'Reasoning…';
    reasoningDetails.appendChild(reasoningSummary);
    var reasoningBody = document.createElement('div');
    reasoningBody.className = 'home-reasoning-body';
    reasoningBody.id = assistantId + '-reasoning';
    reasoningDetails.appendChild(reasoningBody);
    bubbleEl.appendChild(reasoningDetails);
    // Response content area — below reasoning inside the same bubble
    var responseContent = document.createElement('div');
    responseContent.className = 'home-response-content';
    responseContent.id = assistantId + '-content';
    responseContent.innerHTML = '<span class="home-chat-streaming"></span>'; // nosemgrep: insecure-innerhtml, insecure-document-method
    bubbleEl.appendChild(responseContent);
    msgWrap.appendChild(bubbleEl);
    msgWrap.appendChild(agentLabel);
    msgWrap.appendChild(bubbleEl);
    messages.appendChild(msgWrap);
    messages.scrollTop = messages.scrollHeight;

    homeChatStreaming = true;
    homeLastMetadata = null;
    var toolCallCount = 0;
    var queriedSystems = [];
    var citationCounter = 0;
    var traceStepCounter = 0;
    var toolInputMap = {};  // Store tool_input by tool_use_id for trace detail
    var guardrailTriggered = false;

    // Switch to Trace tab and clear previous entries
    switchHomeTab('trace');
    var traceContainer = document.getElementById('home-trace-entries');
    if (traceContainer) {
        traceContainer.innerHTML = ''; // nosemgrep: insecure-innerhtml, insecure-document-method
        // Add user question as first trace entry
        var qEntry = document.createElement('div');
        qEntry.className = 'home-trace-entry decision';
        qEntry.innerHTML = '<div class="home-trace-step-label">Question</div>' + escHtml(msg); // nosemgrep: insecure-innerhtml, insecure-document-method
        traceContainer.appendChild(qEntry);
    }
    try {
        const sessionId = localStorage.getItem(HOME_SESSION_KEY) || homeGenerateSessionId();
        localStorage.setItem(HOME_SESSION_KEY, sessionId);
        const modelId = getHomeModel().id;
        const resp = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt: msg, session_id: sessionId, agent_mode: 'explorer', model_id: modelId }),
        });

        if (!resp.ok) {
            var errText = '';
            try { var errData = await resp.json(); errText = errData.detail || resp.statusText; } catch(_) { errText = resp.statusText; }
            throw new Error(resp.status + ': ' + errText);
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let content = '';
        let buffer = '';
        const el = document.getElementById(assistantId + '-content');
        const reasoningEl = document.getElementById(assistantId + '-reasoning');

        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                if (buffer.trim()) {
                    for (const line of buffer.split('\n')) {
                        if (!line.startsWith('data: ')) continue;
                        const raw = line.slice(6);
                        if (raw === '[DONE]') continue;
                        try {
                            const data = JSON.parse(raw);
                            if (data.type === 'message' && data.content) {
                                content += data.content;
                            } else if (data.type === 'metadata') {
                                var ud = data.data || data.usage || data;
                                if (ud && (ud.inputTokens !== undefined || ud.outputTokens !== undefined || ud.latencyMs !== undefined)) {
                                    homeLastMetadata = ud;
                                }
                            }
                        } catch (_) {}
                    }
                }
                break;
            }
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';
            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const raw = line.slice(6);
                if (raw === '[DONE]') continue;
                try {
                    const data = JSON.parse(raw);
                    if (data.type === 'message' && data.content) {
                        content += data.content;
                        if (el) {
                            // Strip ---FOLLOWUPS--- block during streaming so it doesn't flash as raw text
                            var displayContent = content;
                            var fMatch = displayContent.match(/-*\s*FOLLOWUPS\s*-*/);
                            if (fMatch) displayContent = displayContent.slice(0, fMatch.index).trim();
                            var rendered = typeof marked !== 'undefined'
                                ? (typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(marked.parse(displayContent)) : marked.parse(displayContent))
                                : escHtml(displayContent);
                            el.innerHTML = rendered + '<span class="home-chat-streaming"></span>'; // nosemgrep: insecure-innerhtml, insecure-document-method
                        }
                        messages.scrollTop = messages.scrollHeight;
                    } else if (data.type === 'tool_use' && reasoningEl) {
                        toolCallCount++;
                        // Show reasoning section on first tool call
                        reasoningDetails.classList.add('has-tools');

                        var toolName = data.tool_name || 'tool';
                        var toolUseId = data.tool_use_id || 'home-tool-' + Date.now();
                        var toolInput = data.tool_input || {};

                        // Store tool input for trace detail later
                        toolInputMap[toolUseId] = toolInput;

                        // Track queried systems for citations
                        if (toolName === 'query_system' && toolInput.system_id) {
                            citationCounter++;
                            queriedSystems.push({ num: citationCounter, system_id: toolInput.system_id, tool_id: toolUseId });
                        }

                        // Friendly tool label
                        var toolLabel = toolName;
                        if (toolName === 'query_system') toolLabel = 'Querying ' + (toolInput.system_id || 'system');
                        else if (toolName === 'search_knowledge_base') toolLabel = 'Searching knowledge base';
                        else if (toolName.startsWith('registry___')) toolLabel = toolName.replace('registry___', '').replace(/_/g, ' ');

                        var pill = document.createElement('div');
                        pill.className = 'home-tool-pill';
                        pill.setAttribute('data-tool-id', toolUseId);
                        pill.textContent = toolLabel;

                        var trace = document.createElement('div');
                        trace.className = 'home-tool-trace';
                        trace.setAttribute('data-tool-id', toolUseId);
                        // Show query input
                        var inputStr = toolInput.query || toolInput.concept_id || toolInput.system_id || '';
                        if (inputStr) {
                            trace.innerHTML = '<div class="home-trace-input">' + escHtml(inputStr.slice(0, 300)) + '</div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
                        }
                        trace.classList.add('visible');

                        pill.onclick = function() { trace.classList.toggle('visible'); };
                        reasoningEl.appendChild(pill);
                        reasoningEl.appendChild(trace);

                        reasoningSummary.textContent = 'Reasoning (' + toolCallCount + ' tool call' + (toolCallCount > 1 ? 's' : '') + ')';

                        // Add trace entry to TRACE tab
                        if (traceContainer) {
                            // Only add if not already present (hook events may arrive before messages batch)
                            if (!traceContainer.querySelector('[data-tool-id="' + toolUseId + '"]')) {
                                traceStepCounter++;
                                var tEntry = document.createElement('div');
                                tEntry.className = 'home-trace-entry discovery';
                                tEntry.setAttribute('data-tool-id', toolUseId);
                                // Show just the step label with hourglass — query/result added when tool_result arrives
                                tEntry.innerHTML = '<div class="home-trace-step-label">Step ' + traceStepCounter + ' · ' + escHtml(toolLabel) + '</div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
                                traceContainer.appendChild(tEntry);
                                var traceTab = document.getElementById('home-tab-trace');
                                if (traceTab) traceTab.scrollTop = traceTab.scrollHeight;
                            }
                        }
                        messages.scrollTop = messages.scrollHeight;
                    } else if (data.type === 'tool_result' && reasoningEl) {
                        var resultId = data.tool_use_id;
                        // Only process tool_results for tools we've tracked via tool_use
                        var matchPill = resultId ? reasoningEl.querySelector('.home-tool-pill[data-tool-id="' + resultId + '"]') : null;
                        if (!matchPill) {
                            // Unknown tool result — skip to avoid duplicate content
                        } else {
                        var matchTrace = resultId ? reasoningEl.querySelector('.home-tool-trace[data-tool-id="' + resultId + '"]') : null;
                        if (matchPill) matchPill.classList.add('done');
                        if (matchTrace && data.tool_result && !matchTrace.querySelector('.home-trace-result')) {
                            var summaryText = '';
                            try {
                                var rawResult = data.tool_result;
                                // Handle double-encoded JSON (string containing JSON)
                                if (typeof rawResult === 'string') {
                                    try { rawResult = JSON.parse(rawResult); } catch(_) {}
                                }
                                if (typeof rawResult === 'object' && rawResult !== null) {
                                    summaryText = rawResult.analysis || rawResult.summary || '';
                                    if (!summaryText && rawResult.row_count !== undefined) {
                                        summaryText = (rawResult.row_count || 0) + ' rows returned';
                                    }
                                    if (!summaryText) {
                                        summaryText = rawResult.system_name ? rawResult.system_name + ': ' + (rawResult.row_count || 0) + ' rows' : '';
                                    }
                                } else {
                                    summaryText = String(data.tool_result).slice(0, 200);
                                }
                            } catch(_) {
                                summaryText = String(data.tool_result).slice(0, 200);
                            }
                            if (summaryText) {
                                var resultDiv = document.createElement('div');
                                resultDiv.className = 'home-trace-result';
                                if (typeof marked !== 'undefined') {
                                    var truncResult = summaryText.slice(0, 200);
                                    resultDiv.innerHTML = typeof DOMPurify !== 'undefined' // nosemgrep: insecure-innerhtml, insecure-document-method
                                        ? DOMPurify.sanitize(marked.parse(truncResult))
                                        : marked.parse(truncResult);
                                } else {
                                    resultDiv.textContent = summaryText.slice(0, 200);
                                }
                                matchTrace.appendChild(resultDiv);
                            }
                            // Update trace tab entry with query + result
                            if (traceContainer && data.tool_use_id) {
                                var traceEntry = traceContainer.querySelector('[data-tool-id="' + data.tool_use_id + '"]');
                                if (traceEntry) {
                                    if (traceEntry.childElementCount <= 1) {
                                        // Add query/input detail line — try tool_result.query first,
                                        // then fall back to the original tool_input fields
                                        var queryStr = '';
                                        try {
                                            var p = typeof data.tool_result === 'string' ? JSON.parse(data.tool_result) : data.tool_result;
                                            queryStr = p.query || '';
                                        } catch(_) {}
                                        if (!queryStr) {
                                            // Fall back to original tool_input
                                            var origInput = toolInputMap[data.tool_use_id] || {};
                                            queryStr = origInput.query || origInput.concept_id || origInput.system_id || origInput.search_query || '';
                                        }
                                        if (queryStr) {
                                            var queryDiv = document.createElement('div');
                                            queryDiv.style.cssText = 'font-size:0.72rem;color:var(--accent);opacity:0.7;font-family:IBM Plex Mono,monospace;word-break:break-all;margin-top:0.15rem;';
                                            queryDiv.textContent = queryStr.slice(0, 200);
                                            traceEntry.appendChild(queryDiv);
                                        }
                                        // Add result summary — parse analysis from JSON, render markdown
                                        if (summaryText) {
                                            var traceResult = document.createElement('div');
                                            traceResult.className = 'home-trace-result-text';
                                            traceResult.style.cssText = 'font-size:0.72rem;color:var(--text2);margin-top:0.2rem;';
                                            var truncated = summaryText.slice(0, 180) + (summaryText.length > 180 ? '…' : '');
                                            if (typeof marked !== 'undefined') {
                                                traceResult.innerHTML = typeof DOMPurify !== 'undefined' // nosemgrep: insecure-innerhtml, insecure-document-method
                                                    ? DOMPurify.sanitize(marked.parse(truncated))
                                                    : marked.parse(truncated);
                                            } else {
                                                traceResult.textContent = truncated;
                                            }
                                            traceEntry.appendChild(traceResult);
                                        }
                                    }
                                }
                            }
                        }
                        } // end else (matchPill found)
                    } else if (data.type === 'guardrail' && data.action === 'GUARDRAIL_INTERVENED') {
                        guardrailTriggered = true;
                        if (traceContainer) {
                            var gEntry = document.createElement('div');
                            gEntry.className = 'home-trace-entry guardrail';
                            var gSource = data.source === 'INPUT' ? 'User Input' : 'Agent Output';
                            var gFilters = [];
                            var wasBlocked = false;
                            var latencyMs = 0;
                            var guardrailId = '';
                            var origin = '';
                            try {
                                (data.assessments || []).forEach(function(a) {
                                    var gd = a.appliedGuardrailDetails || {};
                                    if (gd.guardrailId) guardrailId = gd.guardrailId;
                                    if (gd.guardrailOrigin) origin = (gd.guardrailOrigin || []).join(', ');
                                    // contentPolicy is at assessment level, not inside appliedGuardrailDetails
                                    (((a.contentPolicy || {}).filters) || []).forEach(function(f) {
                                        if (f.type) {
                                            gFilters.push({ type: f.type, confidence: f.confidence || '–', action: f.action || '–', strength: f.filterStrength || '' });
                                            if (f.action === 'BLOCKED') wasBlocked = true;
                                        }
                                    });
                                    (((a.topicPolicy || {}).topics) || []).forEach(function(t) {
                                        if (t.name) {
                                            gFilters.push({ type: t.name, confidence: t.confidence || '–', action: t.action || '–', strength: '' });
                                            if (t.action === 'BLOCKED') wasBlocked = true;
                                        }
                                    });
                                    var im = a.invocationMetrics || {};
                                    if (im.guardrailProcessingLatency) latencyMs = im.guardrailProcessingLatency;
                                });
                            } catch(_) {}
                            var modeText = wasBlocked ? 'Content blocked by guardrail' : 'Guardrail violation detected';
                            var modeColor = wasBlocked ? 'var(--red)' : 'var(--amber)';
                            var filtersHtml = gFilters.map(function(f) {
                                var confColor = f.confidence === 'HIGH' ? 'var(--red)' : f.confidence === 'MEDIUM' ? 'var(--amber)' : 'var(--muted)';
                                var actColor = f.action === 'BLOCKED' ? 'var(--red)' : 'var(--muted)';
                                return '<div style="display:flex;align-items:center;gap:0.35rem;font-size:0.68rem;padding:0.2rem 0;">'
                                    + '<span style="color:var(--text);font-family:IBM Plex Mono,monospace;font-weight:600;">' + escHtml(f.type) + '</span>'
                                    + '<span style="font-size:0.58rem;padding:0.08rem 0.25rem;border-radius:3px;background:rgba(248,81,73,0.08);color:' + confColor + ';font-family:IBM Plex Mono,monospace;font-weight:600;">' + escHtml(f.confidence) + '</span>'
                                    + '<span style="font-size:0.58rem;padding:0.08rem 0.25rem;border-radius:3px;border:1px solid ' + actColor + ';color:' + actColor + ';font-family:IBM Plex Mono,monospace;">' + escHtml(f.action) + '</span>'
                                    + (f.strength ? '<span style="font-size:0.55rem;color:var(--muted);font-family:IBM Plex Mono,monospace;">strength: ' + escHtml(f.strength) + '</span>' : '')
                                    + '</div>';
                            }).join('');
                            var metaParts = [];
                            if (guardrailId) metaParts.push('id: ' + guardrailId);
                            if (origin) metaParts.push('origin: ' + origin);
                            if (latencyMs) metaParts.push(latencyMs + 'ms');
                            var metaHtml = metaParts.length ? '<div style="font-size:0.58rem;color:var(--muted);font-family:IBM Plex Mono,monospace;margin-top:0.2rem;">' + escHtml(metaParts.join(' · ')) + '</div>' : '';
                            gEntry.innerHTML = '<div class="home-trace-step-label" style="color:var(--red);">⚠ Guardrail · ' + escHtml(gSource) + '</div>' // nosemgrep: insecure-innerhtml, insecure-document-method
                                + filtersHtml + metaHtml
                                + '<div style="font-size:0.62rem;color:' + modeColor + ';font-style:italic;margin-top:0.15rem;font-family:IBM Plex Mono,monospace;">' + modeText + '</div>';
                            traceContainer.appendChild(gEntry);
                            var traceTab = document.getElementById('home-tab-trace');
                            if (traceTab) traceTab.scrollTop = traceTab.scrollHeight;
                        }
                    } else if (data.type === 'metadata') {
                        var usageData = data.data || data.usage || data;
                        if (usageData && (usageData.inputTokens !== undefined || usageData.outputTokens !== undefined || usageData.latencyMs !== undefined)) {
                            homeLastMetadata = usageData;
                        }
                    }
                } catch (_) {}
            }
        }

        // Finalize — collapse reasoning, parse followups, add sources
        if (el) {
            // Collapse reasoning details
            reasoningDetails.open = false;
            if (!toolCallCount) reasoningDetails.style.display = 'none';

            // Parse ---FOLLOWUPS--- block (flexible: handles FOLLOWUPS---, ---FOLLOWUPS---, etc.)
            var responseText = content;
            var followups = [];
            var actions = [];
            var followupMatch = responseText.match(/-*\s*FOLLOWUPS\s*-*/);
            if (followupMatch) {
                var followupBlock = responseText.slice(followupMatch.index);
                responseText = responseText.slice(0, followupMatch.index).trim();
                var qMatches = followupBlock.matchAll(/Q\d+:\s*(.+)/g);
                var seenQ = new Set();
                for (var m of qMatches) {
                    var q = m[1].trim();
                    if (!seenQ.has(q)) { seenQ.add(q); followups.push(q); }
                }
                var aMatches = followupBlock.matchAll(/A\d+:\s*(.+)/g);
                var seenA = new Set();
                for (var m2 of aMatches) {
                    var a = m2[1].trim();
                    if (!seenA.has(a)) { seenA.add(a); actions.push(a); }
                }
            }

            // Render final answer with citation badges
            var answerHtml = typeof marked !== 'undefined'
                ? (typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(marked.parse(responseText)) : marked.parse(responseText))
                : escHtml(responseText);
            // Replace [N] with styled citation badges
            queriedSystems.forEach(function(s) {
                answerHtml = answerHtml.replaceAll('[' + s.num + ']',
                    '<span class="home-cite-badge" title="' + escHtml(s.system_id) + '">' + s.num + '</span>');
            });
            el.innerHTML = answerHtml; // nosemgrep: insecure-innerhtml, insecure-document-method
            if (!responseText.trim()) el.innerHTML = '<span style="color:var(--muted);font-style:italic;">No response</span>'; // nosemgrep: insecure-innerhtml, insecure-document-method

            // Add source citations footer — only show sources actually cited in the response
            var citedSystems = queriedSystems.filter(function(s) {
                return responseText.indexOf('[' + s.num + ']') !== -1;
            });
            if (citedSystems.length > 0) {
                var srcDiv = document.createElement('div');
                srcDiv.className = 'home-sources';
                srcDiv.innerHTML = '<span>Sources:</span> ' + citedSystems.map(function(s) { // nosemgrep: insecure-innerhtml, insecure-document-method
                    return '<span class="home-source-badge">[' + s.num + '] ' + escHtml(s.system_id) + '</span>';
                }).join(' ');
                msgWrap.appendChild(srcDiv);
            }

            // Add follow-up chips with headings
            if (followups.length > 0 || actions.length > 0) {
                var chipDiv = document.createElement('div');
                chipDiv.className = 'home-followup-chips';
                if (followups.length > 0) {
                    var qHeading = document.createElement('div');
                    qHeading.className = 'home-followup-heading';
                    qHeading.textContent = 'Explore further';
                    chipDiv.appendChild(qHeading);
                    followups.forEach(function(q) {
                        var chip = document.createElement('span');
                        chip.className = 'home-suggestion-chip';
                        chip.textContent = q;
                        chip.onclick = function() { document.getElementById('home-chat-input').value = q; document.getElementById('home-chat-form').dispatchEvent(new Event('submit')); };
                        chipDiv.appendChild(chip);
                    });
                }
                if (actions.length > 0) {
                    var aHeading = document.createElement('div');
                    aHeading.className = 'home-followup-heading home-action-heading';
                    aHeading.textContent = 'Recommended actions';
                    chipDiv.appendChild(aHeading);
                    actions.forEach(function(a) {
                        var chip = document.createElement('span');
                        chip.className = 'home-suggestion-chip home-action-chip';
                        chip.textContent = '⚡ ' + a;
                        chip.onclick = function() { document.getElementById('home-chat-input').value = a; document.getElementById('home-chat-form').dispatchEvent(new Event('submit')); };
                        chipDiv.appendChild(chip);
                    });
                }
                msgWrap.appendChild(chipDiv);
            }

            // ── Create a Workflow section (skip if guardrail was triggered) ──
            if (!guardrailTriggered) {
            (function() {
                var wfDiv = document.createElement('div');
                wfDiv.className = 'home-workflow-section';

                var wfHeading = document.createElement('div');
                wfHeading.className = 'home-followup-heading home-workflow-heading';
                wfHeading.textContent = 'Create a Workflow';
                wfDiv.appendChild(wfHeading);

                var wfDesc = document.createElement('div');
                wfDesc.className = 'home-workflow-desc';
                wfDesc.textContent = 'Save this query as a repeatable scheduled workflow.';
                wfDiv.appendChild(wfDesc);

                // Suggestion 1: based on current prompt
                var sug1 = document.createElement('div');
                sug1.className = 'home-workflow-suggestion';
                sug1.innerHTML = '<div class="home-wf-sug-title">' + escHtml(msg) + '</div>' // nosemgrep: insecure-innerhtml, insecure-document-method
                    + '<div class="home-wf-sug-meta">Based on your current query</div>';
                sug1.onclick = function() { showWorkflowModal(msg, _generateWorkflowTitle(msg)); };
                wfDiv.appendChild(sug1);

                // Suggestion 2: a scheduled-workflow variation
                // Prefer actions (they tend to be monitoring/checking tasks that suit recurring schedules).
                // Fall back to followups only if they look like repeatable monitoring queries.
                var variation = '';
                var variationTitle = '';
                var _isRepeatable = function(q) {
                    return /\b(check|monitor|verify|inspect|audit|review|report|status|health|alert|scan|validate|track|assess)\b/i.test(q);
                };
                // Try actions first — they're naturally workflow-oriented
                if (actions.length > 0) {
                    variation = actions[0];
                    variationTitle = 'Recommended action workflow';
                }
                // Fall back to a followup only if it looks like a repeatable monitoring query
                if (!variation && followups.length > 0) {
                    for (var fi = 0; fi < followups.length; fi++) {
                        if (_isRepeatable(followups[fi])) {
                            variation = followups[fi];
                            variationTitle = 'Suggested monitoring workflow';
                            break;
                        }
                    }
                }
                if (variation) {
                    var sug2 = document.createElement('div');
                    sug2.className = 'home-workflow-suggestion';
                    sug2.innerHTML = '<div class="home-wf-sug-title">' + escHtml(variation) + '</div>' // nosemgrep: insecure-innerhtml, insecure-document-method
                        + '<div class="home-wf-sug-meta">' + escHtml(variationTitle) + '</div>';
                    sug2.onclick = function() { showWorkflowModal(variation, _generateWorkflowTitle(variation)); };
                    wfDiv.appendChild(sug2);
                }

                msgWrap.appendChild(wfDiv);
            })();
            } // end guardrail check

            homeFinalizeMessage(bubbleEl, assistantId, responseText, msg, homeLastMetadata);

            // Add assistant response to memory cache for real-time updates
            if (responseText.trim()) addHomeMemoryEvent('assistant', responseText);

            // Scroll chat to bottom so follow-ups are visible
            setTimeout(function() { messages.scrollTop = messages.scrollHeight; }, 100);
        }
    } catch (e) {
        const el = document.getElementById(assistantId);
        if (el) el.innerHTML = '<span style="color:var(--red);">Error: ' + escHtml(e.message) + '</span>'; // nosemgrep: insecure-innerhtml, insecure-document-method
    }
    homeChatStreaming = false;
}

// ============================================================================
// Utilities
// ============================================================================

// escHtml — delegates to global escapeHTML from utils.js
var escHtml = escapeHTML;

/** Generate a UUID v4 session ID (36 chars). */
function homeGenerateSessionId() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        var r = Math.random() * 16 | 0;
        var v = c === 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}

// ============================================================================
// Init
// ============================================================================

// ── Message Footer: Feedback, Copy, Timestamp, Metrics ───────────────────────

const homeFeedbackStore = new Map();

function homeFinalizeMessage(el, msgId, content, userMsg, metadata) {
    var footer = document.createElement('div');
    footer.style.cssText = 'display:flex;align-items:center;justify-content:space-between;margin-top:0.35rem;padding:0 0.25rem;font-size:0.75rem;color:var(--muted);';

    var left = document.createElement('div');
    left.style.cssText = 'display:flex;align-items:center;gap:0.5rem;';

    var fbWrap = document.createElement('span');
    fbWrap.style.cssText = 'display:flex;gap:0.15rem;';
    var thumbUp = document.createElement('button');
    thumbUp.innerHTML = '&#x1F44D;'; // nosemgrep: insecure-innerhtml, insecure-document-method
    thumbUp.title = 'Helpful';
    thumbUp.style.cssText = 'background:none;border:none;cursor:pointer;font-size:0.85rem;opacity:0.5;padding:2px;transition:opacity 0.15s;';
    thumbUp.onmouseover = function(){ this.style.opacity='1'; };
    thumbUp.onmouseout = function(){ if(!this.dataset.selected) this.style.opacity='0.5'; };
    thumbUp.onclick = function(){ homeSubmitFeedback(msgId, 'positive', null, false, false); homeMarkFeedback(fbWrap, 'positive', msgId); };

    var thumbDown = document.createElement('button');
    thumbDown.innerHTML = '&#x1F44E;'; // nosemgrep: insecure-innerhtml, insecure-document-method
    thumbDown.title = 'Not helpful';
    thumbDown.style.cssText = 'background:none;border:none;cursor:pointer;font-size:0.85rem;opacity:0.5;padding:2px;transition:opacity 0.15s;';
    thumbDown.onmouseover = function(){ this.style.opacity='1'; };
    thumbDown.onmouseout = function(){ if(!this.dataset.selected) this.style.opacity='0.5'; };
    thumbDown.onclick = function(){ homeShowFeedbackModal(msgId, fbWrap); };

    fbWrap.appendChild(thumbUp);
    fbWrap.appendChild(thumbDown);
    left.appendChild(fbWrap);

    var ts = document.createElement('span');
    ts.textContent = new Date().toLocaleTimeString();
    left.appendChild(ts);
    footer.appendChild(left);

    var right = document.createElement('div');
    right.style.cssText = 'display:flex;align-items:center;gap:0.5rem;';

    if (metadata) {
        var parts = [];
        if (metadata.inputTokens !== undefined) parts.push(metadata.inputTokens + ' in');
        if (metadata.outputTokens !== undefined) parts.push(metadata.outputTokens + ' out');
        if (metadata.latencyMs !== undefined) parts.push((metadata.latencyMs / 1000).toFixed(2) + 's');
        if (parts.length) {
            var metricsEl = document.createElement('span');
            metricsEl.style.cssText = 'color:var(--accent);font-family:"IBM Plex Mono",monospace;font-size:0.7rem;';
            metricsEl.textContent = parts.join(' \u2022 ');
            right.appendChild(metricsEl);
        }
    }

    var copyBtn = document.createElement('button');
    copyBtn.innerHTML = '&#x1F4CB;'; // nosemgrep: insecure-innerhtml, insecure-document-method
    copyBtn.title = 'Copy response';
    copyBtn.style.cssText = 'background:none;border:none;cursor:pointer;font-size:0.85rem;opacity:0.5;padding:2px;transition:opacity 0.15s;';
    copyBtn.onmouseover = function(){ this.style.opacity='1'; };
    copyBtn.onmouseout = function(){ this.style.opacity='0.5'; };
    copyBtn.onclick = function(){
        var text = el.textContent || content;
        navigator.clipboard.writeText(text).then(function(){
            copyBtn.innerHTML = '&#x2705;'; // nosemgrep: insecure-innerhtml, insecure-document-method
            setTimeout(function(){ copyBtn.innerHTML = '&#x1F4CB;'; }, 2000); // nosemgrep: insecure-innerhtml, insecure-document-method
        });
    };
    right.appendChild(copyBtn);
    footer.appendChild(right);

    el.parentNode.insertBefore(footer, el.nextSibling);
    // Constrain footer width to match the chat bubble
    footer.style.maxWidth = '90%';

    homeFeedbackStore.set(msgId, { userMessage: userMsg, assistantResponse: content });
}

function homeMarkFeedback(fbWrap, sentiment, msgId) {
    var btns = fbWrap.querySelectorAll('button');
    btns.forEach(function(b){ b.style.opacity = '0.3'; b.style.pointerEvents = 'none'; b.dataset.selected = ''; });
    var idx = sentiment === 'positive' ? 0 : 1;
    if (btns[idx]) { btns[idx].style.opacity = '1'; btns[idx].dataset.selected = '1'; }
    // Offer a deliberate second step after a thumbs-up: save to the KB so
    // the agent can surface this Q/A as a worked example for similar
    // future questions. One-shot — posts is_validated=true on click.
    if (sentiment === 'positive' && msgId) {
        homeRenderSaveToKBPill(fbWrap, msgId);
    }
}

function homeRenderSaveToKBPill(fbWrap, msgId) {
    if (!fbWrap || fbWrap.querySelector('.home-save-to-kb-pill')) return;
    var pill = document.createElement('button');
    pill.className = 'home-save-to-kb-pill';
    pill.type = 'button';
    pill.title = 'Teach the agent by saving this answer to the knowledge base';
    pill.textContent = '+ Save to knowledge base';
    pill.style.cssText = 'margin-left:0.4rem;padding:0.1rem 0.55rem;border:1px solid var(--border);border-radius:999px;background:var(--surface2);color:var(--muted);font-size:0.68rem;cursor:pointer;transition:background 0.15s,color 0.15s;pointer-events:auto;opacity:1;';
    pill.onmouseover = function(){ if(!this.disabled) this.style.background = 'var(--surface3)'; };
    pill.onmouseout = function(){ if(!this.disabled) this.style.background = 'var(--surface2)'; };
    pill.onclick = function(){ homeSaveToKB(msgId, pill); };
    fbWrap.appendChild(pill);
}

async function homeSaveToKB(msgId, pill) {
    var ctx = homeFeedbackStore.get(msgId) || {};
    if (pill) {
        pill.disabled = true;
        pill.textContent = 'Saving\u2026';
        pill.style.cursor = 'wait';
    }
    var ok = await homeSubmitFeedback(msgId, 'positive', null, false, true);
    if (!pill) return;
    if (ok && ok.validated_answer_stored) {
        pill.textContent = '\u2713 Saved to knowledge base';
        pill.style.color = 'var(--green, #16a34a)';
        pill.style.cursor = 'default';
        pill.onmouseover = null;
        pill.onmouseout = null;
        pill.onclick = null;
    } else if (ok) {
        // Logged to DynamoDB but KB write was skipped (e.g. KB not configured)
        pill.textContent = '\u2713 Recorded';
        pill.style.cursor = 'default';
        pill.onclick = null;
    } else {
        pill.textContent = 'Save failed \u2014 retry';
        pill.disabled = false;
        pill.style.cursor = 'pointer';
    }
}

function homeSubmitFeedback(msgId, sentiment, comment, isCorrection, isValidated) {
    var ctx = homeFeedbackStore.get(msgId) || {};
    var sessionId = localStorage.getItem(HOME_SESSION_KEY) || '';
    return fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            message_id: msgId,
            session_id: sessionId,
            user_message: ctx.userMessage || '',
            assistant_response: ctx.assistantResponse || '',
            tools_used: [],
            sentiment: sentiment,
            user_comment: comment,
            is_correction: !!isCorrection,
            is_validated: !!isValidated
        })
    }).then(function(resp){
        if (!resp.ok) return null;
        return resp.json().catch(function(){ return {}; });
    }).catch(function(e){ console.error('Feedback error:', e); return null; });
}

function homeShowFeedbackModal(msgId, fbWrap) {
    var existing = document.getElementById('home-feedback-modal');
    if (existing) existing.remove();
    var modal = document.createElement('div');
    modal.id = 'home-feedback-modal';
    modal.style.cssText = 'position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.5);';
    modal.innerHTML = '<div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:1.5rem;max-width:440px;width:90%;">' // nosemgrep: insecure-innerhtml, insecure-document-method
        + '<h3 style="margin:0 0 0.5rem;color:var(--text);font-size:1rem;">Share your feedback</h3>'
        + '<textarea id="home-fb-comment" rows="3" style="width:100%;padding:0.5rem;border:1px solid var(--border);border-radius:8px;background:var(--surface2);color:var(--text);resize:none;font-family:inherit;" placeholder="What would have made this better?"></textarea>'
        + '<p style="font-size:0.72rem;color:var(--muted);margin:0.5rem 0 0;"><strong>Correction</strong> teaches the agent for next time. <strong>Comment</strong> records the feedback for review.</p>'
        + '<div style="display:flex;justify-content:flex-end;gap:0.5rem;margin-top:0.75rem;flex-wrap:wrap;">'
        + '<button onclick="document.getElementById(\'home-feedback-modal\').remove()" style="padding:0.4rem 1rem;border:1px solid var(--border);border-radius:8px;background:none;color:var(--text);cursor:pointer;">Cancel</button>'
        + '<button id="home-fb-submit-comment" style="padding:0.4rem 1rem;border:1px solid var(--border);border-radius:8px;background:none;color:var(--text);cursor:pointer;">Comment</button>'
        + '<button id="home-fb-submit-correction" style="padding:0.4rem 1rem;border:none;border-radius:8px;background:var(--accent);color:#fff;cursor:pointer;">Correction</button>'
        + '</div></div>';
    document.body.appendChild(modal);
    var _fbMouseDownOnBackdrop = false;
    modal.addEventListener('mousedown', function(e){ _fbMouseDownOnBackdrop = (e.target === modal); });
    modal.addEventListener('mouseup', function(e){
        if(_fbMouseDownOnBackdrop && e.target === modal) modal.remove();
        _fbMouseDownOnBackdrop = false;
    });
    function _submit(isCorrection){
        var comment = (document.getElementById('home-fb-comment').value || '').trim();
        modal.remove();
        homeSubmitFeedback(msgId, 'negative', comment || null, isCorrection, false);
        homeMarkFeedback(fbWrap, 'negative', msgId);
    }
    document.getElementById('home-fb-submit-comment').onclick = function(){ _submit(false); };
    document.getElementById('home-fb-submit-correction').onclick = function(){ _submit(true); };
    document.getElementById('home-fb-comment').focus();
}

document.addEventListener('DOMContentLoaded', function() {
    // Warm all caches in background if empty (first visit after login)
    if (typeof DataCache !== 'undefined') DataCache.warmAll();
    initHomeSelectors();
    loadHomeSystems();
    loadHomeGraph();
    loadHomeVocab();
    loadHomeShortcuts();
    loadHomeWorkflows();
    console.log('[home] Session initialized:', localStorage.getItem(HOME_SESSION_KEY) || 'none');
});

// ============================================================================
// Memory Tab (mirrors the memory sidebar component)
// ============================================================================

var homeMemoryTab = 'events';
var homeMemoryCache = {};

function switchHomeMemoryTab(tab) {
    homeMemoryTab = tab;
    ['events', 'episodes', 'facts', 'summaries', 'preferences'].forEach(function(t) {
        var el = document.getElementById('home-mem-' + t);
        if (el) el.style.display = t === tab ? '' : 'none';
        var btn = document.querySelector('.home-mem-tab[data-memtab="' + t + '"]');
        if (btn) {
            if (t === tab) {
                btn.style.color = 'var(--accent)';
                btn.style.borderBottomColor = 'var(--accent)';
            } else {
                btn.style.color = 'var(--muted)';
                btn.style.borderBottomColor = 'transparent';
            }
        }
    });
    loadHomeMemoryTab(tab);
}

function loadHomeMemoryTab(tab) {
    if (tab === 'events') loadHomeMemEvents();
    else if (tab === 'episodes') loadHomeMemEpisodes();
    else loadHomeMemSemantic(tab);
}

function homeMemSessionId() {
    return localStorage.getItem(HOME_SESSION_KEY) || '';
}

function homeMemFormatDate(timestamp) {
    if (!timestamp) return '';
    var d = new Date(timestamp);
    var h = d.getHours(), ampm = h >= 12 ? 'PM' : 'AM';
    h = h % 12 || 12;
    return (d.getMonth()+1) + '/' + d.getDate() + '/' + d.getFullYear() + ' ' + h + ':' + d.getMinutes().toString().padStart(2,'0') + ' ' + ampm;
}

async function loadHomeMemEvents(force) {
    var container = document.getElementById('home-mem-events');
    if (!container) return;
    var sid = homeMemSessionId();
    if (!sid) { container.innerHTML = '<p style="color:var(--muted);font-size:0.8rem;text-align:center;padding:2rem;">No session active</p>'; return; } // nosemgrep: insecure-innerhtml, insecure-document-method
    if (!force && homeMemoryCache.events && homeMemoryCache.sid === sid) {
        renderHomeMemEvents(homeMemoryCache.events, container);
        return;
    }
    container.innerHTML = '<p style="color:var(--muted);font-size:0.8rem;text-align:center;padding:2rem;">Loading events…</p>'; // nosemgrep: insecure-innerhtml, insecure-document-method
    try {
        var resp = await fetch('/api/memory/events?session_id=' + sid);
        if (!resp.ok) throw new Error('Failed');
        var data = await resp.json();
        homeMemoryCache.events = data.messages || [];
        homeMemoryCache.sid = sid;
        renderHomeMemEvents(homeMemoryCache.events, container);
    } catch(e) {
        container.innerHTML = '<p style="color:var(--red,#f85149);font-size:0.8rem;text-align:center;padding:2rem;">Failed to load events</p>'; // nosemgrep: insecure-innerhtml, insecure-document-method
    }
}

function renderHomeMemEvents(messages, container) {
    if (!messages || !messages.length) {
        container.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--muted);font-size:0.8rem;">No conversation history yet.<br><span style="font-size:0.72rem;opacity:0.7;">Start chatting to see events here</span></div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
        return;
    }
    container.innerHTML = messages.map(function(evt, idx) { // nosemgrep: insecure-innerhtml, insecure-document-method
        var role = (evt.role || '').toLowerCase();
        var isUser = role === 'user';
        var emoji = isUser ? '👤' : '🤖';
        var label = isUser ? 'You' : 'Agent';
        var ts = evt.timestamp ? new Date(evt.timestamp).toLocaleTimeString() : '';
        var content = evt.content || '';
        var rendered = isUser ? escHtml(content) : (typeof marked !== 'undefined' ? (typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(marked.parse(content)) : marked.parse(content)) : escHtml(content));
        var contentClass = isUser ? '' : ' markdown-content';
        var needsExpander = content.length > 300;
        return '<div style="padding:0.6rem;border-radius:8px;margin-bottom:0.5rem;background:var(--surface2);border:1px solid var(--border);">'
            + '<div style="display:flex;align-items:center;gap:0.4rem;margin-bottom:0.35rem;">'
            + '<span style="font-size:0.68rem;font-weight:600;padding:0.1rem 0.35rem;border-radius:3px;background:rgba(0,216,255,0.12);color:var(--accent);font-family:IBM Plex Mono,monospace;">#' + (idx+1) + '</span>'
            + '<span style="font-size:1rem;">' + emoji + '</span>'
            + '<span style="font-size:0.72rem;font-weight:600;color:var(--text);">' + label + '</span>'
            + '<span style="font-size:0.68rem;color:var(--muted);margin-left:auto;">' + ts + '</span>'
            + '</div>'
            + (needsExpander
                ? '<details class="home-mem-expander" style="font-size:0.78rem;color:var(--text);line-height:1.5;">'
                  + '<summary style="cursor:pointer;font-size:0.72rem;color:var(--accent);font-family:IBM Plex Mono,monospace;user-select:none;list-style:none;display:flex;align-items:center;gap:0.3rem;">'
                  + '<svg class="mem-evt-chevron" style="width:12px;height:12px;transition:transform 0.2s;flex-shrink:0;" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"/></svg>'
                  + 'Show full message</summary>'
                  + '<div class="' + contentClass + '" style="padding-top:0.3rem;">' + rendered + '</div></details>'
                : '<div class="' + contentClass + '" style="font-size:0.78rem;color:var(--text);line-height:1.5;">' + rendered + '</div>')
            + '</div>';
    }).join('');

    // Toggle chevron and label on expand/collapse
    container.querySelectorAll('.home-mem-expander').forEach(function(d) {
        d.addEventListener('toggle', function() {
            var summary = d.querySelector('summary');
            if (summary) {
                summary.innerHTML = '<svg class="mem-evt-chevron" style="width:12px;height:12px;transition:transform 0.2s;flex-shrink:0;' + (d.open ? 'transform:rotate(90deg);' : '') + '" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"/></svg>' // nosemgrep: insecure-innerhtml, insecure-document-method
                    + (d.open ? 'Hide message' : 'Show full message');
            }
        });
    });
}

async function loadHomeMemEpisodes(force) {
    var container = document.getElementById('home-mem-episodes');
    if (!container) return;
    var sid = homeMemSessionId();
    if (!sid) { container.innerHTML = '<p style="color:var(--muted);font-size:0.8rem;text-align:center;padding:2rem;">No session active</p>'; return; } // nosemgrep: insecure-innerhtml, insecure-document-method
    if (!force && homeMemoryCache.episodes && homeMemoryCache.sid === sid) {
        renderHomeMemEpisodes(homeMemoryCache.episodes, container);
        return;
    }
    container.innerHTML = '<p style="color:var(--muted);font-size:0.8rem;text-align:center;padding:2rem;">Loading episodes…</p>'; // nosemgrep: insecure-innerhtml, insecure-document-method
    try {
        var resp = await fetch('/api/memory/episodic?session_id=' + sid);
        if (!resp.ok) throw new Error('Failed');
        var data = await resp.json();
        homeMemoryCache.episodes = data.episodes || [];
        homeMemoryCache.sid = sid;
        renderHomeMemEpisodes(homeMemoryCache.episodes, container);
    } catch(e) {
        container.innerHTML = '<p style="color:var(--red,#f85149);font-size:0.8rem;text-align:center;padding:2rem;">Failed to load episodes</p>'; // nosemgrep: insecure-innerhtml, insecure-document-method
    }
}

function renderHomeMemEpisodes(items, container) {
    if (!items || !items.length) {
        container.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--muted);font-size:0.8rem;">No episodes captured yet.<br><span style="font-size:0.72rem;opacity:0.7;">Episodes appear as the agent processes interactions</span></div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
        return;
    }
    container.innerHTML = items.map(function(item, idx) { // nosemgrep: insecure-innerhtml, insecure-document-method
        var ts = homeMemFormatDate(item.timestamp || item.createdAt);
        var parsed = null;
        try { parsed = JSON.parse(item.content); if (!parsed.scenario && !parsed.intent && !parsed.outcome) parsed = null; } catch(e) {}
        if (parsed) {
            var parts = '';
            if (parsed.scenario) parts += '<div style="margin-bottom:0.3rem;"><span style="font-size:0.68rem;font-weight:600;color:var(--muted);">Scenario:</span><p style="font-size:0.78rem;color:var(--text);margin:0.15rem 0 0;">' + escHtml(parsed.scenario) + '</p></div>';
            if (parsed.intent) parts += '<div style="margin-bottom:0.3rem;"><span style="font-size:0.68rem;font-weight:600;color:var(--muted);">Intent:</span><p style="font-size:0.78rem;color:var(--text);margin:0.15rem 0 0;">' + escHtml(parsed.intent) + '</p></div>';
            if (parsed.outcome) parts += '<div style="margin-bottom:0.3rem;"><span style="font-size:0.68rem;font-weight:600;color:var(--muted);">Outcome:</span><p style="font-size:0.78rem;color:var(--text);margin:0.15rem 0 0;">' + escHtml(parsed.outcome) + '</p></div>';
            return '<div style="padding:0.6rem;border-radius:8px;margin-bottom:0.5rem;background:var(--surface2);border:1px solid var(--border);">'
                + '<div style="display:flex;align-items:center;gap:0.4rem;margin-bottom:0.35rem;">'
                + '<span style="font-size:0.68rem;font-weight:600;padding:0.1rem 0.35rem;border-radius:3px;background:rgba(0,216,255,0.12);color:var(--accent);font-family:IBM Plex Mono,monospace;">#' + (idx+1) + '</span>'
                + '<span style="font-size:1rem;">🎬</span>'
                + '<span style="font-size:0.68rem;color:var(--muted);margin-left:auto;">' + ts + '</span>'
                + '</div>' + parts + '</div>';
        }
        var content = item.content || '';
        if (content.length > 400) content = content.substring(0, 400) + '…';
        return '<div style="padding:0.6rem;border-radius:8px;margin-bottom:0.5rem;background:var(--surface2);border:1px solid var(--border);">'
            + '<div style="display:flex;align-items:center;gap:0.4rem;margin-bottom:0.35rem;">'
            + '<span style="font-size:0.68rem;font-weight:600;padding:0.1rem 0.35rem;border-radius:3px;background:rgba(0,216,255,0.12);color:var(--accent);font-family:IBM Plex Mono,monospace;">#' + (idx+1) + '</span>'
            + '<span style="font-size:1rem;">🎬</span>'
            + '<span style="font-size:0.68rem;color:var(--muted);margin-left:auto;">' + ts + '</span>'
            + '</div>'
            + '<p style="font-size:0.78rem;color:var(--text);line-height:1.5;">' + escHtml(content) + '</p></div>';
    }).join('');
}

async function loadHomeMemSemantic(type, force) {
    var container = document.getElementById('home-mem-' + type);
    if (!container) return;
    var sid = homeMemSessionId();
    if (!sid) { container.innerHTML = '<p style="color:var(--muted);font-size:0.8rem;text-align:center;padding:2rem;">No session active</p>'; return; } // nosemgrep: insecure-innerhtml, insecure-document-method
    if (!force && homeMemoryCache[type] && homeMemoryCache.sid === sid) {
        renderHomeMemSemantic(type, homeMemoryCache[type], container);
        return;
    }
    container.innerHTML = '<p style="color:var(--muted);font-size:0.8rem;text-align:center;padding:2rem;">Loading ' + type + '…</p>'; // nosemgrep: insecure-innerhtml, insecure-document-method
    try {
        var resp = await fetch('/api/memory/semantic?session_id=' + sid + '&type=' + type);
        if (!resp.ok) throw new Error('Failed');
        var data = await resp.json();
        var items = data[type] || data.items || data.facts || [];
        homeMemoryCache[type] = items;
        homeMemoryCache.sid = sid;
        renderHomeMemSemantic(type, items, container);
    } catch(e) {
        container.innerHTML = '<p style="color:var(--red,#f85149);font-size:0.8rem;text-align:center;padding:2rem;">Failed to load ' + type + '</p>'; // nosemgrep: insecure-innerhtml, insecure-document-method
    }
}

function renderHomeMemSemantic(type, items, container) {
    if (!items || !items.length) {
        var msgs = { facts: 'No facts extracted yet', summaries: 'No summaries available yet', preferences: 'No preferences learned yet' };
        container.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--muted);font-size:0.8rem;">' + (msgs[type] || 'No data') + '<br><span style="font-size:0.72rem;opacity:0.7;">Data will appear as you chat</span></div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
        return;
    }
    var emojis = { facts: '💡', summaries: '📝', preferences: '⭐' };
    var emoji = emojis[type] || '📄';

    if (type === 'summaries') {
        // Parse XML topics
        var allTopics = [];
        items.forEach(function(item) {
            var ts = item.timestamp || item.createdAt;
            var regex = /<topic name="([^"]+)">\s*([\s\S]*?)\s*<\/topic>/g;
            var match;
            while ((match = regex.exec(item.content)) !== null) {
                allTopics.push({ name: match[1].replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&'), content: match[2].trim(), timestamp: ts, idx: allTopics.length });
            }
        });
        if (!allTopics.length) { container.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--muted);font-size:0.8rem;">No summaries available yet</div>'; return; } // nosemgrep: insecure-innerhtml, insecure-document-method
        container.innerHTML = allTopics.map(function(t) { // nosemgrep: insecure-innerhtml, insecure-document-method
            var topicName = t.name.replace(/_/g, ' ').replace(/\b\w/g, function(c) { return c.toUpperCase(); });
            return '<div style="padding:0.6rem;border-radius:8px;margin-bottom:0.5rem;background:var(--surface2);border:1px solid var(--border);">'
                + '<div style="display:flex;align-items:center;gap:0.4rem;margin-bottom:0.35rem;">'
                + '<span style="font-size:0.68rem;font-weight:600;padding:0.1rem 0.35rem;border-radius:3px;background:rgba(0,216,255,0.12);color:var(--accent);font-family:IBM Plex Mono,monospace;">#' + (t.idx+1) + '</span>'
                + '<span style="font-size:1rem;">📝</span>'
                + '<span style="font-size:0.68rem;color:var(--muted);margin-left:auto;">' + homeMemFormatDate(t.timestamp) + '</span>'
                + '</div>'
                + '<div style="margin-bottom:0.2rem;"><span style="font-size:0.68rem;font-weight:600;color:var(--muted);">Topic:</span> <span style="font-size:0.78rem;font-weight:600;color:var(--text);">' + escHtml(topicName) + '</span></div>'
                + '<p style="font-size:0.78rem;color:var(--text);line-height:1.5;margin:0.2rem 0 0;">' + escHtml(t.content) + '</p></div>';
        }).join('');
        return;
    }

    if (type === 'preferences') {
        container.innerHTML = items.map(function(item, idx) { // nosemgrep: insecure-innerhtml, insecure-document-method
            var ts = homeMemFormatDate(item.timestamp || item.createdAt);
            var parsed = null;
            try { parsed = JSON.parse(item.content); } catch(e) {}
            if (parsed && (parsed.preference || parsed.context)) {
                var html = '<div style="padding:0.6rem;border-radius:8px;margin-bottom:0.5rem;background:var(--surface2);border:1px solid var(--border);">'
                    + '<div style="display:flex;align-items:center;gap:0.4rem;margin-bottom:0.35rem;">'
                    + '<span style="font-size:0.68rem;font-weight:600;padding:0.1rem 0.35rem;border-radius:3px;background:rgba(0,216,255,0.12);color:var(--accent);font-family:IBM Plex Mono,monospace;">#' + (idx+1) + '</span>'
                    + '<span style="font-size:1rem;">⭐</span>'
                    + '<span style="font-size:0.68rem;color:var(--muted);margin-left:auto;">' + ts + '</span></div>';
                if (parsed.preference) html += '<p style="font-size:0.78rem;font-weight:600;color:var(--text);margin:0.2rem 0;">' + escHtml(parsed.preference) + '</p>';
                if (parsed.context) html += '<p style="font-size:0.72rem;color:var(--text);opacity:0.85;margin:0.15rem 0;">' + escHtml(parsed.context) + '</p>';
                if (parsed.categories && parsed.categories.length) html += '<div style="display:flex;flex-wrap:wrap;gap:0.25rem;margin-top:0.3rem;">' + parsed.categories.map(function(c) { return '<span style="font-size:0.65rem;padding:0.1rem 0.35rem;border-radius:999px;background:var(--surface3,var(--border));color:var(--muted);">' + escHtml(c) + '</span>'; }).join('') + '</div>';
                return html + '</div>';
            }
            return '<div style="padding:0.6rem;border-radius:8px;margin-bottom:0.5rem;background:var(--surface2);border:1px solid var(--border);">'
                + '<div style="display:flex;align-items:center;gap:0.4rem;margin-bottom:0.35rem;">'
                + '<span style="font-size:0.68rem;font-weight:600;padding:0.1rem 0.35rem;border-radius:3px;background:rgba(0,216,255,0.12);color:var(--accent);font-family:IBM Plex Mono,monospace;">#' + (idx+1) + '</span>'
                + '<span style="font-size:1rem;">⭐</span>'
                + '<span style="font-size:0.68rem;color:var(--muted);margin-left:auto;">' + ts + '</span></div>'
                + '<p style="font-size:0.78rem;color:var(--text);line-height:1.5;">' + escHtml(item.content) + '</p></div>';
        }).join('');
        return;
    }

    // Facts (default)
    container.innerHTML = items.map(function(item, idx) { // nosemgrep: insecure-innerhtml, insecure-document-method
        var ts = homeMemFormatDate(item.timestamp || item.createdAt);
        return '<div style="padding:0.6rem;border-radius:8px;margin-bottom:0.5rem;background:var(--surface2);border:1px solid var(--border);">'
            + '<div style="display:flex;align-items:center;gap:0.4rem;margin-bottom:0.35rem;">'
            + '<span style="font-size:0.68rem;font-weight:600;padding:0.1rem 0.35rem;border-radius:3px;background:rgba(0,216,255,0.12);color:var(--accent);font-family:IBM Plex Mono,monospace;">#' + (idx+1) + '</span>'
            + '<span style="font-size:1rem;">' + emoji + '</span>'
            + '<span style="font-size:0.68rem;color:var(--muted);margin-left:auto;">' + ts + '</span></div>'
            + '<p style="font-size:0.78rem;color:var(--text);line-height:1.5;">' + escHtml(item.content) + '</p></div>';
    }).join('');
}

/** Add a message to the home memory event cache and re-render if the events sub-tab is visible. */
function addHomeMemoryEvent(role, content) {
    var sid = homeMemSessionId();
    if (!sid) return;
    if (homeMemoryCache.sid !== sid) {
        homeMemoryCache = { sid: sid, events: [] };
    }
    if (!homeMemoryCache.events) homeMemoryCache.events = [];
    homeMemoryCache.events.unshift({ role: role, content: content, timestamp: new Date().toISOString() });
    // Re-render if the memory tab and events sub-tab are currently visible
    var memTab = document.getElementById('home-tab-memory');
    if (memTab && memTab.style.display !== 'none' && homeMemoryTab === 'events') {
        var container = document.getElementById('home-mem-events');
        if (container) renderHomeMemEvents(homeMemoryCache.events, container);
    }
}

// ============================================================================
// Prompt Templates Popover (lightning bolt button)
// ============================================================================

var homeTemplatesCache = null;
var homeTemplatesOpen = false;
var homeShortcutCount = 0; // number of templates shown as cards

function showHomeTemplatesBtn() {
    var btn = document.getElementById('home-templates-btn');
    if (btn) btn.style.display = '';
    // Ensure popover stays closed
    var popover = document.getElementById('home-templates-popover');
    if (popover) popover.style.display = 'none';
    homeTemplatesOpen = false;
}

function toggleHomeTemplates() {
    homeTemplatesOpen = !homeTemplatesOpen;
    var popover = document.getElementById('home-templates-popover');
    if (!popover) return;
    if (homeTemplatesOpen) {
        popover.style.display = '';
        loadHomeTemplatesList();
    } else {
        popover.style.display = 'none';
    }
}

async function loadHomeTemplatesList() {
    var container = document.getElementById('home-templates-list');
    if (!container) return;
    if (homeTemplatesCache) {
        renderHomeTemplatesList(homeTemplatesCache, container);
        return;
    }
    container.innerHTML = '<div style="padding:1rem;text-align:center;color:var(--muted);font-size:0.78rem;">Loading…</div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
    try {
        var templates = (typeof DataCache !== 'undefined') ? await DataCache.getOrFetch('templates') : null;
        if (!templates) {
            var resp = await fetch('/api/templates');
            templates = await resp.json();
        }
        homeTemplatesCache = templates || [];
        renderHomeTemplatesList(homeTemplatesCache, container);
    } catch(e) {
        container.innerHTML = '<div style="padding:1rem;text-align:center;color:var(--red,#f85149);font-size:0.78rem;">Failed to load templates</div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
    }
}

function renderHomeTemplatesList(templates, container) {
    if (!templates || !templates.length) {
        container.innerHTML = '<div style="padding:1rem;text-align:center;color:var(--muted);font-size:0.78rem;">No templates configured</div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
        return;
    }
    // If shortcut cards are still visible, only show overflow templates not already on cards
    var shortcutsVisible = document.getElementById('home-shortcut-cards') && document.getElementById('home-shortcut-cards').offsetParent !== null;
    var displayTemplates = (shortcutsVisible && homeShortcutCount > 0) ? templates.slice(homeShortcutCount) : templates;
    if (!displayTemplates.length) {
        container.innerHTML = '<div style="padding:1rem;text-align:center;color:var(--muted);font-size:0.78rem;">All templates shown above</div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
        return;
    }
    container.innerHTML = displayTemplates.map(function(t, i) { // nosemgrep: insecure-innerhtml, insecure-document-method
        return '<button class="home-tpl-item" data-tpl-idx="' + i + '">'
            + '<div style="flex:1;min-width:0;">'
            + '<div style="font-size:0.8rem;font-weight:600;color:var(--text);line-height:1.3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">' + escHtml(t.title) + '</div>'
            + '<div style="font-size:0.7rem;color:var(--muted);margin-top:0.15rem;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;">' + escHtml(t.description || '') + '</div>'
            + '</div>'
            + '<svg style="width:14px;height:14px;color:var(--muted);flex-shrink:0;" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M13 7l5 5m0 0l-5 5m5-5H6"/></svg>'
            + '</button>';
    }).join('');
    container.querySelectorAll('.home-tpl-item').forEach(function(btn, i) {
        btn.addEventListener('click', function() {
            var t = displayTemplates[i];
            var prompt = t.prompt_detail || t.title;
            document.getElementById('home-chat-input').value = prompt;
            toggleHomeTemplates();
            document.getElementById('home-chat-form').dispatchEvent(new Event('submit', { cancelable: true }));
        });
    });
}

// Close popover on outside click
document.addEventListener('click', function(e) {
    if (!homeTemplatesOpen) return;
    var popover = document.getElementById('home-templates-popover');
    var btn = document.getElementById('home-templates-btn');
    if (popover && !popover.contains(e.target) && btn && !btn.contains(e.target)) {
        homeTemplatesOpen = false;
        popover.style.display = 'none';
    }
});

// ============================================================================
// Workflows — Modal, Tab, CRUD
// ============================================================================

function _generateWorkflowTitle(prompt) {
    var t = (prompt || '').trim();
    // Strip question marks and trailing punctuation
    t = t.replace(/[?!.]+$/, '').trim();
    // Remove leading question/command phrases (greedy, multi-word)
    t = t.replace(/^(what|which|how|where|when|why|are there|is there|do we have|can you|could you|please|show me|tell me|give me|analyze|check|find|list|get|pull|run|look at|look for|identify|investigate|compare|review|summarize|describe|explain)\s+(any|all|the|our|my|some|those|these|a|an)?\s*/i, '');
    // Remove filler phrases
    t = t.replace(/^(patterns|data|information|details|status|overview|summary)\s+(related to|about|for|on|of|regarding|around)\s+/i, '');
    t = t.replace(/\s+(related to|about|for|on|of|regarding|around)\s+/i, ': ');
    // Title case
    t = t.replace(/\b\w/g, function(c) { return c.toUpperCase(); });
    // Truncate smartly
    if (t.length > 50) {
        var sp = t.lastIndexOf(' ', 45);
        t = t.substring(0, sp > 15 ? sp : 45);
    }
    t = t.replace(/[,;:\s]+$/, '');
    return t || 'New Workflow';
}

function showWorkflowModal(prompt, defaultTitle) {
    var existing = document.getElementById('home-workflow-modal');
    if (existing) existing.remove();

    var modal = document.createElement('div');
    modal.id = 'home-workflow-modal';
    modal.style.cssText = 'position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.5);';
    modal.innerHTML = '<div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:1.5rem;max-width:480px;width:90%;">' // nosemgrep: insecure-innerhtml, insecure-document-method
        + '<h3 style="margin:0 0 0.75rem;color:var(--text);font-size:1rem;font-family:IBM Plex Mono,monospace;">Create Workflow</h3>'
        + '<label style="font-size:0.72rem;color:var(--muted);font-weight:600;display:block;margin-bottom:0.2rem;">Title</label>'
        + '<input id="wf-modal-title" type="text" value="' + escHtml(defaultTitle || '') + '" style="width:100%;padding:0.45rem 0.6rem;border:1px solid var(--border);border-radius:8px;background:var(--surface2);color:var(--text);font-family:inherit;font-size:0.85rem;margin-bottom:0.6rem;" />'
        + '<label style="font-size:0.72rem;color:var(--muted);font-weight:600;display:block;margin-bottom:0.2rem;">Prompt</label>'
        + '<textarea id="wf-modal-prompt" rows="3" style="width:100%;padding:0.45rem 0.6rem;border:1px solid var(--border);border-radius:8px;background:var(--surface2);color:var(--text);resize:vertical;font-family:inherit;font-size:0.82rem;margin-bottom:0.6rem;">' + escHtml(prompt || '') + '</textarea>'
        + '<label style="font-size:0.72rem;color:var(--muted);font-weight:600;display:block;margin-bottom:0.2rem;">Model</label>'
        + '<select id="wf-modal-model" style="width:100%;padding:0.45rem 0.6rem;border:1px solid var(--border);border-radius:8px;background:var(--surface2);color:var(--text);font-family:inherit;font-size:0.82rem;margin-bottom:0.6rem;box-sizing:border-box;">'
        + buildModelOptions(localStorage.getItem('agentcore-selected-model') || '', true)
        + '</select>'
        + '<label style="font-size:0.72rem;color:var(--muted);font-weight:600;display:block;margin-bottom:0.2rem;">Schedule</label>'
        + '<select id="wf-modal-schedule-type" onchange="wfModalScheduleChanged()" style="width:100%;padding:0.45rem 0.6rem;border:1px solid var(--border);border-radius:8px;background:var(--surface2);color:var(--text);font-family:inherit;font-size:0.82rem;margin-bottom:0.4rem;box-sizing:border-box;-webkit-appearance:none;appearance:none;background-image:url(&quot;data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' width=\'12\' height=\'12\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'%23888\' stroke-width=\'2\'%3E%3Cpath d=\'M6 9l6 6 6-6\'/%3E%3C/svg%3E&quot;);background-repeat:no-repeat;background-position:right 0.6rem center;padding-right:2rem;">'
        + '<option value="manual" selected>Manual Only</option>'
        + '<option value="hours">Every X Hours</option>'
        + '<option value="daily">Every Day at…</option>'
        + '<option value="weekdays">Weekdays at…</option>'
        + '</select>'
        + '<div id="wf-modal-schedule-options" style="display:flex;gap:0.5rem;align-items:center;margin-bottom:0.75rem;">'
        + '<div id="wf-modal-interval-wrap" style="display:none;flex:1;">'
        + '<label style="font-size:0.65rem;color:var(--muted);display:block;margin-bottom:0.15rem;">Hours</label>'
        + '<select id="wf-modal-interval" style="width:100%;padding:0.4rem 0.5rem;border:1px solid var(--border);border-radius:6px;background:var(--surface2);color:var(--text);font-family:inherit;font-size:0.82rem;">'
        + '<option value="1">1</option><option value="2">2</option><option value="3">3</option>'
        + '<option value="4">4</option><option value="6">6</option><option value="8">8</option>'
        + '<option value="12">12</option><option value="24">24</option>'
        + '</select></div>'
        + '<div id="wf-modal-time-wrap" style="flex:1;">'
        + '<label style="font-size:0.65rem;color:var(--muted);display:block;margin-bottom:0.15rem;">Time (UTC)</label>'
        + '<input id="wf-modal-time" type="time" value="08:00" style="width:100%;padding:0.4rem 0.5rem;border:1px solid var(--border);border-radius:6px;background:var(--surface2);color:var(--text);font-family:inherit;font-size:0.82rem;" />'
        + '</div></div>'
        + '<div style="display:flex;justify-content:flex-end;gap:0.5rem;">'
        + '<button onclick="document.getElementById(\'home-workflow-modal\').remove()" style="padding:0.45rem 1rem;border:1px solid var(--border);border-radius:8px;background:none;color:var(--text);cursor:pointer;font-family:inherit;">Cancel</button>'
        + '<button id="wf-modal-save" style="padding:0.45rem 1rem;border:none;border-radius:8px;background:var(--green,#2ea043);color:#fff;cursor:pointer;font-family:inherit;font-weight:600;">Save Workflow</button>'
        + '</div></div>';
    document.body.appendChild(modal);
    // Track mousedown origin to prevent closing when drag-selecting text inside the modal
    var _wfMouseDownOnBackdrop = false;
    modal.addEventListener('mousedown', function(e) { _wfMouseDownOnBackdrop = (e.target === modal); });
    modal.addEventListener('mouseup', function(e) {
        if (_wfMouseDownOnBackdrop && e.target === modal) modal.remove();
        _wfMouseDownOnBackdrop = false;
    });

    // Initialize schedule options visibility
    wfModalScheduleChanged();

    document.getElementById('wf-modal-save').onclick = async function() {
        var title = (document.getElementById('wf-modal-title').value || '').trim();
        var prompt = (document.getElementById('wf-modal-prompt').value || '').trim();
        var scheduleType = document.getElementById('wf-modal-schedule-type').value;
        var interval = parseInt(document.getElementById('wf-modal-interval').value) || 0;
        var time = document.getElementById('wf-modal-time').value || '08:00';
        var modelId = document.getElementById('wf-modal-model').value || '';
        if (!title || !prompt) return;

        this.disabled = true;
        this.textContent = 'Saving…';
        try {
            var resp = await fetch('/api/workflows', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title: title, prompt: prompt,
                    schedule_type: scheduleType,
                    schedule_interval: interval,
                    schedule_time: time,
                    model_id: modelId,
                }),
            });
            if (!resp.ok) throw new Error('Failed');
            modal.remove();
            if (typeof loadHomeWorkflows === 'function') loadHomeWorkflows(true);
        } catch (e) {
            this.disabled = false;
            this.textContent = 'Save Workflow';
            alert('Failed to save workflow: ' + e.message);
        }
    };
    document.getElementById('wf-modal-title').focus();
}

function wfModalScheduleChanged() {
    var type = document.getElementById('wf-modal-schedule-type').value;
    var intervalWrap = document.getElementById('wf-modal-interval-wrap');
    var timeWrap = document.getElementById('wf-modal-time-wrap');
    var optionsWrap = document.getElementById('wf-modal-schedule-options');
    if (type === 'manual') {
        optionsWrap.style.display = 'none';
    } else if (type === 'hours') {
        optionsWrap.style.display = '';
        intervalWrap.style.display = '';
        timeWrap.style.display = 'none';
    } else {
        optionsWrap.style.display = '';
        intervalWrap.style.display = 'none';
        timeWrap.style.display = '';
    }
}

// ── Workflows Tab ────────────────────────────────────────────────────────

var homeWorkflowsCache = null;

async function loadHomeWorkflows(force) {
    var container = document.getElementById('home-workflows-content');
    if (!container) return;
    if (!force && homeWorkflowsCache) {
        renderHomeWorkflows(homeWorkflowsCache, container);
        return;
    }
    // Try instant render from localStorage before showing spinner
    if (!force && typeof DataCache !== 'undefined') {
        var cached = DataCache.get('workflows');
        if (cached) {
            homeWorkflowsCache = cached;
            renderHomeWorkflows(cached, container);
            return;
        }
    }
    container.innerHTML = '<div class="home-loading"><span class="loading-dot">.</span><span class="loading-dot">.</span><span class="loading-dot">.</span></div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
    try {
        var data;
        if (force && typeof DataCache !== 'undefined') {
            data = await DataCache.refresh('workflows');
        } else if (typeof DataCache !== 'undefined') {
            data = await DataCache.getOrFetch('workflows');
        }
        if (!data) {
            var resp = await fetch('/api/workflows');
            data = await resp.json();
        }
        homeWorkflowsCache = data;
        renderHomeWorkflows(data, container);
    } catch (e) {
        container.innerHTML = '<div class="home-loading" style="color:var(--red);">Failed to load workflows</div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
    }
}

function renderHomeWorkflows(workflows, container) {
    if (!workflows || !workflows.length) {
        container.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--muted);font-size:0.8rem;">' // nosemgrep: insecure-innerhtml, insecure-document-method
            + '<svg style="width:28px;height:28px;color:var(--muted);opacity:0.4;margin:0 auto 0.5rem;" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182M20.016 4.656v4.992"/></svg>'
            + 'No workflows yet.<br><span style="font-size:0.72rem;opacity:0.7;">Create one from a chat response.</span></div>';
        return;
    }

    container.innerHTML = workflows.map(function(wf) { // nosemgrep: insecure-innerhtml, insecure-document-method
        var sched = wf.schedule_display || 'Manual';
        return '<div class="home-wf-row" data-wf-id="' + escHtml(wf.workflow_id) + '">'
            + '<div class="home-wf-row-top">'
            + '<span class="home-wf-row-title">' + escHtml(wf.title) + '</span>'
            + '<span class="home-wf-row-prompt">' + escHtml(wf.prompt) + '</span>'
            + '</div>'
            + '<div class="home-wf-row-foot">'
            + '<span class="home-wf-row-tag">🕐 ' + escHtml(sched) + '</span>'
            + '<span class="home-wf-row-tag">· ' + escHtml(getWorkflowModelLabel(wf.model_id)) + '</span>'
            + '<div class="home-wf-row-actions">'
            + '<button class="home-wf-action-btn" onclick="runWorkflowNow(\'' + escHtml(wf.workflow_id) + '\')" title="Run now">▶</button>'
            + '<button class="home-wf-action-btn" onclick="viewWorkflowResults(\'' + escHtml(wf.workflow_id) + '\')" title="View results">📋</button>'
            + '<button class="home-wf-action-btn home-wf-delete-btn" onclick="deleteWorkflow(\'' + escHtml(wf.workflow_id) + '\')" title="Delete">🗑</button>'
            + '</div></div></div>';
    }).join('');
}

async function runWorkflowNow(workflowId) {
    var card = document.querySelector('.home-wf-card[data-wf-id="' + workflowId + '"]');
    if (card) {
        var btn = card.querySelector('.home-wf-action-btn');
        if (btn) { btn.disabled = true; btn.textContent = '⏳'; }
    }
    try {
        var resp = await fetch('/api/workflows/' + workflowId + '/run', { method: 'POST' });
        if (!resp.ok) throw new Error('Run failed');
        var result = await resp.json();
        // Show result in a quick toast
        showWorkflowToast(result.status === 'success' ? '✅ Workflow completed' : '❌ Workflow failed', result.status);
        // Refresh the list
        loadHomeWorkflows(true);
    } catch (e) {
        showWorkflowToast('❌ ' + e.message, 'error');
    }
    if (card) {
        var btn = card.querySelector('.home-wf-action-btn');
        if (btn) { btn.disabled = false; btn.textContent = '▶'; }
    }
}

async function deleteWorkflow(workflowId) {
    if (!confirm('Delete this workflow and all its results?')) return;
    try {
        var resp = await fetch('/api/workflows/' + workflowId, { method: 'DELETE' });
        if (!resp.ok) throw new Error('Delete failed');
        homeWorkflowsCache = null;
        loadHomeWorkflows(true);
    } catch (e) {
        showWorkflowToast('❌ ' + e.message, 'error');
    }
}

async function viewWorkflowResults(workflowId) {
    var container = document.getElementById('home-workflows-content');
    if (!container) return;
    container.innerHTML = '<div class="home-loading"><span class="loading-dot">.</span><span class="loading-dot">.</span><span class="loading-dot">.</span></div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
    try {
        var resp = await fetch('/api/workflows/' + workflowId);
        if (!resp.ok) throw new Error('Not found');
        var data = await resp.json();
        renderWorkflowResults(data, container);
    } catch (e) {
        container.innerHTML = '<div class="home-loading" style="color:var(--red);">Failed to load results</div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
    }
}

function renderWorkflowResults(wfData, container) {
    var results = wfData.results || [];
    var backBtn = '<a href="javascript:void(0)" onclick="loadHomeWorkflows(true)" style="color:var(--accent);font-size:0.72rem;font-family:IBM Plex Mono,monospace;text-decoration:none;display:inline-flex;align-items:center;gap:0.3rem;margin-bottom:0.5rem;">'
        + '<svg style="width:12px;height:12px;" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M19 12H5M12 19l-7-7 7-7"/></svg> Back to workflows</a>';

    var header = '<div style="margin-bottom:0.5rem;">'
        + '<div style="font-size:0.85rem;font-weight:600;color:var(--text);font-family:IBM Plex Mono,monospace;">' + escHtml(wfData.title) + '</div>'
        + '<div style="font-size:0.72rem;color:var(--muted);margin-top:0.15rem;">' + escHtml(wfData.prompt) + '</div>'
        + '</div>';

    if (!results.length) {
        container.innerHTML = backBtn + header + '<div style="text-align:center;padding:1.5rem;color:var(--muted);font-size:0.8rem;">No results yet. Run the workflow to see results here.</div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
        return;
    }

    var html = backBtn + header + results.map(function(r) {
        var ts = r.timestamp ? new Date(r.timestamp).toLocaleString() : '';
        var tokens = (r.input_tokens || 0) + ' in / ' + (r.output_tokens || 0) + ' out';
        var latency = r.latency_ms ? (r.latency_ms / 1000).toFixed(1) + 's' : '';
        var fullMd = r.response_md || '';
        var pri = r.priority || 'normal';
        var errorBadge = r.status !== 'success' ? '<span style="color:var(--red);font-size:0.65rem;font-weight:600;">✕ error</span> ' : '';

        var mainMd = fullMd, followupHtml = '';
        var fuMatch = fullMd.match(/-*\s*FOLLOWUPS\s*-+/i);
        var fIdx = fuMatch ? fuMatch.index : -1;
        if (fIdx !== -1) {
            mainMd = fullMd.substring(0, fIdx).trim();
            var fLines = fullMd.substring(fIdx + fuMatch[0].length).trim().split('\n').filter(function(l){return l.trim();});
            if (fLines.length) {
                var questions = [], actions = [];
                fLines.forEach(function(l) {
                    var t = l.trim();
                    if (t.match(/^A\d+:/)) actions.push(t.replace(/^A\d+:\s*/, ''));
                    else questions.push(t.replace(/^Q\d+:\s*/, ''));
                });
                followupHtml = '<div class="home-followup-chips" style="margin-top:0.5rem;padding-top:0.5rem;">';
                if (questions.length) {
                    followupHtml += '<div class="home-followup-heading">Explore Further</div>';
                    questions.forEach(function(q) { followupHtml += '<span class="home-suggestion-chip">' + escHtml(q) + '</span>'; });
                }
                if (actions.length) {
                    followupHtml += '<div class="home-followup-heading home-action-heading">Recommended Actions</div>';
                    actions.forEach(function(a) { followupHtml += '<span class="home-suggestion-chip home-action-chip">⚡ ' + escHtml(a) + '</span>'; });
                }
                followupHtml += '</div>';
            }
        }

        var rendered = typeof marked !== 'undefined'
            ? (typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(marked.parse(mainMd)) : marked.parse(mainMd))
            : escHtml(mainMd);

        return '<div class="home-rr-row" data-priority="' + escHtml(pri) + '">'
            + '<details class="home-wf-res-exp"><summary class="home-rr-row-head" style="cursor:pointer;list-style:none;">'
            + '<svg class="wf-result-chevron" style="width:11px;height:11px;transition:transform 0.15s;flex-shrink:0;color:var(--muted);" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"/></svg>'
            + errorBadge
            + '<span class="home-rr-priority"' + (pri === 'urgent' ? ' data-p="urgent"' : '') + '>' + escHtml(pri) + '</span>'
            + '<span class="home-rr-ts">' + ts + '</span>'
            + '</summary>'
            + '<div class="home-rr-response">' + rendered + '</div>' + followupHtml
            + '<div class="home-rr-stats"><span>' + tokens + '</span>' + (latency ? '<span>· ' + latency + '</span>' : '') + '</div>'
            + '</details>'
            + '</div>';
    }).join('');

    container.innerHTML = html; // nosemgrep: insecure-innerhtml, insecure-document-method

    container.querySelectorAll('.home-wf-res-exp').forEach(function(d) {
        // Chevron rotation handled by CSS [open] selector
    });
}

function showWorkflowToast(message, type) {
    var toast = document.createElement('div');
    toast.style.cssText = 'position:fixed;bottom:1.5rem;right:1.5rem;z-index:9999;padding:0.6rem 1rem;border-radius:8px;font-size:0.82rem;font-family:IBM Plex Mono,monospace;color:#fff;animation:fadeIn 0.25s ease-out;'
        + (type === 'error' ? 'background:var(--red,#f85149);' : 'background:var(--green,#2ea043);');
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(function() { toast.style.opacity = '0'; toast.style.transition = 'opacity 0.3s'; }, 2500);
    setTimeout(function() { toast.remove(); }, 3000);
}

// ── Workflow sub-tabs ────────────────────────────────────────────────────
var _wfSubtab = 'list';
function switchWfSubtab(tab) {
    _wfSubtab = tab;
    document.querySelectorAll('.home-wf-subtab').forEach(function(b) {
        var active = b.dataset.wfsub === tab;
        b.style.borderBottomColor = active ? 'var(--accent)' : 'transparent';
        b.style.color = active ? 'var(--accent)' : 'var(--muted)';
    });
    document.getElementById('home-wf-sub-list').style.display = tab === 'list' ? '' : 'none';
    document.getElementById('home-wf-sub-results').style.display = tab === 'results' ? '' : 'none';
    if (tab === 'results') loadHomeRecentResults();
}

var _recentResultsCache = null;
async function loadHomeRecentResults(force) {
    var container = document.getElementById('home-recent-results-content');
    if (!container) return;
    if (!force && _recentResultsCache) { renderHomeRecentResults(_recentResultsCache, container); return; }
    container.innerHTML = '<div class="home-loading"><span class="loading-dot">.</span><span class="loading-dot">.</span><span class="loading-dot">.</span></div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
    try {
        var resp = await fetch('/api/workflows/recent-results');
        var data = await resp.json();
        _recentResultsCache = data;
        renderHomeRecentResults(data, container);
    } catch (e) {
        container.innerHTML = '<div class="home-loading" style="color:var(--red);">Failed to load results</div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
    }
}

function renderHomeRecentResults(results, container) {
    if (!results || !results.length) {
        container.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--muted);font-size:0.8rem;">No results yet.<br><span style="font-size:0.72rem;opacity:0.7;">Run a workflow to see results here</span></div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
        return;
    }
    container.innerHTML = results.map(function(r) { // nosemgrep: insecure-innerhtml, insecure-document-method
        var ts = r.timestamp ? new Date(r.timestamp).toLocaleString('en-US',{month:'2-digit',day:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'}) : '';
        var md = r.response_md || '';
        var mainMd = md;
        var fuMatch = md.match(/-*\s*FOLLOWUPS\s*-+/i);
        if (fuMatch) mainMd = md.substring(0, fuMatch.index).trim();
        var rendered = typeof marked !== 'undefined' ? (typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(marked.parse(mainMd)) : marked.parse(mainMd)) : escHtml(mainMd);
        var tok = (r.input_tokens||0)+' in / '+(r.output_tokens||0)+' out';
        var lat = r.latency_ms ? (r.latency_ms/1000).toFixed(1)+'s' : '';
        var pri = r.priority || 'normal';
        var errorBadge = r.status !== 'success' ? '<span style="color:var(--red);font-size:0.65rem;font-weight:600;">✕ error</span> ' : '';
        return '<div class="home-rr-row" data-priority="' + escHtml(pri) + '">'
            + '<details class="home-rr-exp"><summary class="home-rr-row-head" style="cursor:pointer;list-style:none;">'
            + '<svg class="rr-chev" style="width:11px;height:11px;transition:transform 0.15s;flex-shrink:0;color:var(--muted);" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"/></svg>'
            + errorBadge
            + '<span class="home-rr-row-title">' + escHtml(r.workflow_title || '') + '</span>'
            + '<span class="home-rr-priority"' + (pri === 'urgent' ? ' data-p="urgent"' : '') + '>' + escHtml(pri) + '</span>'
            + '<span class="home-rr-ts">' + ts + '</span>'
            + '</summary>'
            + '<div style="font-size:0.7rem;color:var(--muted);font-style:italic;padding:0.3rem 0 0.15rem;border-bottom:1px solid var(--border);margin-bottom:0.3rem;">' + escHtml(r.workflow_prompt || '') + '</div>'
            + '<div class="home-rr-response">' + rendered + '</div>'
            + '<div class="home-rr-stats"><span>' + tok + '</span>' + (lat ? '<span>· ' + lat + '</span>' : '') + '</div>'
            + '</details>'
            + '</div>';
    }).join('');
    container.querySelectorAll('.home-rr-exp').forEach(function(d) {
        // Chevron rotation handled by CSS [open] selector
    });
}
