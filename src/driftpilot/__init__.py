"""DriftPilot。"""

# 版を書く所はここだけ。pyproject.toml はここから読む(ADR-0188 決定1。dynamic = ["version"])。
# setuptools がコードを動かさずに読めるよう、文字をそのまま書いた 1 行にしておく。
# 配布物(固めた実行ファイル)では importlib.metadata で版を取れないことがあるため、画面と CLI もここを読む
__version__ = "0.2.0"
