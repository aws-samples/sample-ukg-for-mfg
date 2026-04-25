/**
 * Data Discovery Page — mfg-digital-thread
 * System explorer + Discovery Agent chat.
 */

let discStreaming = false;
let discLastMetadata = null;
const DISC_SESSION_KEY = 'disc-session-id';
const DISC_MODEL_KEY = 'agentcore-selected-model'; // shared with chat/home

// Same model list as home.js / chat.js
const DISC_MODELS = [
    { id: "global.anthropic.claude-opus-4-6-v1", name: "Claude Opus 4.6" },
    { id: "global.anthropic.claude-sonnet-4-6", name: "Claude Sonnet 4.6" },
    { id: "global.anthropic.claude-opus-4-5-20251101-v1:0", name: "Claude Opus 4.5" },
    { id: "global.anthropic.claude-sonnet-4-5-20250929-v1:0", name: "Claude Sonnet 4.5" },
    { id: "global.anthropic.claude-haiku-4-5-20251001-v1:0", name: "Claude Haiku 4.5" },
    { id: "us.amazon.nova-pro-v1:0", name: "Nova Pro" },
    { id: "global.amazon.nova-2-lite-v1:0", name: "Nova 2 Lite" },
];
const DISC_DEFAULT_MODEL = "global.amazon.nova-2-lite-v1:0";

function discGetModel() {
    var stored = localStorage.getItem(DISC_MODEL_KEY);
    if (stored) {
        var m = DISC_MODELS.find(function(x) { return x.id === stored; });
        if (m) return m.id;
    }
    return DISC_DEFAULT_MODEL;
}

function discSetModel(modelId) {
    localStorage.setItem(DISC_MODEL_KEY, modelId);
}

function discInitModelSelector() {
    var sel = document.getElementById('disc-model-select');
    if (!sel) return;
    var current = discGetModel();
    sel.innerHTML = DISC_MODELS.map(function(m) { // nosemgrep: insecure-innerhtml, insecure-document-method
        return '<option value="' + m.id + '"' + (m.id === current ? ' selected' : '') + '>' + m.name + '</option>';
    }).join('');
    sel.addEventListener('change', function() { discSetModel(this.value); });
}

function discSessionId() {
    var id = localStorage.getItem(DISC_SESSION_KEY);
    if (!id || id.length !== 36) {
        id = 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
            var r = Math.random() * 16 | 0;
            var v = c === 'x' ? r : (r & 0x3 | 0x8);
            return v.toString(16);
        });
        localStorage.setItem(DISC_SESSION_KEY, id);
        console.log('[discover] New session created:', id);
    }
    return id;
}

// escH — delegates to global escapeHTML from utils.js
var escH = escapeHTML;

// ── System Explorer (shared component) ───────────────────────────────────────
var discExplorer = new SystemExplorer({
    listEl: document.getElementById('disc-systems-list'),
    countEl: document.getElementById('disc-sys-count'),
    detailEl: document.getElementById('disc-detail'),
    detailHeaderEl: document.getElementById('disc-detail-header'),
    detailBodyEl: document.getElementById('disc-detail-body'),
    hideOnDrill: [
        document.querySelector('.disc-systems'),
        document.querySelector('.disc-guide'),
    ],
    showStats: true,
    statsEls: {
        systems: document.getElementById('disc-gs-systems'),
        concepts: document.getElementById('disc-gs-concepts'),
        equivs: document.getElementById('disc-gs-equivs'),
    },
});

// Legacy aliases so inline onclick handlers and chat refresh still work
function discLoadSystems(forceRefresh) { return discExplorer.load(forceRefresh); }

// ── Chat with Discovery Agent ────────────────────────────────────────────────
function discAskAgent(question) {
    var q = question.replace(/^"|"$/g, '');
    document.getElementById('disc-input').value = q;
    discSendChat(new Event('submit'));
}

