"""
database.py — Backend MongoDB (prioritas) + SQLite (fallback otomatis).

Logika backend:
  • Jika MONGO_URI diset di .env → coba koneksi MongoDB saat startup.
    - Berhasil  → _USE_MONGO=True, semua query ke Mongo.
    - Gagal     → _USE_MONGO=False, semua query ke SQLite lokal.

  • Jika MONGO_URI kosong → langsung pakai SQLite.

Fallback otomatis saat Mongo DOWN di tengah jalan:
  • Setiap query Mongo yang gagal otomatis di-retry ke SQLite lokal.
  • Data tidak hilang — tersimpan di DB_PATH sebagai backup.
  • Saat bot restart dan Mongo kembali online, run_migrations() akan
    memindahkan data lokal tersebut ke Mongo (duplikat dihapus).

Migrasi otomatis saat startup (run_migrations):
  1. Jika IMPORT_FROM diset → baca data dari file lama, pindahkan ke DB_PATH
     (selalu SQLite lokal dulu), lalu kosongkan file sumber.
  2. Jika DB_PATH punya data DAN MongoDB aktif → pindahkan ke MongoDB,
     lalu kosongkan DB_PATH.
  Duplikat di MongoDB selalu dihapus setelah import.
  Saat migrasi: detail per-field (uid, dll) yang TERBARU menang — bukan
  nama file eksternal.
"""
import os
import re
import sqlite3
from datetime import datetime, timezone, timedelta

from config import DB_PATH, BOT_DATA_DIR, MONGO_URI, CODE_BOT, IMPORT_FROM

os.makedirs(BOT_DATA_DIR, exist_ok=True)

# ══════════════════════════════════════════════════════════════════════════════
# Deteksi & inisialisasi backend
# ══════════════════════════════════════════════════════════════════════════════

_USE_MONGO = False
_mdb       = None

if MONGO_URI:
    try:
        import dns.resolver
        dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
        dns.resolver.default_resolver.nameservers = ['8.8.8.8', '8.8.4.4']

        import pymongo
        _client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        _client.admin.command("ping")
        _mdb = _client[f"psv_bot_{CODE_BOT}"]
        _USE_MONGO = True
        print(f"✅ MongoDB aktif: database 'psv_bot_{CODE_BOT}'")
    except Exception as _e:
        print(f"⚠️  MongoDB gagal ({_e}) — fallback ke SQLite.")
        _USE_MONGO = False

if not _USE_MONGO:
    print(f"📁 SQLite aktif: {DB_PATH}")


# ══════════════════════════════════════════════════════════════════════════════
# SQLite backend
# ══════════════════════════════════════════════════════════════════════════════

def _sqlite_conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _sqlite_q(sql: str, params: tuple = (), *, commit: bool = False):
    con = _sqlite_conn()
    try:
        cur = con.execute(sql, params)
        rows = cur.fetchall()
        if commit:
            con.commit()
        return rows
    except Exception as e:
        print(f"[SQLite] {e} | {sql[:80]}")
        return []
    finally:
        con.close()


def _sqlite_q_atomic(*queries) -> bool:
    con = _sqlite_conn()
    try:
        for sql, params in queries:
            con.execute(sql, params)
        con.commit()
        return True
    except Exception as e:
        try:
            con.rollback()
        except Exception:
            pass
        print(f"[SQLite TX] Rollback: {e}")
        return False
    finally:
        con.close()


# ══════════════════════════════════════════════════════════════════════════════
# MongoDB backend — SQL mini-translator
# ══════════════════════════════════════════════════════════════════════════════

def _next_id(col_name: str) -> int:
    result = _mdb["counters"].find_one_and_update(
        {"_id": col_name}, {"$inc": {"seq": 1}}, upsert=True, return_document=True,
    )
    return result["seq"]


def _parse_where(where_str: str, params: list, param_idx: int) -> tuple:
    flt = {}
    if not where_str.strip():
        return flt, param_idx
    for cond in re.split(r'\s+AND\s+', where_str.strip(), flags=re.IGNORECASE):
        cond = cond.strip().rstrip(")")
        if not cond:
            continue
        m = re.match(r"(\w+)\s*=\s*\?", cond)
        if m:
            flt[m.group(1)] = params[param_idx] if param_idx < len(params) else None
            param_idx += 1; continue
        m = re.match(r"(\w+)\s*!=\s*\?", cond)
        if m:
            flt[m.group(1)] = {"$ne": params[param_idx] if param_idx < len(params) else None}
            param_idx += 1; continue
        m = re.match(r"(\w+)\s*>\s*\?", cond)
        if m:
            val = params[param_idx] if param_idx < len(params) else None; param_idx += 1
            flt[m.group(1)] = {**flt.get(m.group(1), {}), "$gt": val} if isinstance(flt.get(m.group(1)), dict) else {"$gt": val}; continue
        m = re.match(r"(\w+)\s*>=\s*\?", cond)
        if m:
            val = params[param_idx] if param_idx < len(params) else None; param_idx += 1
            flt[m.group(1)] = {**flt.get(m.group(1), {}), "$gte": val} if isinstance(flt.get(m.group(1)), dict) else {"$gte": val}; continue
        m = re.match(r"(\w+)\s*<=\s*\?", cond)
        if m:
            val = params[param_idx] if param_idx < len(params) else None; param_idx += 1
            flt[m.group(1)] = {**flt.get(m.group(1), {}), "$lte": val} if isinstance(flt.get(m.group(1)), dict) else {"$lte": val}; continue
        m = re.match(r"(\w+)\s*=\s*'([^']*)'", cond)
        if m:
            flt[m.group(1)] = m.group(2); continue
        m = re.match(r"(\w+)\s*=\s*(\d+)$", cond)
        if m:
            flt[m.group(1)] = int(m.group(2)); continue
        m = re.match(r"(\w+)\s*!=\s*(\d+)$", cond)
        if m:
            flt[m.group(1)] = {"$ne": int(m.group(2))}; continue
    return flt, param_idx


