# MyCloud — Personal Cloud Storage
Your own private cloud hosted on your laptop, files stored on your pendrive.

---

## Setup (one time)

### 1. Install Python
Download from https://python.org (version 3.8 or above)

### 2. Install dependencies
Open terminal / command prompt in this folder and run:
```
pip install -r requirements.txt
```

### 3. Run the server
```
python app.py
```

### 4. Open in browser
```
http://localhost:5000
```

---

## How to use

### As Admin
- Login with: **admin / admin123**
- Go to "My Clouds" tab
- Click "Create New Cloud"
  - Cloud name: e.g. `Vishnu's Cloud`
  - Cloud password: e.g. `mypassword123`
  - Storage path: your pendrive path
    - Windows: `D:\` or `E:\`
    - Linux: `/media/yourname/pendrive`
    - Mac: `/Volumes/MyPendrive`
  - Storage limit: e.g. `100` (GB)
- Click Create — a folder is made on your pendrive automatically

### As User (from any device)
- Go to http://YOUR_LAPTOP_IP:5000
- Register a new account
- Search for the cloud by name
- Enter the cloud password to connect
- Upload / download / delete files

---

## Access from outside your home Wi-Fi

### Option 1: Cloudflare Tunnel (recommended, free)
1. Download cloudflared: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
2. Run: `cloudflared tunnel --url http://localhost:5000`
3. You get a public URL like `https://xyz.trycloudflare.com`
4. Share that URL — anyone can access your cloud from anywhere

### Option 2: Ngrok (easy for testing)
1. Download from https://ngrok.com
2. Run: `ngrok http 5000`
3. Use the https URL shown

---

## Find your laptop's local IP (for home Wi-Fi access)
- Windows: open cmd → type `ipconfig` → look for IPv4 Address (e.g. 192.168.1.5)
- Linux/Mac: open terminal → type `ifconfig` or `ip addr`
- Then open `http://192.168.1.5:5000` from any device on same Wi-Fi

---

## Pendrive path examples
| OS | Example path |
|---|---|
| Windows | `D:\` or `E:\MyCloudStorage` |
| Linux | `/media/vishnu/MyPendrive` |
| Mac | `/Volumes/MyPendrive` |

---

## Notes
- Keep laptop ON and connected to internet for remote access
- Files are stored directly on your pendrive in a subfolder
- Database (mycloud.db) stores usernames, passwords (hashed), file metadata
- Default admin password can be changed in the database
