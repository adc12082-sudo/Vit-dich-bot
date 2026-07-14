# Discord Auto-Translate Bot (Việt ↔ Anh)

Bot tự động phát hiện tin nhắn tiếng Anh/Việt (kể cả từ **bot khác**), dịch sang
ngôn ngữ còn lại, xóa tin gốc và gửi lại bản dịch **giả danh đúng tên + avatar**
người/bot gửi gốc (qua Webhook) — nhìn như tin nhắn tự đổi ngôn ngữ.

Miễn phí 100%, không cần API key trả phí, không giới hạn số lượt dịch.

---

## Bước 1 — Tạo bot Discord (nếu chưa có)

1. Vào https://discord.com/developers/applications → **New Application**
2. Vào tab **Bot** → **Add Bot**
3. Bật 3 mục sau (rất quan trọng):
   - **MESSAGE CONTENT INTENT**
   - **SERVER MEMBERS INTENT** (không bắt buộc nhưng nên bật)
   - **PRESENCE INTENT** (không bắt buộc)
4. Bấm **Reset Token** để lấy Token → copy lại, dán vào file `.env` (mục `DISCORD_TOKEN`)
5. Vào tab **OAuth2 → URL Generator**:
   - Scopes: `bot`
   - Bot Permissions: `Manage Messages`, `Manage Webhooks`, `Read Message History`,
     `View Channels`, `Send Messages`
6. Copy link tạo ra, mở bằng trình duyệt → mời bot vào server của bạn

## Bước 2 — Cấu hình

```bash
cp .env.example .env
```

Mở file `.env`, điền `DISCORD_TOKEN`. Nếu muốn giới hạn bot chỉ dịch ở vài
kênh nhất định, điền ID kênh vào `TRANSLATE_CHANNEL_IDS` (cách nhau bằng dấu phẩy).
Để trống = áp dụng toàn server.

## Bước 3 — Chạy thử (test nhanh, kể cả trên điện thoại qua Termux)

```bash
pip install -r requirements.txt
python bot.py
```

Nếu thấy dòng `Bot đã sẵn sàng dịch tự động.` là thành công.

## Bước 4 — Host miễn phí 24/7 (để không cần bật máy/điện thoại liên tục)

Khuyên dùng **Railway** (free tier) vì dễ dùng nhất từ điện thoại:

1. Đưa code này lên GitHub (tạo repo mới, upload các file trong thư mục này)
2. Vào https://railway.app → đăng nhập bằng GitHub
3. **New Project → Deploy from GitHub repo** → chọn repo vừa tạo
4. Vào tab **Variables** → thêm biến `DISCORD_TOKEN` (và `TRANSLATE_CHANNEL_IDS` nếu cần)
5. Railway tự động cài đặt và chạy `python bot.py` — bot sẽ online 24/7

(Có thể làm toàn bộ bước này từ điện thoại qua trình duyệt, không cần máy tính.)

**Lựa chọn thay thế:** Render.com (free web service) — cách làm tương tự.

---

## Lưu ý quan trọng

- Bot cần quyền **Manage Messages** để xóa tin gốc, và **Manage Webhooks** để
  giả danh tên/avatar. Nếu thiếu quyền, bot sẽ báo lỗi trong log nhưng không crash.
- Vì dùng thư viện dịch miễn phí không chính thức (`deep-translator` gọi qua
  Google Translate web), nếu gọi **quá nhiều trong thời gian rất ngắn** có thể bị
  Google tạm chặn vài phút. Với quy mô 1 server Discord thông thường thì hiếm khi gặp.
- Bot chỉ dịch khi phát hiện rõ ràng là tiếng Anh hoặc tiếng Việt; tin nhắn
  ngôn ngữ khác, chỉ có link/emoji, hoặc dịch ra giống hệt bản gốc sẽ được bỏ qua.
- Đã có cơ chế chống lặp vô hạn (bot sẽ không tự dịch lại tin do chính nó gửi ra).
