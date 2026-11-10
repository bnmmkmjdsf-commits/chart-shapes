# 株スクリーナー

無料のデータだけで作った、米国株の財務スクリーナーと、米国・日本株のチャート形状検索。

- **米国株スクリーナー**: SEC EDGAR の決算（直近12か月）と Yahoo Finance の株価で、割安度・配当・収益性・成長性などを条件に絞り込む。直近1ヶ月のチャートの形とも組み合わせられる
- **チャート形状検索**: 直近の値動きを9つの形（上昇ストップ? / 上昇? / 急上昇? / 調整? / もみ合い? / リバウンド? / 急落? / 下落? / 下げとまった?）に分け、形ごとに銘柄を探す。米国株・日本株の両方、期間は1ヶ月〜1年

## セットアップ（初回のみ）

Windows のパス長制限を避けるため、仮想環境はプロジェクトの外（短いパス）に作る。

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m venv "$env:USERPROFILE\.venvs\us-screener"
& "$env:USERPROFILE\.venvs\us-screener\Scripts\python.exe" -m pip install -r requirements.txt
```

## 使い方

```powershell
# SEC はアクセス元の名前と連絡先を User-Agent に入れるよう求めている
$env:SEC_USER_AGENT = "Your Name you@example.com"

# データ取得。初回は SEC の全社分があるので15分前後、2回目以降は5分前後（ほとんどが株価履歴）。
# 画面の「データを更新」ボタンでも同じことができる
& "$env:USERPROFILE\.venvs\us-screener\Scripts\python.exe" -m screener.build

# 週足の「下ひげ × 出来高増」ページ（日米）を作る → site/index.html。GitHub Actions が平日に動かして公開している
& "$env:USERPROFILE\.venvs\us-screener\Scripts\python.exe" -m screener.gallery

# 画面を起動 → http://localhost:8501
& "$env:USERPROFILE\.venvs\us-screener\Scripts\python.exe" -m streamlit run app.py --server.address localhost
```

## 構成

| ファイル | 役割 |
|---|---|
| `screener/edgar.py` | SEC EDGAR の companyfacts API から各社の 10-K・10-Q の数字を取り、直近12か月（TTM）を計算 |
| `screener/prices.py` | Yahoo のスクリーナー API から株価・時価総額（米国・日本）、yfinance で直近1年の日足 |
| `screener/jpx.py` | JPX の東証上場銘柄一覧から、日本語の銘柄名・市場区分・33業種 |
| `screener/shapes.py` | チャートの形の判定（Streamlit の画面用） |
| `screener/pullback.py`, `screener/gallery.py` | 週足の「下ひげ × 出来高増」の判定と、公開ページの書き出し |
| `screener/build.py` | 全部を取得して `data/` に保存（米国株 `screener.parquet`、日本株 `jp.parquet`、日足 `history_*.parquet`） |
| `app.py`, `views/` | Streamlit の画面（`views/us_screener.py`、`views/chart_shapes.py`） |

## 財務データの仕組み（米国株）

- 損益・キャッシュフローは TTM = 前期の年間値 + 今期の累計 − 前年同期の累計。第4四半期単独の値は「年間値 − 9か月累計」で出す
- 会社ごとの数字は `data/cache/facts/{CIK}.json` に保存する（直近4年分・必要な科目だけ）
- 2回目以降は EDGAR の日次インデックスで「前回取得した後に 10-K / 10-Q などを出した会社」を調べ、その会社だけ取り直す。
  どの日まで確認したかと、各社の最新の決算書類は `data/cache/facts/_state.json` に記録している
- 取得する科目を変えると、その時刻より前に取ったデータを全社分取り直す（途中で止まっても続きから再開できる）
- companyfacts API には、提出済みの決算が載っていない会社が数%ある（2026年10月時点で V・KO・Citi など約110社）。
  その場合は提出書類に添付された XBRL インスタンスを直接読んで補う
- 四半期の累計が無い会社（上場直後など）は、3か月ごとの数字をつないで累計を作る。年次決算がまだ無い会社は四半期の指標だけ出す

## チャートの形の判定

- 期間内の株価を「途中で傾きが変わる2本の直線」で近似し（折れ点は期間の1/3〜2/3で最もよく合う位置）、
  前の線・後の線の動きを、その銘柄のふだんの値動き（過去1年の日次変動率）を物差しにして測る
- 2つの動きを平面上の矢印と見て、向き（8方向）で形を決める。前後どちらもほとんど動いていなければ「もみ合い?」。
  「急上昇?」「急落?」は後の線で大きく動いたものだけ
- しきい値は `shapes.py` の `FLAT`（もみ合いの幅）と `STRONG`（急上昇・急落の強さ）

## 制約

- 日本株は株価・チャートの形だけ（財務データは無い）
- 米国株のチャートの形は、時価総額3億ドル以上の銘柄が対象
- IFRS で 20-F を出す外国企業（TSM、ASML、トヨタなど）は財務データなし。US-GAAP の 20-F 提出企業（PDD など）は年次のみ
- 決算期を変更した会社や、合併直後の会社は TTM が計算できず、古い決算のままになることがある
- Yahoo は非公式 API なので、仕様変更や回数制限で止まることがある
- 持株会社化などで CIK が変わった会社は、`edgar.py` の `PREDECESSORS` に旧 CIK を登録して数字を合わせる
