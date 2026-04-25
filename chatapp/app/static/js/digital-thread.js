/**
 * Digital Thread sidebar JavaScript.
 *
 * Handles theme toggling, tab switching, data loading, and rendering
 * for the Digital Thread sidebar (Systems + Vocabulary tabs).
 */

// ============================================================================
// State
// ============================================================================

let currentDtTab = 'systems';
const DT_THEME_KEY = 'agentcore-dt-theme';
const DT_COLLAPSED_KEY = 'agentcore-dt-collapsed';
let dtCache = { systems: null, vocabulary: null };
let dtVocabExpanded = {};
let dtVocabSearchTerm = '';

// ============================================================================
// Theme
// ============================================================================

function initDtTheme() {
    var saved = localStorage.getItem(DT_THEME_KEY);
    applyDtTheme(saved || 'dark');
}

function toggleDtTheme() {
    var sidebar = document.getElementById('dt-sidebar');
    var current = sidebar ? sidebar.dataset.sidebarTheme || 'light' : 'light';
    var next = current === 'dark' ? 'light' : 'dark';
    applyDtTheme(next);
    localStorage.setItem(DT_THEME_KEY, next);
}

function applyDtTheme(theme) {
    ['dt-sidebar', 'dt-sidebar-inner', 'dt-toggle-collapsed'].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.dataset.sidebarTheme = theme;
    });
    var strip = document.getElementById('sidebar-toggle-strip');
    if (strip) strip.dataset.sidebarTheme = theme;

    var sun = document.getElementById('dt-theme-icon-sun');
    var moon = document.getElementById('dt-theme-icon-moon');
    if (sun && moon) {
        if (theme === 'dark') { sun.classList.remove('hidden'); moon.classList.add('hidden'); }
        else { sun.classList.add('hidden'); moon.classList.remove('hidden'); }
    }
    if (currentDtTab) updateDtTabStyling(currentDtTab);
}

// ============================================================================
// Tabs
// ============================================================================

function updateDtTabStyling(active) {
    ['systems', 'vocabulary'].forEach(function (t) {
        var btn = document.getElementById('dt-tab-' + t);
        if (!btn) return;
        if (t === active) {
            btn.style.color = 'var(--sidebar-tab-active-text)';
            btn.style.background = 'var(--sidebar-tab-active-bg)';
            btn.style.borderColor = 'var(--sidebar-tab-active-text)';
            btn.classList.add('border-b-2');
        } else {
            btn.style.color = 'var(--sidebar-text-muted)';
            btn.style.background = 'transparent';
            btn.style.borderColor = 'transparent';
            btn.classList.remove('border-b-2');
        }
    });
}

function switchDtTab(tab) {
    ['systems', 'vocabulary'].forEach(function (t) {
        var el = document.getElementById('dt-tab-content-' + t);
        if (el) {
            if (t === tab) el.classList.remove('hidden');
            else el.classList.add('hidden');
        }
    });
    currentDtTab = tab;
    updateDtTabStyling(tab);
    loadDtTab(tab);
}

// ============================================================================
// Helpers
// ============================================================================

/** Escape HTML to prevent XSS. */
function dtEscape(text) {
    var div = document.createElement('div');
    div.textContent = text || '';
    return div.innerHTML; // nosemgrep: insecure-innerhtml, insecure-document-method
}

function dtSpinner(label) {
    return '<div class="flex items-center justify-center py-4">'
        + '<svg class="w-5 h-5 animate-spin mr-2" style="color: var(--sidebar-badge-text);" fill="none" viewBox="0 0 24 24">'
        + '<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>'
        + '<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>'
        + '</svg><span class="text-sm" style="color: var(--sidebar-text-muted);">Loading ' + label + '...</span></div>';
}

function dtError(label, tab) {
    return '<div class="text-center py-4">'
        + '<svg class="w-8 h-8 text-red-400 mx-auto mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>'
        + '<p class="text-red-500 text-sm">Failed to load ' + label + '</p>'
        + '<button onclick="loadDtTab(\'' + tab + '\', true)" class="mt-2 text-xs hover:opacity-80 underline" style="color: var(--sidebar-badge-text);">Try again</button></div>';
}

