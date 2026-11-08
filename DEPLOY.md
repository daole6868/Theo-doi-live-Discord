# 🚀 Deploy Gachaz Live Bot lên VPS Cloudfly

Pattern giống bot `fb-monitor` đã deploy trước, chỉ thêm bước cài FFmpeg + libopus cho voice.

> **Đang nâng cấp từ bản cũ?** Đọc mục [11. Nâng cấp từ bản cũ](#11-nâng-cấp-từ-bản-cũ) trước.

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
  sqlite3 git nano
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
ADMIN_ROLE_ID=0
USER_CHANNEL_ID=xxx
LEADERBOARD_CHANNEL_ID=xxx
LATE_ALERT_CHANNEL_ID=xxx
CHECK_CTV_CHANNEL_ID=xxx
```

- `GUILD_ID`: bot **chỉ** tính giờ / nhắc trong server này.
- `ADMIN_ROLE_ID`: role quản lý được bấm Admin Dashboard (ngoài người có quyền Administrator). Để `0` nếu chỉ cho Administrator.

Save: `Ctrl+O` → `Enter` → `Ctrl+X`.

---

## 6. ⚠️ Discord Developer Portal (BẮT BUỘC)

Vào https://discord.com/developers/applications → chọn bot:

1. **Bot** → mục **Privileged Gateway Intents** → bật **SERVER MEMBERS INTENT** → Save.
   Thiếu bước này bot sẽ **không khởi động được** (lỗi `PrivilegedIntentsRequired`).
   Bot **không** cần Message Content Intent nữa.
2. **OAuth2 → URL Generator** → tick scope **`bot`** và **`applications.commands`**
   (để có lệnh `/setup`) → tick các quyền ở mục 10 → mở link để mời lại bot vào server.
   Bot đã có trong server thì mời lại bằng link mới cũng được, không mất gì.

---

## 7. Test chạy 1 lần

```bash
python main.py
```

Nếu thấy `✅ Bot đã đăng nhập: [tên bot]` → OK, `Ctrl+C` để dừng.

Lần đầu chạy, vào kênh admin gõ **`/setup`** để bot gửi các panel.

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

**Chạy bộ test tự động (không cần token thật):**
```bash
python -m unittest discover -s tests -t .
```

---

## 8. Setup systemd auto-restart

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

## 9. Xem log / Restart / Stop

```bash
# Log systemd (real-time)
sudo journalctl -u gachaz-bot -f

# Log file bot (có rotate 5MB × 5)
tail -f /opt/gachaz-live-bot/logs/bot.log

# Restart (khi update code) / Stop / Start
sudo systemctl restart gachaz-bot
sudo systemctl stop gachaz-bot
sudo systemctl start gachaz-bot
```

Restart / stop bằng systemctl là **an toàn**: bot ghi lại mốc thời gian trước khi tắt,
phiên live đang chạy được chốt đúng lúc tắt, khoảng bot offline **không** được tính giờ.

---

## 10. ⚠️ QUAN TRỌNG: Discord Permissions cho Bot Role

Vào Discord Server → **Server Settings** → **Roles** → chọn Role của Bot → **Permissions**:

**Bắt buộc:**
- ✅ View Channel
- ✅ Send Messages
- ✅ Read Message History
- ✅ Embed Links
- ✅ Manage Channels *(tạo/xóa voice channel)*
- ✅ Manage Roles *(tạo/xóa role, gán role — role bot phải cao hơn role sẽ quản)*
- ✅ **Move Members** *(Voice Alert ngắt CTV khỏi voice)*
- ✅ **Connect** *(bot vào voice channel để phát nhắc)*
- ✅ **Speak** *(phát giọng nói)*

Voice Alert giờ áp dụng cho **mọi kênh voice** (trừ kênh AFK) → bot cần Connect/Speak
ở mọi kênh voice CTV có thể vào. Kênh nào chặn bot thì bot vẫn ngắt được (Move Members)
nhưng không phát được giọng nhắc.

Nếu thiếu bất kỳ permission nào → tính năng đó sẽ fail (log sẽ báo `Forbidden`).

---

## 11. Nâng cấp từ bản cũ

```bash
cd /opt/gachaz-live-bot

# 1. Sao lưu dữ liệu trước
mkdir -p /root/backup
tar czf /root/backup/gachaz-data-truoc-nang-cap.tar.gz data/ .env

# 2. Lấy code mới (git pull, hoặc scp đè code — KHÔNG đè thư mục data/ và file .env)
git pull

# 3. Cài thư viện theo phiên bản mới + cập nhật file systemd
source .venv/bin/activate
pip install -r requirements.txt
sudo cp gachaz-bot.service /etc/systemd/system/
sudo systemctl daemon-reload

# 4. Làm mục 6 (bật Server Members Intent + mời lại bot với scope applications.commands)

# 5. Khởi động lại
sudo systemctl restart gachaz-bot
```

**Lần đầu chạy bản mới, bot tự làm:**
- Chuyển lịch sử live từ `data/data_sessions.json` sang `data/sessions.db` (SQLite, giữ vĩnh viễn).
  File JSON cũ được đổi tên thành `data_sessions.json.migrated` để đối chiếu nếu cần.
- Chuyển lịch live tiếp theo sang dạng gắn theo từng CTV.
- Phiên đang live lúc nâng cấp: CTV vẫn đang live thì tính tiếp; phiên cũ không xác minh được
  (vd. "phiên ma" do lỗi bản cũ) thì bỏ, không cộng giờ ảo.
- Gửi lại các panel 1 lần (panel Check CTV đổi sang ô chọn thành viên).
- Không gửi trùng bảng xếp hạng hôm qua.

Lệnh `!setup` cũ đã thay bằng **`/setup`**.

---

## 🔧 Troubleshooting

### Bot không khởi động, log báo `PrivilegedIntentsRequired`
→ Chưa bật **Server Members Intent** (mục 6).

### Không thấy lệnh `/setup`
→ Mời lại bot với scope `applications.commands` (mục 6), kiểm tra `GUILD_ID` trong `.env`
đúng server, rồi restart bot. Lệnh chỉ hiện với người có quyền Administrator.

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
├── data/                     ← Dữ liệu (tự tạo)
│   ├── sessions.db           ← Lịch sử live + audio (SQLite, giữ vĩnh viễn)
│   └── data_*.json           ← Kênh, CTV, khách đăng ký, cài đặt...
├── logs/                     ← Bot log (rotate 5MB)
├── core/ helpers/ views/ cogs/
└── tests/                    ← Test tự động

/etc/systemd/system/
└── gachaz-bot.service        ← Bản copy từ file trên
```

---

## 💡 Tips

**Có 2 bot cùng VPS (bot này + fb-monitor):**
- Service name khác nhau (`gachaz-bot` vs `fb-monitor`) → không conflict
- Working dir khác nhau → không conflict
- Log riêng → dễ quản lý
- Không cần chia CPU/RAM đặc biệt

**Backup data mỗi ngày (2h sáng):**
```bash
mkdir -p /root/backup
crontab -e
# Thêm dòng sau (sao lưu SQLite an toàn khi bot đang chạy + các file JSON):
0 2 * * * cd /opt/gachaz-live-bot && sqlite3 data/sessions.db ".backup data/sessions-backup.db" && tar czf /root/backup/gachaz-data-$(date +\%Y\%m\%d).tar.gz data/*.json data/sessions-backup.db
```

**Kiểm tra RAM/CPU bot:**
```bash
# Bot dùng bao nhiêu RAM
ps aux | grep 'python main.py' | grep -v grep

# Discord voice thường tốn ~50-100MB RAM, CPU ~1-5% khi phát audio
```
