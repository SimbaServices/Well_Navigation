import urllib.request

html = urllib.request.urlopen("http://127.0.0.1:5050/").read().decode()
print("home_len", len(html))
print("home_map", "well-map" in html)
print("home_filters", "active-filters" in html)
print("home_add_filter", "Add filter" in html)
print("home_map_js", "/static/js/map.js" in html)
print("home_workspace", "workspace-tabs" in html)
print("home_search_tab", "tab-search" in html)
print("home_map_tab", "tab-map" in html)
