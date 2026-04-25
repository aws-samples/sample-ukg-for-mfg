/**
 * Browser-side localStorage cache for frequently-fetched data.
 * Keys are prefixed with 'dt-cache:' to avoid collisions.
 *
 * Cached resources:
 *   - templates  → /api/templates
 *   - systems    → /api/registry/systems
 *   - vocabulary → /api/registry/vocabulary
 *   - graph      → /api/registry/graph?edge_type=concepts
 *   - workflows  → /api/workflows
 */

var DataCache = (function() {
    var PREFIX = 'dt-cache:';

    var ENDPOINTS = {
        templates:  '/api/templates',
        systems:    '/api/registry/systems',
        vocabulary: '/api/registry/vocabulary',
        graph:      '/api/registry/graph?edge_type=concepts',
        workflows:  '/api/workflows',
    };

    /** Per-key transforms applied before caching to keep storage small */
    var TRANSFORMS = {
        workflows: function(data) {
            if (!Array.isArray(data)) return data;
            return data.map(function(w) {
                var copy = {};
                for (var k in w) {
                    if (k === 'results') continue;          // strip full results
                    if (k === 'response_md') continue;      // strip markdown bodies
                    copy[k] = w[k];
                }
                return copy;
            });
        },
    };

    /** Write data + timestamp to localStorage */
    function _set(key, data) {
        try {
            localStorage.setItem(PREFIX + key, JSON.stringify({
                ts: Date.now(),
                data: data,
            }));
        } catch (_) { /* quota exceeded — silently skip */ }
    }

    /** Read cached entry (returns null if missing) */
    function _get(key) {
        try {
            var raw = localStorage.getItem(PREFIX + key);
            if (!raw) return null;
            return JSON.parse(raw);
        } catch (_) { return null; }
    }

    /** Fetch a resource from the network and cache it */
    async function _fetchAndCache(key) {
        var url = ENDPOINTS[key];
        if (!url) return null;
        try {
            var resp = await fetch(url);
            if (!resp.ok) return null;
            var data = await resp.json();
            var toStore = TRANSFORMS[key] ? TRANSFORMS[key](data) : data;
            _set(key, toStore);
            return toStore;
        } catch (_) { return null; }
    }

    return {
        /**
         * Get cached data for a key. Returns null if not cached.
         */
        get: function(key) {
            var entry = _get(key);
            return entry ? entry.data : null;
        },

        /**
         * Force-fetch from network, update cache, return data.
         */
        refresh: async function(key) {
            return await _fetchAndCache(key);
        },

        /**
         * Get cached data if available, otherwise fetch from network.
         */
        getOrFetch: async function(key) {
            var entry = _get(key);
            if (entry) return entry.data;
            return await _fetchAndCache(key);
        },

        /**
         * Warm all caches by fetching every endpoint in parallel.
         * Called on login and on manual refresh.
         * Skips keys that have their own load functions (e.g. workflows).
         */
        warmAll: function() {
            var skip = { workflows: true };
            var keys = Object.keys(ENDPOINTS).filter(function(k) { return !skip[k]; });
            return Promise.all(keys.map(function(k) {
                return _fetchAndCache(k);
            }));
        },

        /**
         * Clear all cached entries.
         */
        clearAll: function() {
            Object.keys(ENDPOINTS).forEach(function(k) {
                localStorage.removeItem(PREFIX + k);
            });
        },
    };
})();
