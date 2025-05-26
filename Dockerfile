# ベースイメージとしてPython 3.11を使用 (Zenn記事の環境に類似)
FROM python:3.11-slim

# 作業ディレクトリを設定
WORKDIR /app

# システムのアップデートとPopplerのインストール (pdf2imageに必要)
# Zenn記事のDockerfileにはないが、私たちのボットには必須
RUN apt-get update && \
    apt-get install -y --no-install-recommends poppler-utils locales && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 日本語ロケールの設定 (Zenn記事参考)
RUN localedef -f UTF-8 -i ja_JP ja_JP.UTF-8
ENV LANG ja_JP.UTF-8
ENV LANGUAGE ja_JP:ja
ENV LC_ALL ja_JP.UTF-8
ENV TZ Asia/Tokyo
ENV TERM xterm

# requirements.txt をコピーしてライブラリをインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# プロジェクトの他のファイル（main.py, keep_alive.pyなど）をコピー
COPY . .

# keep_alive.py (Flaskサーバー) が使用するポートを公開 (Koyeb設定と合わせる)
EXPOSE 8080

# ボットの実行コマンド
CMD ["python", "main.py"]