def _extract_where(sql: str) -> str:
    m = re.search(r'\bWHERE\b\s+(.+?)(?:\s+ORDER\s+BY|\s+LIMIT\s|\s*$)', sql, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""


def _mongo_insert(sql: str, params: tuple):
    m = re.match(
        r'INSERT\s+(OR\s+(?:REPLACE|IGNORE)\s+)?INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)',
        sql, re.IGNORECASE
    )
    if not m:
        return []
    mode  = (m.group(1) or "").upper().strip()
    table = m.group(2)
    cols  = [c.strip() for c in m.group(3).split(",")]
    vals  = []
    p_idx = 0
    for tok in [t.strip() for t in m.group(4).split(",")]:
        if tok == "?":
            vals.append(params[p_idx] if p_idx < len(params) else None); p_idx += 1
        elif tok.startswith("'") and tok.endswith("'"):
            vals.append(tok[1:-1])
        elif re.match(r'^-?\d+$', tok):
            vals.append(int(tok))
        else:
            vals.append(tok)
    doc = dict(zip(cols, vals))
    col = _mdb[table]
    pk_map = {"users": "user_id", "postingan": "file_id", "settings": "key",
              "transaksi_wd": "id", "invite_links": "code", "counters": "_id"}
    pk = pk_map.get(table)

    def _cflt(t, d):
        if t == "watch_sessions":   return {"user_id": d.get("user_id"), "video_msg_id": d.get("video_msg_id")}
        if t == "kartu_blur":       return {"user_id": d.get("user_id"), "postingan_id": d.get("postingan_id")}
        if t == "log_channel_msgs": return {"msg_id": d.get("msg_id"),   "channel_id":   d.get("channel_id")}
        return None

    if "OR REPLACE" in mode:
        flt = ({pk: doc[pk]} if pk and pk in doc else None) or _cflt(table, doc)
        # Untuk tabel dengan auto-increment id, pastikan id selalu ada saat replace
        if table in ("postingan", "transaksi_wd") and "id" not in doc:
            existing = col.find_one(flt, {"id": 1, "_id": 0}) if flt else None
            if existing and existing.get("id") is not None:
                doc["id"] = existing["id"]
            else:
                doc["id"] = _next_id(table)
        col.replace_one(flt, doc, upsert=True) if flt else col.insert_one(doc)
    elif "OR IGNORE" in mode:
        flt = ({pk: doc[pk]} if pk and pk in doc else None) or _cflt(table, doc)
        if flt:
            col.update_one(flt, {"$setOnInsert": doc}, upsert=True)
        else:
            try:
                col.insert_one(doc)
            except Exception:
                pass
    else:
        # INSERT biasa — auto-generate id untuk tabel auto-increment
        if table == "transaksi_wd" and "id" not in doc:
            doc["id"] = _next_id("transaksi_wd")
        elif table == "postingan" and "id" not in doc:
            doc["id"] = _next_id("postingan")
        try:
            col.insert_one(doc)
        except Exception:
            pass
    return []


def _mongo_update(sql: str, params: tuple):
    m = re.match(r'UPDATE\s+(\w+)\s+SET\s+(.+?)\s+WHERE\s+(.+)', sql, re.IGNORECASE | re.DOTALL)
    if not m:
        return []
    table = m.group(1); set_str = m.group(2).strip(); where_s = m.group(3).strip()
    col = _mdb[table]; params = list(params); p_idx = 0; inc_ops = {}; set_ops = {}
    for clause in _split_set_clauses(set_str):
        clause = clause.strip()
        m2 = re.match(r'(\w+)\s*=\s*\1\s*([+\-])\s*\?', clause)
        if m2:
            f = m2.group(1); op = m2.group(2); v = params[p_idx] if p_idx < len(params) else 0; p_idx += 1
            inc_ops[f] = v if op == "+" else -v; continue
        m2 = re.match(r'(\w+)\s*=\s*\1\s*([+\-])\s*(\d+)', clause)
        if m2:
            f = m2.group(1); op = m2.group(2); v = int(m2.group(3))
            inc_ops[f] = v if op == "+" else -v; continue
        m2 = re.match(r'(\w+)\s*=\s*\?', clause)
        if m2:
            f = m2.group(1); v = params[p_idx] if p_idx < len(params) else None; p_idx += 1
            set_ops[f] = v; continue
        m2 = re.match(r"(\w+)\s*=\s*'([^']*)'", clause)
        if m2:
            set_ops[m2.group(1)] = m2.group(2); continue
        m2 = re.match(r"(\w+)\s*=\s*(\d+)$", clause)
        if m2:
            set_ops[m2.group(1)] = int(m2.group(2)); continue
    update_op = {}
    if set_ops:
        update_op["$set"] = set_ops
    if inc_ops:
        update_op["$inc"] = inc_ops
    if not update_op:
        return []
    flt, _ = _parse_where(where_s, params, p_idx)
    col.update_many(flt, update_op)
    return []


def _split_set_clauses(set_str: str) -> list:
    clauses = []; depth = 0; current = []
    for ch in set_str:
        if ch == "(": depth += 1
        elif ch == ")": depth -= 1
        if ch == "," and depth == 0:
            clauses.append("".join(current).strip()); current = []
        else:
            current.append(ch)
    if current:
        clauses.append("".join(current).strip())
    return clauses


def _mongo_delete(sql: str, params: tuple):
    m = re.match(r'DELETE\s+FROM\s+(\w+)(?:\s+WHERE\s+(.+))?', sql, re.IGNORECASE | re.DOTALL)
    if not m:
        return []
    table     = m.group(1)
    where_raw = (m.group(2) or "").strip()
    if not where_raw:
        # DELETE FROM table (tanpa WHERE) → hapus semua
        _mdb[table].delete_many({})
        return []
    flt, _ = _parse_where(where_raw, list(params), 0)
    _mdb[table].delete_many(flt)
    return []


def _mongo_select(sql: str, params: tuple):
    m = re.match(r'SELECT\s+(.+?)\s+FROM\s+(\w+)', sql, re.IGNORECASE | re.DOTALL)
    if not m:
        return []
    fields_str = m.group(1).strip(); table = m.group(2).strip(); col = _mdb[table]
    flt, _ = _parse_where(_extract_where(sql), list(params), 0)
    if re.match(r'COUNT\s*\(\s*\*\s*\)', fields_str, re.IGNORECASE):
        return [(col.count_documents(flt),)]
    fields = None if fields_str.strip() == "*" else [f.strip() for f in fields_str.split(",")]
    proj = {"_id": 0}
    if fields:
        for f in fields:
            proj[f] = 1
    cursor = col.find(flt, proj)
    m_ord = re.search(r'ORDER\s+BY\s+(\w+)(?:\s+(ASC|DESC))?', sql, re.IGNORECASE)
    if m_ord:
        import pymongo as _pm
        if "RANDOM" in sql.upper():
            import random as _r; docs = list(cursor); _r.shuffle(docs)
        else:
            docs = list(cursor.sort(m_ord.group(1), _pm.DESCENDING if (m_ord.group(2) or "").upper() == "DESC" else _pm.ASCENDING))
    else:
        docs = list(cursor)
    m_lim = re.search(r'LIMIT\s+(\d+)', sql, re.IGNORECASE)
    if m_lim:
        docs = docs[:int(m_lim.group(1))]
    if not docs:
        return []
    if fields is None:
        keys = list(docs[0].keys())
        return [tuple(d.get(k) for k in keys) for d in docs]
    return [tuple(d.get(f) for f in fields) for d in docs]


def _mongo_q(sql: str, params: tuple = (), *, commit: bool = False):
    sql_upper = sql.strip().upper()
    try:
        if sql_upper.startswith("CREATE TABLE") or sql_upper.startswith("ALTER TABLE"):
            return []
        if sql_upper.startswith("INSERT"):  return _mongo_insert(sql, params)
        if sql_upper.startswith("UPDATE"):  return _mongo_update(sql, params)
        if sql_upper.startswith("DELETE"):  return _mongo_delete(sql, params)
        if sql_upper.startswith("SELECT"):  return _mongo_select(sql, params)
        return []
    except Exception as e:
        print(f"[MongoDB] {e} | {sql[:80]}")
        return []


def _mongo_q_atomic_smart(*queries) -> bool:
    """
    Eksekusi beberapa query secara atomik di MongoDB.
    Untuk UPDATE dengan kondisi (mis. kuota_star >= X), cek terlebih dahulu
    apakah dokumen memenuhi syarat sebelum menjalankan semua operasi.
    Jika salah satu syarat tidak terpenuhi → return False (tidak ada yang dijalankan).
    """
    try:
        # ── Fase validasi: cek semua UPDATE bersyarat ─────────────────────────
        for sql, params in queries:
            sql_s = sql.strip()
            if not sql_s.upper().startswith("UPDATE"):
                continue
            where_raw = _extract_where(sql_s)
            if not where_raw:
                continue
            # Hitung jumlah ? di bagian SET untuk skip param SET
            m_set = re.match(r'UPDATE\s+\w+\s+SET\s+(.+?)\s+WHERE', sql_s, re.IGNORECASE | re.DOTALL)
            q_count_set = m_set.group(1).count("?") if m_set else 0
            flt, _ = _parse_where(where_raw, list(params), q_count_set)
            if not flt:
                continue
            m_tbl = re.match(r'UPDATE\s+(\w+)', sql_s, re.IGNORECASE)
            if not m_tbl:
                continue
            tbl_name = m_tbl.group(1)
            # Jika tidak ada dokumen yang cocok dengan filter → atomik gagal
            if _mdb[tbl_name].count_documents(flt) == 0:
                return False

        # ── Fase eksekusi: jalankan semua query ───────────────────────────────
        for sql, params in queries:
            _mongo_q(sql, params, commit=True)
        return True

    except Exception as e:
        print(f"[MongoDB Atomic] {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Fallback tracker — deteksi Mongo down saat runtime
# ══════════════════════════════════════════════════════════════════════════════

_mongo_down = False   # True saat Mongo terdeteksi gagal di tengah jalan


def _is_mongo_alive() -> bool:
    """Ping Mongo. False jika timeout/error → tandai _mongo_down=True."""
    global _mongo_down
    try:
        _mdb.client.admin.command("ping", serverSelectionTimeoutMS=2000)
        if _mongo_down:
            print("[DB] ✅ MongoDB kembali online.")
            _mongo_down = False
        return True
    except Exception:
        if not _mongo_down:
            print("[DB] ⚠️  MongoDB tidak dapat dijangkau — fallback ke SQLite lokal.")
            _mongo_down = True
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Public API — dengan fallback otomatis ke SQLite jika Mongo down
# ══════════════════════════════════════════════════════════════════════════════

def q(sql: str, params: tuple = (), *, commit: bool = False):
    """
    Jalankan satu query.
    - Mode Mongo  : kirim ke Mongo. Jika Mongo down → fallback SQLite lokal.
    - Mode SQLite : langsung ke SQLite.
    """
    if _USE_MONGO:
        if _mongo_down or not _is_mongo_alive():
            return _sqlite_q(sql, params, commit=commit)
        return _mongo_q(sql, params, commit=commit)
    return _sqlite_q(sql, params, commit=commit)


def q_atomic(*queries) -> bool:
    """
    Jalankan beberapa query secara atomik.
    - Mode Mongo  : atomik Mongo. Jika Mongo down → fallback SQLite lokal.
    - Mode SQLite : atomik SQLite.
    """
    if _USE_MONGO:
        if _mongo_down or not _is_mongo_alive():
            return _sqlite_q_atomic(*queries)
        return _mongo_q_atomic_smart(*queries)
    return _sqlite_q_atomic(*queries)


# ══════════════════════════════════════════════════════════════════════════════
# Shortcut functions
# ══════════════════════════════════════════════════════════════════════════════

def get_user(uid: int):
    r = q("SELECT kuota_star, brangkas_durasi, state, coin, is_banned FROM users WHERE user_id=?", (uid,))
    if not r:
        return (0, 0, "MAIN_MENU", 0, 0)
    kuota, brangkas, state, coin, banned = r[0]
    return (
        int(kuota    or 0),
        int(brangkas or 0),
        state or "MAIN_MENU",
        int(coin     or 0),
        int(banned   or 0),
    )


def get_ghost_eye(uid: int) -> int:
    r = q("SELECT ghost_eye FROM users WHERE user_id=?", (uid,))
    return int(r[0][0] or 0) if r else 0


def set_state(uid: int, state: str):
    q("UPDATE users SET state=? WHERE user_id=?", (state, uid), commit=True)


def ensure_user(uid: int, username: str = "", first_name: str = ""):
    """
    Pastikan user ada di DB. Sertakan SEMUA kolom dengan nilai default agar
    MongoDB juga mendapat dokumen lengkap (MongoDB tidak punya DEFAULT kolom).
    """
    q(
        "INSERT OR IGNORE INTO users "
        "(user_id, username, first_name, kuota_star, brangkas_durasi, coin, "
        "is_banned, state, ghost_eye, is_video_banned, total_stars_used, last_star_used) "
        "VALUES (?,?,?,0,0,0,0,'MAIN_MENU',0,0,0,'')",
        (uid, username or "", first_name or ""), commit=True,
    )
    q("UPDATE users SET username=?, first_name=? WHERE user_id=?",
      (username or "", first_name or "", uid), commit=True)


def is_banned(uid: int) -> bool:
    r = q("SELECT is_banned FROM users WHERE user_id=?", (uid,))
    return bool(r and r[0][0])


def get_setting(key: str, default: str = "") -> str:
    r = q("SELECT value FROM settings WHERE key=?", (key,))
    return r[0][0] if r else default


def has_pending_wd_confirmation(uid: int) -> bool:
    r = q("SELECT id FROM transaksi_wd WHERE user_id=? AND done_owner=1 AND done_user=0 AND status='PENDING'", (uid,))
    return bool(r)


def create_invite_link(owner_id: int, code: str, created_at: str):
    if _USE_MONGO:
        threshold = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        try:
            _mdb["invite_links"].delete_many({"owner_id": owner_id, "created_at": {"$lt": threshold}})
        except Exception:
            pass
    else:
        q("DELETE FROM invite_links WHERE owner_id=? "
          "AND (CAST(strftime('%s','now') AS INTEGER) - CAST(strftime('%s', created_at) AS INTEGER)) > 3600",
          (owner_id,), commit=True)
    q("INSERT OR REPLACE INTO invite_links (code, owner_id, created_at, used) VALUES (?,?,?,0)",
      (code, owner_id, created_at), commit=True)


def get_invite_link(code: str):
    r = q("SELECT owner_id, created_at, used FROM invite_links WHERE code=?", (code,))
    return r[0] if r else None


def mark_invite_used(code: str):
    q("UPDATE invite_links SET used=1 WHERE code=?", (code,), commit=True)


def delete_invite(code: str):
    q("DELETE FROM invite_links WHERE code=?", (code,), commit=True)


# ══════════════════════════════════════════════════════════════════════════════
# Migrasi data antar database
# ══════════════════════════════════════════════════════════════════════════════
#
# Mendukung dua skenario saat startup:
#
#   A) IMPORT_FROM diset di .env (bot lama, file .db berbeda lokasi):
#      → Baca dari file itu, pindahkan ke DB_PATH (SQLite lokal), lalu kosongkan sumber.
#
#   B) DB_PATH sendiri punya data DAN MongoDB aktif:
#      → Pindahkan dari DB_PATH ke MongoDB, lalu kosongkan DB_PATH.
#
# Aturan saat upsert ke MongoDB:
#   • Setiap field per-detail (uid, kurs, dll.) yang LEBIH BARU menang.
#   • field preserve (thumb_file_id, blur_file_id, pengirim_label) hanya
#     di-overwrite jika nilai di Mongo KOSONG — nilai lokal tidak menimpa data
#     yang sudah lebih lengkap di Mongo.
# ─────────────────────────────────────────────────────────────────────────────

_TABLES = [
    # (nama_tabel,       pk_field_tunggal, [composite_key_fields])
    ("users",            "user_id",   []),
    ("postingan",        "id",        []),
    ("settings",         "key",       []),
    ("transaksi_wd",     "id",        []),
    ("watch_sessions",   None,        ["user_id", "video_msg_id"]),
    ("kartu_blur",       None,        ["user_id", "postingan_id"]),
    ("invite_links",     "code",      []),
    ("log_channel_msgs", None,        ["msg_id", "channel_id"]),
]
_AUTOINCREMENT_TABLES = {"postingan", "transaksi_wd"}


def _sqlite_row_count(path: str) -> int:
    """
    Jumlah total baris dari semua tabel di SQLite.
    Cek semua tabel — bukan hanya users — agar postingan/kartu_blur
    yang tersisa setelah migrasi parsial tetap ikut terbawa.
    """
    if not os.path.exists(path):
        return 0
    try:
        conn  = sqlite3.connect(path)
        total = 0
        for tbl, _, _ in _TABLES:
            try:
                total += conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            except Exception:
                pass
        conn.close()
        return total
    except Exception:
        return 0


def _read_all_from_sqlite(path: str) -> dict:
    """Baca semua tabel dari file SQLite → dict {nama_tabel: [dict_row, ...]}."""
    result = {}
    conn   = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    for tbl, _, _ in _TABLES:
        try:
            result[tbl] = [dict(r) for r in conn.execute(f"SELECT * FROM {tbl}").fetchall()]
        except Exception:
            result[tbl] = []
    conn.close()
    return result


def _clear_sqlite(path: str):
    """Hapus semua data dari semua tabel di file SQLite (file tetap ada)."""
    try:
        conn = sqlite3.connect(path)
        for tbl, _, _ in _TABLES:
            try:
                conn.execute(f"DELETE FROM {tbl}")
            except Exception:
                pass
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"   ⚠️  Gagal mengosongkan {path}: {e}")


def _push_to_mongo(tables_data: dict) -> int:
    """
    Upsert semua data ke MongoDB. Return total baris yang diproses.

    Aturan per-field:
      • set_always   : field biasa → selalu di-set ke nilai dari SQLite
                       (data lebih baru, mis. kuota_star, state, dll.)
      • Field preserve (thumb_file_id, blur_file_id, pengirim_label):
        - Jika nilai dari SQLite NON-KOSONG → di-set (menang).
        - Jika nilai dari SQLite KOSONG → hanya isi jika Mongo juga kosong
          (gunakan $setOnInsert untuk insert baru; untuk update gunakan
           $set dengan $cond tidak tersedia di update_one biasa, jadi
           kita cek dulu via find_one).

    Dengan cara ini:
      • Data Mongo yang sudah lebih lengkap (thumb_file_id terisi) TIDAK
        ditimpa oleh versi SQLite yang lebih miskin.
      • Data baru dari SQLite (kuota coin lebih tinggi, state terbaru, dll.)
        SELALU menang karena di-set langsung.
    """
    _PRESERVE_IF_NONEMPTY = {
        "postingan": {"thumb_file_id", "blur_file_id", "pengirim_label"},
    }

    total = 0
    for tbl, pk_field, composite_keys in _TABLES:
        rows = tables_data.get(tbl, [])
        if not rows:
            continue
        col      = _mdb[tbl]
        max_id   = 0
        preserve = _PRESERVE_IF_NONEMPTY.get(tbl, set())

        for doc in rows:
            if pk_field:
                flt = {pk_field: doc.get(pk_field)}
                if flt[pk_field] is None:
                    continue
            else:
                flt = {k: doc[k] for k in composite_keys if k in doc}
                if len(flt) < len(composite_keys):
                    continue

            # Pisah field: set_always vs field preserve
            set_always      = {}
            preserve_nonempty = {}  # nilai SQLite non-kosong → overwrite
            preserve_empty    = {}  # nilai SQLite kosong → jangan timpa Mongo

            for k, v in doc.items():
                if k in preserve:
                    if v is not None and v != "":
                        preserve_nonempty[k] = v   # menang
                    else:
                        preserve_empty[k] = v      # jangan timpa
                else:
                    set_always[k] = v

            # Gabungkan set_always + preserve_nonempty ke $set
            final_set = {**set_always, **preserve_nonempty}
            update_op = {}
            if final_set:
                update_op["$set"] = final_set

            # Untuk field preserve yang kosong di SQLite: gunakan $setOnInsert
            # agar hanya terisi saat dokumen belum ada di Mongo (insert baru).
            # Saat update (dokumen sudah ada), nilai Mongo yang non-kosong dipertahankan.
            if preserve_empty:
                update_op["$setOnInsert"] = preserve_empty

            if update_op:
                col.update_one(flt, update_op, upsert=True)
            total += 1
            if tbl in _AUTOINCREMENT_TABLES and pk_field:
                max_id = max(max_id, doc.get(pk_field) or 0)

        if tbl in _AUTOINCREMENT_TABLES and max_id > 0:
            _mdb["counters"].update_one(
                {"_id": tbl}, {"$max": {"seq": max_id}}, upsert=True
            )
        print(f"   ✔ {tbl:<20} {len(rows)} baris")
    return total


def _push_to_sqlite(tables_data: dict, dest_path: str) -> int:
    """INSERT OR REPLACE semua data ke file SQLite lain. Return total baris."""
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    total = 0
    conn  = sqlite3.connect(dest_path)
    conn.execute("PRAGMA journal_mode=WAL")
    # Pastikan tabel ada di DB tujuan sebelum insert
    _ensure_sqlite_tables(conn)
    for tbl, _, _ in _TABLES:
        rows = tables_data.get(tbl, [])
        if not rows:
            continue
        for doc in rows:
            cols = list(doc.keys()); vals = list(doc.values())
            ph   = ",".join(["?"] * len(cols))
            try:
                conn.execute(f"INSERT OR REPLACE INTO {tbl} ({','.join(cols)}) VALUES ({ph})", vals)
                total += 1
            except Exception:
                pass
        print(f"   ✔ {tbl:<20} {len(rows)} baris")
    conn.commit()
    conn.close()
    return total


def _ensure_sqlite_tables(conn):
    """Buat semua tabel di koneksi SQLite yang diberikan (idempotent)."""
    stmts = [
        """CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            kuota_star INTEGER DEFAULT 0, brangkas_durasi INTEGER DEFAULT 0,
            coin INTEGER DEFAULT 0, is_banned INTEGER DEFAULT 0,
            state TEXT DEFAULT 'MAIN_MENU', ghost_eye INTEGER DEFAULT 0,
            is_video_banned INTEGER DEFAULT 0,
            total_stars_used INTEGER DEFAULT 0, last_star_used TEXT DEFAULT '')""",
        """CREATE TABLE IF NOT EXISTS postingan (
            id INTEGER PRIMARY KEY AUTOINCREMENT, file_id TEXT UNIQUE,
            creator_id INTEGER, durasi INTEGER DEFAULT 0,
            caption TEXT DEFAULT '', is_skipped INTEGER DEFAULT 0,
            blur_file_id TEXT DEFAULT '', file_type TEXT DEFAULT 'video',
            pengirim_label TEXT DEFAULT '', thumb_file_id TEXT DEFAULT '')""",
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)",
        """CREATE TABLE IF NOT EXISTS transaksi_wd (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            jumlah_coin INTEGER DEFAULT 0, status TEXT DEFAULT 'PENDING',
            done_user INTEGER DEFAULT 0, done_owner INTEGER DEFAULT 0,
            msg_id_user INTEGER DEFAULT 0, msg_id_owner INTEGER DEFAULT 0)""",
        """CREATE TABLE IF NOT EXISTS watch_sessions (
            user_id INTEGER, video_msg_id INTEGER, postingan_id INTEGER,
            creator_id INTEGER, expired_at TEXT, status TEXT DEFAULT 'ACTIVE',
            PRIMARY KEY (user_id, video_msg_id))""",
        """CREATE TABLE IF NOT EXISTS kartu_blur (
            user_id INTEGER, postingan_id INTEGER, msg_id INTEGER,
            sudah_dibuka INTEGER DEFAULT 0, sent_at TEXT DEFAULT '',
            PRIMARY KEY (user_id, postingan_id))""",
        """CREATE TABLE IF NOT EXISTS invite_links (
            code TEXT PRIMARY KEY, owner_id INTEGER, created_at TEXT,
            used INTEGER DEFAULT 0)""",
        """CREATE TABLE IF NOT EXISTS log_channel_msgs (
            msg_id INTEGER, channel_id INTEGER, postingan_id INTEGER,
            PRIMARY KEY (msg_id, channel_id))""",
    ]
    for s in stmts:
        try:
            conn.execute(s)
        except Exception:
            pass
    # ALTER TABLE untuk kolom baru (upgrade skema)
    alters = [
        "ALTER TABLE users ADD COLUMN ghost_eye INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN is_video_banned INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN first_name TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN total_stars_used INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN last_star_used TEXT DEFAULT ''",
        "ALTER TABLE postingan ADD COLUMN is_skipped INTEGER DEFAULT 0",
        "ALTER TABLE postingan ADD COLUMN blur_file_id TEXT DEFAULT ''",
        "ALTER TABLE postingan ADD COLUMN file_type TEXT DEFAULT 'video'",
        "ALTER TABLE postingan ADD COLUMN pengirim_label TEXT DEFAULT ''",
        "ALTER TABLE postingan ADD COLUMN thumb_file_id TEXT DEFAULT ''",
        "ALTER TABLE kartu_blur ADD COLUMN sent_at TEXT DEFAULT ''",
    ]
    for a in alters:
        try:
            conn.execute(a)
        except Exception:
            pass
    conn.commit()


