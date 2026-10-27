#!/usr/bin/env python3
"""Uyunks TikTok engine: auth (code -> tokens), post (one video), queue (scheduled posts).
Tokens are stored encrypted (AES via openssl, passphrase in TOKEN_PASSPHRASE secret) in data/tokens.enc."""
import json, os, sys, time, subprocess, urllib.request, urllib.parse, datetime, glob

API = "https://open.tiktokapis.com"
KEY = os.environ["TIKTOK_CLIENT_KEY"]
SECRET = os.environ["TIKTOK_CLIENT_SECRET"]
PASS = os.environ["TOKEN_PASSPHRASE"]
REDIRECT = "https://deni-adr.github.io/"
TOK = "data/tokens.enc"

def mask(v):
    if v: print(f"::add-mask::{v}")

def req(method, url, data=None, headers=None, raw=None):
    h = headers or {}
    body = raw
    if data is not None and raw is None:
        if h.get("Content-Type", "").startswith("application/json"):
            body = json.dumps(data).encode()
        else:
            body = urllib.parse.urlencode(data).encode()
    r = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            txt = resp.read().decode() or "{}"
            return resp.status, json.loads(txt) if txt.strip().startswith("{") else {"raw": txt}
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try: return e.code, json.loads(txt)
        except Exception: return e.code, {"raw": txt}

def save_tokens(t):
    t["saved_at"] = int(time.time())
    os.makedirs("data", exist_ok=True)
    p = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-a", "-pass", "env:TOKEN_PASSPHRASE", "-out", TOK],
                       input=json.dumps(t).encode(), check=True)

def load_tokens():
    out = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-a", "-pass", "env:TOKEN_PASSPHRASE", "-in", TOK],
                         capture_output=True, check=True).stdout
    t = json.loads(out); mask(t.get("access_token")); mask(t.get("refresh_token")); return t

def token_call(data):
    data.update({"client_key": KEY, "client_secret": SECRET})
    s, d = req("POST", API + "/v2/oauth/token/", data, {"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache"})
    mask(d.get("access_token")); mask(d.get("refresh_token"))
    if "access_token" not in d:
        sys.exit(f"Token error: {d.get('error')} {d.get('error_description')}")
    return d

def access_token():
    t = load_tokens()
    if time.time() > t["saved_at"] + int(t.get("expires_in", 86400)) - 600:
        n = token_call({"grant_type": "refresh_token", "refresh_token": t["refresh_token"]})
        save_tokens(n); t = n; print("Access token refreshed.")
    return t["access_token"]

def auth(code):
    code = urllib.parse.unquote(code.strip())
    d = token_call({"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT})
    save_tokens(d)
    s, u = req("GET", API + "/v2/user/info/?fields=open_id,display_name", headers={"Authorization": "Bearer " + d["access_token"]})
    print("Connected TikTok account:", u.get("data", {}).get("user", {}).get("display_name"), "| scopes:", d.get("scope"))

def fetch_video(src):
    if src.startswith("http"):
        path = "/tmp/video.mp4"; urllib.request.urlretrieve(src, path); return path
    return src  # path inside repo, e.g. videos/abc.mp4

def post(src, caption, privacy=""):
    tok = access_token()
    H = {"Authorization": "Bearer " + tok, "Content-Type": "application/json; charset=UTF-8"}
    s, ci = req("POST", API + "/v2/post/publish/creator_info/query/", {}, H)
    info = ci.get("data", {})
    opts = info.get("privacy_level_options", ["SELF_ONLY"])
    print("Creator:", info.get("creator_nickname"), "| allowed privacy:", opts)
    if not privacy or privacy not in opts:
        privacy = "SELF_ONLY" if "SELF_ONLY" in opts else opts[0]
    path = fetch_video(src); size = os.path.getsize(path)
    MB = 1024 * 1024
    chunk = size if size <= 64 * MB else 10 * MB
    count = max(1, size // chunk)
    body = {"post_info": {"title": caption[:2200], "privacy_level": privacy,
                          "disable_comment": False, "disable_duet": False, "disable_stitch": False},
            "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}}
    s, d = req("POST", API + "/v2/post/publish/video/init/", body, H)
    if d.get("error", {}).get("code") not in ("ok", None) or "data" not in d:
        sys.exit(f"Init error: {d.get('error')}")
    pid, url = d["data"]["publish_id"], d["data"]["upload_url"]
    with open(path, "rb") as f:
        for i in range(count):
            start = i * chunk
            end = size - 1 if i == count - 1 else start + chunk - 1
            f.seek(start); blob = f.read(end - start + 1)
            s, _ = req("PUT", url, raw=blob, headers={"Content-Type": "video/mp4", "Content-Length": str(len(blob)),
                                                      "Content-Range": f"bytes {start}-{end}/{size}"})
            print(f"Chunk {i+1}/{count}: HTTP {s}")
    for _ in range(40):
        time.sleep(10)
        s, st = req("POST", API + "/v2/post/publish/status/fetch/", {"publish_id": pid}, H)
        status = st.get("data", {}).get("status")
        print("Status:", status, st.get("data", {}).get("fail_reason", ""))
        if status in ("PUBLISH_COMPLETE", "FAILED"): break
    if status != "PUBLISH_COMPLETE": sys.exit(f"Post not completed: {status}")
    print(f"Posted as {privacy}.")

def queue():
    now = datetime.datetime.now(datetime.timezone.utc)
    for qf in sorted(glob.glob("queue/*.json")):
        item = json.load(open(qf))
        at = datetime.datetime.fromisoformat(item["at"].replace("Z", "+00:00"))
        if at <= now:
            print("Posting", qf)
            try:
                post(item["video"], item.get("caption", ""), item.get("privacy", ""))
                os.makedirs("queue/done", exist_ok=True); os.rename(qf, qf.replace("queue/", "queue/done/"))
            except SystemExit as e:
                print("FAILED", qf, e); item["error"] = str(e); json.dump(item, open(qf, "w"), indent=2)

if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "auth": auth(sys.argv[2])
    elif cmd == "post": post(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "", sys.argv[4] if len(sys.argv) > 4 else "")
    elif cmd == "queue": queue()
