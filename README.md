# Fastgraph

日足・週足・月足で「長い下ひげを付けて、出来高が増えた」銘柄を、日本株・米国株それぞれチャートで並べたページ。
無料のデータ（Yahoo Finance と JPX）だけで作り、GitHub Actions が平日に更新して GitHub Pages で公開している。

https://bnmmkmjdsf-commits.github.io/chart-shapes/

## 条件

取引が終わった直近の足（日足・週足・月足のどれも同じ条件）で、

- 下ひげが値幅（高値−安値）の33%以上、かつ株価の2%以上
- 出来高がその前の平均（日足は25日、週足は13週、月足は12か月）の1.5倍以上
- 陽線（終値が始値より上）
- 時価総額が日本株は300億円以上、米国株は10億ドル以上

しきい値は `screener/pullback.py` と `screener/gallery.py` の先頭にある。

## 二択

「二択」タブは、条件に合った銘柄から同じ市場の2つを並べ、良いと思う方を押して記録する。
言葉にしにくい選び方を、選んだ方と選ばなかった方の特徴（下ひげの長さ、出来高の増え方、直前の下げ、株価の位置、中期線との距離と向き、時価総額の順位）の差から数える。
「勝ち残り」（選んだ方を残して次の相手を出す）、「名前を隠す」（チャートの形だけで選ぶ）、「答え合わせ」（選んだ日の株価から今までの値動きを、選んだ方と選ばなかった方で比べる）もある。
記録はブラウザの localStorage（`duels-v1`）に入り、「記録をコピー」で JSON として取り出せる。処理は `screener/gallery_template.html` の中だけにある。

## 更新のしかた

`.github/workflows/update.yml` が動かす。

- 平日の朝7:00（日本時間、米国市場の終値のあと）に米国株、夕方16:30（東証の終値のあと）に日本株を取り直す。
  動いていない方の市場は、公開中のページのデータをそのまま使う
- 手動で動かすとき: `gh workflow run update.yml --ref main`（日米とも取り直す）
- 見た目や文言だけ変えたとき: `gh workflow run update.yml --ref main -f reuse_data=true`（株価を取り直さず、20秒ほどで終わる）
- main 以外のブランチで動かすと公開はせず、作ったページを Actions の成果物（`page`）に残す

## 手元で動かす

Windows のパス長制限を避けるため、仮想環境はプロジェクトの外（短いパス）に作る。

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m venv "$env:USERPROFILE\.venvs\us-screener"
& "$env:USERPROFILE\.venvs\us-screener\Scripts\python.exe" -m pip install -r requirements.txt
& "$env:USERPROFILE\.venvs\us-screener\Scripts\python.exe" -m screener.gallery   # → site/index.html
```

## 構成

| ファイル | 役割 |
|---|---|
| `screener/prices.py` | Yahoo のスクリーナー API から株価・時価総額（米国・日本）、yfinance でローソク足（日足・月足）。週足は日足から作る |
| `screener/jpx.py` | JPX の東証上場銘柄一覧から、日本語の銘柄名・33業種 |
| `screener/pullback.py` | 「下ひげ × 出来高増」の判定 |
| `screener/gallery.py`, `screener/gallery_template.html` | ページの書き出しと、ページの見た目 |
| `screener/published.py`, `screener/retemplate.py` | 公開中のページのデータを取り出す・それを使ってページだけ作り直す |

## 制約

- Yahoo は非公式 API なので、仕様変更や回数制限で止まることがある
- 祝日は見ていないので、休場日は前の営業日の足のままになる
- 以前あった米国株の財務スクリーナー（SEC EDGAR）とチャート形状検索（Streamlit）は、使わなくなったので消した。Git の履歴には残っている