def _deduplicate_mongo() -> int:
    """
    Hapus dokumen duplikat dari semua koleksi MongoDB.
    Pertahankan yang paling baru (_id terbesar), hapus yang lama.
    """
    import pymongo
    total = 0

    for col_name, pk_field in [("users", "user_id"), ("postingan", "id"), ("settings", "key"),
                                ("transaksi_wd", "id"), ("invite_links", "code")]:
        col = _mdb[col_name]; seen = {}; to_del = []
        for doc in col.find({}, {"_id": 1, pk_field: 1}).sort("_id", pymongo.ASCENDING):
            pk_val = doc.get(pk_field)
            if pk_val is None: continue
            if pk_val in seen: to_del.append(seen[pk_val])
            seen[pk_val] = doc["_id"]
        if to_del:
            total += col.delete_many({"_id": {"$in": to_del}}).deleted_count

    for col_name, key_fields in [("watch_sessions", ["user_id", "video_msg_id"]),
                                  ("kartu_blur", ["user_id", "postingan_id"]),
                                  ("log_channel_msgs", ["msg_id", "channel_id"])]:
        col = _mdb[col_name]
        proj = {"_id": 1, **{f: 1 for f in key_fields}}
        seen = {}; to_del = []
        for doc in col.find({}, proj).sort("_id", pymongo.ASCENDING):
            pk = tuple(doc.get(f) for f in key_fields)
            if None in pk: continue
            if pk in seen: to_del.append(seen[pk])
            seen[pk] = doc["_id"]
        if to_del:
            total += col.delete_many({"_id": {"$in": to_del}}).deleted_count

    return total


