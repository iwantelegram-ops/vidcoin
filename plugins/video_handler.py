"""
plugins/video_handler.py — Upload, broadcast, tonton video, dan broadcast terjadwal.

PERBAIKAN v10:
  - _fetch_all_channel_videos menggunakan tabel log_channel_msgs (DB lokal)
    sebagai ganti client.get_chat_history() yang tidak bisa dipakai bot.
  - _kirim_log_channel kini menyimpan msg_id ke log_channel_msgs setelah
    berhasil kirim ke channel.
  - kirim_video_selamat_datang juga menggunakan DB, bukan get_chat_history.
"""
import asyncio
import os
import random as _random
from datetime import datetime, timedelta, timezone

import pytz
from PIL import Image, ImageFilter
from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import Message, CallbackQuery

from config import JATAH_GRATIS, HARGA_STAR, DURASI_DITAMBAH, NILAI_BLUR, OWNER_ID, \
    BROADCAST_SLOT_HOURS, DOWNLOADS_DIR, ASSETS_DIR, BLUR_CACHE_DIR, DEFAULT_THUMB
from database import q, q_atomic, get_user, get_ghost_eye, set_state
from keyboards import send, kb_watch, kb_add_time_full

TZ_WIB = pytz.timezone("Asia/Jakarta")

_sedang_proses: set[int] = set()   # anti-spam watch
_sesi: dict[tuple, dict] = {}       # {(uid, msg_id): {hangus, creator_id, pid}}

KARTU_TTL   = 12 * 60 * 60  # 12 jam dalam detik
_SEND_DELAY = 0.065          # jeda antar pesan broadcast (detik) → ~15 msg/detik (batas aman)


# ── Utilitas waktu ────────────────────────────────────────────────────────────

def _utc() -> datetime:
    return datetime.now(timezone.utc)


# ── Utilitas gambar ───────────────────────────────────────────────────────────

def _blur(src: str, dst: str) -> bool:
    try:
        Image.open(src).filter(ImageFilter.GaussianBlur(NILAI_BLUR)).save(dst)
        return True
    except Exception:
        return False


