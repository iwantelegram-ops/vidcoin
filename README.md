# Jual Video Bot — MongoDB Edition v4

Bot Premium Sharing Video dengan backend MongoDB (prioritas) + SQLite (fallback offline).

---

## Perubahan v4 (dari v3)

### database.py
- **Migrasi penuh ke Mongo**: `_push_to_mongo` kini menggunakan logika field-level:
  - Field biasa (kuota_star, state, coin, dll.) → **selalu ditulis** dari SQLite ke Mongo (data terbaru menang)
  - Field preserve (thumb_file_id, blur_file_id, pengirim_label):
    - Nilai **non-kosong** dari SQLite → overwrite Mongo
    - Nilai **kosong** dari SQLite → `$setOnInsert` (tidak menimpa nilai Mongo yang sudah ada)
- **`_mongo_delete`**: Sekarang mendukung `DELETE FROM table` **tanpa WHERE** (hapus semua dokumen) — fix crash saat reset
- **`_mongo_q_atomic_smart`**: Fix logika validasi UPDATE bersyarat — hitung `?` di bagian SET dengan benar sebelum parse WHERE
- **`_ensure_sqlite_tables`**: Fungsi baru yang dapat dipanggil dari koneksi eksternal — dipakai `_push_to_sqlite` agar tabel selalu ada di DB tujuan
- **`_init_sqlite`**: Menggunakan `_ensure_sqlite_tables` — menghilangkan duplikasi kode
- Rename `_sqlite_user_count` → `_sqlite_row_count` (lebih akurat, hitung semua tabel)
- Rename `_run_migration` → `_run_migration_to_mongo` (lebih eksplisit)

### plugins/photo_handler.py
- Fix: `INSERT INTO transaksi_wd` kini tanpa kolom `id` eksplisit — MongoDB counter (`_next_id`) dipanggil otomatis oleh `_mongo_insert`
- Fix: Query ambil `tx_id` setelah insert kini aman untuk Mongo (cari berdasarkan `user_id + status + done_user + done_owner ORDER BY id DESC LIMIT 1`)

### plugins/admin.py
- Fix: Query `UPDATE users SET {field}` diganti dengan mapping SQL eksplisit (`_SQL_ADD`, `_SQL_GET`, `_SQL_SET`) — menghilangkan potensi SQL injection dan kompatibel dengan Mongo translator

### plugins/reset_db.py
- Fix: Import `_mdb` dari `database` menggunakan `try/except` yang benar — tidak crash jika Mongo down
- Fix: `DELETE FROM table` (tanpa WHERE) kini berfungsi di Mongo backend

---

## Setup

1. Copy `.env.example` ke `.env` dan isi semua nilai
2. Jalankan: `python init_db.py`
3. Jalankan: `python main.py`

### Variabel .env penting

```env
API_ID=...
API_HASH=...
BOT_TOKEN=...
OWNER_ID=...
CODE_BOT=nama_unik_bot

# MongoDB (prioritas — isi untuk aktifkan)
MONGO_URI=mongodb+srv://user:pass@cluster.mongodb.net/

# SQLite path (opsional — default: ~/psv_bot_data/{CODE_BOT}/data.db)
SQLITE_PATH=

# Migrasi satu kali dari DB lama (hapus setelah berhasil)
IMPORT_FROM=
```

### Alur migrasi data saat startup

```
IMPORT_FROM (SQLite lama)
        ↓  Tahap 1: selalu lokal ke lokal
    DB_PATH (SQLite lokal)
        ↓  Tahap 2: hanya jika MongoDB aktif
    MongoDB  ← tujuan akhir
```

- Detail per-field yang **lebih baru menang** (bukan nama file eksternal sebagai acuan)
- Duplikat di MongoDB otomatis dihapus setelah migrasi

---

## Persyaratan

```
pyrogram==2.0.106
TgCrypto
Pillow
pytz
python-dotenv
pymongo
dnspython
```