def _run_migration_to_mongo(source_path: str, label: str):
    """
    Baca data dari source_path SQLite → push ke MongoDB → kosongkan source.
    Hanya dipanggil saat _USE_MONGO=True.
    """
    row_count = _sqlite_row_count(source_path)
    if row_count == 0:
        return

    print(
        f"\n{'═'*60}\n"
        f"📦 [{label}] {row_count} baris ditemukan di:\n"
        f"   {source_path}\n"
        f"   → Memindahkan ke MongoDB 'psv_bot_{CODE_BOT}'...\n"
        f"{'═'*60}"
    )

    try:
        tables_data = _read_all_from_sqlite(source_path)
        total = _push_to_mongo(tables_data)
        dupes = _deduplicate_mongo()
        extra = f"   🧹 Duplikat dihapus : {dupes} dokumen\n" if dupes else ""
        _clear_sqlite(source_path)
        print(
            f"\n{'═'*60}\n"
            f"✅ [{label}] Selesai!\n"
            f"   📤 Diproses       : {total} records\n"
            f"{extra}"
            f"   📭 File sumber dikosongkan.\n"
            f"{'═'*60}\n"
        )
    except Exception as e:
        import traceback
        print(f"⚠️  [{label}] Gagal: {e}")
        traceback.print_exc()