def _make_placeholder_thumb(path: str) -> bool:
    """
    Buat gambar placeholder visual (gradient biru-ungu + ikon play) sebagai
    fallback thumbnail. Hasilnya terlihat seperti cover video sungguhan saat
    di-spoiler Telegram — bukan kotak hitam solid.
    """
    try:
        from PIL import ImageDraw
        W, H = 640, 360
        img = Image.new("RGB", (W, H))
        pix = img.load()

        # Gradient diagonal biru gelap → ungu
        for y in range(H):
            for x in range(W):
                r = int(25  + (x / W) * 55  + (y / H) * 25)
                g = int(10  + (y / H) * 18)
                b = int(90  + (x / W) * 75  + (y / H) * 55)
                pix[x, y] = (min(r, 255), min(g, 255), min(b, 255))

        draw = ImageDraw.Draw(img)

        # Lingkaran latar ikon play
        cx, cy, cr = W // 2, H // 2, 62
        draw.ellipse([cx - cr, cy - cr, cx + cr, cy + cr],
                     fill=(255, 255, 255, 0))   # putih semi-transparan via overlay manual
        # Overlay manual (PIL RGB tidak support alpha fill langsung)
        overlay = Image.new("RGB", (W, H), (255, 255, 255))
        mask    = Image.new("L",   (W, H), 0)
        ImageDraw.Draw(mask).ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=50)
        img = Image.composite(overlay, img, mask)
        draw = ImageDraw.Draw(img)

        # Segitiga play
        ph = 38
        pts = [(cx - ph // 2 + 10, cy - ph), (cx - ph // 2 + 10, cy + ph), (cx + ph + 4, cy)]
        draw.polygon(pts, fill=(35, 15, 90))

        os.makedirs(os.path.dirname(path), exist_ok=True)
        img.save(path, "JPEG", quality=88)
        return True
    except Exception as _e:
        print(f"[PLACEHOLDER] Gagal buat placeholder: {_e}")
        return False


async def _resolve_blur_for_broadcast(client, pid: int, file_id: str) -> str:
    """
    Kembalikan path foto lokal terbaik untuk kartu blur spoiler broadcast.

    Prioritas:
    1) BLUR_CACHE_DIR/{pid}.jpg     — cache dari upload asli
    2) Regenerasi lazy dari thumb_file_id di DB
    3) Blur cache ACAK dari pool video lain
    4) DEFAULT_THUMB                — fallback mutlak

    Selalu mengembalikan path lokal (str) sehingga has_spoiler=True selalu berfungsi.
    """
    import shutil
    OWN_CACHE = os.path.join(BLUR_CACHE_DIR, f"{pid}.jpg")

    os.makedirs(BLUR_CACHE_DIR, exist_ok=True)
    os.makedirs(DOWNLOADS_DIR,  exist_ok=True)

    # ── 1) Cache asli sudah ada ──────────────────────────────────────────────
    if os.path.exists(OWN_CACHE):
        return OWN_CACHE

    # ── 2) Regenerasi lazy dari thumb_file_id yang tersimpan di DB ───────────
    tmp_thumb = os.path.join(DOWNLOADS_DIR, f"regen_thumb_{pid}.jpg")
    tmp_blur  = os.path.join(DOWNLOADS_DIR, f"regen_blur_{pid}.jpg")
    try:
        thumb_row = q("SELECT thumb_file_id FROM postingan WHERE id=?", (pid,))
        thumb_fid = (thumb_row[0][0] if thumb_row and thumb_row[0] else None) or None

        if not thumb_fid:
            raise FileNotFoundError("thumb_file_id kosong di DB")

        await client.download_media(thumb_fid, file_name=tmp_thumb)

        if os.path.exists(tmp_thumb) and _blur(tmp_thumb, tmp_blur):
            shutil.copy2(tmp_blur, OWN_CACHE)
            print(f"[BLUR_REGEN] PID {pid}: blur cache diregenerasi dari thumbnail Telegram.")
            return OWN_CACHE
        else:
            raise RuntimeError("_blur gagal menghasilkan file")

    except Exception as _e:
        print(f"[BLUR_REGEN] PID {pid}: regenerasi gagal ({_e}) → coba pool acak.")
    finally:
        for _p in (tmp_thumb, tmp_blur):
            try:
                if os.path.exists(_p):
                    os.remove(_p)
            except Exception:
                pass

    # ── 3) Ambil blur acak dari pool lokal (file .jpg yang sudah ada di disk) ──
    try:
        pool = [
            f for f in os.listdir(BLUR_CACHE_DIR)
            if f.endswith(".jpg") and f != f"{pid}.jpg"
        ]
        if pool:
            chosen      = _random.choice(pool)
            chosen_path = os.path.join(BLUR_CACHE_DIR, chosen)
            print(f"[BLUR_POOL] PID {pid}: pakai blur lokal '{chosen}' dari pool ({len(pool)} item).")
            return chosen_path
    except Exception as _e:
        print(f"[BLUR_POOL] PID {pid}: gagal baca pool lokal ({_e}).")

    # ── 3b) Coba regenerasi dari blur_file_id DB milik PID lain ─────────────
    # Pool lokal kosong atau tidak cukup variatif — cari PID lain di DB yang
    # punya blur_file_id (file_id Telegram yang sudah pernah dikirim),
    # download, blur, simpan ke cache agar bisa dipakai sekarang dan berikutnya.
    try:
        kandidat = q(
            "SELECT id, blur_file_id FROM postingan "
            "WHERE is_skipped=0 AND blur_file_id != '' AND id != ?",
            (pid,)
        )
        if kandidat:
            kandidat = list(kandidat)
            _random.shuffle(kandidat)  # acak urutan agar tidak selalu PID yang sama
            for (other_pid, other_blur_fid) in kandidat:
                other_cache = os.path.join(BLUR_CACHE_DIR, f"{other_pid}.jpg")
                # Kalau cache PID lain sudah ada, pakai langsung
                if os.path.exists(other_cache):
                    print(f"[BLUR_POOL_DB] PID {pid}: pakai cache PID {other_pid} dari disk.")
                    return other_cache
                # Belum ada — download blur_file_id lalu simpan sebagai cache
                tmp_dl = os.path.join(DOWNLOADS_DIR, f"pool_dl_{other_pid}.jpg")
                tmp_bl = os.path.join(DOWNLOADS_DIR, f"pool_blur_{other_pid}.jpg")
                try:
                    await client.download_media(other_blur_fid, file_name=tmp_dl)
                    if os.path.exists(tmp_dl) and _blur(tmp_dl, tmp_bl):
                        shutil.copy2(tmp_bl, other_cache)
                        print(f"[BLUR_POOL_DB] PID {pid}: regenerasi cache PID {other_pid} dari blur_file_id DB.")
                        return other_cache
                except Exception as _dl_e:
                    print(f"[BLUR_POOL_DB] PID {pid}: gagal download blur_file_id PID {other_pid} ({_dl_e}).")
                finally:
                    for _p in (tmp_dl, tmp_bl):
                        try:
                            if os.path.exists(_p): os.remove(_p)
                        except Exception:
                            pass
    except Exception as _e:
        print(f"[BLUR_POOL_DB] PID {pid}: gagal query DB ({_e}).")

    # ── 4) Fallback mutlak: default thumb (placeholder visual, bukan kotak hitam) ─
    if not os.path.exists(DEFAULT_THUMB):
        if not _make_placeholder_thumb(DEFAULT_THUMB):
            try:
                os.makedirs(ASSETS_DIR, exist_ok=True)
                Image.new("RGB", (640, 360), (30, 30, 45)).save(DEFAULT_THUMB)
            except Exception:
                pass
    return DEFAULT_THUMB


# ── Utilitas teks ─────────────────────────────────────────────────────────────

def _creator_tag(creator_id: int) -> str:
    r = q("SELECT username FROM users WHERE user_id=?", (creator_id,))
    return f"@{r[0][0]}" if r and r[0][0] else f"`{creator_id}`"


def _build_cap(pengirim_label: str, dur_str: str, jam: str, keterangan: str) -> str:
    mnt = DURASI_DITAMBAH // 60
    cap = (
        f"▶️ **VIDEO SEDANG DIPUTAR**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Pengirim: {pengirim_label}\n"
        f"⏱️ Durasi asli: `{dur_str}`\n"
        f"⌛ Akses berakhir: **{jam} WIB**\n"
    )
    if keterangan:
        cap += f"\n📝 **Keterangan:**\n_{keterangan}_\n"
    cap += (
        f"\n━━━━━━━━━━━━━━━━━━━━━\n"
        f"➕ Tekan tombol di bawah untuk tambah durasi.\n"
        f"Biaya: **{HARGA_STAR} ⭐** = +{mnt} menit.\n"
        f"_Star beli via menu ⭐ Star / Kuota._"
    )
    return cap


# ── Errors yang tidak perlu di-retry (user tidak aktif / blokir bot) ─────────
_SKIP_ERRORS = (
    "UserDeactivated", "UserBlocked", "InputUserDeactivated",
    "PeerIdInvalid", "ChatWriteForbidden", "UserIsBot",
    "AccessTokenExpired", "BotBlocked",
)


def _is_ignorable(e: Exception) -> bool:
    """True jika error ini tidak perlu di-retry — langsung lewati user."""
    name = type(e).__name__
    msg  = str(e)
    return name in _SKIP_ERRORS or any(s in msg for s in _SKIP_ERRORS)


# ── Helper kirim foto aman (rate-limit + FloodWait + exponential backoff) ─────

async def _safe_send_photo(client, tid: int, photo, caption: str,
                            reply_markup, protect_content: bool = True,
                            has_spoiler: bool = False):
    """
    Kirim foto ke satu user dengan perlindungan penuh:
      • Max 3 percobaan
      • FloodWait: tunggu fw.value + 2 detik (tambah buffer), lalu retry
      • FloodWait ke-2: tunggu fw.value * 1.5 + 2 (lebih sabar)
      • Error permanen (user blokir / nonaktif): langsung skip, tidak retry
    """
    wait_extra = 2  # buffer awal (detik) di atas yang diminta Telegram
    for attempt in range(3):
        try:
            return await client.send_photo(
                tid, photo,
                caption=caption,
                reply_markup=reply_markup,
                protect_content=protect_content,
                has_spoiler=has_spoiler,
            )
        except FloodWait as fw:
            if attempt < 2:
                wait = int(fw.value * (1 + attempt * 0.5)) + wait_extra
                await asyncio.sleep(wait)
            else:
                return None   # menyerah setelah 3 kali FloodWait
        except Exception as e:
            if _is_ignorable(e):
                return None   # skip permanen — jangan retry
            if attempt < 2:
                await asyncio.sleep(1 + attempt)  # backoff ringan untuk error sementara
            else:
                return None
    return None


# ── Helper copy pesan (untuk owner blast) — semua tipe pesan didukung ─────────

async def _safe_copy(source_msg, tid: int):
    """
    Salin pesan ke user dengan perlindungan penuh (FloodWait + backoff).
    Digunakan oleh _exec_owner_blast di admin.py.
    Mendukung semua tipe: teks, foto, video, stiker, dokumen, audio, dll.
    """
    wait_extra = 2
    for attempt in range(3):
        try:
            await source_msg.copy(tid)
            return True
        except FloodWait as fw:
            if attempt < 2:
                wait = int(fw.value * (1 + attempt * 0.5)) + wait_extra
                await asyncio.sleep(wait)
            else:
                return False
        except Exception as e:
            if _is_ignorable(e):
                return False
            if attempt < 2:
                await asyncio.sleep(1 + attempt)
            else:
                return False
    return False


# ── 24-jam auto-delete kartu blur ─────────────────────────────────────────────

async def _auto_delete_kartu(client, tid: int, msg_id: int, pid: int,
                              delay_seconds: float):
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)
    row = q(
        "SELECT sudah_dibuka FROM kartu_blur "
        "WHERE user_id=? AND postingan_id=? AND msg_id=?",
        (tid, pid, msg_id),
    )
    if row and not row[0][0]:
        try:
            await client.delete_messages(tid, msg_id)
        except Exception:
            pass
        q(
            "UPDATE kartu_blur SET sudah_dibuka=1 "
            "WHERE user_id=? AND postingan_id=? AND msg_id=?",
            (tid, pid, msg_id), commit=True,
        )


# ── Helper daftarkan kartu blur ───────────────────────────────────────────────

def _register_kartu(tid: int, pid: int, msg_id: int, now_str: str):
    q(
        "INSERT OR REPLACE INTO kartu_blur "
        "(user_id,postingan_id,msg_id,sudah_dibuka,sent_at) VALUES (?,?,?,0,?)",
        (tid, pid, msg_id, now_str), commit=True,
    )


# ── Upload video (kiriman dari user) ──────────────────────────────────────────

@Client.on_message((filters.video | filters.document) & filters.private)
async def on_video(client: Client, message: Message):
    if not message.from_user:
        return
    uid = message.from_user.id
    try:
        _, _, state, _, _ = get_user(uid)
    except Exception:
        return

    if not state.startswith("KIRIM_VIDEO_"):
        # Owner sedang broadcast manual → serahkan ke owner_broadcast_catcher
        if state == "OWNER_BROADCAST":
            return
        return await send(client, uid,
            "⚠️ **Akses ditolak.**\n\nGunakan **📝 Buat Postingan** terlebih dahulu.",
            state=state)

    vban = q("SELECT is_video_banned FROM users WHERE user_id=?", (uid,))
    if vban and vban[0][0]:
        set_state(uid, "MAIN_MENU")
        return await send(client, uid,
            "⚠️ **Akses postingan Anda sedang dibatasi sementara.**\n\n"
            "_Silakan hubungi Owner untuk informasi lebih lanjut._",
            state="MAIN_MENU")

    # Tentukan label pengirim berdasarkan mode yang dipilih
    if state == "KIRIM_VIDEO_ANONIM":
        pengirim = "Anonim"
    elif state == "KIRIM_VIDEO_NAMA":
        pengirim = (message.from_user.first_name or "").strip() or f"User{uid}"
    else:
        # Backward compatibility dengan state lama yang menyimpan nama langsung
        pengirim = state.removeprefix("KIRIM_VIDEO_")

    if message.video:
        fid       = message.video.file_id
        fuid      = message.video.file_unique_id
        durasi    = message.video.duration or 0
        thumbs    = message.video.thumbs
        file_type = "video"
    else:
        fid       = message.document.file_id
        fuid      = message.document.file_unique_id
        durasi    = 0
        thumbs    = getattr(message.document, "thumbs", None)
        file_type = "document"

    keterangan = (message.caption or "").strip()

    await send(client, uid, "⏳ Memproses dan menyebarkan video...", state=state)

    # Buat thumbnail blur
    t_path  = os.path.join(DOWNLOADS_DIR, f"thumb_{fuid}.jpg")
    b_path  = os.path.join(DOWNLOADS_DIR, f"blur_{fuid}.jpg")
    default = DEFAULT_THUMB

    if thumbs:
        try:
            await client.download_media(thumbs[0].file_id, file_name=t_path)
            b_path = b_path if _blur(t_path, b_path) else default
        except Exception:
            b_path = default
    else:
        b_path = default

    # Pastikan DEFAULT_THUMB ada dan berupa gambar visual (bukan kotak hitam)
    if not os.path.exists(default):
        if not _make_placeholder_thumb(default):
            try:
                Image.new("RGB", (640, 360), (40, 40, 60)).save(default)
            except Exception:
                pass

    if not os.path.exists(b_path):
        # b_path temp tidak ada → pakai default (sudah dipastikan ada di atas)
        b_path = default

    # Ambil thumb_file_id untuk regenerasi blur cache di masa depan
    thumb_fid_save = thumbs[0].file_id if thumbs else ""

    # Simpan postingan (termasuk label pengirim untuk ditampilkan saat video diputar)
    q("INSERT OR REPLACE INTO postingan (file_id, creator_id, durasi, caption, file_type, pengirim_label, thumb_file_id) VALUES (?,?,?,?,?,?,?)",
      (fid, uid, durasi, keterangan, file_type, pengirim, thumb_fid_save), commit=True)
    pid_row = q("SELECT id FROM postingan WHERE file_id=?", (fid,))
    if not pid_row or pid_row[0][0] is None:
        return await send(client, uid, "❌ Gagal menyimpan ke database.", state="MAIN_MENU")
    pid = pid_row[0][0]

    # Teks broadcast
    dur_str = f"{durasi // 60}m {durasi % 60}s" if durasi else "—"
    grt_str = f"{JATAH_GRATIS // 60}m" if JATAH_GRATIS >= 60 else f"{JATAH_GRATIS}s"
    caption = (
        f"🔔 **VIDEO BARU TERSEDIA!**\n\n"
        f"👤 Pengirim: **{pengirim}**\n"
        f"⏱️ Durasi: `{dur_str}`\n\n"
        f"🎁 **{grt_str} pertama GRATIS!**\n"
        f"Tambah durasi pakai ⭐ Star.\n"
        f"Klik tombol untuk mulai menonton 👇"
    )
    if keterangan:
        caption += f"\n\n💬 **Keterangan:**\n_{keterangan}_"

    # Broadcast ke semua user kecuali kreator & owner
    semua = q(
        "SELECT user_id FROM users WHERE user_id != ? AND user_id != ?",
        (uid, OWNER_ID),
    )
    sukses          = 0
    blur_fid_saved  = False
    now_str         = _utc().isoformat()
    kb              = kb_watch(pid)

    for (tid,) in semua:
        kartu = await _safe_send_photo(client, tid, b_path, caption, kb, has_spoiler=True)
        if kartu:
            _register_kartu(tid, pid, kartu.id, now_str)
            sukses += 1

            if not blur_fid_saved and kartu.photo:
                q("UPDATE postingan SET blur_file_id=? WHERE id=?",
                  (kartu.photo.file_id, pid), commit=True)
                blur_fid_saved = True

            asyncio.create_task(
                _auto_delete_kartu(client, tid, kartu.id, pid, KARTU_TTL)
            )
        await asyncio.sleep(_SEND_DELAY)

    # Kirim log ke channel
    full_name = (
        (message.from_user.first_name or "")
        + (" " + message.from_user.last_name if message.from_user.last_name else "")
    ).strip() or f"User{uid}"
    uname_asli = message.from_user.username or ""
    asyncio.create_task(
        _kirim_log_channel(
            client, uid, full_name, uname_asli, pengirim, fid, dur_str, keterangan, pid
        )
    )

    # Simpan blur image ke cache permanen (path absolut)
    blur_cache_path = os.path.join(BLUR_CACHE_DIR, f"{pid}.jpg")
    try:
        os.makedirs(BLUR_CACHE_DIR, exist_ok=True)
        if b_path != default and os.path.exists(b_path):
            import shutil
            shutil.copy2(b_path, blur_cache_path)
    except Exception as _e:
        print(f"[BLUR_CACHE] Gagal simpan cache: {_e}")

    # Bersihkan file temp
    for p in (t_path, b_path):
        if p != default and os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    set_state(uid, "MAIN_MENU")
    await send(
        client, uid,
        f"🚀 **Broadcast selesai!**\n\n"
        f"✅ Terkirim ke **{sukses}** pengguna.\n"
        + (f"💬 Keterangan: _{keterangan}_\n" if keterangan else "")
        + f"\n💡 Setiap Star penonton = 🪙 Koin masuk ke dompet Anda.",
        state="MAIN_MENU",
    )


# ── Log ke channel ────────────────────────────────────────────────────────────

async def _kirim_log_channel(
    client, uid: int, full_name: str, uname: str,
    pengirim_label: str, fid: str, dur_str: str, keterangan: str, pid: int,
):
    """
    Kirim video ke log channel dan simpan msg_id ke tabel log_channel_msgs.
    Penyimpanan msg_id ini yang memungkinkan broadcast terjadwal membaca
    video tanpa perlu get_chat_history (yang tidak bisa dipakai bot).
    """
    from database import get_setting
    from keyboards import kb_log

    ch_str = get_setting("log_channel_id", "")
    if not ch_str:
        return
    try:
        ch_id = int(ch_str)
    except ValueError:
        return

    row     = q("SELECT is_skipped FROM postingan WHERE id=?", (pid,))
    is_skip = bool(row[0][0]) if row else False
    mode    = "Anonim" if pengirim_label == "Anonim" else f"Nama Tampil — {pengirim_label}"
    teks = (
        f"📋 **LOG VIDEO KIRIMAN**\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 **Identitas Asli Pengirim:**\n"
        f"   Nama: **{full_name}**\n"
        f"   Username: @{uname or '—'}\n"
        f"   ID: `{uid}`\n\n"
        f"📝 **Mode Kirim:** {mode}\n"
        f"⏱️ **Durasi:** `{dur_str}`\n"
    )
    if keterangan:
        teks += f"💬 **Keterangan:** _{keterangan}_"

    sent_msg = None
    for _attempt in range(3):
        try:
            sent_msg = await client.send_video(ch_id, fid, caption=teks,
                                               reply_markup=kb_log(uid, pid, is_skip))
            break
        except FloodWait as fw:
            if _attempt < 2:
                await asyncio.sleep(int(fw.value * (1 + _attempt * 0.5)) + 2)
            else:
                print(f"[LOG] FloodWait berulang, kirim log dibatalkan: {fw.value}s")
        except Exception as e:
            if _is_ignorable(e):
                break
            if _attempt < 2:
                await asyncio.sleep(2 + _attempt * 2)
            else:
                print(f"[LOG] Gagal kirim log ke channel {ch_id}: {e}")

    # ── KUNCI PERBAIKAN: simpan msg_id agar broadcast bisa baca tanpa get_chat_history ──
    if sent_msg:
        q(
            "INSERT OR IGNORE INTO log_channel_msgs (msg_id, channel_id, postingan_id) "
            "VALUES (?,?,?)",
            (sent_msg.id, ch_id, pid), commit=True,
        )
        print(f"[LOG] Tersimpan: channel={ch_id} msg_id={sent_msg.id} pid={pid}")


# ════════════════════════════════════════════════════════════════════════════════
# BROADCAST TERJADWAL DARI LOG CHANNEL
# ════════════════════════════════════════════════════════════════════════════════
#
# PENTING — Mengapa menggunakan DB, bukan get_chat_history:
#   Bot Telegram TIDAK DIIZINKAN memanggil messages.GetHistory (MTProto).
#   Error: [400 BOT_METHOD_INVALID] — The method can't be used by bots.
#   Solusi: Setiap kali bot kirim video ke log channel, msg_id disimpan ke
#   tabel log_channel_msgs. Broadcast terjadwal membaca dari tabel ini.
# ════════════════════════════════════════════════════════════════════════════════

async def broadcast_scheduler(client):
    """
    Scheduler berbasis jam tetap WIB, dikonfigurasi via BROADCAST_SLOT_HOURS di config.py.
    """
    SLOT_HOURS = tuple(sorted(BROADCAST_SLOT_HOURS))

    slot_str = ", ".join(f"{h:02d}:00" for h in SLOT_HOURS)
    print(f"🕐 Broadcast scheduler dimulai (slot WIB: {slot_str}).")

    while True:
        now_wib  = datetime.now(TZ_WIB)
        next_run = None
        for h in SLOT_HOURS:
            candidate = now_wib.replace(hour=h, minute=0, second=0, microsecond=0)
            if candidate > now_wib:
                next_run = candidate
                break
        if next_run is None:
            tomorrow = now_wib + timedelta(days=1)
            next_run = tomorrow.replace(
                hour=SLOT_HOURS[0], minute=0, second=0, microsecond=0)

        wait_sec = (next_run - now_wib).total_seconds()
        print(f"[BROADCAST] Jadwal berikutnya: "
              f"{next_run.strftime('%Y-%m-%d %H:%M WIB')} "
              f"(dalam {int(wait_sec // 3600)}j {int((wait_sec % 3600) // 60)}m)")
        await asyncio.sleep(wait_sec)

        try:
            await _do_broadcast_from_channel(client)
        except Exception as e:
            print(f"[BROADCAST] Error: {e}")


def _get_log_channel_entries(ch_id: int) -> list[tuple[int, int]]:
    """
    Ambil semua (msg_id, postingan_id) dari log_channel_msgs untuk channel ini,
    diurutkan dari terlama ke terbaru (msg_id ASC).

    Ini menggantikan _fetch_all_channel_videos yang menggunakan get_chat_history
    — method yang tidak bisa dipakai bot.
    """
    rows = q(
        "SELECT msg_id, postingan_id FROM log_channel_msgs "
        "WHERE channel_id=? ORDER BY msg_id ASC",
        (ch_id,),
    )
    return rows  # list of (msg_id, postingan_id)


async def _do_broadcast_from_channel(client):
    """
    Ambil 1 video dari log channel (via DB) → broadcast ke semua user sebagai kartu blur anonim.
    - Tidak perlu membaca riwayat channel (get_chat_history tidak bisa dipakai bot).
    - creator_id = OWNER_ID → Star/Ghost Eye masuk koin owner.
    - Owner dikecualikan dari penerima broadcast.
    - Video di-skip dilewati; jika semua skip, broadcast dibatalkan.
    """
    from database import get_setting

    ch_str = get_setting("log_channel_id", "")
    if not ch_str:
        print("[BROADCAST] Log channel belum dikonfigurasi, broadcast dilewati.")
        return
    try:
        ch_id = int(ch_str)
    except ValueError:
        print("[BROADCAST] log_channel_id tidak valid.")
        return

    last_msg_id = int(get_setting("broadcast_last_msg_id", "0"))

    # Ambil semua entri dari DB (sudah diurutkan terlama → terbaru)
    entries = _get_log_channel_entries(ch_id)
    if not entries:
        print("[BROADCAST] Tidak ada video di log channel (tabel log_channel_msgs kosong).")
        print("[BROADCAST] Pastikan bot sudah mengirim video ke channel dan log_channel_id sudah diset.")
        return

    # Tentukan urutan mulai: setelah last_msg_id, wrap ke awal jika sudah habis
    start_idx = 0
    for i, (msg_id, _) in enumerate(entries):
        if msg_id > last_msg_id:
            start_idx = i
            break
    else:
        start_idx = 0  # semua sudah terkirim → mulai dari awal

    ordered = entries[start_idx:] + entries[:start_idx]

    # Cari video pertama yang tidak di-skip
    chosen_msg_id = None
    chosen_pid    = None
    tried         = set()

    for (msg_id, pid) in ordered:
        if msg_id in tried:
            break
        tried.add(msg_id)

        row = q("SELECT is_skipped FROM postingan WHERE id=?", (pid,))
        if row and row[0][0]:
            continue  # di-skip
        if not row:
            continue  # tidak ada di postingan

        chosen_msg_id = msg_id
        chosen_pid    = pid
        break

    if not chosen_msg_id or not chosen_pid:
        print("[BROADCAST] Semua video di-skip atau tidak tersedia.")
        return

    # Update posisi terakhir
    q("INSERT OR REPLACE INTO settings (key,value) VALUES ('broadcast_last_msg_id',?)",
      (str(chosen_msg_id),), commit=True)

    # Ambil data postingan dari DB (satu query — termasuk creator_id dan pengirim_label)
    p_row = q(
        "SELECT file_id, blur_file_id, durasi, caption, creator_id, pengirim_label "
        "FROM postingan WHERE id=?",
        (chosen_pid,),
    )
    if not p_row:
        print(f"[BROADCAST] PID {chosen_pid} tidak ditemukan di postingan.")
        return

    file_id, blur_fid, durasi, orig_caption, creator_id, pengirim_label_db = p_row[0]
    # Hormati pilihan kreator: Anonim tetap Anonim, nama tampil tetap nama tampil
    bcast_label = (pengirim_label_db or "").strip() or "Anonim"

    # Dapatkan foto lokal terbaik (cache / regenerasi / pool acak / default)
    # _resolve_blur_for_broadcast selalu mengembalikan path lokal →
    # has_spoiler=True dijamin bekerja (Pyrogram kirim sebagai fresh upload)
    photo_src      = await _resolve_blur_for_broadcast(client, chosen_pid, file_id)
    blur_fid_saved = True  # file lokal sudah ada, simpan file_id baru hanya jika belum punya blur_fid

    # Susun caption kartu blur — gunakan label asli dari DB (Anonim / nama kreator)
    dur_str_bcast = f"{durasi // 60}m {durasi % 60}s" if durasi else "—"
    grt_str_bcast = f"{JATAH_GRATIS // 60}m" if JATAH_GRATIS >= 60 else f"{JATAH_GRATIS}s"
    bcast_caption = (
        f"🔔 **VIDEO BARU TERSEDIA!**\n\n"
        f"👤 Pengirim: **{bcast_label}**\n"
        f"⏱️ Durasi: `{dur_str_bcast}`\n\n"
        f"🎁 **{grt_str_bcast} pertama GRATIS!**\n"
        f"Tambah durasi pakai ⭐ Star.\n"
        f"Klik tombol untuk mulai menonton 👇"
    )
    if orig_caption:
        bcast_caption += f"\n\n💬 **Keterangan:**\n_{orig_caption}_"

    # Kecualikan owner DAN pemilik konten asli dari penerima broadcast
    if creator_id and creator_id != OWNER_ID:
        semua = q(
            "SELECT user_id FROM users WHERE user_id != ? AND user_id != ?",
            (OWNER_ID, creator_id),
        )
    else:
        semua = q("SELECT user_id FROM users WHERE user_id != ?", (OWNER_ID,))

    sukses  = 0
    now_str = _utc().isoformat()
    kb      = kb_watch(chosen_pid)
    # blur_fid_saved sudah diset di blok pemilihan photo_src di atas — jangan timpa

    for (tid,) in semua:
        # has_spoiler=True agar tampil sebagai kartu blur, sama seperti kirim manual
        kartu = await _safe_send_photo(client, tid, photo_src, bcast_caption, kb, has_spoiler=True)
        if kartu:
            _register_kartu(tid, chosen_pid, kartu.id, now_str)
            sukses += 1

            # Simpan blur_file_id hanya jika belum ada (fallback terakhir)
            if not blur_fid_saved and kartu.photo:
                q("UPDATE postingan SET blur_file_id=? WHERE id=?",
                  (kartu.photo.file_id, chosen_pid), commit=True)
                # Selanjutnya tetap kirim dari file lokal (tidak beralih ke file_id)
                blur_fid_saved = True

            asyncio.create_task(
                _auto_delete_kartu(client, tid, kartu.id, chosen_pid, KARTU_TTL)
            )
        await asyncio.sleep(_SEND_DELAY)

    print(f"[BROADCAST] channel_msg_id={chosen_msg_id} → PID {chosen_pid} → "
          f"terkirim ke {sukses}/{len(semua)} user (kreator dikecualikan).")


# ── Kirim satu video acak ke user baru (dari log channel) ────────────────────

async def kirim_video_selamat_datang(client, uid: int):
    """
    Dipanggil saat user baru /start (bukan owner).
    Tunggu 10 detik lalu kirim 1 video acak dari log channel sebagai kartu blur anonim.
    - Menggunakan tabel log_channel_msgs (DB lokal), bukan get_chat_history.
    - Fallback ke tabel postingan lokal jika log channel belum dikonfigurasi / kosong.
    """
    if uid == OWNER_ID:
        return

    await asyncio.sleep(10)

    from database import get_setting

    ch_str   = get_setting("log_channel_id", "")
    pid      = None
    blur_fid = None

    if ch_str:
        try:
            ch_id   = int(ch_str)
            entries = _get_log_channel_entries(ch_id)

            # Filter yang tidak di-skip dan bukan milik user sendiri
            eligible = []
            for (msg_id, p_id) in entries:
                row = q("SELECT is_skipped, creator_id FROM postingan WHERE id=?", (p_id,))
                if not row:
                    continue
                if row[0][0]:
                    continue  # di-skip
                if row[0][1] == uid:
                    continue  # video milik user sendiri, skip
                eligible.append(p_id)

            if eligible:
                pid = _random.choice(eligible)
                row = q("SELECT blur_file_id FROM postingan WHERE id=?", (pid,))
                blur_fid = row[0][0] if row and row[0][0] else None

        except Exception as e:
            print(f"[WELCOME] Gagal ambil video dari DB log channel: {e}")

    # Fallback ke postingan lokal jika channel kosong / belum dikonfigurasi
    # Kecualikan video milik user sendiri
    if not pid:
        videos = q(
            "SELECT id, blur_file_id FROM postingan "
            "WHERE is_skipped=0 AND creator_id != ? "
            "ORDER BY RANDOM() LIMIT 1",
            (uid,)
        )
        if not videos:
            return
        pid, blur_fid = videos[0]

    # TIDAK mengubah pengirim_label di DB — label asli kreator tetap terjaga.
    # Caption welcome menampilkan "Anonim" tanpa menyentuh data DB kreator.


    # Gunakan _resolve_blur_for_broadcast agar spoiler selalu bekerja
    # (cache / regenerasi / pool acak / default — sama dengan broadcast terjadwal)
    # Ambil semua data sekaligus (satu query) termasuk pengirim_label
    row_w = q("SELECT file_id, durasi, caption, pengirim_label FROM postingan WHERE id=?", (pid,))
    fid_w          = row_w[0][0] if row_w else ""
    dur_welcome    = row_w[0][1] if row_w else 0
    cap_welcome    = row_w[0][2] if row_w else ""
    label_welcome  = (row_w[0][3] if row_w and row_w[0][3] else "").strip() or "Anonim"
    photo_src_w = await _resolve_blur_for_broadcast(client, pid, fid_w)

    # Caption welcome: hormati pilihan kreator (Anonim / nama tampil)
    dur_str_w   = f"{dur_welcome // 60}m {dur_welcome % 60}s" if dur_welcome else "—"
    grt_str_w   = f"{JATAH_GRATIS // 60}m" if JATAH_GRATIS >= 60 else f"{JATAH_GRATIS}s"
    welcome_caption = (
        f"🔔 **VIDEO TERSEDIA!**\n\n"
        f"👤 Pengirim: **{label_welcome}**\n"
        f"⏱️ Durasi: `{dur_str_w}`\n\n"
        f"🎁 **{grt_str_w} pertama GRATIS!**\n"
        f"Tambah durasi pakai ⭐ Star.\n"
        f"Klik tombol untuk mulai menonton 👇"
    )
    if cap_welcome:
        welcome_caption += f"\n\n💬 **Keterangan:**\n_{cap_welcome}_"

    kb      = kb_watch(pid)
    now_str = _utc().isoformat()

    kartu = await _safe_send_photo(client, uid, photo_src_w, welcome_caption, kb, has_spoiler=True)
    if kartu:
        _register_kartu(uid, pid, kartu.id, now_str)
        asyncio.create_task(
            _auto_delete_kartu(client, uid, kartu.id, pid, KARTU_TTL)
        )
    else:
        # Fallback jika foto tidak bisa dikirim (user blokir / jaringan error)
        # Kirim notif teks agar user tetap tahu ada video
        try:
            await client.send_message(
                uid,
                f"🔔 **Ada video yang menunggu Anda!**\n\n"
                f"Klik tombol di bawah untuk menonton 👇",
                reply_markup=kb,
            )
        except Exception:
            pass


# ── Callback: tonton video ────────────────────────────────────────────────────

async def cb_watch(client: Client, cq: CallbackQuery, pid: int):
    uid = cq.from_user.id
    if uid in _sedang_proses:
        return await cq.answer("⏳ Sedang memuat...", show_alert=False)
    _sedang_proses.add(uid)
    try:
        row = q("SELECT file_id, creator_id, durasi, caption, file_type, pengirim_label FROM postingan WHERE id=?", (pid,))
        if not row:
            return await cq.answer("❌ Video tidak tersedia.", show_alert=True)
        fid, creator_id, durasi, keterangan, file_type, pengirim_label = row[0]
        file_type = file_type or "video"

        # Jika kreator sendiri yang mencoba tonton videonya → tolak
        if uid == creator_id:
            return await cq.answer(
                "⚠️ Anda tidak bisa menonton video yang Anda sendiri kirim.",
                show_alert=True
            )

        try:
            _, _, _, _, banned = get_user(uid)
        except Exception:
            return await cq.answer("❌ Gagal membaca akun.", show_alert=True)

        if banned:
            return await cq.answer("🚫 Akun Anda di-banned!", show_alert=True)

        # Tandai kartu sudah dibuka dan hapus dari chat user segera
        kartu_rows = q(
            "SELECT msg_id FROM kartu_blur "
            "WHERE user_id=? AND postingan_id=? AND sudah_dibuka=0",
            (uid, pid),
        )
        q(
            "UPDATE kartu_blur SET sudah_dibuka=1 "
            "WHERE user_id=? AND postingan_id=?",
            (uid, pid), commit=True,
        )
        for (kid,) in kartu_rows:
            try:
                await client.delete_messages(uid, kid)
            except Exception:
                pass

        dur_str = f"{durasi // 60}m {durasi % 60}s" if durasi else "—"
        now     = _utc()
        exp     = now + timedelta(seconds=JATAH_GRATIS)
        jam     = exp.astimezone(TZ_WIB).strftime("%H:%M:%S")

        # Gunakan pengirim_label dari DB (Anonim / nama tampil).
        # Fallback ke _creator_tag untuk postingan lama yang belum menyimpan label.
        display_label = (pengirim_label or "").strip()
        if not display_label:
            display_label = _creator_tag(creator_id)

        ghost   = get_ghost_eye(uid)
        cap     = _build_cap(display_label, dur_str, jam, keterangan)

        try:
            if file_type == "document":
                video_msg = await client.send_document(
                    uid, fid,
                    caption=cap,
                    reply_markup=kb_add_time_full(creator_id, ghost),
                    protect_content=True,
                )
            else:
                video_msg = await client.send_video(
                    uid, fid,
                    caption=cap,
                    reply_markup=kb_add_time_full(creator_id, ghost),
                    protect_content=True,
                )
        except Exception as _send_err:
            print(f"[WATCH] Gagal kirim video uid={uid} pid={pid}: {_send_err}")
            return await cq.answer("❌ Gagal mengirim video. Coba lagi.", show_alert=True)
        if not video_msg:
            return await cq.answer("❌ Gagal mengirim video.", show_alert=True)

        await cq.answer("▶️ Video sedang diputar!")

        q(
            "INSERT OR REPLACE INTO watch_sessions "
            "(user_id, video_msg_id, postingan_id, creator_id, expired_at, status) "
            "VALUES (?,?,?,?,?,'ACTIVE')",
            (uid, video_msg.id, pid, creator_id, exp.isoformat()), commit=True,
        )

        key = (uid, video_msg.id)
        # Set _sesi[key] SEKARANG (sebelum task jalan) untuk mencegah
        # _extend_session salah kira belum ada timer dan spawn duplikat.
        _sesi[key] = {"hangus": exp, "creator_id": creator_id, "pid": pid}
        asyncio.create_task(_timer(client, uid, video_msg.id, pid, creator_id, exp))

    except Exception as e:
        print(f"[WATCH] Error uid={uid} pid={pid}: {e}")
        await cq.answer("❌ Terjadi kesalahan.", show_alert=True)
    finally:
        _sedang_proses.discard(uid)


# ── Callback: tambah durasi dengan Star ──────────────────────────────────────

async def cb_add_time(client: Client, cq: CallbackQuery, creator_id: int):
    uid    = cq.from_user.id
    msg_id = cq.message.id
    key    = (uid, msg_id)

    try:
        kuota, _, _, _, banned = get_user(uid)
    except Exception:
        return await cq.answer("❌ Gagal membaca akun.", show_alert=True)

    if banned:
        return await cq.answer("🚫 Akun Anda di-banned!", show_alert=True)

    if kuota < HARGA_STAR:
        return await cq.answer(
            f"⚠️ Star tidak cukup!\n\n"
            f"Anda punya: {kuota} ⭐\n"
            f"Dibutuhkan: {HARGA_STAR} ⭐\n\n"
            f"Beli Star via menu ⭐ Star / Kuota.",
            show_alert=True)

    # ── Cek sesi SEBELUM potong star ─────────────────────────────────────────
    # Cek di memori dulu (lebih cepat, tidak tergantung status DB)
    if key in _sesi:
        old_exp = _sesi[key]["hangus"]
        pid     = _sesi[key]["pid"]
    else:
        # Fallback ke DB — ambil tanpa filter status agar bisa perpanjang
        # meski timer sudah lewat sedikit (race condition tipis)
        sesi_row = q(
            "SELECT expired_at, postingan_id FROM watch_sessions "
            "WHERE user_id=? AND video_msg_id=?",
            (uid, msg_id),
        )
        if not sesi_row:
            return await cq.answer("⚠️ Sesi tidak ditemukan.", show_alert=True)
        # Tolak jika sudah DONE lebih dari DURASI_DITAMBAH detik yang lalu
        # (artinya video memang sudah lama dihapus, bukan sekadar expired tipis)
        raw_exp = sesi_row[0][0]
        old_exp = datetime.fromisoformat(raw_exp)
        if old_exp.tzinfo is None:
            old_exp = old_exp.replace(tzinfo=timezone.utc)
        grace = timedelta(seconds=DURASI_DITAMBAH)
        if _utc() - old_exp > grace:
            return await cq.answer(
                "⚠️ Waktu perpanjangan habis.\nVideo sudah tidak tersedia.",
                show_alert=True,
            )
        pid = sesi_row[0][1]

    # ── Potong star + kredit koin kreator (atomik) ────────────────────────────
    from datetime import datetime, timezone as _tz
    _now_iso = datetime.now(_tz.utc).isoformat()
    ok = q_atomic(
        ("UPDATE users SET kuota_star = kuota_star - ? WHERE user_id=? AND kuota_star >= ?",
         (HARGA_STAR, uid, HARGA_STAR)),
        ("UPDATE users SET coin = coin + 1 WHERE user_id=?",
         (creator_id,)),
        ("UPDATE users SET total_stars_used = total_stars_used + ?, last_star_used = ? WHERE user_id=?",
         (HARGA_STAR, _now_iso, uid)),
    )
    if not ok:
        return await cq.answer(
            "❌ Gagal memproses. Star tidak mencukupi atau terjadi kesalahan.",
            show_alert=True
        )

    await _extend_session(client, cq, uid, msg_id, pid, creator_id, key, old_exp)


# ── Callback: tambah durasi dengan Ghost Eye ─────────────────────────────────

async def cb_add_time_ghost(client: Client, cq: CallbackQuery, creator_id: int):
    uid    = cq.from_user.id
    msg_id = cq.message.id
    key    = (uid, msg_id)

    try:
        _, _, _, _, banned = get_user(uid)
    except Exception:
        return await cq.answer("❌ Gagal membaca akun.", show_alert=True)

    if banned:
        return await cq.answer("🚫 Akun Anda di-banned!", show_alert=True)

    ghost = get_ghost_eye(uid)
    if ghost < 1:
        kuota2, _, _, _, _ = get_user(uid)
        if kuota2 < HARGA_STAR:
            return await cq.answer(
                "⚠️ Token Anda habis!\n\n"
                "Untuk menambah durasi gratis, undang teman bergabung.\n"
                "Klik tombol 🎁 Durasi Gratis di menu utama.",
                show_alert=True)
        return await cq.answer(
            "❌ Ghost Eye habis!\nGunakan ⭐ Star untuk tambah durasi.",
            show_alert=True)

    # ── Cek sesi SEBELUM kurangi ghost eye ───────────────────────────────────
    if key in _sesi:
        old_exp = _sesi[key]["hangus"]
        pid     = _sesi[key]["pid"]
    else:
        sesi_row = q(
            "SELECT expired_at, postingan_id FROM watch_sessions "
            "WHERE user_id=? AND video_msg_id=?",
            (uid, msg_id),
        )
        if not sesi_row:
            return await cq.answer("⚠️ Sesi tidak ditemukan.", show_alert=True)
        raw_exp = sesi_row[0][0]
        old_exp = datetime.fromisoformat(raw_exp)
        if old_exp.tzinfo is None:
            old_exp = old_exp.replace(tzinfo=timezone.utc)
        grace = timedelta(seconds=DURASI_DITAMBAH)
        if _utc() - old_exp > grace:
            return await cq.answer(
                "⚠️ Waktu perpanjangan habis.\nVideo sudah tidak tersedia.",
                show_alert=True,
            )
        pid = sesi_row[0][1]

    # ── Kurangi ghost eye (atomik) ────────────────────────────────────────────
    ok = q_atomic(
        ("UPDATE users SET ghost_eye = ghost_eye - 1 WHERE user_id=? AND ghost_eye > 0",
         (uid,)),
    )
    if not ok:
        return await cq.answer(
            "❌ Ghost Eye tidak mencukupi atau gagal diproses.",
            show_alert=True
        )

    await _extend_session(client, cq, uid, msg_id, pid, creator_id, key, old_exp)


# ── Helper: perpanjang sesi ───────────────────────────────────────────────────

async def _extend_session(client, cq, uid, msg_id, pid, creator_id, key, old_exp):
    # Hitung new_exp: jika key masih di _sesi pakai hangus terkini,
    # jika tidak (timer sudah expired) pakai NOW sebagai base agar tidak langsung expired lagi
    if key in _sesi:
        base = _sesi[key]["hangus"]
    else:
        # Timer sudah selesai → base dari sekarang agar durasi benar-benar ditambah
        base = max(old_exp, _utc())

    new_exp = base + timedelta(seconds=DURASI_DITAMBAH)

    if key in _sesi:
        # Timer masih jalan → cukup update hangus, _timer akan ikut
        _sesi[key]["hangus"] = new_exp
    else:
        # Timer sudah berhenti → buat sesi & timer baru
        _sesi[key] = {"hangus": new_exp, "creator_id": creator_id, "pid": pid}
        asyncio.create_task(_timer(client, uid, msg_id, pid, creator_id, new_exp))

    # Pastikan status watch_session kembali ACTIVE agar sesi terdaftar
    q(
        "UPDATE watch_sessions SET expired_at=?, status='ACTIVE' "
        "WHERE user_id=? AND video_msg_id=?",
        (new_exp.isoformat(), uid, msg_id), commit=True,
    )

    jam     = new_exp.astimezone(TZ_WIB).strftime("%H:%M:%S")
    krow    = q("SELECT caption, durasi, pengirim_label FROM postingan WHERE id=?", (pid,))
    ket     = krow[0][0] if krow else ""
    dur     = krow[0][1] if krow else 0
    saved_label = (krow[0][2] if krow and krow[0][2] else "").strip()
    display_label = saved_label if saved_label else _creator_tag(creator_id)
    dur_str = f"{dur // 60}m {dur % 60}s" if dur else "—"
    ghost   = get_ghost_eye(uid)
    mnt     = DURASI_DITAMBAH // 60
    cap     = _build_cap(display_label, dur_str, jam, ket)

    try:
        await cq.message.edit_caption(
            caption=cap,
            reply_markup=kb_add_time_full(creator_id, ghost),
        )
    except Exception:
        pass
    await cq.answer(f"✅ +{mnt} menit ditambahkan!", show_alert=True)


# ── Timer sesi ────────────────────────────────────────────────────────────────

async def _timer(client, uid, msg_id, pid, creator_id, exp):
    key = (uid, msg_id)
    # Hanya inisialisasi jika belum di-set oleh cb_watch (cegah race condition)
    if key not in _sesi:
        _sesi[key] = {"hangus": exp, "creator_id": creator_id, "pid": pid}
    while True:
        await asyncio.sleep(5)
        if key not in _sesi:
            return
        if (_sesi[key]["hangus"] - _utc()).total_seconds() <= 0:
            break
    sesi = _sesi.pop(key, None)
    if sesi:
        await _expire(client, uid, msg_id, sesi["pid"], sesi["creator_id"])


async def _expire(client, uid, msg_id, pid, creator_id):
    """Hapus video + kartu blur saat expired."""
    q("UPDATE watch_sessions SET status='DONE' WHERE user_id=? AND video_msg_id=?",
      (uid, msg_id), commit=True)
    try:
        await client.delete_messages(uid, msg_id)
    except Exception:
        pass

    kartu = q(
        "SELECT msg_id FROM kartu_blur "
        "WHERE user_id=? AND postingan_id=? AND sudah_dibuka=0",
        (uid, pid),
    )
    for (kid,) in kartu:
        try:
            await client.delete_messages(uid, kid)
        except Exception:
            pass
    q("UPDATE kartu_blur SET sudah_dibuka=1 WHERE user_id=? AND postingan_id=?",
      (uid, pid), commit=True)

    set_state(uid, "MAIN_MENU")
    await send(client, uid,
        "⏱️ **Waktu menonton habis!**\n\n"
        "Video telah dimusnahkan dari chat.\n"
        "_Tunggu broadcast video baru untuk menonton lagi._",
        state="MAIN_MENU")


# ── Restore sesi setelah restart ──────────────────────────────────────────────

async def restore_sessions(client):
    rows = q(
        "SELECT user_id, video_msg_id, postingan_id, creator_id, expired_at "
        "FROM watch_sessions WHERE status='ACTIVE'"
    )
    if not rows:
        return
    print(f"🔄 Restore {len(rows)} sesi tonton...")
    now = _utc()
    for uid, mid, pid, cid, exp_str in rows:
        try:
            exp = datetime.fromisoformat(exp_str)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
        except Exception:
            q("UPDATE watch_sessions SET status='DONE' WHERE user_id=? AND video_msg_id=?",
              (uid, mid), commit=True)
            continue

        if (exp - now).total_seconds() <= 0:
            asyncio.create_task(_expire(client, uid, mid, pid, cid))
        else:
            frow = q("SELECT file_id FROM postingan WHERE id=?", (pid,))
            if frow:
                asyncio.create_task(_timer(client, uid, mid, pid, cid, exp))
            else:
                q("UPDATE watch_sessions SET status='DONE' WHERE user_id=? AND video_msg_id=?",
                  (uid, mid), commit=True)


async def restore_blur_timers(client):
    rows = q(
        "SELECT user_id, postingan_id, msg_id, sent_at FROM kartu_blur "
        "WHERE sudah_dibuka=0 AND sent_at != ''"
    )
    if not rows:
        return
    print(f"🔄 Restore {len(rows)} timer kartu blur...")
    now = _utc()
    for uid, pid, msg_id, sent_at_str in rows:
        try:
            sent_at = datetime.fromisoformat(sent_at_str)
            if sent_at.tzinfo is None:
                sent_at = sent_at.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        remaining = ((sent_at + timedelta(seconds=KARTU_TTL)) - now).total_seconds()
        asyncio.create_task(
            _auto_delete_kartu(client, uid, msg_id, pid, max(0.0, remaining))
        )
