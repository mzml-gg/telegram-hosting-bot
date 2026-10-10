#!/usr/bin/env python3
"""Host-side backup worker for Pterodactyl volumes.

Run daily on the VPS. It reads the bot SQLite database, archives volumes for
servers expiring within BACKUP_WINDOW_HOURS, and sends a Telegram notification
to the configured developer. Telegram's Bot API document limit is respected.
"""
import json, os, sqlite3, subprocess, tempfile, urllib.parse, urllib.request
from datetime import datetime, timezone

ENV_FILE = os.getenv("BACKUP_ENV_FILE", "/etc/arab-code-hosting-backup.env")
VOLUME_ROOT = os.getenv("PTERO_VOLUME_ROOT", "/var/lib/pterodactyl/volumes")
BACKUP_ROOT = os.getenv("BACKUP_ROOT", "/var/backups/arab-code-hosting")
DB_PATH = os.getenv("BOT_DB_PATH", "/var/lib/pterodactyl/volumes/e54f2383-a3ca-4172-a98e-7afac2f57d30/hosting_bot.sqlite3")
WINDOW_HOURS = int(os.getenv("BACKUP_WINDOW_HOURS", "30"))
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
    os.makedirs(BACKUP_ROOT, exist_ok=True)
    conn=sqlite3.connect(DB_PATH); conn.row_factory=sqlite3.Row
    rows=conn.execute("SELECT server_id,telegram_id,expires_at FROM owned_servers").fetchall()
    now=datetime.now(); cutoff=now.timestamp()+WINDOW_HOURS*3600
    for row in rows:
        try: expiry=datetime.fromisoformat(row["expires_at"])
        except Exception: continue
        if expiry.timestamp() > cutoff or expiry.timestamp() < now.timestamp()-86400*2: continue
        req=conn.execute("SELECT status FROM backup_requests WHERE server_id=?",(row["server_id"],)).fetchone()
        if req and req["status"] == "sent": continue
        try:
            attrs=panel_get(env,f"/servers/{row['server_id']}")["attributes"]
            uuid=attrs["uuid"]
            volume=os.path.join(VOLUME_ROOT,uuid)
            if not os.path.isdir(volume): raise RuntimeError("volume not found")
            stamp=datetime.now().strftime("%Y%m%d-%H%M%S")
            archive=os.path.join(BACKUP_ROOT,f"server-{row['server_id']}-{stamp}.tar.gz")
            subprocess.run(["tar","-czf",archive,"-C",volume,"."],check=True,timeout=900)
            size=os.path.getsize(archive)
            caption=(f"📦 Backup before expiry\nServer: {row['server_id']}\n"
                     f"Owner Telegram ID: {row['telegram_id']}\nExpires: {row['expires_at']}\n"
                     f"Size: {size/1024/1024:.1f} MB")
            target=env["DEVELOPER_ID"]
            if size <= MAX_TELEGRAM_BYTES:
                telegram(env,"sendDocument",{"chat_id":target,"caption":caption},archive)
                status="sent"
            else:
                telegram(env,"sendMessage",{"chat_id":target,"text":caption+"\n⚠️ File exceeds Telegram's 49 MB upload limit; it remains on the VPS."})
                status="stored_large"
            conn.execute("INSERT INTO backup_requests(server_id,requested_at,status) VALUES(?,?,?) ON CONFLICT(server_id) DO UPDATE SET requested_at=excluded.requested_at,status=excluded.status",(row["server_id"],datetime.now().isoformat(),status)); conn.commit()
            try: telegram(env,"sendMessage",{"chat_id":row["telegram_id"],"text":f"📦 تم تجهيز نسخة احتياطية لسيرفرك {row['server_id']} قبل الانتهاء. تواصل مع المطور للتجديد."})
            except Exception: pass
        except Exception as exc:
            conn.execute("UPDATE backup_requests SET status=? WHERE server_id=?",(f"error:{str(exc)[:160]}",row["server_id"])); conn.commit()
            try: telegram(env,"sendMessage",{"chat_id":env["DEVELOPER_ID"],"text":f"❌ Backup failed for server {row['server_id']}: {exc}"})
            except Exception: pass
    conn.close()

if __name__ == "__main__": main()