function discNewChat() {
    localStorage.removeItem(DISC_SESSION_KEY);
    var newId = discSessionId(); // generate fresh UUID
    console.log('[discover] New chat started, session:', newId);

    // Visual feedback on the New button
    var btn = document.querySelector('.disc-chat-new');
    if (btn) {
        var origText = btn.innerHTML; // nosemgrep: insecure-innerhtml, insecure-document-method
        btn.innerHTML = '<svg style="width:12px;height:12px;" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" /></svg> Created'; // nosemgrep: insecure-innerhtml, insecure-document-method
        btn.style.color = 'var(--green)';
        btn.style.borderColor = 'var(--green)';
        setTimeout(function() {
            btn.innerHTML = origText; // nosemgrep: insecure-innerhtml, insecure-document-method
            btn.style.color = '';
            btn.style.borderColor = '';
        }, 1200);
    }

    var msgs = document.getElementById('disc-messages');
    msgs.innerHTML = '<div class="disc-welcome" id="disc-welcome">' // nosemgrep: insecure-innerhtml, insecure-document-method
        + '<svg style="width:36px;height:36px;color:var(--accent);opacity:0.4;" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/><path d="M11 8v6M8 11h6"/></svg>'
        + '<div style="font-size:1.1rem;font-weight:600;color:var(--text);">Data Discovery Agent</div>'
        + '<div style="font-size:0.82rem;color:var(--muted);max-width:400px;line-height:1.5;">I can help you explore manufacturing systems, understand data relationships, discover field mappings, and analyze how systems connect.</div>'
        + '</div>';
    document.getElementById('disc-suggestions').style.display = '';
}

