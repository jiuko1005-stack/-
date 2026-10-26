"""たすけチャンネル用：Whisper で台本（scenes.json）と声の時刻を合わせる。

  segments モード：録音から場面ごとの声の区間を出し、segments.json の start / end を書き換える
  srt モード    ：完成した動画から、台本どおりの文字の字幕ファイル（.srt）を作る

聞き取った文字をそのまま使うのではなく、台本の文字と突き合わせて時刻だけをもらう。
なので聞き間違いがあっても、字幕の文字は台本のまま正しく出る。
"""
import argparse
import difflib
import json
import os
import re
import shutil
import sys
import time
import unicodedata

MIN_RUN = 3        # この文字数以上続けて一致したところだけを手がかりにする（1〜2文字の偶然の一致を無視）
LOW_COVER = 0.6    # 台本の文字のうち一致した割合がこれ未満の場面は「要確認」
SRT_MAX = 28       # 字幕1枚の最大文字数。超えたら「、」で分ける
SRT_TAIL = 0.3     # 字幕を声の終わりから少し残す秒数
SRT_MIN = 1.0      # 字幕の最短表示秒数

SKIP = set("、。，．,.！!？?「」『』（）()【】［］[]・…‥〜~:：;；\"'“”‘’ 　")


def norm_text(s):
    """比べる用に文字をそろえる（全角半角・カタカナ→ひらがな・記号と空白を消す）。"""
    out = []
    for c in unicodedata.normalize("NFKC", s):
        if c in SKIP or c.isspace() or unicodedata.category(c)[0] in "PZ":
            continue
        o = ord(c)
        if 0x30A1 <= o <= 0x30F6:
            c = chr(o - 0x60)
        out.append(c.lower())
    return "".join(out)


def load_scenes(path):
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    scenes = data["scenes"] if isinstance(data, dict) else data
    return [s for s in scenes if (s.get("text") or "").strip()]


def transcribe(path, model_name, device, compute_type, retranscribe):
    """Whisper で聞き取り、1文字ずつの時刻 [(文字, 開始, 終了)] を返す。結果はキャッシュする。"""
    cache = f"{path}.whisper-{model_name}.json"
    if os.path.exists(cache) and not retranscribe:
        print(f"前回の聞き取り結果を使います：{cache}")
        with open(cache, encoding="utf-8") as f:
            return [tuple(x) for x in json.load(f)]

    from faster_whisper import WhisperModel
    print(f"Whisper（{model_name}）で聞き取り中…（初回はモデルのダウンロードがあります）")
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, info = model.transcribe(path, language="ja", word_timestamps=True,
                                      vad_filter=True, beam_size=5)
    chars = []
    for seg in segments:
        print(f"  {seg.start:7.1f}s  {seg.text.strip()}")
        for w in seg.words or []:
            t = norm_text(w.word)
            if not t:
                continue
            d = (w.end - w.start) / len(t)
            for i, c in enumerate(t):
                chars.append((c, round(w.start + d * i, 3), round(w.start + d * (i + 1), 3)))
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(chars, f, ensure_ascii=False)
    return chars


def align(units, rec):
    """units = [表示用の文字列, …]。各 unit の (開始, 終了, 一致した割合) を返す（見つからなければ None）。"""
    a, owner = [], []
    for ui, text in enumerate(units):
        for c in norm_text(text):
            a.append(c)
            owner.append(ui)
    b = "".join(c for c, _, _ in rec)
    sm = difflib.SequenceMatcher(None, "".join(a), b, autojunk=False)
    hit = [[] for _ in units]
    total = [0] * len(units)
    for ui in owner:
        total[ui] += 1
    for blk in sm.get_matching_blocks():
        if blk.size < MIN_RUN:
            continue
        for k in range(blk.size):
            hit[owner[blk.a + k]].append(blk.b + k)

    res = []
    for ui in range(len(units)):
        if not hit[ui]:
            res.append(None)
            continue
        res.append([rec[min(hit[ui])][1], rec[max(hit[ui])][2], len(hit[ui]) / max(total[ui], 1)])

    # 見つからなかったところは前後の間に置く
    for i, r in enumerate(res):
        if r is None:
            prev = next((res[j][1] for j in range(i - 1, -1, -1) if res[j]), 0.0)
            nxt = next((res[j][0] for j in range(i + 1, len(res)) if res[j]), prev)
            res[i] = [prev, max(prev, nxt), 0.0]
    # 前の終わりより前に始まらないようにする
    for i in range(1, len(res)):
        if res[i][0] < res[i - 1][1]:
            res[i][0] = res[i - 1][1]
            res[i][1] = max(res[i][1], res[i][0])
    return res


def rec_text(rec, start, end):
    return "".join(c for c, s, _ in rec if start - 0.2 <= s <= end + 0.2)


# ---------------------------------------------------------------- segments モード

def entries_of(old):
    """前の segments.json の中の場面の入れ物を id で引けるようにする（形はそのまま残す）。"""
    if isinstance(old, dict) and isinstance(old.get("segments"), list):
        old = old["segments"]
    if isinstance(old, list):
        return {str(e.get("id")): e for e in old if isinstance(e, dict) and "id" in e}
    if isinstance(old, dict):
        return {str(k): v for k, v in old.items() if isinstance(v, dict)}
    return {}


