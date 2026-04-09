"""サムネイル生成スクリプト — 記事タイトルからアイキャッチ画像を自動生成。

Pillow で テキスト + グラデーション背景の画像を生成する。
API不要・トークンコストゼロ。
"""

import hashlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent
OUTPUT_DIR = ROOT / "data" / "thumbnails"

# Note推奨サイズ: 1280x670
WIDTH = 1280
HEIGHT = 670

# Note風: 淡いパステル背景 + ジャンル色アクセント
COLOR_PALETTES = {
    # (背景グラデ開始, 背景グラデ終了, アクセント色)
    "AI活用術":         [(232, 242, 255), (255, 255, 255), (41, 98, 255)],
    "SNS運用ノウハウ":   [(255, 235, 245), (255, 255, 255), (236, 64, 122)],
    "副業・収益化ガイド": [(232, 250, 240), (255, 255, 255), (0, 168, 107)],
    "default":          [(245, 240, 255), (255, 255, 255), (124, 92, 220)],
}

# フォント候補（上から優先、macOS / Windows / Linux 全対応）
FONT_CANDIDATES = [
    # macOS
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    # Windows
    "C:\\Windows\\Fonts\\YuGothB.ttc",
    "C:\\Windows\\Fonts\\meiryob.ttc",
    "C:\\Windows\\Fonts\\msgothic.ttc",
    "C:\\Windows\\Fonts\\YuGothic-Bold.ttf",
    # Linux
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    # Fallback
    "AppleGothic",
]


def _find_font(size: int) -> ImageFont.FreeTypeFont:
    """利用可能な日本語フォントを探す。"""
    for candidate in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _create_gradient(width: int, height: int, color1: tuple, color2: tuple) -> Image.Image:
    """斜めグラデーション背景を生成する。"""
    img = Image.new("RGB", (width, height))
    pixels = img.load()

    for y in range(height):
        for x in range(width):
            t = (x / width * 0.6 + y / height * 0.4)
            r = int(color1[0] + (color2[0] - color1[0]) * t)
            g = int(color1[1] + (color2[1] - color1[1]) * t)
            b = int(color1[2] + (color2[2] - color1[2]) * t)
            pixels[x, y] = (r, g, b)

    return img


def _wrap_title_fit(title: str, font, draw: ImageDraw.Draw, max_width: int) -> list[str]:
    """フォントとピクセル幅に基づいてタイトルを折り返す。"""
    # 【】の前で分割
    title = title.replace("【", "\n【")
    parts = [p.strip() for p in title.split("\n") if p.strip()]

    lines = []
    for part in parts:
        # 「！」で自然に区切る
        if "！" in part:
            segments = part.split("！", 1)
            first = segments[0] + "！"
            second = segments[1].strip()
            # firstが収まるか確認
            bbox = draw.textbbox((0, 0), first, font=font)
            if bbox[2] - bbox[0] <= max_width:
                lines.append(first)
                if second:
                    lines.extend(_split_to_fit(second, font, draw, max_width))
            else:
                # firstも長すぎるので分割
                lines.extend(_split_to_fit(part, font, draw, max_width))
        else:
            bbox = draw.textbbox((0, 0), part, font=font)
            if bbox[2] - bbox[0] <= max_width:
                lines.append(part)
            else:
                lines.extend(_split_to_fit(part, font, draw, max_width))

    return lines


def _split_to_fit(text: str, font, draw: ImageDraw.Draw, max_width: int) -> list[str]:
    """テキストをピクセル幅に収まるよう分割する。"""
    # 区切りやすい文字
    break_chars = "のをにでがはもへとや・、！」）"
    lines = []
    remaining = text

    while remaining:
        # 全体が収まるならそのまま
        bbox = draw.textbbox((0, 0), remaining, font=font)
        if bbox[2] - bbox[0] <= max_width:
            lines.append(remaining)
            break

        # 1文字ずつ増やして収まる最大長を探す
        best_break = len(remaining) // 2
        for i in range(1, len(remaining)):
            substr = remaining[:i]
            bbox = draw.textbbox((0, 0), substr, font=font)
            if bbox[2] - bbox[0] > max_width:
                best_break = i - 1
                break

        # 区切り文字で自然な位置を探す
        natural_break = best_break
        for i in range(best_break, max(best_break - 8, 0), -1):
            if remaining[i - 1] in break_chars:
                natural_break = i
                break

        lines.append(remaining[:natural_break])
        remaining = remaining[natural_break:]

    return lines


def _wrap_title(title: str) -> list[str]:
    """タイトルを適切な幅で改行する。均等な行長を目指す。"""
    # 【】の前で分割
    title = title.replace("【", "\n【")
    parts = [p.strip() for p in title.split("\n") if p.strip()]

    lines = []
    for part in parts:
        # 「！」で自然に区切る
        if "！" in part:
            segments = part.split("！", 1)
            first = segments[0] + "！"
            second = segments[1].strip()
            if second:
                lines.append(first)
                lines.append(second)
            else:
                lines.append(first)
        elif len(part) <= 20:
            lines.append(part)
        else:
            # 均等に2行に分割
            mid = len(part) // 2
            # 区切り文字の近くで分割
            best = mid
            for i in range(max(0, mid - 5), min(len(part), mid + 5)):
                if part[i] in "のをにでがはもへとや・、":
                    best = i + 1
                    break
            lines.append(part[:best])
            lines.append(part[best:])

    return lines


def _add_decorations(draw: ImageDraw.Draw, width: int, height: int):
    """装飾要素を追加する（控えめに）。"""
    pass  # シンプルなデザインのため装飾なし