async function discSendChat(event) {
    if (event) event.preventDefault();
    var input = document.getElementById('disc-input');
    var msg = (input.value || '').trim();
    if (!msg || discStreaming) return;
    input.value = '';

    var welcome = document.getElementById('disc-welcome');
    if (welcome) welcome.remove();
    document.getElementById('disc-suggestions').style.display = 'none';

    var msgs = document.getElementById('disc-messages');
    msgs.innerHTML += '<div class="disc-msg user">' + escH(msg) + '</div>'; // nosemgrep: insecure-innerhtml, insecure-document-method

    var aId = 'disc-a-' + Date.now();
    msgs.innerHTML += '<div class="disc-msg agent" id="' + aId + '"><div class="disc-agent-label">Agent</div><div class="disc-agent-content"><span class="disc-chat-streaming"></span></div></div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
    msgs.scrollTop = msgs.scrollHeight;

    discStreaming = true;
    discLastMetadata = null;
    document.getElementById('disc-chat-loading').style.display = '';

    try {
        var sessionId = discSessionId();
        var modelId = discGetModel();
        var resp = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt: msg, session_id: sessionId, agent_mode: 'discovery', model_id: modelId }),
        });

        if (!resp.ok) {
            var errText = '';
            try { var errData = await resp.json(); errText = errData.detail || resp.statusText; } catch(_) { errText = resp.statusText; }
            throw new Error(resp.status + ': ' + errText);
        }

        var reader = resp.body.getReader();
        var decoder = new TextDecoder();
        var content = '';
        var el = document.getElementById(aId);
        var contentEl = el ? el.querySelector('.disc-agent-content') : null;
        var buffer = '';

        while (true) {
            var result = await reader.read();
            if (result.done) break;
            buffer += decoder.decode(result.value, { stream: true });

            // Process complete lines only (SSE lines end with \n)
            var parts = buffer.split('\n');
            buffer = parts.pop() || ''; // keep incomplete last line in buffer

            for (var i = 0; i < parts.length; i++) {
                var line = parts[i];
                if (!line.startsWith('data: ')) continue;
                var raw = line.slice(6);
                if (raw === '[DONE]') continue;
                try {
                    var data = JSON.parse(raw);
                    if (data.type === 'message' && data.content) {
                        content += data.content;
                        if (contentEl) {
                            var rendered = typeof marked !== 'undefined'
                                ? (typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(marked.parse(content)) : marked.parse(content))
                                : escH(content);
                            contentEl.innerHTML = rendered + '<span class="disc-chat-streaming"></span>'; // nosemgrep: insecure-innerhtml, insecure-document-method
                        }
                        msgs.scrollTop = msgs.scrollHeight;
                    } else if (data.type === 'tool_use') {
                        var toolUseId = data.tool_use_id || 'disc-tool-' + Date.now();
                        // Format tool input for display
                        var toolInput = data.tool_input || {};
                        var inputDisplay = Object.keys(toolInput).map(function(k) {
                            var v = toolInput[k];
                            if (typeof v === 'object') v = JSON.stringify(v);
                            return escH(k) + ': ' + escH(String(v));
                        }).join('\n') || 'No input';
                        var inputJson = JSON.stringify(toolInput, null, 2);

                        // Build a tool card matching the /chat page style
                        var toolEl = document.createElement('div');
                        toolEl.className = 'disc-tool-card';
                        toolEl.setAttribute('data-tool-id', toolUseId);
                        toolEl.setAttribute('data-tool-name', data.tool_name || 'tool');
                        toolEl.innerHTML = // nosemgrep: insecure-innerhtml, insecure-document-method
                            '<button class="disc-tool-header" onclick="this.parentElement.classList.toggle(\'open\')">'
                            + '<span class="disc-tool-dot"></span>'
                            + '<span class="disc-tool-label">Tool:</span>'
                            + '<span class="disc-tool-name">' + escH(data.tool_name || 'tool') + '</span>'
                            + '<span class="disc-tool-status">Running…</span>'
                            + '<svg class="disc-tool-chevron" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M19 9l-7 7-7-7"/></svg>'
                            + '</button>'
                            + '<div class="disc-tool-details">'
                            + '<div class="disc-tool-section">'
                            + '<div class="disc-tool-section-hdr">⚡ Input Parameters</div>'
                            + '<pre class="disc-tool-pre"><code>' + escH(inputDisplay) + '</code></pre>'
                            + '</div>'
                            + '<div class="disc-tool-result-section" style="display:none;">'
                            + '<div class="disc-tool-section-hdr">✓ Tool Result</div>'
                            + '<pre class="disc-tool-pre disc-tool-result"><code>Waiting…</code></pre>'
                            + '</div>'
                            + '</div>';

                        // Insert tool card before the agent message element
                        // Collect all tool cards for this message into a container
                        var toolsWrap = el.previousElementSibling;
                        if (!toolsWrap || !toolsWrap.classList.contains('disc-tools-wrap')) {
                            toolsWrap = document.createElement('div');
                            toolsWrap.className = 'disc-tools-wrap';
                            msgs.insertBefore(toolsWrap, el);
                        }
                        // Insert tool card before the toggle button (if it exists) so button stays at bottom
                        var existingBtn = toolsWrap.querySelector('.disc-tools-more');
                        if (existingBtn) {
                            toolsWrap.insertBefore(toolEl, existingBtn);
                        } else {
                            toolsWrap.appendChild(toolEl);
                        }

                        // Apply maxVisibleTools collapse (show first 2, hide rest)
                        var allCards = toolsWrap.querySelectorAll('.disc-tool-card');
                        var MAX_VISIBLE = 2;
                        if (allCards.length > MAX_VISIBLE) {
                            // Only collapse if not already expanded
                            if (!toolsWrap.dataset.expanded) {
                                for (var ti = 0; ti < allCards.length; ti++) {
                                    allCards[ti].style.display = ti < MAX_VISIBLE ? '' : 'none';
                                }
                            }
                            // Add or update toggle button
                            var moreBtn = toolsWrap.querySelector('.disc-tools-more');
                            var hiddenCount = allCards.length - MAX_VISIBLE;
                            if (!moreBtn) {
                                moreBtn = document.createElement('button');
                                moreBtn.className = 'disc-tools-more';
                                moreBtn.onclick = function() {
                                    var wrap = this.parentElement;
                                    var cards = wrap.querySelectorAll('.disc-tool-card');
                                    var isExpanded = wrap.dataset.expanded === '1';
                                    if (isExpanded) {
                                        // Collapse: hide cards beyond MAX_VISIBLE
                                        for (var j = 0; j < cards.length; j++) {
                                            cards[j].style.display = j < MAX_VISIBLE ? '' : 'none';
                                        }
                                        wrap.dataset.expanded = '';
                                        var hc = cards.length - MAX_VISIBLE;
                                        this.textContent = 'Show ' + hc + ' more tool' + (hc > 1 ? 's' : '') + '\u2026';
                                    } else {
                                        // Expand: show all
                                        cards.forEach(function(c) { c.style.display = ''; });
                                        wrap.dataset.expanded = '1';
                                        this.textContent = 'Show less';
                                    }
                                };
                            }
                            // Always ensure button is the last child
                            toolsWrap.appendChild(moreBtn);
                            if (!toolsWrap.dataset.expanded) {
                                moreBtn.textContent = 'Show ' + hiddenCount + ' more tool' + (hiddenCount > 1 ? 's' : '') + '\u2026';
                            }
                        }

                        msgs.scrollTop = msgs.scrollHeight;
                    } else if (data.type === 'tool_result') {
                        // Find the matching tool card and update it
                        var resultId = data.tool_use_id;
                        var toolsWraps = msgs.querySelectorAll('.disc-tools-wrap');
                        var matchCard = null;
                        if (resultId) {
                            for (var wi = toolsWraps.length - 1; wi >= 0 && !matchCard; wi--) {
                                matchCard = toolsWraps[wi].querySelector('[data-tool-id="' + resultId + '"]');
                            }
                        }
                        if (!matchCard) {
                            // Fallback: last tool card without .done
                            var allToolCards = msgs.querySelectorAll('.disc-tool-card:not(.done)');
                            if (allToolCards.length) matchCard = allToolCards[allToolCards.length - 1];
                        }
                        if (matchCard) {
                            var isError = isToolResultError(data.tool_result);

                            matchCard.classList.add('done');
                            var statusEl = matchCard.querySelector('.disc-tool-status');
                            var dotEl = matchCard.querySelector('.disc-tool-dot');
                            if (isError) {
                                matchCard.style.borderColor = 'rgba(248,81,73,0.3)';
                                matchCard.style.background = 'rgba(248,81,73,0.06)';
                                if (dotEl) dotEl.style.background = 'var(--red)';
                                if (statusEl) { statusEl.textContent = 'Error'; statusEl.style.color = 'var(--red)'; }
                            } else {
                                if (statusEl) statusEl.textContent = 'Completed';
                                if (dotEl) dotEl.style.background = 'var(--green)';
                            }
                            // Show result
                            var resultSection = matchCard.querySelector('.disc-tool-result-section');
                            var resultPre = matchCard.querySelector('.disc-tool-result code');
                            if (resultSection && resultPre && data.tool_result) {
                                resultSection.style.display = '';
                                var resultText = data.tool_result;
                                if (typeof resultText === 'object') resultText = JSON.stringify(resultText, null, 2);
                                // Format as key-value if it's JSON
                                try {
                                    var parsed = typeof data.tool_result === 'string' ? JSON.parse(data.tool_result) : data.tool_result;
                                    resultText = Object.keys(parsed).map(function(k) {
                                        var v = parsed[k];
                                        if (typeof v === 'object') v = JSON.stringify(v);
                                        return k + ': ' + v;
                                    }).join('\n');
                                } catch(_) {}
                                resultPre.textContent = resultText;
                            }
                        }
                        // Auto-refresh sidebar when a system is registered
                        if (matchCard && matchCard.getAttribute('data-tool-name') === 'register_system') {
                            discLoadSystems(true);
                        }
                    } else if (data.type === 'metadata') {
                        // Capture token usage metadata
                        var usageData = data.data || data.usage || data;
                        if (usageData && (usageData.inputTokens !== undefined || usageData.outputTokens !== undefined || usageData.latencyMs !== undefined)) {
                            discLastMetadata = usageData;
                        }
                    }
                } catch (_) {}
            }
        }

        if (contentEl && !content) contentEl.innerHTML = '<span style="color:var(--muted);font-style:italic;">No response</span>'; // nosemgrep: insecure-innerhtml, insecure-document-method
        // Remove streaming cursor when done
        if (contentEl) {
            var cur = contentEl.querySelector('.disc-chat-streaming');
            if (cur) cur.remove();
        }

        // Add message footer: feedback, copy, timestamp, metrics
        if (el) {
            discFinalizeMessage(el, aId, content, msg, discLastMetadata);
        }
    } catch (e) {
        var el2 = document.getElementById(aId);
        var ce = el2 ? el2.querySelector('.disc-agent-content') : null;
        if (ce) ce.innerHTML = '<span style="color:var(--red);">Error: ' + escH(e.message) + '</span>'; // nosemgrep: insecure-innerhtml, insecure-document-method
    }

    discStreaming = false;
    document.getElementById('disc-chat-loading').style.display = 'none';
    msgs.scrollTop = msgs.scrollHeight;

    // Refresh systems after agent interaction (may have registered new ones)
    discLoadSystems(true);
}