function dtEmpty(title, subtitle, emoji) {
    return '<div class="text-center py-8">'
        + '<div class="text-4xl mb-3">' + emoji + '</div>'
        + '<p class="text-sm" style="color: var(--sidebar-text-muted);">' + title + '</p>'
        + '<p class="text-xs mt-1" style="color: var(--sidebar-text-muted); opacity: 0.7;">' + subtitle + '</p>'
        + '</div>';
}

// ============================================================================
// Data loading
// ============================================================================

async function loadDtTab(tab, force) {
    if (!force && dtCache[tab]) {
        if (tab === 'systems') renderDtSystems(dtCache[tab]);
        else if (tab === 'vocabulary') renderDtVocabulary(dtCache[tab]);
        return;
    }
    var container = document.getElementById('dt-' + tab + '-content');
    if (!container) return;
    container.innerHTML = dtSpinner(tab); // nosemgrep: insecure-innerhtml, insecure-document-method

    var url = tab === 'vocabulary' ? '/api/registry/vocabulary' : '/api/registry/' + tab;
    try {
        var resp = await fetch(url);
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        var data = await resp.json();

        if (tab === 'systems') {
            if (data.configured === false) {
                container.innerHTML = dtEmpty('Registry not configured', 'Set REGISTRY_TABLE_NAME to enable', '⚙️'); // nosemgrep: insecure-innerhtml, insecure-document-method
                return;
            }
            var items = data.systems || [];
            dtCache.systems = items;
            renderDtSystems(items);
        } else if (tab === 'vocabulary') {
            dtCache.vocabulary = data;
            renderDtVocabulary(data);
        }
    } catch (e) {
        console.error('DT load error (' + tab + '):', e);
        container.innerHTML = dtError(tab, tab); // nosemgrep: insecure-innerhtml, insecure-document-method
    }
}

// ============================================================================
// Systems renderer
// ============================================================================

function systemTypeEmoji(type) {
    var t = (type || '').toUpperCase();
    if (t === 'ERP') return '🏢';
    if (t === 'MES') return '🏭';
    if (t === 'CMMS') return '🔧';
    if (t === 'PLM') return '📐';
    if (t === 'IOT' || t === 'SCADA') return '📡';
    return '💻';
}

function isaLabel(level) {
    if (level === 4) return 'L4 Business';
    if (level === 3) return 'L3 MFG Ops';
    if (level === 2) return 'L2 Control';
    if (level === 1) return 'L1 Sensors';
    return 'L' + level;
}

function statusDot(status) {
    var color = status === 'active' ? '#22c55e' : '#ef4444';
    return '<span class="inline-block w-2 h-2 rounded-full mr-1" style="background:' + color + ';"></span>';
}

function renderDtSystems(items) {
    var c = document.getElementById('dt-systems-content');
    if (!c) return;
    if (!items.length) {
        c.innerHTML = dtEmpty('No systems registered', 'Systems appear after the Discovery Agent runs', '🏭'); // nosemgrep: insecure-innerhtml, insecure-document-method
        return;
    }
    var cards = items.map(function (s) {
        return '<div class="p-3 rounded-lg" style="background: var(--sidebar-card-bg); border: 1px solid var(--sidebar-card-border);">'
            + '<div class="flex items-center gap-2 mb-2">'
            + '<span class="text-lg">' + systemTypeEmoji(s.system_type) + '</span>'
            + '<span class="text-sm font-semibold truncate" style="color: var(--sidebar-text);">' + dtEscape(s.name || s.system_id) + '</span>'
            + '<span class="ml-auto text-xs flex items-center" style="color: var(--sidebar-text-muted);">' + statusDot(s.status) + dtEscape(s.status) + '</span>'
            + '</div>'
            + '<div class="grid grid-cols-2 gap-x-3 gap-y-1 text-xs" style="color: var(--sidebar-text-muted);">'
            + '<div>📍 ' + dtEscape(s.plant || '—') + '</div>'
            + '<div>📡 ' + dtEscape(s.protocol || '—') + '</div>'
            + '<div>📐 ' + isaLabel(s.isa95_level) + '</div>'
            + '<div>🏷️ ' + dtEscape(s.system_type || '—') + '</div>'
            + '</div>'
            + '<div class="flex gap-3 mt-2 text-xs" style="color: var(--sidebar-text-muted);">'
            + '<span class="px-2 py-0.5 rounded" style="background: var(--sidebar-badge-bg); color: var(--sidebar-badge-text);">' + (s.table_count || 0) + ' tables</span>'
            + '<span class="px-2 py-0.5 rounded" style="background: var(--sidebar-badge-bg); color: var(--sidebar-badge-text);">' + (s.field_count || 0) + ' fields</span>'
            + '</div>'
            + '</div>';
    }).join('');
    c.innerHTML = '<div class="dt-systems-grid">' + cards + '</div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
}

