import urllib.request
import json
import time

url = 'http://127.0.0.1:5001/api/user/login'
data = json.dumps({"username": "test", "password": "123"}).encode('utf-8')
req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})

start = time.time()
try:
    with urllib.request.urlopen(req) as f:
        res = f.read()
        print("Status:", f.status)
        print("Response:", res.decode('utf-8'))
except Exception as e:
    print("Error:", e)
print("Time taken:", time.time() - start)