// ── Init ─────────────────────────────────────────────────────────────────────
discInitModelSelector();
discExplorer.load();
// Log session on page load
console.log('[discover] Session initialized:', discSessionId());

// ── Message Footer: Feedback, Copy, Timestamp, Metrics ───────────────────────

const discFeedbackStore = new Map();

function discFinalizeMessage(el, msgId, content, userMsg, metadata) {
    // Build footer row
    var footer = document.createElement('div');
    footer.className = 'disc-msg-footer';
    footer.style.cssText = 'display:flex;align-items:center;justify-content:space-between;margin-top:0.35rem;padding:0 0.25rem;font-size:0.75rem;color:var(--muted);';

    // Left: feedback + timestamp
    var left = document.createElement('div');
    left.style.cssText = 'display:flex;align-items:center;gap:0.5rem;';

    // Feedback buttons
    var fbWrap = document.createElement('span');
    fbWrap.style.cssText = 'display:flex;gap:0.15rem;';
    var thumbUp = document.createElement('button');
    thumbUp.innerHTML = '&#x1F44D;'; // nosemgrep: insecure-innerhtml, insecure-document-method
    thumbUp.title = 'Helpful';
    thumbUp.style.cssText = 'background:none;border:none;cursor:pointer;font-size:0.85rem;opacity:0.5;padding:2px;transition:opacity 0.15s;';
    thumbUp.onmouseover = function(){ this.style.opacity='1'; };
    thumbUp.onmouseout = function(){ if(!this.dataset.selected) this.style.opacity='0.5'; };
    thumbUp.onclick = function(){ discSubmitFeedback(msgId, 'positive', null); discMarkFeedback(fbWrap, 'positive'); };

    var thumbDown = document.createElement('button');
    thumbDown.innerHTML = '&#x1F44E;'; // nosemgrep: insecure-innerhtml, insecure-document-method
    thumbDown.title = 'Not helpful';
    thumbDown.style.cssText = 'background:none;border:none;cursor:pointer;font-size:0.85rem;opacity:0.5;padding:2px;transition:opacity 0.15s;';
    thumbDown.onmouseover = function(){ this.style.opacity='1'; };
    thumbDown.onmouseout = function(){ if(!this.dataset.selected) this.style.opacity='0.5'; };
    thumbDown.onclick = function(){ discShowFeedbackModal(msgId, fbWrap); };

    fbWrap.appendChild(thumbUp);
    fbWrap.appendChild(thumbDown);
    left.appendChild(fbWrap);

    // Timestamp
    var ts = document.createElement('span');
    ts.textContent = new Date().toLocaleTimeString();
    left.appendChild(ts);
    footer.appendChild(left);

    // Right: metrics + copy
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

    // Copy button
    var copyBtn = document.createElement('button');
    copyBtn.innerHTML = '&#x1F4CB;'; // nosemgrep: insecure-innerhtml, insecure-document-method
    copyBtn.title = 'Copy response';
    copyBtn.style.cssText = 'background:none;border:none;cursor:pointer;font-size:0.85rem;opacity:0.5;padding:2px;transition:opacity 0.15s;';
    copyBtn.onmouseover = function(){ this.style.opacity='1'; };
    copyBtn.onmouseout = function(){ this.style.opacity='0.5'; };
    copyBtn.onclick = function(){
        var contentEl = el.querySelector('.disc-agent-content');
        var text = contentEl ? contentEl.textContent : content;
        navigator.clipboard.writeText(text).then(function(){
            copyBtn.innerHTML = '&#x2705;'; // nosemgrep: insecure-innerhtml, insecure-document-method
            setTimeout(function(){ copyBtn.innerHTML = '&#x1F4CB;'; }, 2000); // nosemgrep: insecure-innerhtml, insecure-document-method
        });
    };
    right.appendChild(copyBtn);
    footer.appendChild(right);

    el.appendChild(footer);

    // Store context for feedback
    discFeedbackStore.set(msgId, { userMessage: userMsg, assistantResponse: content });
}