// ============================================================================
// Vocabulary renderer — collapsible groups with search
// ============================================================================

function renderDtVocabulary(data) {
    var c = document.getElementById('dt-vocabulary-content');
    if (!c) return;
    var groups = data.groups || [];
    var total = data.total_concepts || 0;

    if (!groups.length) {
        c.innerHTML = dtEmpty('No vocabulary loaded', 'Canonical concepts define the shared manufacturing language', '🧩'); // nosemgrep: insecure-innerhtml, insecure-document-method
        return;
    }

    var searchBox = '<div class="mb-3">'
        + '<div class="relative">'
        + '<svg class="w-4 h-4 absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" style="color: var(--sidebar-text-muted);" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">'
        + '<path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />'
        + '</svg>'
        + '<input id="dt-vocab-search" type="text" placeholder="Search concepts..." '
        + 'oninput="filterDtVocabulary(this.value)" '
        + 'class="w-full pl-8 pr-8 py-1.5 text-xs rounded-md" '
        + 'style="background: var(--sidebar-card-bg); border: 1px solid var(--sidebar-card-border); color: var(--sidebar-text); outline: none;" '
        + 'onfocus="this.style.borderColor=\'var(--sidebar-tab-active-text)\'" '
        + 'onblur="this.style.borderColor=\'var(--sidebar-card-border)\'" '
        + 'value="' + dtEscape(dtVocabSearchTerm) + '" />'
        + '<button id="dt-vocab-search-clear" onclick="clearDtVocabSearch()" class="absolute right-2 top-1/2 -translate-y-1/2 p-0.5 rounded hover:opacity-80 ' + (dtVocabSearchTerm ? '' : 'hidden') + '" style="color: var(--sidebar-text-muted);" aria-label="Clear search">'
        + '<svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>'
        + '</button>'
        + '</div>'
        + '</div>';

    var header = '<div class="mb-3 text-xs" style="color: var(--sidebar-text-muted);">'
        + '<span class="font-medium" style="color: var(--sidebar-text);">' + total + '</span> canonical concepts across '
        + '<span class="font-medium" style="color: var(--sidebar-text);">' + groups.length + '</span> domains'
        + '<span id="dt-vocab-match-count" class="' + (dtVocabSearchTerm ? '' : 'hidden') + '"> · <span class="font-medium" style="color: var(--sidebar-tab-active-text);">0</span> matches</span>'
        + '</div>';

    var cards = groups.map(function (group, gi) {
        var isExpanded = dtVocabExpanded[gi] === true;
        var conceptCount = 0;
        group.subgroups.forEach(function (sg) { conceptCount += sg.concepts.length; });

        var subgroupHtml = group.subgroups.map(function (sg) {
            var items = sg.concepts.map(function (concept) {
                return '<div class="dt-vocab-item flex items-start gap-2 py-1" data-concept="' + dtEscape((concept.id + ' ' + concept.desc).toLowerCase()) + '">'
                    + '<code class="text-xs px-1.5 py-0.5 rounded shrink-0" style="background: var(--sidebar-badge-bg); color: var(--sidebar-badge-text);">' + dtEscape(concept.id) + '</code>'
                    + '<span class="text-xs" style="color: var(--sidebar-text-muted);">' + dtEscape(concept.desc) + '</span>'
                    + '</div>';
            }).join('');

            return '<div class="dt-vocab-subgroup mb-2">'
                + '<div class="text-xs font-semibold mb-1" style="color: var(--sidebar-text);">' + dtEscape(sg.name) + '</div>'
                + items
                + '</div>';
        }).join('');

        return '<div class="dt-vocab-group rounded-lg mb-2" data-group-index="' + gi + '" style="background: var(--sidebar-card-bg); border: 1px solid var(--sidebar-card-border);">'
            + '<button onclick="toggleDtVocabGroup(' + gi + ')" class="w-full px-3 py-2.5 flex items-center gap-2 text-left hover:opacity-80 transition-opacity">'
            + '<span class="text-lg">' + (group.icon || '📁') + '</span>'
            + '<span class="text-sm font-semibold flex-1" style="color: var(--sidebar-text);">' + dtEscape(group.group) + '</span>'
            + '<span class="dt-vocab-group-count text-xs px-2 py-0.5 rounded-full" style="background: var(--sidebar-badge-bg); color: var(--sidebar-badge-text);">' + conceptCount + '</span>'
            + '<svg class="w-4 h-4 transition-transform ' + (isExpanded ? 'rotate-180' : '') + '" style="color: var(--sidebar-text-muted);" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" /></svg>'
            + '</button>'
            + '<div class="px-3 pb-3 ' + (isExpanded ? '' : 'hidden') + '" id="dt-vocab-group-' + gi + '">'
            + subgroupHtml
            + '</div>'
            + '</div>';
    }).join('');

    c.innerHTML = searchBox + header + cards; // nosemgrep: insecure-innerhtml, insecure-document-method

    // Re-apply filter if there was an active search
    if (dtVocabSearchTerm) {
        filterDtVocabulary(dtVocabSearchTerm);
    }
}

