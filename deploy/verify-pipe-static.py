import urllib.request

js = urllib.request.urlopen("http://127.0.0.1:5050/static/js/map.js?v=pipes2").read().decode()
print("js legend", "LA EIA+BSEE" in js)
print("js source field", "quality_label" in js)
print("js click", "Clicked on pipeline" in js)
print("js t4ish", "Permit / serial" in js)