function discMarkFeedback(fbWrap, sentiment) {
    var btns = fbWrap.querySelectorAll('button');
    btns.forEach(function(b){ b.style.opacity = '0.3'; b.style.pointerEvents = 'none'; b.dataset.selected = ''; });
    var idx = sentiment === 'positive' ? 0 : 1;
    if (btns[idx]) { btns[idx].style.opacity = '1'; btns[idx].dataset.selected = '1'; }
}

function discSubmitFeedback(msgId, sentiment, comment) {
    var ctx = discFeedbackStore.get(msgId) || {};
    fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            message_id: msgId,
            session_id: discSessionId(),
            user_message: ctx.userMessage || '',
            assistant_response: ctx.assistantResponse || '',
            tools_used: [],
            sentiment: sentiment,
            user_comment: comment
        })
    }).catch(function(e){ console.error('Feedback error:', e); });
}

function discShowFeedbackModal(msgId, fbWrap) {
    var existing = document.getElementById('disc-feedback-modal');
    if (existing) existing.remove();
    var modal = document.createElement('div');
    modal.id = 'disc-feedback-modal';
    modal.style.cssText = 'position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.5);';
    modal.innerHTML = '<div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:1.5rem;max-width:400px;width:90%;">' // nosemgrep: insecure-innerhtml, insecure-document-method
        + '<h3 style="margin:0 0 0.5rem;color:var(--text);font-size:1rem;">Share your feedback</h3>'
        + '<textarea id="disc-fb-comment" rows="3" style="width:100%;padding:0.5rem;border:1px solid var(--border);border-radius:8px;background:var(--surface2);color:var(--text);resize:none;font-family:inherit;" placeholder="What would have made this better?"></textarea>'
        + '<div style="display:flex;justify-content:flex-end;gap:0.5rem;margin-top:0.75rem;">'
        + '<button onclick="document.getElementById(\'disc-feedback-modal\').remove()" style="padding:0.4rem 1rem;border:1px solid var(--border);border-radius:8px;background:none;color:var(--text);cursor:pointer;">Cancel</button>'
        + '<button id="disc-fb-submit" style="padding:0.4rem 1rem;border:none;border-radius:8px;background:var(--accent);color:#fff;cursor:pointer;">Submit</button>'
        + '</div></div>';
    document.body.appendChild(modal);
    modal.onclick = function(e){ if(e.target === modal) modal.remove(); };
    document.getElementById('disc-fb-submit').onclick = function(){
        var comment = (document.getElementById('disc-fb-comment').value || '').trim();
        modal.remove();
        discSubmitFeedback(msgId, 'negative', comment || null);
        discMarkFeedback(fbWrap, 'negative');
    };
    document.getElementById('disc-fb-comment').focus();
}
