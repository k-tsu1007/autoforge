"""一時: サムネイル生成テスト。長いタイトルで切れないか確認。"""
import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from thumbnail import generate_thumbnail

titles = [
    "Xで発信しても「いいね」がつかない人に共通する5つのミス——今日から直せる改善チェックリスト",
    "Instagramで保存数は増えるのにフォロワーが増えない人に共通する「3つの落とし穴」——気づいたら数字が変わった",
    "短いタイトル",
]

for i, t in enumerate(titles, 1):
    out = generate_thumbnail(
        title=t,
        genre="SNS運用ノウハウ",
        tags=["X", "発信力"],
        use_sd=True,
    )
    print(f"{i}. {out}")
