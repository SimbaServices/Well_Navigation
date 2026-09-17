import urllib.request

html = urllib.request.urlopen("http://62.238.113.20/").read().decode()
print("tabs", "workspace-tabs" in html)
print("bust", "map.js?v=tabs1" in html)
print("search", 'id="search-panel"' in html)
print("map", 'id="map-panel"' in html)