def run_migrations():
    """
    Dipanggil dari init_db() setiap startup.

    Alur migrasi 2 tahap:

      Tahap 1 — IMPORT_FROM → DB_PATH (SQLite lama → SQLite lokal)
        Selalu lokal-ke-lokal, tidak peduli apakah MongoDB aktif.
        Tujuan: konsolidasikan data lama ke DB_PATH dulu.

      Tahap 2 — DB_PATH → MongoDB (SQLite lokal → MongoDB)
        Hanya jalan kalau MongoDB aktif dan DB_PATH punya data.
        Tujuan: pindahkan DB_PATH yang sudah lengkap ke MongoDB,
        lalu kosongkan DB_PATH agar tidak ganda.

    Contoh .env:
      IMPORT_FROM=~/downloads/xxx/jual_video/jual_video.db  ← sumber lama
      SQLITE_PATH=jual_video.db                             ← DB_PATH (transit lokal)
      MONGO_URI=mongodb+srv://...                           ← tujuan akhir
    """
    # ── Tahap 1: IMPORT_FROM → DB_PATH (selalu SQLite ke SQLite) ────────────
    if IMPORT_FROM and IMPORT_FROM != DB_PATH:
        if not os.path.exists(IMPORT_FROM):
            print(f"⚠️  IMPORT_FROM diset tapi file tidak ditemukan:\n   {IMPORT_FROM}")
        else:
            row_count = _sqlite_row_count(IMPORT_FROM)
            if row_count == 0:
                print(f"📭 [IMPORT_FROM] Tidak ada data di:\n   {IMPORT_FROM}")
            else:
                print(
                    f"\n{'═'*60}\n"
                    f"📦 [IMPORT_FROM] Data ditemukan di:\n"
                    f"   {IMPORT_FROM}\n"
                    f"   → Memindahkan ke SQLite lokal: {DB_PATH}\n"
                    f"{'═'*60}"
                )
                try:
                    tables_data = _read_all_from_sqlite(IMPORT_FROM)
                    if os.path.abspath(IMPORT_FROM) == os.path.abspath(DB_PATH):
                        print("   ⏭️  Sumber sama dengan tujuan — dilewati.")
                    else:
                        total = _push_to_sqlite(tables_data, DB_PATH)
                        _clear_sqlite(IMPORT_FROM)
                        print(
                            f"\n{'═'*60}\n"
                            f"✅ [IMPORT_FROM] Selesai!\n"
                            f"   📤 Dipindahkan    : {total} records\n"
                            f"   📭 File sumber dikosongkan.\n"
                            f"{'═'*60}\n"
                        )
                except Exception as e:
                    import traceback
                    print(f"⚠️  [IMPORT_FROM] Gagal: {e}")
                    traceback.print_exc()

    # ── Tahap 2: DB_PATH → MongoDB (hanya jika MongoDB aktif) ───────────────
    if _USE_MONGO and os.path.exists(DB_PATH):
        _run_migration_to_mongo(DB_PATH, "Migrasi Lokal → MongoDB")


