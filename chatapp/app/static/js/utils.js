/**
 * Shared utility functions for the chatapp.
 * Loaded globally from base.html.
 */

// ============================================================================
// XSS Prevention Helpers
// ============================================================================

/**
 * Sanitize an HTML fragment with DOMPurify if available; otherwise
 * fall back to escaping the string entirely (safe default).
 * @param {string} html
 * @returns {string}
 */
function safeHTML(html) {
    if (html == null) return '';
    var s = String(html);
    if (typeof window.DOMPurify !== 'undefined' && window.DOMPurify.sanitize) {
        return window.DOMPurify.sanitize(s);
    }
    return escapeHTML(s);
}

/**
 * Escape HTML-special characters for safe text interpolation.
 * Uses the browser's own text encoding via DOM for correctness.
 * @param {string} str
 * @returns {string}
 */
function escapeHTML(str) {
    if (str == null) return '';
    var div = document.createElement('div');
    div.textContent = String(str);
    return div.innerHTML; // nosemgrep: insecure-innerhtml, insecure-document-method
}

/**
 * Escape a value for use inside an HTML attribute.
 * @param {string} str
 * @returns {string}
 */
function escapeAttr(str) {
    return escapeHTML(str);
}

// ============================================================================
// Shared Model List — hydrated from /api/models at page load
// ============================================================================
//
// The server-side cost_calculator.MODELS is the single source of truth for
// model ids, display names, and pricing. The frontend fetches it once at
// script load so dropdowns never drift from the Python registry.
//
// Consumers that need the list synchronously can either:
//   * read `SHARED_MODELS` (empty until hydration completes, then populated),
//   * or await `window.modelsReady` (preferred for code that runs early).

var SHARED_MODELS = [];
var SHARED_DEFAULT_MODEL_ID = "";
// Historical alias — workflows use the same default, keep both names so
// existing callers don't break.
var SHARED_DEFAULT_RUNTIME_MODEL = "";

/**
 * Fetch the canonical model list from the server.
 * Populates the module-level caches and resolves `window.modelsReady`.
 * If the fetch fails (offline, logged out, etc.) we leave the caches empty
 * — callers should treat an empty list as "no selection available" and
 * fall back to the server-side default by omitting model_id from the
 * request payload.
 */
window.modelsReady = (async function hydrateModels() {
    try {
        var resp = await fetch('/api/models', { credentials: 'same-origin' });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        var data = await resp.json();
        if (Array.isArray(data.models)) {
            // Mutate in place so aliases like `HOME_MODELS = SHARED_MODELS`
            // observe the hydrated entries without needing re-assignment.
            SHARED_MODELS.length = 0;
            Array.prototype.push.apply(SHARED_MODELS, data.models);
        }
        if (typeof data.default_model_id === 'string') {
            SHARED_DEFAULT_MODEL_ID = data.default_model_id;
            SHARED_DEFAULT_RUNTIME_MODEL = data.default_model_id;
        }
    } catch (err) {
        console.warn('Failed to hydrate model list from /api/models:', err);
    }
    return { models: SHARED_MODELS, defaultId: SHARED_DEFAULT_MODEL_ID };
})();

/** Build <option> HTML for a model <select>. includeBlank adds an empty first option. */
function buildModelOptions(selectedId, includeBlank) {
    var html = '';
    if (includeBlank) html += '<option value="">— Default —</option>';
    SHARED_MODELS.forEach(function(m) {
        html += '<option value="' + m.id + '"' + (m.id === selectedId ? ' selected' : '') + '>' + m.name + '</option>';
    });
    return html;
}

/** Look up a model name by ID. Returns short name or the raw ID. */
function getModelName(modelId) {
    if (!modelId) return '';
    var m = SHARED_MODELS.find(function(x) { return x.id === modelId; });
    return m ? m.name : modelId.split('.').pop();
}

/** Get display label for a workflow's model. Shows "(default)" if none was explicitly set. */
function getWorkflowModelLabel(modelId) {
    if (modelId) return getModelName(modelId);
    return getModelName(SHARED_DEFAULT_RUNTIME_MODEL) + ' (default)';
}

// ============================================================================
// Tool Result Error Detection
// ============================================================================

/**
 * Check if a tool result indicates an error.
 * Works with both string (key-value formatted) and object results.
 *
 * @param {string|object} toolResult - The tool result to check
 * @returns {boolean} True if the result indicates an error
 */
function isToolResultError(toolResult) {
    if (!toolResult) return false;

    if (typeof toolResult === 'string') {
        try {
            var parsed = JSON.parse(toolResult);
            if (parsed && parsed.success === false) return true;
            if (parsed && parsed.error_type) return true;
        } catch (_) {}
        var lr = toolResult.toLowerCase();
        return lr.includes('"success": false') ||
            lr.includes("'success': false") ||
            lr.includes('success: false') ||
            lr.includes('error_type:') ||
            lr.includes('"error_type"');
    }

    if (typeof toolResult === 'object') {
        if (toolResult.success === false) return true;
        if (toolResult.error_type) return true;
        if (toolResult.error !== undefined && toolResult.error !== null) return true;
        if (toolResult.isError === true) return true;
        if (toolResult.status === 'error' || toolResult.status === 'failed') return true;
    }

    return false;
}