/**
 * Filter vocabulary concepts by search term.
 * Matches against concept ID and description. Shows/hides items,
 * auto-expands groups with matches, hides groups with no matches.
 */
function filterDtVocabulary(term) {
    dtVocabSearchTerm = (term || '').trim();
    var query = dtVocabSearchTerm.toLowerCase();
    var clearBtn = document.getElementById('dt-vocab-search-clear');
    var matchCountEl = document.getElementById('dt-vocab-match-count');

    if (clearBtn) clearBtn.classList.toggle('hidden', !dtVocabSearchTerm);
    if (matchCountEl) matchCountEl.classList.toggle('hidden', !dtVocabSearchTerm);

    var items = document.querySelectorAll('.dt-vocab-item');
    var groups = document.querySelectorAll('.dt-vocab-group');
    var totalMatches = 0;

    if (!query) {
        // No search — show everything, restore expand/collapse state
        items.forEach(function (el) { el.classList.remove('hidden'); });
        groups.forEach(function (g) {
            g.classList.remove('hidden');
            var gi = parseInt(g.dataset.groupIndex, 10);
            var body = document.getElementById('dt-vocab-group-' + gi);
            if (body) {
                if (dtVocabExpanded[gi]) body.classList.remove('hidden');
                else body.classList.add('hidden');
            }
            // Restore subgroup visibility
            g.querySelectorAll('.dt-vocab-subgroup').forEach(function (sg) { sg.classList.remove('hidden'); });
        });
        return;
    }

    // Filter items
    items.forEach(function (el) {
        var text = el.dataset.concept || '';
        var match = text.indexOf(query) !== -1;
        el.classList.toggle('hidden', !match);
        if (match) totalMatches++;
    });

    // Show/hide groups and subgroups based on whether they have visible items
    groups.forEach(function (g) {
        var gi = parseInt(g.dataset.groupIndex, 10);
        var body = document.getElementById('dt-vocab-group-' + gi);
        var visibleInGroup = 0;

        if (body) {
            // Check subgroups
            body.querySelectorAll('.dt-vocab-subgroup').forEach(function (sg) {
                var visibleItems = sg.querySelectorAll('.dt-vocab-item:not(.hidden)').length;
                sg.classList.toggle('hidden', visibleItems === 0);
                visibleInGroup += visibleItems;
            });

            // Auto-expand groups with matches
            if (visibleInGroup > 0) body.classList.remove('hidden');
            else body.classList.add('hidden');
        }

        g.classList.toggle('hidden', visibleInGroup === 0);

        // Update the count badge to show filtered count
        var countEl = g.querySelector('.dt-vocab-group-count');
        if (countEl) countEl.textContent = visibleInGroup;
    });

    // Update match count
    if (matchCountEl) {
        matchCountEl.innerHTML = ' · <span class="font-medium" style="color: var(--sidebar-tab-active-text);">' + totalMatches + '</span> match' + (totalMatches !== 1 ? 'es' : ''); // nosemgrep: insecure-innerhtml, insecure-document-method
    }
}

