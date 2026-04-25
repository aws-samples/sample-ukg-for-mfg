/**
 * Shared System Explorer Component
 *
 * Reusable system list + drill-in detail panel used by both
 * /admin/discover and /home pages.
 *
 * Usage:
 *   const explorer = new SystemExplorer({
 *       listEl: document.getElementById('my-systems-list'),
 *       countEl: document.getElementById('my-sys-count'),       // optional
 *       detailEl: document.getElementById('my-detail'),         // optional drill-in panel
 *       detailHeaderEl: document.getElementById('my-detail-hdr'),
 *       detailBodyEl: document.getElementById('my-detail-body'),
 *       onSystemClick: (systemId) => { ... },                   // override click behavior
 *       onBack: () => { ... },                                  // override back behavior
 *       hideOnDrill: [],                                        // elements to hide when drilling in
 *       cssPrefix: 'disc',                                      // CSS class prefix
 *       showStats: false,                                       // show registry summary footer
 *       statsEls: { systems, concepts, equivs },                // stat counter elements
 *   });
 *   explorer.load();
 */

class SystemExplorer {
    constructor(opts) {
        this.listEl = opts.listEl;
        this.countEl = opts.countEl || null;
        this.detailEl = opts.detailEl || null;
        this.detailHeaderEl = opts.detailHeaderEl || null;
        this.detailBodyEl = opts.detailBodyEl || null;
        this.onSystemClick = opts.onSystemClick || null;
        this.onBack = opts.onBack || null;
        this.hideOnDrill = opts.hideOnDrill || [];
        this.prefix = opts.cssPrefix || 'se';
        this.showStats = opts.showStats || false;
        this.statsEls = opts.statsEls || {};

        this.systems = [];
        this.activeSystem = null;
        this.activeDetail = null;
        this.activeTab = 'tables';
    }

    _esc(s) {
        var d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML; // nosemgrep: insecure-innerhtml, insecure-document-method
    }

    async load(forceRefresh) {
        if (this.listEl) {
            this.listEl.innerHTML = '<div class="' + this.prefix + '-loading">Loading systems…</div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
        }
        try {
            var data;
            if (forceRefresh && typeof DataCache !== 'undefined') {
                data = await DataCache.refresh('systems');
            } else if (typeof DataCache !== 'undefined') {
                data = await DataCache.getOrFetch('systems');
            }
            if (!data) {
                var resp = await fetch('/api/registry/systems');
                data = await resp.json();
            }
            if (data.configured === false) {
                if (this.listEl) this.listEl.innerHTML = '<div class="' + this.prefix + '-empty"><div class="' + this.prefix + '-empty-text">Registry not configured</div></div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
                return;
            }
            this.systems = data.systems || [];
            this.renderList();
            if (this.showStats) this.loadStats();
        } catch (e) {
            if (this.listEl) this.listEl.innerHTML = '<div class="' + this.prefix + '-empty"><div class="' + this.prefix + '-empty-text">Could not load systems</div></div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
        }
    }

    renderList() {
        if (this.countEl) this.countEl.textContent = this.systems.length;
        if (!this.listEl) return;

        if (!this.systems.length) {
            this.listEl.innerHTML = '<div class="' + this.prefix + '-empty">' // nosemgrep: insecure-innerhtml, insecure-document-method
                + '<div style="font-size:1.5rem;opacity:0.5;">🏭</div>'
                + '<div class="' + this.prefix + '-empty-text">No systems registered</div>'
                + '<div class="' + this.prefix + '-empty-hint">Use the Discovery Agent to register systems</div></div>';
            return;
        }

        var self = this;
        var typeEmoji = { ERP: '🏢', MES: '🏭', CMMS: '🔧', PLM: '📐', IoT: '📡', Historian: '📡' };
        var html = '';
        this.systems.forEach(function(s) {
            var isActive = self.activeSystem && self.activeSystem.system_id === s.system_id;
            var stype = s.system_type || '';
            var emoji = typeEmoji[stype.toUpperCase()] || typeEmoji[stype] || '📦';
            var statusColor = s.status === 'active' ? 'var(--green)' : 'var(--muted)';
            html += '<div class="se-sys-card ' + (isActive ? 'active' : '') + '" data-type="' + self._esc(stype) + '" data-sid="' + self._esc(s.system_id) + '">'
                + '<div class="se-sys-row">'
                + '<span class="se-sys-badge">' + emoji + ' ' + self._esc(stype) + '</span>'
                + '<span class="se-sys-name">' + self._esc(s.name || s.system_id) + '</span>'
                + '<span style="width:6px;height:6px;border-radius:50%;background:' + statusColor + ';flex-shrink:0;margin-left:auto;"></span>'
                + '</div>'
                + '<div class="se-sys-meta">' + self._esc(s.system_id)
                + (s.table_count ? ' · ' + s.table_count + ' tables' : '')
                + (s.field_count ? ' · ' + s.field_count + ' fields' : '')
                + '</div></div>';
        });
        this.listEl.innerHTML = html; // nosemgrep: insecure-innerhtml, insecure-document-method

        // Bind click handlers
        this.listEl.querySelectorAll('.se-sys-card').forEach(function(card) {
            card.addEventListener('click', function() {
                var sid = card.getAttribute('data-sid');
                if (self.onSystemClick) {
                    self.onSystemClick(sid);
                } else {
                    self.openSystem(sid);
                }
            });
        });
    }