# ══════════════════════════════════════════════════════════════════════════════
# Inisialisasi database
# ══════════════════════════════════════════════════════════════════════════════

def init_db():
    """
    Inisialisasi backend lalu jalankan migrasi data jika diperlukan.

    Urutan:
      1. Siapkan backend utama (MongoDB atau SQLite).
      2. Jika mode Mongo: siapkan juga SQLite sebagai fallback offline.
         (SQLite selalu siap agar data tidak hilang saat Mongo down.)
      3. Jalankan run_migrations() — migrasikan data lama jika ada.
    """
    if _USE_MONGO:
        _init_mongo()
        _init_sqlite()   # siapkan SQLite sebagai fallback offline
    else:
        _init_sqlite()
    run_migrations()


def _init_mongo():
    try:
        import pymongo
        _mdb["users"].create_index("user_id", unique=True)
        _mdb["postingan"].create_index("file_id", unique=True, sparse=True)
        _mdb["postingan"].create_index("id", unique=True, sparse=True)
        _mdb["settings"].create_index("key", unique=True)
        _mdb["transaksi_wd"].create_index("id", unique=True, sparse=True)
        _mdb["watch_sessions"].create_index(
            [("user_id", pymongo.ASCENDING), ("video_msg_id", pymongo.ASCENDING)], unique=True)
        _mdb["kartu_blur"].create_index(
            [("user_id", pymongo.ASCENDING), ("postingan_id", pymongo.ASCENDING)], unique=True)
        _mdb["invite_links"].create_index("code", unique=True)
        _mdb["log_channel_msgs"].create_index(
            [("msg_id", pymongo.ASCENDING), ("channel_id", pymongo.ASCENDING)], unique=True)
        _mdb["counters"].create_index("_id", unique=True)
    except Exception as e:
        print(f"[MongoDB init] {e}")

    # Inisialisasi settings default (hanya jika belum ada)
    for k, v in [("qris_link", "assets/qris.jpg"), ("kurs_star_rp", "5000"),
                 ("kurs_star_jml", "20"), ("kurs_coin_rp", "10000"),
                 ("kurs_coin_jml", "50"), ("broadcast_last_msg_id", "0")]:
        try:
            _mdb["settings"].update_one(
                {"key": k}, {"$setOnInsert": {"key": k, "value": v}}, upsert=True
            )
        except Exception:
            pass

    # Patch dokumen lama yang tidak lengkap
    _mongo_patch_incomplete_users()
    _mongo_patch_incomplete_postingan()

    print(f"✅ MongoDB 'psv_bot_{CODE_BOT}' siap.")