function clearDtVocabSearch() {
    dtVocabSearchTerm = '';
    var input = document.getElementById('dt-vocab-search');
    if (input) { input.value = ''; input.focus(); }
    filterDtVocabulary('');
    // Restore original group counts from cache
    if (dtCache.vocabulary) renderDtVocabulary(dtCache.vocabulary);
}

function toggleDtVocabGroup(index) {
    dtVocabExpanded[index] = !dtVocabExpanded[index];
    var el = document.getElementById('dt-vocab-group-' + index);
    if (el) el.classList.toggle('hidden');
    // Rotate the chevron
    var btn = el ? el.previousElementSibling : null;
    if (btn) {
        var svg = btn.querySelector('svg:last-child');
        if (svg) svg.classList.toggle('rotate-180');
    }
}

// ============================================================================
// Sidebar toggle
// ============================================================================

function toggleDtSidebar() {
    var sidebar = document.getElementById('dt-sidebar');
    if (!sidebar) return;
    var isCollapsed = sidebar.classList.contains('hidden');
    if (isCollapsed) expandDtSidebar();
    else collapseDtSidebar();
    localStorage.setItem(DT_COLLAPSED_KEY, String(!isCollapsed));
}

function expandDtSidebar(skipRefresh) {
    var sidebar = document.getElementById('dt-sidebar');
    var toggleBtn = document.getElementById('dt-toggle-collapsed');
    // Collapse memory sidebar if open (only one panel at a time)
    var memSidebar = document.getElementById('memory-sidebar');
    if (memSidebar && !memSidebar.classList.contains('hidden')) {
        collapseMemorySidebar();
        localStorage.setItem('agentcore-memory-collapsed', 'true');
    }
    if (sidebar) { sidebar.classList.remove('hidden'); sidebar.classList.add('flex'); }
    if (toggleBtn) {
        toggleBtn.style.background = 'var(--sidebar-tab-active-bg)';
        toggleBtn.style.color = 'var(--sidebar-tab-active-text)';
    }
    if (!skipRefresh) refreshDigitalThread();
}

function collapseDtSidebar() {
    var sidebar = document.getElementById('dt-sidebar');
    var toggleBtn = document.getElementById('dt-toggle-collapsed');
    if (sidebar) { sidebar.classList.add('hidden'); sidebar.classList.remove('flex'); }
    if (toggleBtn) {
        toggleBtn.style.background = 'transparent';
        toggleBtn.style.color = 'var(--sidebar-text-muted)';
    }
}

function initDtSidebar() {
    var sidebar = document.getElementById('dt-sidebar');
    if (!sidebar) return;
    var isWide = window.innerWidth >= 1280;
    var saved = localStorage.getItem(DT_COLLAPSED_KEY);
    var shouldCollapse = saved !== null ? saved === 'true' : !isWide;
    if (shouldCollapse) collapseDtSidebar();
    else expandDtSidebar(true);
}

// ============================================================================
// Refresh
// ============================================================================

var isRefreshingDt = false;

async function refreshDigitalThread(force) {
    if (isRefreshingDt) return;
    isRefreshingDt = true;
    if (force) dtCache = { systems: null, vocabulary: null };
    var btn = document.getElementById('dt-refresh-btn');
    var icon = document.getElementById('dt-refresh-icon');
    if (btn) { btn.disabled = true; btn.classList.add('opacity-50'); }
    if (icon) icon.classList.add('animate-spin');
    try {
        await loadDtTab(currentDtTab, force);
    } finally {
        isRefreshingDt = false;
        if (btn) { btn.disabled = false; btn.classList.remove('opacity-50'); }
        if (icon) icon.classList.remove('animate-spin');
    }
}

// ============================================================================
// Init
// ============================================================================

document.addEventListener('DOMContentLoaded', function () {
    initDtTheme();
    initDtSidebar();
    loadDtTab('systems');
});
if (document.readyState !== 'loading') {
    initDtTheme();
    initDtSidebar();
    loadDtTab('systems');
}