    async openSystem(systemId) {
        var sys = this.systems.find(function(s) { return s.system_id === systemId; });
        if (!sys) return;
        this.activeSystem = sys;
        this.renderList();

        if (this.detailEl) {
            this.detailEl.style.display = 'flex';
            this.hideOnDrill.forEach(function(el) { if (el) el.style.display = 'none'; });

            if (this.detailHeaderEl) {
                this.detailHeaderEl.innerHTML = // nosemgrep: insecure-innerhtml, insecure-document-method
                    '<div style="padding:0 0.75rem 0.4rem;">'
                    + '<div style="font-size:1rem;font-weight:700;color:var(--text);">' + this._esc(sys.name || sys.system_id) + '</div>'
                    + '<div style="font-size:0.7rem;color:var(--muted);font-family:\'IBM Plex Mono\',monospace;">'
                    + this._esc(sys.system_id) + ' · ' + this._esc(sys.system_type || '') + (sys.vendor ? ' · ' + this._esc(sys.vendor) : '') + '</div></div>';
            }

            // Reset to tables tab
            this.activeTab = 'tables';
            if (this.detailEl) {
                this.detailEl.querySelectorAll('.se-tab').forEach(function(t) { t.classList.remove('active'); });
                var tablesTab = this.detailEl.querySelector('.se-tab[data-tab="tables"]');
                if (tablesTab) tablesTab.classList.add('active');
            }

            await this.loadDetail(sys);
        }
    }

    closeDetail() {
        this.activeSystem = null;
        this.activeDetail = null;
        if (this.detailEl) this.detailEl.style.display = 'none';
        this.hideOnDrill.forEach(function(el) { if (el) el.style.display = ''; });
        this.renderList();
        if (this.onBack) this.onBack();
    }

    async loadDetail(sys) {
        var body = this.detailBodyEl;
        if (!body) return;
        body.innerHTML = '<div class="se-loading">Loading…</div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
        try {
            var resp = await fetch('/admin/registry/' + encodeURIComponent(sys.system_id));
            var html = await resp.text();
            var parser = new DOMParser();
            var doc = parser.parseFromString(html, 'text/html');

            var tables = {};
            doc.querySelectorAll('table tbody tr').forEach(function(tr) {
                var cells = tr.querySelectorAll('td');
                if (cells.length >= 3) {
                    var tbl = (cells[0].textContent || '').trim();
                    var fld = (cells[1].textContent || '').trim();
                    var dtype = (cells[2].textContent || '').trim();
                    var concept = cells.length >= 4 ? (cells[3].textContent || '').trim() : '';
                    if (tbl && fld) {
                        if (!tables[tbl]) tables[tbl] = [];
                        tables[tbl].push({ name: fld, data_type: dtype, concept: concept });
                    }
                }
            });

            this.activeDetail = { tables: tables };
            this.renderTablesTab();
        } catch (e) {
            body.innerHTML = '<div class="se-empty"><div class="se-empty-text">Failed to load</div><div class="se-empty-hint">' + this._esc(e.message) + '</div></div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
        }
    }

    switchTab(tab) {
        this.activeTab = tab;
        if (this.detailEl) {
            this.detailEl.querySelectorAll('.se-tab').forEach(function(t) { t.classList.remove('active'); });
            var btn = this.detailEl.querySelector('.se-tab[data-tab="' + tab + '"]');
            if (btn) btn.classList.add('active');
        }
        if (tab === 'tables') this.renderTablesTab();
        else if (tab === 'concepts') this.renderConceptsTab();
        else if (tab === 'equivalences') this.renderEquivalencesTab();
    }