def _mongo_patch_incomplete_users():
    """
    Patch one-time: tambahkan field default ke semua dokumen user yang tidak
    memiliki field wajib. Aman dijalankan berulang — hanya mengubah yang missing.
    """
    _USER_DEFAULTS = {
        "kuota_star":       0,
        "brangkas_durasi":  0,
        "coin":             0,
        "is_banned":        0,
        "state":            "MAIN_MENU",
        "ghost_eye":        0,
        "is_video_banned":  0,
        "total_stars_used": 0,
        "last_star_used":   "",
        "username":         "",
        "first_name":       "",
    }
    try:
        patched = 0
        for field, default in _USER_DEFAULTS.items():
            result = _mdb["users"].update_many(
                {field: {"$exists": False}},
                {"$set": {field: default}},
            )
            patched += result.modified_count
        if patched:
            print(f"🔧 MongoDB patch: {patched} field default ditambahkan ke dokumen user lama.")
    except Exception as e:
        print(f"[MongoDB patch] {e}")


def _mongo_patch_incomplete_postingan():
    """
    Patch one-time: tambahkan field yang hilang ke dokumen postingan lama.
    Aman dijalankan berulang.
    """
    _POSTINGAN_DEFAULTS = {
        "thumb_file_id":  "",
        "blur_file_id":   "",
        "pengirim_label": "",
        "file_type":      "video",
        "is_skipped":     0,
        "caption":        "",
        "durasi":         0,
    }
    try:
        patched = 0
        for field, default in _POSTINGAN_DEFAULTS.items():
            result = _mdb["postingan"].update_many(
                {field: {"$exists": False}},
                {"$set": {field: default}},
            )
            patched += result.modified_count
        if patched:
            print(f"🔧 MongoDB patch postingan: {patched} field default ditambahkan ke dokumen postingan lama.")
    except Exception as e:
        print(f"[MongoDB patch postingan] {e}")


def _init_sqlite():
    """Inisialisasi SQLite: buat semua tabel dan settings default."""
    conn = _sqlite_conn()
    _ensure_sqlite_tables(conn)
    conn.close()

    # Settings default
    for k, v in [("qris_link", "assets/qris.jpg"), ("kurs_star_rp", "5000"),
                 ("kurs_star_jml", "20"), ("kurs_coin_rp", "10000"),
                 ("kurs_coin_jml", "50"), ("broadcast_last_msg_id", "0")]:
        _sqlite_q("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", (k, v), commit=True)

    print(f"✅ SQLite siap: {DB_PATH}")
