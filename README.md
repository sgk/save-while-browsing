[English](./README_en.md)

# Save While Browsing

ブラウザでウェブ閲覧中に表示された画像を自動的に保存するPythonスクリプトです。
SeleniumとChrome DevTools Protocol (CDP) を使用して、ネットワークトラフィックから直接画像データを取得します。

## 概要

このツールは、特別に起動されたChromeブラウザで行ったブラウジングセッション中に、指定したサイズ以上の画像を自動的に検出して保存します。

**主な特徴:**
*   **自動保存**: ブラウザを操作するだけで、読み込まれた画像が自動的に保存されます。
*   **サイズフィルタ**: 小さなアイコンやサムネイルを除外するために、最小サイズを指定できます（デフォルト: 480x320）。
*   **ファイル名正規化**: URLからファイル名を生成し、Windows (CP932) でも問題が起きにくいようにファイル名を安全に変換します。

## 環境設定

### 必須要件
*   Python 3.x
*   Google Chrome

### インストール方法

1.  リポジトリをクローンします。
    ```bash
    git clone https://github.com/sgk/save-while-browsing.git
    cd save-while-browsing
    ```

2.  依存ライブラリをインストールします。
    ```bash
    pip install -r requirements.txt
    ```

## 使い方

スクリプトを実行すると、Chromeブラウザが立ち上がります。そのブラウザで保存したい画像があるサイトを閲覧してください。

```bash
python save-while-browsing.py [保存先ディレクトリ] [--min WIDTHxHEIGHT]
```

### 引数
*   `archive_dir` (必須): 画像を保存するディレクトリのパス。存在しない場合は自動作成されます。
*   `--min` (オプション): 保存する画像の最小サイズを `幅x高さ` または数値で指定します。数値の場合は縦横いずれかがその値以上なら対象になります。デフォルトは `480x320` です。

### 実行例

画像を `my_images` フォルダに保存し、サイズが `800x600` 以上のものだけを対象とする場合:

```bash
python save-while-browsing.py my_images --min 800x600
```

終了するには、ターミナルで `Ctrl+C` を押してください。

## 仕組み

1.  **ブラウザ起動**: Seleniumを使用して、独立したプロファイルを持つChromeブラウザを起動します。
2.  **ネットワーク監視**: Chrome DevTools Protocol (CDP) の `Network` ドメインを有効にし、ブラウザが発生させる全てのネットワーク通信を監視します。
3.  **画像検出**: `Network.responseReceived` イベントで `image/` で始まるMIMEタイプのレスポンスを検知します。
4.  **データ取得**: 画像のロードが完了 (`Network.loadingFinished`) すると、`Network.getResponseBody` コマンドを使ってレスポンスボディ（画像データ）を取得します。
5.  **フィルタリングと保存**: 取得したデータをメモリ上で展開し、Pillowを使って画像サイズを確認します。指定された条件（`--min`）を満たす場合のみ、ディスクに保存します。

## 注意事項
*   このツールは `.chrome` というディレクトリをカレントディレクトリに作成し、ブラウザのプロファイルとして使用します。