    renderTablesTab() {
        var body = this.detailBodyEl;
        if (!body) return;
        if (!this.activeDetail || !Object.keys(this.activeDetail.tables).length) {
            body.innerHTML = '<div class="se-empty"><div class="se-empty-text">No tables found</div></div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
            return;
        }
        var self = this;
        var html = '';
        Object.entries(this.activeDetail.tables).forEach(function(entry) {
            var tbl = entry[0], fields = entry[1];
            html += '<div class="se-tbl-card">'
                + '<div class="se-tbl-hdr"><span class="se-tbl-name">' + self._esc(tbl) + '</span>'
                + '<span class="se-tbl-meta">' + fields.length + ' fields ▸</span></div>'
                + '<div class="se-tbl-fields">'
                + fields.map(function(f) {
                    return '<div class="se-field-row"><span class="se-field-name">' + self._esc(f.name) + '</span>'
                        + '<span class="se-field-type">' + self._esc(f.data_type || '') + '</span></div>';
                }).join('')
                + '</div></div>';
        });
        body.innerHTML = html; // nosemgrep: insecure-innerhtml, insecure-document-method

        // Bind toggle
        body.querySelectorAll('.se-tbl-card').forEach(function(card) {
            card.querySelector('.se-tbl-hdr').addEventListener('click', function() {
                card.classList.toggle('open');
            });
        });
    }

    renderConceptsTab() {
        var body = this.detailBodyEl;
        if (!body) return;
        if (!this.activeDetail) { body.innerHTML = '<div class="se-loading">Loading…</div>'; return; } // nosemgrep: insecure-innerhtml, insecure-document-method

        var concepts = {};
        var self = this;
        Object.entries(this.activeDetail.tables).forEach(function(entry) {
            var tbl = entry[0], fields = entry[1];
            fields.forEach(function(f) {
                if (f.concept && f.concept !== '—' && f.concept !== '') {
                    if (!concepts[f.concept]) concepts[f.concept] = [];
                    concepts[f.concept].push(tbl + '.' + f.name);
                }
            });
        });

        if (!Object.keys(concepts).length) {
            body.innerHTML = '<div class="se-empty"><div class="se-empty-text">No concept mappings found</div><div class="se-empty-hint">Ask the Discovery Agent to analyze this system</div></div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
            return;
        }

        var html = '';
        Object.entries(concepts).sort(function(a, b) { return b[1].length - a[1].length; }).forEach(function(entry) {
            html += '<div class="se-concept-card"><div class="se-concept-name">' + self._esc(entry[0].replace(/-/g, ' ')) + '</div>'
                + '<div class="se-concept-fields">' + entry[1].map(self._esc.bind(self)).join(', ') + '</div></div>';
        });
        body.innerHTML = html; // nosemgrep: insecure-innerhtml, insecure-document-method
    }

    async renderEquivalencesTab() {
        var body = this.detailBodyEl;
        if (!body || !this.activeSystem) return;
        body.innerHTML = '<div class="se-loading">Loading equivalences…</div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
        var self = this;
        try {
            var resp = await fetch('/api/registry/equivalences');
            var data = await resp.json();
            var equivs = (data.equivalences || []).filter(function(eq) {
                return eq.source_system === self.activeSystem.system_id || eq.target_system === self.activeSystem.system_id;
            });
            if (!equivs.length) {
                body.innerHTML = '<div class="se-empty"><div class="se-empty-text">No equivalences for this system</div>' // nosemgrep: insecure-innerhtml, insecure-document-method
                    + '<div class="se-empty-hint"><a href="/admin/registry/equivalences" style="color:var(--accent);">View all equivalences →</a></div></div>';
                return;
            }
            var html = '';
            equivs.forEach(function(eq) {
                html += '<div class="se-equiv-card">'
                    + '<div class="se-equiv-concept">' + self._esc(eq.concept_id || '—') + '</div>'
                    + '<div class="se-equiv-path"><code>' + self._esc(eq.source_system + '.' + eq.source_table + '.' + eq.source_field) + '</code>'
                    + ' <span style="color:var(--accent);">→</span> '
                    + '<code>' + self._esc(eq.target_system + '.' + eq.target_table + '.' + eq.target_field) + '</code></div>'
                    + (eq.confidence ? '<div class="se-equiv-conf">confidence: ' + Math.round(eq.confidence * 100) + '%</div>' : '')
                    + '</div>';
            });
            body.innerHTML = html; // nosemgrep: insecure-innerhtml, insecure-document-method
        } catch (e) {
            body.innerHTML = '<div class="se-empty"><div class="se-empty-text">Failed to load equivalences</div></div>'; // nosemgrep: insecure-innerhtml, insecure-document-method
        }
    }

    async loadStats() {
        if (this.statsEls.systems) this.statsEls.systems.textContent = this.systems.length;
        try {
            var resp = await fetch('/api/registry/concepts');
            var data = await resp.json();
            if (this.statsEls.concepts) this.statsEls.concepts.textContent = data.count || 0;
        } catch (_) {}
        try {
            var resp2 = await fetch('/api/registry/equivalences');
            var data2 = await resp2.json();
            if (this.statsEls.equivs) this.statsEls.equivs.textContent = data2.count || 0;
        } catch (_) {}
    }
}
