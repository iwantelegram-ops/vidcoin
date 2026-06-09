"""
plugins/callback_handler.py — Router semua CallbackQuery inline button

PERBAIKAN:
  - Tambah handler untuk bcast_pick_, bcast_yes_, bcast_no (sebelumnya crash).
  - Perbaikan parsing wd_done_owner_ agar tidak crash.
  - Semua tombol inline sekarang ditangani dengan benar.
"""
import asyncio

from pyrogram import Client
from pyrogram.types import CallbackQuery
from config import OWNER_ID
from database import q, q_atomic, get_user, set_state
from keyboards import (
    kb_topup, kb_ban_unban, kb_wd_user, KB_CHAT_CANCEL,
    kb_watch,
)

_tmp_star: dict[int, int] = {}   # uid → jumlah star sementara saat owner sesuaikan topup


@Client.on_callback_query()
async def router(client: Client, cq: CallbackQuery):
    data = cq.data or ""
    uid  = cq.from_user.id

    try:
        await _dispatch(client, cq, data, uid)
    except Exception as e:
        print(f"[CALLBACK] Error: {e} | data={data}")
        try:
            await cq.answer("❌ Terjadi kesalahan sistem.", show_alert=True)
        except Exception:
            pass


async def _dispatch(client: Client, cq: CallbackQuery, data: str, uid: int):

    # ── Tonton video ──────────────────────────────────────────────────────────
    if data.startswith("watch_"):
        try:
            pid = int(data.split("_", 1)[1])
        except (ValueError, IndexError):
            return await cq.answer("❌ Data rusak.", show_alert=True)
        from plugins.video_handler import cb_watch
        await cb_watch(client, cq, pid)

    # ── Tambah durasi dengan Ghost Eye (cek SEBELUM addtime_ biasa) ───────────
    elif data.startswith("addtime_ghost_"):
        try:
            creator_id = int(data.split("_", 2)[2])
        except (ValueError, IndexError):
            return await cq.answer("❌ Data rusak.", show_alert=True)
        from plugins.video_handler import cb_add_time_ghost
        await cb_add_time_ghost(client, cq, creator_id)

    # ── Tambah durasi tonton dengan Star ──────────────────────────────────────
    elif data.startswith("addtime_"):
        try:
            creator_id = int(data.split("_", 1)[1])
        except (ValueError, IndexError):
            return await cq.answer("❌ Data rusak.", show_alert=True)
        from plugins.video_handler import cb_add_time
        await cb_add_time(client, cq, creator_id)

    # ── Topup Star: Owner sesuaikan jumlah ────────────────────────────────────
    elif data.startswith("tr_add_") or data.startswith("tr_sub_"):
        if uid != OWNER_ID:
            return await cq.answer("⛔ Bukan hak Anda.", show_alert=True)

        # tr_add_123456789 → split maxsplit=2 untuk hindari salah parse
        parts = data.split("_", 2)
        if len(parts) < 3:
            return await cq.answer("❌ Data rusak.", show_alert=True)
        aksi = parts[0] + "_" + parts[1]   # "tr_add" atau "tr_sub"
        try:
            target_id = int(parts[2])
        except (ValueError, IndexError):
            return await cq.answer("❌ Data rusak.", show_alert=True)

        if target_id not in _tmp_star:
            _tmp_star[target_id] = 10
        if aksi == "tr_add":
            _tmp_star[target_id] += 10
        else:
            _tmp_star[target_id] = max(0, _tmp_star[target_id] - 10)

        await cq.answer()
        try:
            await cq.message.edit_caption(
                caption=(
                    f"🔔 **Bukti Pembayaran**\n\n"
                    f"👤 User ID: `{target_id}`\n"
                    f"⭐ Jumlah Star: **{_tmp_star[target_id]}**\n\n"
                    f"_Sesuaikan lalu tekan Konfirmasi._"
                ),
                reply_markup=kb_topup(target_id, _tmp_star[target_id]),
            )
        except Exception:
            pass

    # ── Topup Star: Owner konfirmasi kirim ────────────────────────────────────
    elif data.startswith("tr_confirm_"):
        if uid != OWNER_ID:
            return await cq.answer("⛔ Bukan hak Anda.", show_alert=True)
        try:
            target_id = int(data.split("_", 2)[2])
        except (ValueError, IndexError):
            return await cq.answer("❌ Data rusak.", show_alert=True)

        jumlah = _tmp_star.pop(target_id, 10)
        q("UPDATE users SET kuota_star = kuota_star + ? WHERE user_id=?", (jumlah, target_id), commit=True)
        await cq.answer(f"✅ {jumlah} ⭐ dikirim!", show_alert=True)
        try:
            await cq.message.edit_caption(
                caption=f"✅ **SELESAI** — {jumlah} ⭐ dikirim ke `{target_id}`."
            )
        except Exception:
            pass
        try:
            await client.send_message(target_id,
                f"🎉 **Top Up Disetujui!**\n\n"
                f"**+{jumlah} ⭐** ditambahkan ke akun Anda.\n"
                f"Gunakan Star untuk menambah durasi tonton video!")
        except Exception:
            pass

    # ── WD: Owner DONE — dana sudah dikirim ───────────────────────────────────
    elif data.startswith("wd_done_owner_"):
        if uid != OWNER_ID:
            return await cq.answer("⛔ Bukan hak Anda.", show_alert=True)
        # Format: wd_done_owner_{tx_id}_{target_id}_{jumlah}
        parts = data.split("_")
        # parts: ["wd","done","owner", tx_id, target_id, jumlah]
        if len(parts) < 6:
            return await cq.answer("❌ Data rusak.", show_alert=True)
        try:
            tx_id     = int(parts[3])
            target_id = int(parts[4])
            jumlah    = int(parts[5])
        except (ValueError, IndexError):
            return await cq.answer("❌ Data rusak.", show_alert=True)

        row = q("SELECT done_owner FROM transaksi_wd WHERE id=?", (tx_id,))
        if not row:
            return await cq.answer("❌ Transaksi tidak ditemukan.", show_alert=True)
        if row[0][0]:
            return await cq.answer("✅ Sudah diproses sebelumnya.", show_alert=True)

        q("UPDATE transaksi_wd SET done_owner=1 WHERE id=?", (tx_id,), commit=True)
        await cq.answer("✅ Transaksi diproses! Menunggu konfirmasi user.", show_alert=True)

        try:
            await cq.message.edit_caption(
                caption=(
                    f"✅ **Dana Sudah Dikirim ke User.**\n\n"
                    f"TX #{tx_id} — {jumlah} 🪙 untuk `{target_id}`.\n"
                    f"_Menunggu konfirmasi DONE dari user._"
                ),
                reply_markup=kb_ban_unban(target_id),
            )
        except Exception:
            pass

        try:
            umsg = await client.send_message(
                target_id,
                f"💰 **Pembayaran Sudah Dikirim!**\n\n"
                f"Silakan cek rekening / e-wallet Anda.\n"
                f"Jika dana sudah masuk, tekan tombol di bawah.\n\n"
                f"⚠️ _Koin akan dikurangi otomatis setelah konfirmasi._\n\n"
                f"⚠️ **Perhatian:** Selama belum menekan DONE, semua fitur bot "
                f"akan dibatasi sementara.",
                reply_markup=kb_wd_user(tx_id),
            )
            q("UPDATE transaksi_wd SET msg_id_user=? WHERE id=?", (umsg.id, tx_id), commit=True)
        except Exception:
            pass

    # ── WD: User DONE — dana sudah masuk ──────────────────────────────────────
    elif data.startswith("wd_done_user_"):
        try:
            tx_id = int(data.split("_", 3)[3])
        except (ValueError, IndexError):
            return await cq.answer("❌ Data rusak.", show_alert=True)

        row = q("SELECT user_id, jumlah_coin, done_user, done_owner FROM transaksi_wd WHERE id=?", (tx_id,))
        if not row:
            return await cq.answer("❌ Transaksi tidak ditemukan.", show_alert=True)

        tx_uid, jumlah, done_u, done_o = row[0]
        if uid != tx_uid:
            return await cq.answer("⛔ Bukan transaksi Anda.", show_alert=True)
        if done_u:
            return await cq.answer("✅ Sudah dikonfirmasi.", show_alert=True)
        if not done_o:
            return await cq.answer("⏳ Owner belum memproses.", show_alert=True)

        ok = q_atomic(
            ("UPDATE users SET coin = coin - ? WHERE user_id=? AND coin >= ?",
             (jumlah, tx_uid, jumlah)),
            ("UPDATE transaksi_wd SET done_user=1, status='SUCCESS' WHERE id=? AND done_user=0",
             (tx_id,)),
        )
        if not ok:
            return await cq.answer(
                "❌ Gagal memproses. Koin tidak mencukupi atau terjadi kesalahan sistem.",
                show_alert=True
            )

        await cq.answer("🎉 Konfirmasi berhasil! Koin dikurangi.", show_alert=True)

        try:
            await cq.message.edit_text(
                f"🎉 **Widraw Selesai!**\n\n"
                f"🪙 **{jumlah} coin** dikurangi dari dompet Anda.\nTerima kasih! ✅",
                reply_markup=None,
            )
        except Exception:
            pass

        try:
            info = await client.get_users(tx_uid)
            nama = info.first_name if info else f"`{tx_uid}`"
            await client.send_message(OWNER_ID,
                f"✅ **Sistem:** Koin **{nama}** dikurangi **{jumlah} coin** otomatis.")
        except Exception:
            pass

    # ── Batasi posting video (dari log channel) ───────────────────────────────
    elif data.startswith("vban_"):
        if uid != OWNER_ID:
            return await cq.answer("⛔ Bukan hak Anda.", show_alert=True)
        try:
            target_id = int(data.split("_", 1)[1])
        except (ValueError, IndexError):
            return await cq.answer("❌ Data rusak.", show_alert=True)
        q("UPDATE users SET is_video_banned=1 WHERE user_id=?", (target_id,), commit=True)
        await cq.answer(f"🚫 Posting konten user {target_id} dibatasi.", show_alert=True)
        try:
            await client.send_message(
                target_id,
                "⚠️ **Pemberitahuan Sistem**\n\n"
                "Akses pengiriman konten Anda telah **dibatasi sementara** oleh Admin.\n"
                "_Fitur lainnya (tonton video, widraw, chat, dll) masih dapat digunakan seperti biasa._\n\n"
                "Hubungi Owner jika ada pertanyaan."
            )
        except Exception:
            pass

    # ── Pulihkan akses posting video ──────────────────────────────────────────
    elif data.startswith("vunban_"):
        if uid != OWNER_ID:
            return await cq.answer("⛔ Bukan hak Anda.", show_alert=True)
        try:
            target_id = int(data.split("_", 1)[1])
        except (ValueError, IndexError):
            return await cq.answer("❌ Data rusak.", show_alert=True)
        q("UPDATE users SET is_video_banned=0 WHERE user_id=?", (target_id,), commit=True)
        await cq.answer(f"✅ Posting konten user {target_id} dipulihkan.", show_alert=True)
        try:
            await client.send_message(
                target_id,
                "✅ **Pemberitahuan Sistem**\n\n"
                "Akses pengiriman konten Anda telah **dipulihkan**.\n"
                "_Anda dapat mengirim video kembali seperti biasa._"
            )
        except Exception:
            pass

    # ── Skip / Un-skip video di log channel (toggle) ──────────────────────────
    elif data.startswith("skip_"):
        if uid != OWNER_ID:
            return await cq.answer("⛔ Bukan hak Anda.", show_alert=True)
        try:
            parts = data.split("_", 2)
            target_id = int(parts[1])
            pid       = int(parts[2])
        except (ValueError, IndexError):
            return await cq.answer("❌ Data rusak.", show_alert=True)

        row = q("SELECT is_skipped FROM postingan WHERE id=?", (pid,))
        if not row:
            return await cq.answer("❌ Video tidak ditemukan.", show_alert=True)

        new_skip = 0 if row[0][0] else 1
        q("UPDATE postingan SET is_skipped=? WHERE id=?", (new_skip, pid), commit=True)

        from keyboards import kb_log
        try:
            await cq.message.edit_reply_markup(
                reply_markup=kb_log(target_id, pid, bool(new_skip))
            )
        except Exception:
            pass

        status_teks = "⏭️ Video akan dilewati saat broadcast." if new_skip else "▶️ Video kembali masuk antrean broadcast."
        await cq.answer(status_teks, show_alert=True)

    # ── Broadcast Manual: pilih video ─────────────────────────────────────────
    elif data.startswith("bcast_pick_"):
        if uid != OWNER_ID:
            return await cq.answer("⛔ Bukan hak Anda.", show_alert=True)
        try:
            pid = int(data.split("_", 2)[2])
        except (ValueError, IndexError):
            return await cq.answer("❌ Data rusak.", show_alert=True)

        from keyboards import kb_bcast_confirm
        row = q("SELECT durasi, caption FROM postingan WHERE id=?", (pid,))
        if not row:
            return await cq.answer("❌ Video tidak ditemukan.", show_alert=True)
        durasi, caption = row[0]
        dur_str = f"{durasi // 60}m{durasi % 60}s" if durasi else "—"
        label   = (caption[:30] + "…") if caption and len(caption) > 30 else (caption or f"Video #{pid}")

        await cq.answer()
        try:
            await cq.message.edit_text(
                f"📡 **Konfirmasi Broadcast**\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"🎬 Video: #{pid}\n"
                f"⏱️ Durasi: `{dur_str}`\n"
                f"💬 Keterangan: _{label}_\n\n"
                f"Broadcast video ini ke semua user sekarang?",
                reply_markup=kb_bcast_confirm(pid),
            )
        except Exception:
            pass

    # ── Broadcast Manual: konfirmasi kirim ────────────────────────────────────
    elif data.startswith("bcast_yes_"):
        if uid != OWNER_ID:
            return await cq.answer("⛔ Bukan hak Anda.", show_alert=True)
        try:
            pid = int(data.split("_", 2)[2])
        except (ValueError, IndexError):
            return await cq.answer("❌ Data rusak.", show_alert=True)

        await cq.answer("🚀 Broadcast dimulai!", show_alert=False)
        try:
            await cq.message.edit_text("⏳ **Broadcast sedang berjalan...**\n_Harap tunggu._")
        except Exception:
            pass

        asyncio.create_task(_exec_manual_bcast(client, pid, cq.message))

    # ── Broadcast Manual: batal ────────────────────────────────────────────────
    elif data == "bcast_no":
        if uid != OWNER_ID:
            return await cq.answer("⛔ Bukan hak Anda.", show_alert=True)
        await cq.answer("❌ Broadcast dibatalkan.")
        try:
            await cq.message.edit_text("❌ **Broadcast dibatalkan.**")
        except Exception:
            pass

    # ── Mulai Chat (Owner setujui permintaan chat dari user) ───────────────────
    elif data.startswith("chat_start_"):
        if uid != OWNER_ID:
            return await cq.answer("⛔ Bukan hak Anda.", show_alert=True)
        try:
            user_uid = int(data.split("_", 2)[2])
        except (ValueError, IndexError):
            return await cq.answer("❌ Data rusak.", show_alert=True)

        row = q("SELECT state FROM users WHERE user_id=?", (user_uid,))
        if not row or row[0][0] != "CHAT_QUEUE":
            return await cq.answer(
                "⚠️ User sudah tidak dalam antrean (mungkin sudah cancel).",
                show_alert=True
            )

        _, _, owner_state, _, _ = get_user(OWNER_ID)
        if owner_state.startswith("CHAT_WITH_"):
            return await cq.answer(
                "⚠️ Anda sedang dalam sesi chat lain. Akhiri dulu sebelum memulai yang baru.",
                show_alert=True
            )

        set_state(user_uid, f"CHAT_WITH_{OWNER_ID}")
        set_state(OWNER_ID, f"CHAT_WITH_{user_uid}")

        await cq.answer("✅ Chat dimulai!", show_alert=False)

        try:
            await cq.message.delete()
        except Exception:
            pass

        try:
            from plugins.chat_owner import _pending_notif
            _pending_notif.pop(user_uid, None)
        except Exception:
            pass

        await client.send_message(
            OWNER_ID,
            f"💬 **Chat terhubung!**\n\n"
            f"Anda sekarang terhubung dengan user `{user_uid}`.\n"
            f"_Semua pesan akan diteruskan. Klik ❌ Cancel untuk mengakhiri._",
            reply_markup=KB_CHAT_CANCEL,
        )

        try:
            await client.send_message(
                user_uid,
                "✅ **Owner telah terhubung!**\n\n"
                "Anda sekarang dapat berkomunikasi langsung.\n"
                "_Klik ❌ Cancel untuk mengakhiri sesi._",
                reply_markup=KB_CHAT_CANCEL,
            )
        except Exception:
            pass

    # ── Ban / Unban ────────────────────────────────────────────────────────────
    elif data.startswith("ban_"):
        if uid != OWNER_ID:
            return await cq.answer("⛔ Bukan hak Anda.", show_alert=True)
        try:
            target_id = int(data.split("_", 1)[1])
        except (ValueError, IndexError):
            return await cq.answer("❌ Data rusak.", show_alert=True)
        q("UPDATE users SET is_banned=1 WHERE user_id=?", (target_id,), commit=True)
        await cq.answer(f"🚫 User {target_id} di-BAN.", show_alert=True)

    elif data.startswith("unban_"):
        if uid != OWNER_ID:
            return await cq.answer("⛔ Bukan hak Anda.", show_alert=True)
        try:
            target_id = int(data.split("_", 1)[1])
        except (ValueError, IndexError):
            return await cq.answer("❌ Data rusak.", show_alert=True)
        q("UPDATE users SET is_banned=0 WHERE user_id=?", (target_id,), commit=True)
        await cq.answer(f"✅ User {target_id} di-UNBAN.", show_alert=True)

    # ── Konfirmasi ganti Log Channel ──────────────────────────────────────────
    elif data.startswith("lch_confirm_"):
        if uid != OWNER_ID:
            return await cq.answer("⛔ Bukan hak Anda.", show_alert=True)
        new_ch_id = data[len("lch_confirm_"):]
        q("INSERT OR REPLACE INTO settings (key,value) VALUES ('log_channel_id',?)",
          (new_ch_id,), commit=True)
        q("DELETE FROM settings WHERE key='log_channel_id_pending'", commit=True)
        await cq.answer("✅ Log Channel berhasil diganti!", show_alert=True)
        try:
            await cq.message.edit_text(
                f"✅ **Log Channel diganti ke:** `{new_ch_id}`\n\n"
                f"_Semua log video baru akan dikirim ke channel ini._"
            )
        except Exception:
            pass

    elif data == "lch_cancel":
        if uid != OWNER_ID:
            return await cq.answer("⛔ Bukan hak Anda.", show_alert=True)
        q("DELETE FROM settings WHERE key='log_channel_id_pending'", commit=True)
        await cq.answer("❌ Pergantian Log Channel dibatalkan.", show_alert=True)
        try:
            await cq.message.edit_text("❌ **Pergantian Log Channel dibatalkan.**\n\n_Channel sebelumnya tetap aktif._")
        except Exception:
            pass

    # ── Statistik Bot: navigasi kategori + halaman ────────────────────────────
    elif data.startswith("stats_cat_"):
        if uid != OWNER_ID:
            return await cq.answer("⛔ Bukan hak Anda.", show_alert=True)
        parts = data.split("_")
        # format: stats_cat_{category}_{page}
        # parts idx: 0=stats 1=cat 2=category 3=page
        if len(parts) < 4:
            return await cq.answer("❌ Data rusak.", show_alert=True)
        category = parts[2]
        try:
            page = int(parts[3])
        except ValueError:
            return await cq.answer("❌ Data rusak.", show_alert=True)
        from plugins.admin import build_stats_page_text
        text, kb = build_stats_page_text(category, page)
        await cq.answer()
        try:
            await cq.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass

    elif data == "stats_menu":
        if uid != OWNER_ID:
            return await cq.answer("⛔ Bukan hak Anda.", show_alert=True)
        from plugins.admin import _stats_summary_text
        from keyboards import kb_stats_menu
        await cq.answer()
        try:
            await cq.message.edit_text(_stats_summary_text(), reply_markup=kb_stats_menu())
        except Exception:
            pass

    # ── Reset Database (konfirmasi / batal dari /reset owner) ─────────────────
    elif data in ("reset_confirm", "reset_cancel"):
        from plugins.reset_db import cb_reset
        await cb_reset(client, cq)

    else:
        await cq.answer("❓ Aksi tidak dikenal.", show_alert=True)