def generate_thumbnail(title: str, genre: str = "", tags: list[str] = None,
                       use_sd: bool = True) -> str:
    """タイトルからサムネイル画像を生成し、パスを返す。

    Args:
        title: 記事タイトル
        genre: ジャンル
        tags: タグ一覧
        use_sd: True の場合、Stable Diffusion で背景画像を生成。失敗時はPillowにフォールバック
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ファイル名をタイトルのハッシュから生成
    title_hash = hashlib.md5(title.encode()).hexdigest()[:10]
    output_path = OUTPUT_DIR / f"thumb_{title_hash}.png"

    # 既に存在する場合はスキップ
    if output_path.exists():
        return str(output_path)

    # ジャンル → パレット選択
    palette = COLOR_PALETTES["default"]
    for key, colors in COLOR_PALETTES.items():
        if key != "default" and key in (genre or ""):
            palette = colors
            break
    if palette == COLOR_PALETTES["default"]:
        gl = (genre or "").lower()
        if "ai" in gl or "chatgpt" in gl or "活用" in gl:
            palette = COLOR_PALETTES["AI活用術"]
        elif "sns" in gl or "運用" in gl or "instagram" in gl or "x" == gl:
            palette = COLOR_PALETTES["SNS運用ノウハウ"]
        elif "副業" in gl or "収益" in gl or "稼" in gl:
            palette = COLOR_PALETTES["副業・収益化ガイド"]

    bg_start, bg_end, accent = palette[0], palette[1], palette[2]

    # SDアイコン生成（512x512の正方形）
    import os as _os
    sd_icon_path = None
    if use_sd and _os.environ.get("USE_SD", "1") != "0":
        try:
            from core.image.sd import is_sd_available, generate_image, title_to_sd_prompt
            if is_sd_available():
                prompt = title_to_sd_prompt(title, genre)
                print(f"SDプロンプト: {prompt}")
                sd_icon_path = generate_image(
                    prompt=prompt,
                    width=512,
                    height=512,
                    steps=20,
                )
        except Exception as e:
            print(f"SDアイコン生成スキップ: {e}")

    # 背景: 淡いパステルグラデ
    img = _create_gradient(WIDTH, HEIGHT, bg_start, bg_end).convert("RGBA")
    draw = ImageDraw.Draw(img, "RGBA")

    # 左端アクセントバー（ジャンル色）
    draw.rectangle([(0, 0), (16, HEIGHT)], fill=accent)

    # 右側にSDアイコンを配置（白背景を活かしたまま貼る）
    icon_area_w = 480
    icon_area_h = 480
    icon_x = WIDTH - icon_area_w - 60
    icon_y = (HEIGHT - icon_area_h) // 2

    if sd_icon_path and Path(sd_icon_path).exists():
        try:
            icon = Image.open(sd_icon_path).convert("RGBA")
            icon = icon.resize((icon_area_w, icon_area_h), Image.LANCZOS)
            # 白背景を半透明にして馴染ませる（softmask）
            from PIL import ImageFilter
            mask = Image.new("L", icon.size, 255)
            # 端をフェード
            fade = 60
            for i in range(fade):
                a = int(255 * (i / fade))
                ImageDraw.Draw(mask).rectangle(
                    [(i, i), (icon.size[0] - i - 1, icon.size[1] - i - 1)],
                    outline=a,
                )
            mask = mask.filter(ImageFilter.GaussianBlur(8))
            img.paste(icon, (icon_x, icon_y), mask)
        except Exception as e:
            print(f"アイコン合成失敗: {e}")

    # タイトル: 左60%エリアに大きく黒文字
    text_left = 60
    text_right = icon_x - 30
    max_text_width = text_right - text_left

    # フォントサイズ自動調整 — 全行が縦に収まるまで縮小
    font = None
    lines = []
    max_line_count = 6
    available_height = HEIGHT - 160  # タグ・余白を引いた使用可能高さ
    for font_size in [72, 64, 58, 52, 46, 40, 36, 32, 28]:
        font = _find_font(font_size)
        lines = _wrap_title_fit(title, font, draw, max_text_width)
        line_height = font_size + 12
        total_h = len(lines) * line_height
        if len(lines) <= max_line_count and total_h <= available_height:
            break

    line_height = font_size + 12
    total_h = len(lines) * line_height
    start_y = (HEIGHT - total_h) // 2

    for i, line in enumerate(lines):
        y = start_y + i * line_height
        # 軽い影で立体感
        draw.text((text_left + 1, y + 1), line, fill=(0, 0, 0, 40), font=font)
        draw.text((text_left, y), line, fill=(30, 30, 40), font=font)

    # ジャンルタグ（左上に小さく）
    if genre:
        tag_font = _find_font(22)
        tag_text = f"  {genre}  "
        tbox = draw.textbbox((0, 0), tag_text, font=tag_font)
        tw, th = tbox[2] - tbox[0], tbox[3] - tbox[1]
        tag_x, tag_y = text_left, 50
        draw.rounded_rectangle(
            [(tag_x, tag_y), (tag_x + tw + 20, tag_y + th + 16)],
            radius=20, fill=accent,
        )
        draw.text((tag_x + 10, tag_y + 6), tag_text, fill=(255, 255, 255), font=tag_font)

    img = img.convert("RGB")
    img.save(str(output_path), "PNG", quality=95)
    print(f"サムネイル生成: {output_path}")
    return str(output_path)


if __name__ == "__main__":
    # テスト生成
    path = generate_thumbnail(
        "ChatGPTで週報・日報を3分で完成！上司に刺さる報告書をコピペで作るプロンプト術",
        genre="AI活用術",
        tags=["AI", "ChatGPT", "業務効率化"],
    )
    print(f"生成完了: {path}")
