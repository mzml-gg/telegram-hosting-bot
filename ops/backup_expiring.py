#!/usr/bin/env python3
"""Host-side backup worker for Pterodactyl volumes.

Run daily on the VPS. It reads the bot SQLite database, archives volumes for
servers expiring within BACKUP_WINDOW_HOURS, and sends a Telegram notification
to the configured developer. Telegram's Bot API document limit is respected.
"""
import json, os, sqlite3, subprocess, tempfile, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta

ENV_FILE = os.getenv("BACKUP_ENV_FILE", "/etc/arab-code-hosting-backup.env")
VOLUME_ROOT = os.getenv("PTERO_VOLUME_ROOT", "/var/lib/pterodactyl/volumes")
BACKUP_ROOT = os.getenv("BACKUP_ROOT", "/var/backups/arab-code-hosting")
DB_PATH = os.getenv("BOT_DB_PATH", "/var/lib/pterodactyl/volumes/BOT_SERVER_UUID/hosting_bot.sqlite3")
WINDOW_HOURS = int(os.getenv("BACKUP_WINDOW_HOURS", "30"))
GRACE_HOURS = int(os.getenv("BACKUP_GRACE_HOURS", "1"))
MAX_TELEGRAM_BYTES = 49 * 1024 * 1024


def load_env(path):
    values = {}
    if os.path.exists(path):
        for line in open(path):
            line=line.strip()
            if line and not line.startswith("#") and "=" in line:
                k,v=line.split("=",1); values[k]=v.strip().strip('"').strip("'")
    values.update({k:v for k,v in os.environ.items() if k in {"BOT_TOKEN","APPLICATION_API_KEY","PANEL_URL","DEVELOPER_ID"}})
    return values


def panel_get(env, endpoint):
    req=urllib.request.Request(env["PANEL_URL"].rstrip("/")+"/api/application"+endpoint,
        headers={"Authorization":"Bearer "+env["APPLICATION_API_KEY"],"Accept":"application/json"})
    with urllib.request.urlopen(req, timeout=30) as r: return json.load(r)


def telegram(env, method, fields=None, document=None):
    url=f"https://api.telegram.org/bot{env['BOT_TOKEN']}/{method}"
    if document:
        import mimetypes
        boundary="----ArabCodeBackupBoundary"
        body=[]
        for k,v in fields.items(): body += [f"--{boundary}",f'Content-Disposition: form-data; name="{k}"',"",str(v)]
        data=open(document,"rb").read(); name=os.path.basename(document)
        body += [f"--{boundary}",f'Content-Disposition: form-data; name="document"; filename="{name}"',"Content-Type: application/gzip","",data,f"--{boundary}--",""]
        raw=b""
        for part in body:
            raw += (part if isinstance(part,bytes) else str(part).encode()) + b"\r\n"
        req=urllib.request.Request(url, data=raw, headers={"Content-Type":f"multipart/form-data; boundary={boundary}"})
    else:
        req=urllib.request.Request(url, data=urllib.parse.urlencode(fields or {}).encode())
    with urllib.request.urlopen(req, timeout=120) as r: return json.load(r)