# ── Broadcast manual dari pilihan video ───────────────────────────────────────

async def _exec_manual_bcast(client, pid: int, notif_msg):
    """Background task: broadcast satu video (dari pid) ke semua user."""
    import asyncio
    from config import OWNER_ID, JATAH_GRATIS
    from keyboards import kb_watch
    from plugins.video_handler import (
        _safe_send_photo, _register_kartu, _auto_delete_kartu,
        _resolve_blur_for_broadcast, KARTU_TTL, _utc,
    )

    p_row = q("SELECT file_id, blur_file_id, durasi, caption, creator_id FROM postingan WHERE id=?", (pid,))
    if not p_row:
        try:
            await notif_msg.edit_text("❌ Video tidak ditemukan.")
        except Exception:
            pass
        return

    file_id, blur_fid, durasi, orig_caption, creator_id = p_row[0]

    # Gunakan _resolve_blur_for_broadcast agar has_spoiler=True bekerja sempurna
    # (file lokal → Telegram treat sebagai fresh upload → spoiler dijamin tampil)
    photo_src = await _resolve_blur_for_broadcast(client, pid, file_id)

    dur_str = f"{durasi // 60}m {durasi % 60}s" if durasi else "—"
    grt_str = f"{JATAH_GRATIS // 60}m" if JATAH_GRATIS >= 60 else f"{JATAH_GRATIS}s"
    caption = (
        f"🔔 **VIDEO TERSEDIA!**\n\n"
        f"⏱️ Durasi: `{dur_str}`\n\n"
        f"🎁 **{grt_str} pertama GRATIS!**\n"
        f"Tambah durasi pakai ⭐ Star.\n"
        f"Klik tombol untuk mulai menonton 👇"
    )
    if orig_caption:
        caption += f"\n\n💬 _{orig_caption}_"

    # Kecualikan owner DAN kreator konten asli dari penerima broadcast
    if creator_id and creator_id != OWNER_ID:
        semua = q(
            "SELECT user_id FROM users WHERE user_id != ? AND user_id != ?",
            (OWNER_ID, creator_id),
        )
    else:
        semua = q("SELECT user_id FROM users WHERE user_id != ?", (OWNER_ID,))
    total   = len(semua)
    sukses  = 0
    now_str = _utc().isoformat()
    kb      = kb_watch(pid)

    for i, (tid,) in enumerate(semua, 1):
        kartu = await _safe_send_photo(client, tid, photo_src, caption, kb, has_spoiler=True)
        if kartu:
            _register_kartu(tid, pid, kartu.id, now_str)
            sukses += 1
            asyncio.create_task(_auto_delete_kartu(client, tid, kartu.id, pid, KARTU_TTL))

        if i % 50 == 0 or i == total:
            try:
                pct = int(i / total * 100) if total else 100
                await notif_msg.edit_text(
                    f"⏳ **Mengirim broadcast... {pct}%**\n\n"
                    f"📤 Diproses: {i}/{total}\n"
                    f"✅ Berhasil: {sukses}\n"
                    f"❌ Gagal   : {i - sukses}"
                )
            except Exception:
                pass

        await asyncio.sleep(0.065)

    try:
        await notif_msg.edit_text(
            f"✅ **Broadcast Selesai!**\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Terkirim: **{sukses}** pengguna\n"
            f"❌ Gagal  : **{total - sukses}** pengguna\n"
            f"👥 Total  : **{total}** pengguna"
        )
    except Exception:
        pass
