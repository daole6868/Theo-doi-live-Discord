# 🚀 Deploy Gachaz Live Bot lên VPS Cloudfly

Pattern giống bot `fb-monitor` đã deploy trước, chỉ thêm bước cài FFmpeg + libopus cho voice.

---

## 1. Requirements

- Ubuntu 20.04 / 22.04 / 24.04
- Python 3.10+ (Ubuntu 22.04 có sẵn Python 3.10)
- Có sudo quyền
- Bot Discord đã tạo, có TOKEN

---

## 2. SSH vào VPS + cài system deps

```bash
ssh root@your-vps-ip

# Update
apt update && apt upgrade -y

# Cài đủ deps
apt install -y \
  python3 python3-pip python3-venv python3-dev \
  ffmpeg libopus0 libopus-dev \
  libffi-dev libnacl-dev \
  git nano
```

**Verify FFmpeg:**
```bash
ffmpeg -version
# Nếu ra version → OK
```

---

## 3. Upload code lên VPS

### Cách 1: dùng SCP từ máy Windows
```bash
# Trên Windows PowerShell:
scp -r C:\gachaz-live-bot root@your-vps-ip:/opt/
```

### Cách 2: dùng Git (nếu bạn push code lên GitHub private)
```bash
cd /opt
git clone https://github.com/your-username/gachaz-live-bot.git
```

---

## 4. Setup venv + install deps

```bash
cd /opt/gachaz-live-bot

# Tạo virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Upgrade pip + install
pip install -U pip
pip install -r requirements.txt
```

---

## 5. Config `.env`

```bash
cp .env.example .env
nano .env
```

Điền đầy đủ:
```env
DISCORD_TOKEN=your_real_token_here
GUILD_ID=1235074618180636772
TARGET_CATEGORY_ID=1235075173498097686
ADMIN_CHANNEL_ID=xxx
USER_CHANNEL_ID=xxx
LEADERBOARD_CHANNEL_ID=xxx
LATE_ALERT_CHANNEL_ID=xxx
CHECK_CTV_CHANNEL_ID=xxx
```

Save: `Ctrl+O` → `Enter` → `Ctrl+X`.

---

## 6. Test chạy 1 lần

```bash
python main.py
```

Nếu thấy `✅ Bot đã đăng nhập: [tên bot]` → OK, `Ctrl+C` để dừng.

**Nếu lỗi TTS/voice:**
```bash
# Test edge-tts riêng
python -c "
import asyncio, edge_tts
async def t():
    c = edge_tts.Communicate('xin chào test', 'vi-VN-HoaiMyNeural')
    await c.save('/tmp/test.mp3')
    print('OK')
asyncio.run(t())
"
ls -la /tmp/test.mp3
# Nếu file có size > 0 → edge-tts work
```

---

## 7. Setup systemd auto-restart

```bash
# Copy file service kèm project
sudo cp /opt/gachaz-live-bot/gachaz-bot.service /etc/systemd/system/

# Reload + enable + start
sudo systemctl daemon-reload
sudo systemctl enable gachaz-bot
sudo systemctl start gachaz-bot

# Check status
sudo systemctl status gachaz-bot
```

Nếu status là `active (running)` → **BOT ĐÃ CHẠY 24/7**.

---

## 8. Xem log

```bash
# Log systemd (real-time)
sudo journalctl -u gachaz-bot -f

# Log file bot (có rotate 5MB × 5)
tail -f /opt/gachaz-live-bot/logs/bot.log
```

---

## 9. Restart / Stop bot

```bash
# Restart (khi update code)
sudo systemctl restart gachaz-bot

# Stop
sudo systemctl stop gachaz-bot

# Start
sudo systemctl start gachaz-bot
```

---

## 10. ⚠️ QUAN TRỌNG: Discord Permissions cho Bot Role

Vào Discord Server → **Server Settings** → **Roles** → chọn Role của Bot → **Permissions**:

**Bắt buộc:**
- ✅ View Channel
- ✅ Send Messages
- ✅ Read Message History
- ✅ Manage Channels *(tạo/xóa voice channel)*
- ✅ Manage Roles *(tạo/xóa role, gán role — role bot phải cao hơn role sẽ quản)*
- ✅ **Move Members** *(kick CTV khỏi voice — VOICE ALERT)*
- ✅ **Connect** *(bot vào voice channel)*
- ✅ **Speak** *(phát audio TTS)*

Nếu thiếu bất kỳ permission nào → tính năng đó sẽ fail (log sẽ báo `Forbidden`).

---

## 🔧 Troubleshooting

### Bot không phát được voice
```bash
# 1. Kiểm tra FFmpeg
which ffmpeg
ffmpeg -version

# 2. Kiểm tra libopus
dpkg -l | grep opus

# 3. Test kết nối speech.platform.bing.com
curl -I https://speech.platform.bing.com
```

### PyNaCl install fail
```bash
apt install -y libnacl-dev libffi-dev libsodium-dev
pip install --force-reinstall PyNaCl
```

### Bot không kick được CTV
- Check log: `sudo journalctl -u gachaz-bot | grep Forbidden`
- Discord: Role bot cần **cao hơn** role CTV trong hierarchy
- Discord: Bot cần permission **Move Members**

### Update code
```bash
cd /opt/gachaz-live-bot

# Nếu dùng git
git pull

# Nếu dùng scp (chạy từ máy local)
# scp -r C:\gachaz-live-bot\* root@vps:/opt/gachaz-live-bot/

# Restart bot
sudo systemctl restart gachaz-bot
```

### Xem lịch sử log gần nhất
```bash
sudo journalctl -u gachaz-bot -n 200 --no-pager
```

---

## 📁 Cấu trúc VPS sau khi deploy

```
/opt/gachaz-live-bot/
├── .env                      ← Secrets (không commit)
├── .venv/                    ← Virtual env
├── main.py
├── config.py
├── requirements.txt
├── gachaz-bot.service        ← Systemd template
├── data/                     ← JSON persist tự tạo
├── logs/                     ← Bot log (rotate 5MB)
├── core/ helpers/ views/ cogs/

/etc/systemd/system/
└── gachaz-bot.service        ← Symlink từ file trên
```

---

## 💡 Tips

**Có 2 bot cùng VPS (bot này + fb-monitor):**
- Service name khác nhau (`gachaz-bot` vs `fb-monitor`) → không conflict
- Working dir khác nhau → không conflict
- Log riêng → dễ quản lý
- Không cần chia CPU/RAM đặc biệt

**Backup data:**
```bash
# Cron job backup mỗi ngày
crontab -e
# Thêm: 
0 2 * * * cd /opt/gachaz-live-bot && tar czf /root/backup/gachaz-data-$(date +\%Y\%m\%d).tar.gz data/
```

**Kiểm tra RAM/CPU bot:**
```bash
# Bot dùng bao nhiêu RAM
ps aux | grep 'python main.py' | grep -v grep

# Discord voice thường tốn ~50-100MB RAM, CPU ~1-5% khi phát audio
```
