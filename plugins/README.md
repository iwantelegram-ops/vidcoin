# Jual Video Bot — MongoDB Edition v4

Bot Telegram **Premium Sharing Video** berbasis [Pyrogram](https://docs.pyrogram.org/), dengan backend database **MongoDB (prioritas) + SQLite (fallback offline otomatis)**. Bot dirancang untuk berjalan di Termux / VPS Linux, mendukung multi-user, sistem koin, referral, broadcast video terjadwal, dan panel admin lengkap.

---

## Daftar Isi

1. [Fitur Utama](#fitur-utama)
2. [Struktur Proyek](#struktur-proyek)
3. [Persyaratan](#persyaratan)
4. [Instalasi & Setup](#instalasi--setup)
5. [Konfigurasi `.env`](#konfigurasi-env)
6. [Mekanisme Database](#mekanisme-database)
   - [Arsitektur Dual-Backend](#arsitektur-dual-backend)
   - [Struktur Tabel / Koleksi](#struktur-tabel--koleksi)
   - [Lokasi File Database](#lokasi-file-database)
   - [Lokasi File Runtime (Gambar, Download, Cache)](#lokasi-file-runtime)
   - [Cara Kerja MongoDB Translator](#cara-kerja-mongodb-translator)
   - [Fallback Otomatis saat Mongo Down](#fallback-otomatis-saat-mongo-down)
   - [Sistem Migrasi Data](#sistem-migrasi-data)
7. [Alur Bot](#alur-bot)
8. [Panel Admin](#panel-admin)
9. [Broadcast Terjadwal](#broadcast-terjadwal)
10. [Sistem Referral & Ghost Eye](#sistem-referral--ghost-eye)
11. [Perubahan v4 dari v3](#perubahan-v4-dari-v3)

---

## Fitur Utama

- **Upload & sharing video premium** — user menonton video dengan kuota durasi (Star)
- **Sistem koin & Star** — top-up Star untuk menambah durasi tonton
- **Blur kartu** — thumbnail video diblur sampai user membeli akses
- **Ghost Eye** — mata siluman; pemilik konten mendapat notifikasi diam-diam saat videonya ditonton
- **Sistem referral** — user mendapat Ghost Eye gratis tiap mengajak teman baru
- **Withdraw koin** — user bisa menarik koin ke owner, owner konfirmasi via inline button
- **Broadcast otomatis** — video dikirim ke semua user setiap slot jam WIB yang dikonfigurasi
- **Broadcast manual** — owner kirim pesan/video/foto ke semua user sekaligus
- **Panel admin lengkap** — statistik pengguna, kelola kurs, update QRIS, log channel, ban/unban
- **Restore session saat restart** — transaksi WD pending dan sesi tonton aktif dipulihkan otomatis
- **Backend dual** — MongoDB sebagai primary, SQLite sebagai fallback offline transparan

---

## Struktur Proyek

```
jual_video-mongo-v4/
├── main.py                  # Entry point — inisialisasi bot & restore session
├── config.py                # Semua konfigurasi & path (baca dari .env)
├── database.py              # Backend MongoDB + SQLite, migrasi, public API
├── keyboards.py             # Semua definisi keyboard inline Telegram
├── init_db.py               # Helper satu kali: inisialisasi database & direktori
├── requirements.txt         # Dependensi Python
├── .env.example             # Template konfigurasi lingkungan
├── .gitignore
└── plugins/
    ├── __init__.py
    ├── start.py             # /start, proses referral deep link
    ├── admin.py             # Panel admin, statistik, blast, kelola kurs/QRIS
    ├── video_handler.py     # Upload video, broadcast, sesi tonton, blur timer
    ├── photo_handler.py     # Upload bukti pembayaran top-up Star
    ├── callback_handler.py  # Router semua CallbackQuery inline button
    ├── text_handler.py      # Handler input teks (state machine)
    ├── user_menu.py         # Menu utama user, dompet, withdraw
    ├── chat_owner.py        # Pesan masuk ke bot → diteruskan ke owner
    ├── log_channel.py       # Kirim log ke channel Telegram
    ├── wd_guard.py          # Guard: cek WD pending sebelum aksi lain
    ├── btn_cleanup.py       # Bersihkan tombol inline pesan lama
    └── keyboard_guard.py    # Guard keyboard — tolak input di luar state
```

---

## Persyaratan

```
Python >= 3.9
pyrogram==2.0.106
TgCrypto
Pillow
pytz
python-dotenv
pymongo
dnspython
```

Install semua sekaligus:

```bash
pip install -r requirements.txt
```

---

## Instalasi & Setup

```bash
# 1. Clone atau ekstrak proyek
unzip jual_video-mongo-v4.zip
cd jual_video-mongo-v4

# 2. Install dependensi
pip install -r requirements.txt

# 3. Salin dan isi konfigurasi
cp .env.example .env
nano .env          # isi minimal: API_ID, API_HASH, BOT_TOKEN, OWNER_ID, CODE_BOT

# 4. Inisialisasi database & direktori (sekali saja)
python init_db.py

# 5. Jalankan bot
python main.py
```

---

## Konfigurasi `.env`

| Variabel | Wajib | Keterangan |
|---|---|---|
| `API_ID` | ✅ | Dari https://my.telegram.org/apps |
| `API_HASH` | ✅ | Dari https://my.telegram.org/apps |
| `BOT_TOKEN` | ✅ | Dari @BotFather |
| `OWNER_ID` | ✅ | User ID Telegram pemilik (angka) — dari @userinfobot |
| `CODE_BOT` | ✅ | Kode unik bot ini — digunakan sebagai nama database MongoDB dan subfolder data |
| `MONGO_URI` | opsional | URI MongoDB Atlas / lokal. Jika kosong, otomatis pakai SQLite |
| `SQLITE_PATH` | opsional | Path file `.db` lokal. Jika kosong, default ke `~/psv_bot_data/{CODE_BOT}/data.db` |
| `IMPORT_FROM` | opsional | Path database bot **lama** untuk migrasi satu kali. Kosongkan setelah berhasil |

### Format `SQLITE_PATH`

```env
# Nama saja (tanpa / dan tanpa .db) → ~/psv_bot_data/nama.db
SQLITE_PATH=jual_video

# Path relatif ke home
SQLITE_PATH=~/psv_data/toko.db

# Path absolut
SQLITE_PATH=/data/data/com.termux/files/home/psv/data.db
```

---

## Mekanisme Database

### Arsitektur Dual-Backend

Bot menggunakan dua backend database secara bersamaan:

```
┌─────────────────────────────────────────────────────────┐
│                     Aplikasi Bot                        │
│  q(sql)  /  q_atomic(*queries)  ← Public API           │
└────────────────────┬────────────────────────────────────┘
                     │
          ┌──────────▼──────────┐
          │   _USE_MONGO ?       │
          └──────┬──────┬───────┘
              Ya │      │ Tidak
                 │      │
    ┌────────────▼─┐  ┌─▼──────────────────┐
    │  _is_mongo   │  │  SQLite Backend     │
    │  _alive() ?  │  │  ~/psv_bot_data/    │
    └──┬───────┬───┘  │  {CODE_BOT}/data.db │
   Ya  │       │ Tidak└────────────────────┘
       │       │
  ┌────▼────┐  └──► SQLite (fallback)
  │ MongoDB │
  │ Backend │
  └─────────┘
```

**Aturan pemilihan backend:**

1. Saat startup, jika `MONGO_URI` diisi → coba koneksi MongoDB dengan timeout 5 detik.
   - Berhasil → `_USE_MONGO = True`; semua query dikirim ke MongoDB.
   - Gagal → `_USE_MONGO = False`; bot langsung pakai SQLite lokal, tanpa error ke pengguna.
2. Jika `MONGO_URI` kosong → langsung SQLite, tanpa mencoba MongoDB sama sekali.
3. Saat runtime, setiap query ke MongoDB didahului `_is_mongo_alive()` (ping 2 detik). Jika Mongo tidak merespons → query dialihkan ke SQLite lokal secara transparan.

SQLite **selalu diinisialisasi** meskipun MongoDB aktif, sehingga bot tetap menyimpan data lokal sebagai buffer offline.

---

### Struktur Tabel / Koleksi

Semua tabel SQLite memiliki koleksi MongoDB yang identik (field = kolom).

#### `users` — Data pengguna

| Field | Tipe | Keterangan |
|---|---|---|
| `user_id` | INTEGER / PK | Telegram User ID |
| `username` | TEXT | Username Telegram (tanpa @) |
| `first_name` | TEXT | Nama depan Telegram |
| `kuota_star` | INTEGER | Saldo Star untuk beli durasi tonton |
| `brangkas_durasi` | INTEGER | Total detik durasi yang sudah dibeli |
| `coin` | INTEGER | Koin yang bisa ditarik (withdraw) |
| `is_banned` | INTEGER | 0=aktif, 1=banned |
| `is_video_banned` | INTEGER | 0=aktif, 1=dilarang tonton video |
| `state` | TEXT | State mesin status user (misal: `MAIN_MENU`, `WAITING_WD_AMOUNT`) |
| `ghost_eye` | INTEGER | Jumlah Ghost Eye yang dimiliki |
| `total_stars_used` | INTEGER | Akumulasi total Star yang pernah dipakai |
| `last_star_used` | TEXT | Timestamp terakhir pakai Star (ISO 8601) |

#### `postingan` — Katalog video/konten

| Field | Tipe | Keterangan |
|---|---|---|
| `id` | INTEGER / PK / AUTOINCREMENT | ID internal postingan |
| `file_id` | TEXT / UNIQUE | Telegram file_id video (unik) |
| `creator_id` | INTEGER | User ID yang mengupload |
| `durasi` | INTEGER | Durasi video dalam detik |
| `caption` | TEXT | Keterangan video |
| `is_skipped` | INTEGER | 1 = dilewati dari broadcast |
| `blur_file_id` | TEXT | Telegram file_id thumbnail terblur (cache Telegram) |
| `file_type` | TEXT | Tipe konten: `video` atau `photo` |
| `pengirim_label` | TEXT | Label nama pengirim untuk caption |
| `thumb_file_id` | TEXT | Telegram file_id thumbnail asli |

> **Catatan field preserve:** `thumb_file_id`, `blur_file_id`, dan `pengirim_label` diperlakukan khusus saat migrasi — nilai non-kosong dari sumber **selalu menang**; nilai kosong tidak akan menimpa data MongoDB yang sudah terisi.

#### `settings` — Konfigurasi dinamis bot

| Field | Keterangan |
|---|---|
| `key` | Nama setting (PK) |
| `value` | Nilai setting |

Setting default yang diinisialisasi otomatis:

| Key | Default | Keterangan |
|---|---|---|
| `qris_link` | `assets/qris.jpg` | Path/link gambar QRIS pembayaran |
| `kurs_star_rp` | `5000` | Harga 1 paket Star dalam rupiah |
| `kurs_star_jml` | `20` | Jumlah Star per paket |
| `kurs_coin_rp` | `10000` | Nilai 1 koin dalam rupiah (untuk WD) |
| `kurs_coin_jml` | `50` | Jumlah koin per satuan WD |
| `broadcast_last_msg_id` | `0` | msg_id terakhir yang di-broadcast (untuk resume) |

#### `transaksi_wd` — Riwayat Withdraw Koin

| Field | Keterangan |
|---|---|
| `id` | ID transaksi (AUTOINCREMENT) |
| `user_id` | ID user yang WD |
| `jumlah_coin` | Jumlah koin yang ditarik |
| `status` | `PENDING` atau `DONE` |
| `done_user` | 1 = user sudah konfirmasi terima |
| `done_owner` | 1 = owner sudah proses |
| `msg_id_user` | ID pesan di chat user (untuk restore tombol) |
| `msg_id_owner` | ID pesan di chat owner (untuk restore tombol) |

#### `watch_sessions` — Sesi Tonton Aktif

| Field | Keterangan |
|---|---|
| `user_id` | ID user |
| `video_msg_id` | ID pesan video yang sedang ditonton |
| `postingan_id` | ID postingan |
| `creator_id` | ID kreator video |
| `expired_at` | Waktu kadaluarsa sesi (ISO 8601 UTC) |
| `status` | `ACTIVE` atau `EXPIRED` |

Composite PK: `(user_id, video_msg_id)`

#### `kartu_blur` — Kartu Akses Blur per User per Video

| Field | Keterangan |
|---|---|
| `user_id` | ID user |
| `postingan_id` | ID postingan |
| `msg_id` | ID pesan kartu yang dikirim ke user |
| `sudah_dibuka` | 1 = user sudah beli akses |
| `sent_at` | Timestamp pengiriman kartu (ISO 8601) |

Composite PK: `(user_id, postingan_id)`

#### `invite_links` — Kode Referral

| Field | Keterangan |
|---|---|
| `code` | Kode unik link referral (PK) |
| `owner_id` | ID user pembuat link |
| `created_at` | Timestamp pembuatan (ISO 8601) |
| `used` | 1 = sudah terpakai |

Link referral otomatis dihapus setelah 1 jam jika belum terpakai.

#### `kartu_blur` — Log Pesan Channel

Tabel `log_channel_msgs` menyimpan mapping antara pesan di log channel dan postingan:

| Field | Keterangan |
|---|---|
| `msg_id` | ID pesan di channel Telegram |
| `channel_id` | ID channel Telegram |
| `postingan_id` | ID postingan terkait |

Composite PK: `(msg_id, channel_id)`

#### `counters` — Auto-increment MongoDB

Karena MongoDB tidak punya `AUTOINCREMENT` bawaan, koleksi khusus `counters` digunakan sebagai generator ID:

| `_id` | `seq` |
|---|---|
| `postingan` | Nilai ID terakhir untuk tabel postingan |
| `transaksi_wd` | Nilai ID terakhir untuk tabel transaksi_wd |

Setiap insert baru memanggil `_next_id(col_name)` yang melakukan atomic `$inc` pada koleksi ini.

---

### Lokasi File Database

Semua path ditentukan berdasarkan variabel `CODE_BOT` dan `SQLITE_PATH` di `.env`.

#### Path default (tanpa konfigurasi manual):

```
~/psv_bot_data/{CODE_BOT}/
├── data.db              ← File SQLite utama (DB_PATH)
├── psv_bot.session      ← File sesi Pyrogram (otomatis dibuat Pyrogram)
├── psv_bot.session-journal  ← Journal WAL SQLite sesi
├── downloads/           ← Video sementara saat upload (DOWNLOADS_DIR)
├── assets/
│   ├── default_thumb.jpg    ← Thumbnail placeholder default
│   ├── qris.jpg             ← Gambar QRIS pembayaran (QRIS_DEFAULT)
│   └── blur_cache/          ← Cache thumbnail terblur (BLUR_CACHE_DIR)
│       └── {file_id}.jpg    ← Thumbnail terblur per video
```

Contoh nyata di Termux jika `CODE_BOT=tokoku`:

```
/data/data/com.termux/files/home/psv_bot_data/tokoku/
├── data.db
├── psv_bot.session
├── downloads/
└── assets/
    ├── default_thumb.jpg
    ├── qris.jpg
    └── blur_cache/
```

#### Nama database MongoDB:

```
psv_bot_{CODE_BOT}
```

Contoh: `CODE_BOT=tokoku` → database MongoDB bernama `psv_bot_tokoku`.

Semua koleksi (`users`, `postingan`, `settings`, dst.) berada di dalam database tersebut.

---

### Lokasi File Runtime

File-file yang dihasilkan bot selama berjalan:

| File / Direktori | Keterangan |
|---|---|
| `downloads/{file_id}.mp4` | Video sementara saat owner upload — dihapus otomatis setelah selesai diproses |
| `assets/default_thumb.jpg` | Thumbnail placeholder (gradient biru-ungu + ikon play). Dibuat otomatis saat startup jika tidak ada atau ukuran < 5 KB |
| `assets/qris.jpg` | Gambar QRIS untuk pembayaran top-up. Upload via panel admin `/update_qris` |
| `assets/blur_cache/{postingan_id}.jpg` | Cache thumbnail terblur per video. Dibuat saat pertama kali video dibroadcast, digunakan ulang untuk pengiriman berikutnya. **Disimpan sebagai file lokal**, bukan di Telegram, agar tidak perlu blur ulang |

> File video di `downloads/` bersifat **sementara** — hanya ada selama proses upload berlangsung dan langsung dihapus setelah `file_id` Telegram berhasil diperoleh.

---

### Cara Kerja MongoDB Translator

`database.py` mengimplementasikan **mini SQL-to-MongoDB translator** agar seluruh kode plugin dapat menulis query dalam sintaks SQL standar, yang kemudian diterjemahkan secara otomatis ke operasi MongoDB.

Fungsi publik yang digunakan di seluruh plugin hanya dua:

```python
q(sql, params, commit=False)         # satu query
q_atomic(*queries)                    # beberapa query atomik
```

Di balik layar, setiap query SQL diparse dengan regex dan dipetakan ke operasi MongoDB:

| SQL | MongoDB |
|---|---|
| `SELECT ... FROM tbl WHERE ...` | `collection.find(filter, projection)` |
| `INSERT INTO tbl (...) VALUES (...)` | `collection.insert_one(doc)` |
| `INSERT OR REPLACE INTO ...` | `collection.replace_one(filter, doc, upsert=True)` |
| `INSERT OR IGNORE INTO ...` | `collection.update_one(filter, {$setOnInsert: doc}, upsert=True)` |
| `UPDATE tbl SET col=col+? WHERE ...` | `collection.update_many(filter, {$inc: {col: val}})` |
| `UPDATE tbl SET col=? WHERE ...` | `collection.update_many(filter, {$set: {col: val}})` |
| `DELETE FROM tbl WHERE ...` | `collection.delete_many(filter)` |
| `DELETE FROM tbl` (tanpa WHERE) | `collection.delete_many({})` |
| `SELECT COUNT(*) FROM tbl WHERE ...` | `collection.count_documents(filter)` |
| `CREATE TABLE IF NOT EXISTS ...` | *(diabaikan — MongoDB tidak butuh DDL)* |
| `ALTER TABLE ...` | *(diabaikan)* |

**Operator WHERE yang didukung:** `=`, `!=`, `>`, `>=`, `<=`, `AND` (multi-kondisi).

**`ORDER BY`** diterjemahkan ke `.sort()`. **`LIMIT`** diterjemahkan ke slicing Python setelah query.

**Atomisitas di MongoDB** (`q_atomic`): karena MongoDB tidak mendukung multi-collection transaction pada cluster gratis (M0), atomisitas diimplementasikan dengan fase validasi dua tahap:
1. **Fase validasi**: setiap UPDATE bersyarat (misal: `kuota_star >= 1`) dicek terlebih dahulu apakah dokumen memenuhi syarat.
2. **Fase eksekusi**: jika semua validasi lolos, semua query dieksekusi satu per satu.

Jika validasi gagal, tidak ada query yang dieksekusi — return `False`.

---

### Fallback Otomatis saat Mongo Down

Saat MongoDB tidak dapat dijangkau di tengah runtime (bukan hanya saat startup), bot mendeteksinya secara otomatis:

```
Bot sedang jalan → user kirim pesan
    │
    ▼
q(sql) dipanggil
    │
    ▼
_is_mongo_alive() → ping MongoDB (timeout 2 detik)
    │
    ├── Berhasil → query ke MongoDB (normal)
    │
    └── Gagal → _mongo_down = True
               query dialihkan ke SQLite lokal
               (data tetap tersimpan di DB_PATH)
               │
               ▼
         Saat bot restart & Mongo kembali online:
         run_migrations() memindahkan data SQLite
         yang terkumpul ke MongoDB
         (duplikat dihapus otomatis)
```

**Implikasi:** user tidak merasakan gangguan — bot tetap merespons dan data tidak hilang. Saat Mongo kembali online dan bot di-restart, semua data offline otomatis tersinkron ke MongoDB.

---

### Sistem Migrasi Data

Migrasi dijalankan otomatis setiap kali `init_db()` dipanggil (saat bot start), melalui fungsi `run_migrations()`.

#### Dua skenario migrasi:

**Tahap 1 — `IMPORT_FROM` → `DB_PATH` (selalu SQLite ke SQLite)**

Digunakan untuk memindahkan data dari **bot lama** ke bot baru sebelum naik ke MongoDB.

```
IMPORT_FROM (file .db lama)
        │
        ▼  INSERT OR REPLACE per baris per tabel
    DB_PATH (SQLite lokal baru)
        │
        ▼  (file IMPORT_FROM dikosongkan setelah selesai)
```

**Tahap 2 — `DB_PATH` → MongoDB (hanya jika MongoDB aktif)**

Digunakan untuk menaikkan data SQLite lokal ke MongoDB sebagai database utama.

```
DB_PATH (SQLite lokal)
        │
        ▼  upsert per dokumen dengan aturan field-level
    MongoDB psv_bot_{CODE_BOT}
        │
        ▼  _deduplicate_mongo() → hapus dokumen duplikat
        ▼  DB_PATH dikosongkan (tabel tetap ada, data dihapus)
```

#### Contoh alur lengkap migrasi dari bot lama:

```env
# .env — konfigurasi migrasi
IMPORT_FROM=~/downloads/bot_lama/jual_video.db   ← sumber data lama
SQLITE_PATH=jual_video                            ← transit lokal baru
MONGO_URI=mongodb+srv://user:pass@cluster...      ← tujuan akhir
CODE_BOT=tokoku
```

```
Startup pertama:
  Tahap 1: ~/downloads/bot_lama/jual_video.db
               ↓ (baca semua tabel, INSERT OR REPLACE)
           ~/psv_bot_data/jual_video.db  ← DB_PATH
               ↓ (file sumber dikosongkan)
  Tahap 2: ~/psv_bot_data/jual_video.db
               ↓ (upsert ke MongoDB, field-level merge)
           MongoDB: psv_bot_tokoku
               ↓ (duplikat dihapus, DB_PATH dikosongkan)

Startup berikutnya:
  Tahap 1: IMPORT_FROM tidak ada data → dilewati
  Tahap 2: DB_PATH tidak ada data → dilewati
  Bot langsung pakai MongoDB.
```

#### Aturan merge field saat migrasi ke MongoDB:

| Tipe Field | Aturan |
|---|---|
| Field biasa (`kuota_star`, `state`, `coin`, dll.) | **Selalu ditulis** dari sumber ke MongoDB — data terbaru menang |
| Field preserve: `thumb_file_id`, `blur_file_id`, `pengirim_label` | Nilai **non-kosong** dari sumber → overwrite MongoDB |
| Field preserve: nilai **kosong** dari sumber | Gunakan `$setOnInsert` — tidak menimpa nilai MongoDB yang sudah terisi |

Dengan aturan ini, migrasi parsial (bot sudah sempat berjalan di MongoDB) tidak akan merusak data thumbnail atau label yang sudah lebih lengkap di MongoDB.

#### Deduplication MongoDB:

Setelah setiap migrasi ke MongoDB, fungsi `_deduplicate_mongo()` dijalankan otomatis:

- Untuk tabel dengan single PK (`users`, `postingan`, `settings`, `transaksi_wd`, `invite_links`): dokumen dengan PK duplikat → **pertahankan yang `_id` terbesar** (paling baru), hapus yang lama.
- Untuk tabel composite key (`watch_sessions`, `kartu_blur`, `log_channel_msgs`): duplikat composite key → pertahankan dokumen terbaru.

#### Inisialisasi indeks MongoDB:

Saat `_init_mongo()` dipanggil, indeks unique dibuat otomatis:

```python
users            → unique index: user_id
postingan        → unique index: file_id (sparse), id (sparse)
settings         → unique index: key
transaksi_wd     → unique index: id (sparse)
watch_sessions   → unique compound: (user_id, video_msg_id)
kartu_blur       → unique compound: (user_id, postingan_id)
invite_links     → unique index: code
log_channel_msgs → unique compound: (msg_id, channel_id)
counters         → unique index: _id
```

---

## Alur Bot

```
User kirim /start
    │
    ▼
ensure_user() — buat record jika belum ada
    │
    ├── Ada kode referral di deep link?
    │       ↓ Ya
    │   Validasi kode, beri Ghost Eye ke referrer (+10 per undangan)
    │
    ▼
Tampilkan main menu (keyboard inline)
    │
    ├── 🎬 Tonton Video
    │       ↓
    │   Pilih video → cek kartu_blur → kirim thumbnail blur + tombol Tonton
    │       ↓ user klik Tonton
    │   Cek kuota Star → buat watch_session → kirim video + timer
    │       ↓ timer habis
    │   _expire() → hapus pesan video, update kartu_blur.sudah_dibuka
    │
    ├── ⭐ Top-up Star
    │       ↓
    │   User kirim bukti bayar (foto)
    │       ↓ owner konfirmasi
    │   q_atomic: kurangi kuota owner + tambah Star user
    │
    └── 💰 Withdraw Koin
            ↓
        User input jumlah koin
            ↓
        Insert transaksi_wd (PENDING)
            ↓ owner klik Proses
        Status DONE, user notifikasi
```

---

## Panel Admin

Akses hanya untuk `OWNER_ID`. Perintah yang tersedia:

| Perintah | Fungsi |
|---|---|
| `/admin` | Tampilkan panel admin |
| `/stats` | Statistik pengguna (top Star, top coin, dsb.) |
| `/wallet {uid} {+/-}{jumlah} {star\|coin\|ghost}` | Sesuaikan saldo user |
| `/del_id {file_id}` | Hapus postingan dari database |
| `/ghost_eye` | Kelola menu Ghost Eye |
| `/update_qris` | Upload gambar QRIS baru |
| `/update_kurs` | Ubah kurs Star/koin |
| `/log_channel` | Set/unset channel log |
| `/broadcast` | Broadcast manual ke semua user |

---

## Broadcast Terjadwal

Bot secara otomatis mengirim video ke semua user pada slot jam WIB yang dikonfigurasi di `config.py`:

```python
BROADCAST_SLOT_HOURS = (0, 3, 6, 9, 12, 15, 18, 21)  # setiap 3 jam
```

Mekanisme:
1. `broadcast_scheduler()` berjalan sebagai asyncio task sejak startup.
2. Setiap slot jam, `_do_broadcast_from_channel()` dipanggil.
3. Bot mengambil daftar video dari `log_channel_msgs` (tabel DB lokal — bukan dari `get_chat_history` yang tidak bisa dipakai bot).
4. Satu video dipilih secara acak dan dikirim ke semua user aktif dengan delay `_SEND_DELAY = 0.065 detik` per user (≈ 15 msg/detik, batas aman Telegram).
5. `broadcast_last_msg_id` di `settings` diperbarui setiap broadcast agar bisa resume.

---

## Sistem Referral & Ghost Eye

- Setiap user mendapat link referral unik via `/start?code=XXXX` (deep link).
- Link berlaku **1 jam** sejak dibuat dan hanya bisa dipakai **sekali**.
- Saat user baru mendaftar via link referral → **referrer mendapat `GHOST_EYE_PER_REFERRAL` (default: 10) Ghost Eye**.
- **Ghost Eye** memungkinkan pemilik konten melihat siapa yang menonton videonya secara diam-diam (notifikasi tersembunyi ke creator_id).
- Ghost Eye bisa digunakan sebagai pengganti Star untuk menambah durasi tonton (fitur `addtime_ghost`).

---

## Perubahan v4 dari v3

### `database.py`
- **Migrasi penuh ke Mongo** — `_push_to_mongo` kini menggunakan logika field-level: field biasa selalu ditulis, field preserve (`thumb_file_id`, `blur_file_id`, `pengirim_label`) hanya di-overwrite jika nilai sumber non-kosong.
- **`_mongo_delete`** — Sekarang mendukung `DELETE FROM table` tanpa `WHERE` (hapus semua dokumen) — fix crash saat reset.
- **`_mongo_q_atomic_smart`** — Fix logika validasi UPDATE bersyarat — hitung `?` di bagian SET dengan benar sebelum parse WHERE.
- **`_ensure_sqlite_tables`** — Fungsi baru yang dapat dipanggil dari koneksi eksternal — dipakai `_push_to_sqlite` agar tabel selalu ada di DB tujuan.
- **`_init_sqlite`** — Menggunakan `_ensure_sqlite_tables` — eliminasi duplikasi kode.
- Rename `_sqlite_user_count` → `_sqlite_row_count` (hitung semua tabel, bukan hanya `users`).
- Rename `_run_migration` → `_run_migration_to_mongo` (nama lebih eksplisit).

### `plugins/photo_handler.py`
- Fix: `INSERT INTO transaksi_wd` kini tanpa kolom `id` eksplisit — MongoDB counter (`_next_id`) dipanggil otomatis oleh `_mongo_insert`.
- Fix: Query ambil `tx_id` setelah insert kini aman untuk Mongo (cari berdasarkan `user_id + status + done_user + done_owner ORDER BY id DESC LIMIT 1`).

### `plugins/admin.py`
- Fix: Query `UPDATE users SET {field}` diganti dengan mapping SQL eksplisit (`_SQL_ADD`, `_SQL_GET`, `_SQL_SET`) — menghilangkan potensi SQL injection dan kompatibel dengan Mongo translator.

### `plugins/reset_db.py`
- Fix: Import `_mdb` dari `database` menggunakan `try/except` yang benar — tidak crash jika Mongo down.
- Fix: `DELETE FROM table` (tanpa WHERE) kini berfungsi di Mongo backend.

### `plugins/video_handler.py`
- `_fetch_all_channel_videos` menggunakan tabel `log_channel_msgs` (DB lokal) sebagai ganti `client.get_chat_history()` yang tidak bisa dipakai bot.
- `_kirim_log_channel` menyimpan `msg_id` ke `log_channel_msgs` setelah berhasil kirim ke channel.
- `kirim_video_selamat_datang` juga menggunakan DB, bukan `get_chat_history`.