def run_segments(args):
    scenes = [s for s in load_scenes(args.scenes) if s.get("kind") != "op"]
    rec = transcribe(args.audio, args.model, args.device, args.compute_type, args.retranscribe)
    res = align([s["text"] for s in scenes], rec)

    old, entries = None, {}
    if os.path.exists(args.segments):
        with open(args.segments, encoding="utf-8-sig") as f:
            old = json.load(f)
        entries = entries_of(old)
        if not entries:
            print("！ 前の segments.json の形が読めなかったので、新しく作ります")
            old = None

    print()
    print(f"{'場面':<12}{'種類':<6}{'開始':>8}{'終了':>8}{'前との差':>10}{'一致':>6}")
    warn = []
    for i, (s, (st, en, cover)) in enumerate(zip(scenes, res)):
        sid = str(s["id"])
        diff = ""
        e = entries.get(sid)
        if e is not None and "start" in e:
            diff = f"{st - float(e['start']):+.2f}"
        flag = "  ★要確認" if cover < LOW_COVER else ""
        print(f"{sid:<12}{s.get('kind', ''):<6}{st:8.2f}{en:8.2f}{diff:>10}{cover:6.0%}{flag}")
        if flag:
            # 前後の場面のあいだに聞こえた声を全部見せる
            lo = res[i - 1][1] + 0.3 if i > 0 else 0.0
            hi = res[i + 1][0] - 0.3 if i + 1 < len(res) else en
            warn.append((sid, s["text"], rec_text(rec, lo, hi)))
        if old is not None:
            if e is None:
                print(f"    ！ {sid} は前の segments.json にないので書き込みません")
                continue
            e["start"], e["end"] = round(st, 2), round(en, 2)

    if old is None:
        old = [{"id": s["id"], "start": round(r[0], 2), "end": round(r[1], 2)} for s, r in zip(scenes, res)]

    if args.dry_run:
        print("\n（--dry-run なので segments.json は書き換えていません）")
    else:
        if os.path.exists(args.segments):
            stamp = time.strftime("%Y%m%d-%H%M%S")
            bak = re.sub(r"\.json$", f".{stamp}.bak.json", args.segments)
            shutil.copyfile(args.segments, bak)
            print(f"\n前の版を残しました：{bak}")
        with open(args.segments, "w", encoding="utf-8") as f:
            json.dump(old, f, ensure_ascii=False, indent=2)
        print(f"書き込みました：{args.segments}")

    for sid, text, heard in warn:
        print(f"\n★ {sid}：台本と聞き取りが大きく違います。読み方を変えたか、区切りがずれています")
        print(f"   台本　：{text}")
        print(f"   聞き取り：{heard or '（なし）'}")


# ---------------------------------------------------------------- srt モード

def split_caption(s):
    s = s.strip()
    if len(s) <= SRT_MAX:
        return [s] if s else []
    cuts = [i + 1 for i, c in enumerate(s) if c in "、，, 　" and 0 < i < len(s) - 1]
    cut = min(cuts, key=lambda i: abs(i - len(s) / 2)) if cuts else SRT_MAX
    return split_caption(s[:cut]) + split_caption(s[cut:])


def captions_of(scenes):
    caps = []
    for s in scenes:
        for sent in re.findall(r"[^。！？!?]+[。！？!?]?", s["text"].replace("\n", "")):
            for c in split_caption(sent):
                c = c.rstrip("。、 　").strip()
                if norm_text(c):
                    caps.append(c)
    return caps


def fmt(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def run_srt(args):
    caps = captions_of(load_scenes(args.scenes))
    rec = transcribe(args.video, args.model, args.device, args.compute_type, args.retranscribe)
    res = align(caps, rec)
    out = args.out or re.sub(r"(_final)?\.[^.\\/]+$", ".srt", args.video)

    lines, low = [], 0
    for i, (cap, (st, en, cover)) in enumerate(zip(caps, res)):
        nxt = res[i + 1][0] if i + 1 < len(res) else en + 5
        en = min(max(en + SRT_TAIL, st + SRT_MIN), max(nxt, st + 0.1))
        low += cover < LOW_COVER
        lines.append(f"{i + 1}\n{fmt(st)} --> {fmt(en)}\n{cap}\n")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n字幕 {len(caps)} 枚を書き出しました：{out}")
    if low:
        print(f"★ うち {low} 枚は聞き取りとの一致が少なく、時刻があやしいです。YouTube の字幕エディタで確認してください")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default="large-v3-turbo", help="Whisper のモデル（軽くするなら small / medium）")
    p.add_argument("--device", default="auto", help="auto / cpu / cuda")
    p.add_argument("--compute-type", default="int8")
    p.add_argument("--retranscribe", action="store_true", help="前回の聞き取り結果を使わずに聞き取り直す")
    sub = p.add_subparsers(dest="mode", required=True)

    a = sub.add_parser("segments", help="録音から segments.json を作る")
    a.add_argument("--scenes", default=os.path.join("narration", "scenes.json"))
    a.add_argument("--audio", required=True, help="録音ファイル（video.json の narration と同じもの）")
    a.add_argument("--segments", default=os.path.join("narration", "segments.json"))
    a.add_argument("--dry-run", action="store_true", help="結果を表示するだけで書き換えない")

    b = sub.add_parser("srt", help="完成した動画から字幕ファイルを作る")
    b.add_argument("--scenes", default=os.path.join("narration", "scenes.json"))
    b.add_argument("--video", required=True, help="out\\<name>_final.mp4")
    b.add_argument("--out", help="出力先（省略すると動画と同じ場所に .srt）")

    args = p.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    (run_segments if args.mode == "segments" else run_srt)(args)


if __name__ == "__main__":
    main()
