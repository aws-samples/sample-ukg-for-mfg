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
// Shared Model List — single source of truth for all pages
// ============================================================================

var SHARED_MODELS = [
    { id: "global.anthropic.claude-opus-4-6-v1", name: "Claude Opus 4.6", description: "IN [$5.00] - OUT [$25.00]" },
    { id: "global.anthropic.claude-sonnet-4-6", name: "Claude Sonnet 4.6", description: "IN [$3.00] - OUT [$15.00]" },
    { id: "global.anthropic.claude-opus-4-5-20251101-v1:0", name: "Claude Opus 4.5", description: "IN [$5.00] - OUT [$25.00]" },
    { id: "global.anthropic.claude-sonnet-4-5-20250929-v1:0", name: "Claude Sonnet 4.5", description: "IN [$3.00] - OUT [$15.00]" },
    { id: "global.anthropic.claude-haiku-4-5-20251001-v1:0", name: "Claude Haiku 4.5", description: "IN [$1.00] - OUT [$5.00]" },
];

var SHARED_DEFAULT_MODEL_ID = "global.anthropic.claude-sonnet-4-6";

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
var SHARED_DEFAULT_RUNTIME_MODEL = "global.anthropic.claude-sonnet-4-6";

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