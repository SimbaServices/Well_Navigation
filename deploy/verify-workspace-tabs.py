"""Check the live home page ships the search/map workspace tabs."""

from __future__ import annotations

import urllib.request

html = urllib.request.urlopen("http://127.0.0.1:5050/").read().decode()
css = urllib.request.urlopen("http://127.0.0.1:5050/static/css/app.css?v=tabs1").read().decode()
js = urllib.request.urlopen("http://127.0.0.1:5050/static/js/map.js?v=tabs1").read().decode()
checks = {
    "workspace": 'class="workspace"' in html,
    "tab_search": 'id="tab-search"' in html,
    "tab_map": 'id="tab-map"' in html,
    "search_panel": 'id="search-panel"' in html,
    "map_panel": 'id="map-panel"' in html,
    "css_phone_tabs": ".workspace-tabs" in css and "max-width: 960px" in css,
    "js_pane": "showWorkspacePane" in js and "revealMapOnPhone" in js,
}
for name, ok in checks.items():
    print(name, ok)
print("ok", all(checks.values()))