def main():
    env=load_env(ENV_FILE)
    for key in ("BOT_TOKEN","APPLICATION_API_KEY","PANEL_URL","DEVELOPER_ID"):
        if not env.get(key): raise SystemExit(f"missing {key}")
    volume_root=env.get("PTERO_VOLUME_ROOT", VOLUME_ROOT)
    backup_root=env.get("BACKUP_ROOT", BACKUP_ROOT)
    db_path=env.get("BOT_DB_PATH", DB_PATH)
    os.makedirs(backup_root, exist_ok=True)
    conn=sqlite3.connect(db_path); conn.row_factory=sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS backup_requests (server_id INTEGER PRIMARY KEY, requested_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending')")
    for col in ("expires_at_snapshot", "archive_path"):
        try: conn.execute(f"ALTER TABLE backup_requests ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError: pass
    conn.commit()
    rows=conn.execute("SELECT server_id,telegram_id,expires_at FROM owned_servers").fetchall()
    now=datetime.now(); cutoff=now.timestamp()+WINDOW_HOURS*3600
    for row in rows:
        try: expiry=datetime.fromisoformat(row["expires_at"])
        except Exception: continue
        req=conn.execute("SELECT * FROM backup_requests WHERE server_id=?",(row["server_id"],)).fetchone()
        if req and req["status"] == "sent": continue
        # A renewal after preparation cancels the pending archive.
        if req and req["status"] == "prepared":
            try: snapshot=datetime.fromisoformat(req["expires_at_snapshot"])
            except Exception: snapshot=expiry
            if expiry > snapshot + timedelta(minutes=1):
                if req["archive_path"] and os.path.exists(req["archive_path"]): os.remove(req["archive_path"])
                conn.execute("UPDATE backup_requests SET status=?,archive_path=NULL WHERE server_id=?",("cancelled_renewed",row["server_id"])); conn.commit(); continue
            if now.timestamp() < snapshot.timestamp() + GRACE_HOURS*3600: continue
            archive=req["archive_path"]
            if not archive or not os.path.exists(archive):
                conn.execute("UPDATE backup_requests SET status=? WHERE server_id=?",("error:prepared archive missing",row["server_id"])); conn.commit(); continue
            size=os.path.getsize(archive)
            caption=(f"📦 Backup after non-renewal\nServer: {row['server_id']}\nOwner Telegram ID: {row['telegram_id']}\n"
                     f"Expired: {row['expires_at']}\nSize: {size/1024/1024:.1f} MB")
            try:
                if size <= MAX_TELEGRAM_BYTES:
                    telegram(env,"sendDocument",{"chat_id":env["DEVELOPER_ID"],"caption":caption},archive); status="sent"
                else:
                    telegram(env,"sendMessage",{"chat_id":env["DEVELOPER_ID"],"text":caption+"\n⚠️ File exceeds Telegram's 49 MB upload limit; it remains on the VPS."}); status="stored_large"
                conn.execute("UPDATE backup_requests SET status=? WHERE server_id=?",(status,row["server_id"])); conn.commit()
            except Exception as exc:
                conn.execute("UPDATE backup_requests SET status=? WHERE server_id=?",(f"error:{str(exc)[:160]}",row["server_id"])); conn.commit()
            continue
        # Prepare an archive when the server is near expiry, but do not send it yet.
        if expiry.timestamp() > cutoff or expiry.timestamp() < now.timestamp()-86400*2: continue
        try:
            attrs=panel_get(env,f"/servers/{row['server_id']}")["attributes"]
            volume=os.path.join(volume_root,attrs["uuid"])
            if not os.path.isdir(volume): raise RuntimeError("volume not found")
            stamp=datetime.now().strftime("%Y%m%d-%H%M%S")
            archive=os.path.join(backup_root,f"server-{row['server_id']}-{stamp}.tar.gz")
            subprocess.run(["tar","-czf",archive,"-C",volume,"."],check=True,timeout=900)
            conn.execute("INSERT INTO backup_requests(server_id,requested_at,status,expires_at_snapshot,archive_path) VALUES(?,?,?,?,?) ON CONFLICT(server_id) DO UPDATE SET requested_at=excluded.requested_at,status=excluded.status,expires_at_snapshot=excluded.expires_at_snapshot,archive_path=excluded.archive_path",(row["server_id"],datetime.now().isoformat(),"prepared",row["expires_at"],archive)); conn.commit()
            try: telegram(env,"sendMessage",{"chat_id":env["DEVELOPER_ID"],"text":f"📦 تم تجهيز نسخة مؤقتة للسيرفر {row['server_id']}. سيتم إرسالها بعد الانتهاء إذا لم يتم التجديد."})
            except Exception: pass
        except Exception as exc:
            conn.execute("INSERT INTO backup_requests(server_id,requested_at,status) VALUES(?,?,?) ON CONFLICT(server_id) DO UPDATE SET requested_at=excluded.requested_at,status=excluded.status",(row["server_id"],datetime.now().isoformat(),f"error:{str(exc)[:160]}")); conn.commit()
            try: telegram(env,"sendMessage",{"chat_id":env["DEVELOPER_ID"],"text":f"❌ Backup preparation failed for server {row['server_id']}: {exc}"})
            except Exception: pass
    conn.close()

if __name__ == "__main__": main()
