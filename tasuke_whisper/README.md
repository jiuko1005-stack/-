# たすけチャンネル：Whisper で声の区切りと字幕を作る

「親のお風呂」の作り方（引継ぎ資料）の、次の2つを楽にするためのスクリプトです。

- **声の区切り**：`-Step align` のあと、ずれた場面を `probe.ps1` で探して `segments.json` を手で直していた作業
- **字幕**：YouTube にアップする字幕ファイル（.srt）を、台本どおりの文字で作る

Whisper の聞き取りを台本（`narration\scenes.json`）と突き合わせて、**時刻だけ**をもらいます。聞き間違い（「浴槽」→「浴そう」など）があっても、字幕の文字は台本のまま出ます。

## はじめに1回だけ

1. Python を入れる（PowerShell で）
   ```powershell
   winget install Python.Python.3.12
   ```
2. PowerShell を開き直して、faster-whisper を入れる
   ```powershell
   py -m pip install faster-whisper
   ```
3. `whisper_align.py` を作業フォルダ（`oyano_ofuro` をコピーしたフォルダ）に置く

初めて動かすときに、Whisper のモデル（約1.5GB）が自動でダウンロードされます。

## 声の区切りを作る（録音のあと）

いつもどおり `-Step align` を1回動かして `segments.json` を作ってから、次を実行します。

```powershell
py whisper_align.py segments --audio <録音ファイル>
```

- `segments.json` の **start / end だけ** を書き換えます。ほかの項目と形はそのまま残ります
- 前の版は `segments.日時.bak.json` に残ります
- 書き換えずに結果だけ見たいときは、最後に `--dry-run` を付けます

場面ごとに次の表が出ます。

```
場面        種類     開始    終了  前との差  一致
hook        slide    4.22    9.08     +1.22   96%
c1a         cutA    13.52   13.70     +3.24    7%  ★要確認
```

- **一致**：台本の文字のうち、聞き取りと一致した割合です。60%未満は「★要確認」になります
- ★の場面には、台本と、その前後で実際に聞こえた声が並べて表示されます。録音で読み方や順番を変えたときは、引継ぎ資料のルールどおり台本（`scenes.json`）の方を直して、もう一度実行します
- 2回目からは前回の聞き取り結果を使うので、すぐ終わります。録音を録り直したときは `--retranscribe` を付けます

あとはいつもどおり `-Step` なしで書き出します。

## 字幕を作る（書き出しのあと）

```powershell
py whisper_align.py srt --video out\<name>_final.mp4
```

- `out\<name>.srt` ができます。YouTube Studio の「字幕」→「ファイルをアップロード」→「タイミングあり」で上げます
- 完成品の動画から聞き取るので、OP やアニメが入っても時刻がずれません（BGM が入っていても Whisper は聞き取れます）
- 字幕は1枚28文字までで、長い文は「、」で分けます。文末の「。」は付けません
- 「★ うち○枚は時刻があやしい」と出たら、YouTube の字幕エディタでその部分を見てください

## うまくいかないとき

| 起きたこと | 対処 |
|---|---|
| 聞き取りが遅い | `--model medium` か `--model small` を付ける（軽いほど精度は下がります） |
| 「前の segments.json の形が読めなかった」と出る | 先に `-Step align` を動かして `segments.json` を作ってから実行する |
| ★要確認がたくさん出る | `scenes.json` の台本と、実際の録音の中身が合っているか確かめる |
