"""Stable Diffusion 画像生成ヘルパー (diffusers使用)。

GTX 1660 Ti (6GB VRAM) で動く軽量設定。

使い方:
    from core.image.sd import generate_image
    path = generate_image("a cat sitting on a chair", "output.png")
"""

import os
import sys
from pathlib import Path
from typing import Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

ROOT = Path(__file__).parent
SD_OUTPUT_DIR = ROOT / "data" / "sd_images"

# モデル設定（軽量化）
DEFAULT_MODEL = "runwayml/stable-diffusion-v1-5"
DEFAULT_WIDTH = 512
DEFAULT_HEIGHT = 512  # GTX 1660 Ti fp32 で安全に回るサイズ
DEFAULT_STEPS = 20
DEFAULT_GUIDANCE = 7.5

_pipe = None


def _load_pipeline(model: str = DEFAULT_MODEL):
    """SDパイプラインをロード（シングルトン）。"""
    global _pipe
    if _pipe is not None:
        return _pipe

    try:
        import torch
        from diffusers import StableDiffusionPipeline

        device = "cuda" if torch.cuda.is_available() else "cpu"
        # GTX 16xx 系は fp16 で NaN を出すバグがあるため fp32 を使う
        dtype = torch.float32

        print(f"SD pipeline loading: {model} on {device}")
        _pipe = StableDiffusionPipeline.from_pretrained(
            model,
            torch_dtype=dtype,
            safety_checker=None,
            requires_safety_checker=False,
        )
        _pipe = _pipe.to(device)

        # メモリ削減
        if device == "cuda":
            try:
                _pipe.enable_attention_slicing()
                _pipe.enable_vae_slicing()
            except Exception:
                pass

        print("SD pipeline loaded")
        return _pipe
    except Exception as e:
        print(f"SD pipeline load failed: {e}")
        return None


def generate_image(
    prompt: str,
    output_path: Optional[str] = None,
    negative_prompt: str = "ugly, blurry, low quality, distorted, bad anatomy, watermark, text",
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    steps: int = DEFAULT_STEPS,
    guidance: float = DEFAULT_GUIDANCE,
    seed: Optional[int] = None,
) -> Optional[str]:
    """プロンプトから画像を生成してファイルパスを返す。

    Args:
        prompt: 英語プロンプト
        output_path: 保存先パス（Noneなら自動）
        negative_prompt: ネガティブプロンプト
        width/height: 解像度
        steps: ステップ数（多いほど高品質、遅い）
        guidance: ガイダンススケール
        seed: 再現性のためのシード

    Returns:
        生成された画像のパス、失敗時 None
    """
    pipe = _load_pipeline()
    if pipe is None:
        return None

    SD_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if output_path is None:
        import hashlib
        h = hashlib.md5(prompt.encode()).hexdigest()[:10]
        output_path = str(SD_OUTPUT_DIR / f"sd_{h}.png")

    try:
        import torch
        generator = None
        if seed is not None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
            generator = torch.Generator(device=device).manual_seed(seed)

        result = pipe(
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            num_inference_steps=steps,
            guidance_scale=guidance,
            generator=generator,
        )
        image = result.images[0]
        image.save(output_path)
        print(f"SD generated: {output_path}")
        return output_path
    except Exception as e:
        print(f"SD generation failed: {e}")
        return None


def is_sd_available() -> bool:
    """SDが利用可能かチェック（torch + cuda）。"""
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


# Ollama がたまに返す変な単語/抽象語/長文を弾くためのチェック
_BANNED_TOKENS = {
    "concept", "abstract", "feeling", "idea", "thought", "process",
    "strategy", "method", "tip", "tips", "guide", "people", "person",
    "user", "team", "scene", "scenery", "background", "image",
    "illustration", "icon", "vector", "design", "style",
}


def _validate_icon_subject(s: str) -> bool:
    """Ollama 出力が「具体的な単一オブジェクト」として使えるかを検証する。"""
    if not s or not s.isascii():
        return False
    # 英字・空白・ハイフンのみ許可
    import re
    if not re.fullmatch(r"[a-z0-9 \-]+", s):
        return False
    words = s.split()
    if not (2 <= len(words) <= 10):
        return False
    # 冠詞で始まる
    if words[0] not in ("a", "an"):
        return False
    # 禁止語が含まれてない
    if any(w in _BANNED_TOKENS for w in words):
        return False
    return True


ICON_STYLE_SUFFIX = (
    ", flat vector illustration, centered single icon, white background, "
    "pastel colors, minimal, simple shapes, no text, no letters, clean, "
    "modern infographic icon style"
)


def title_to_sd_prompt(title: str, genre: str = "") -> str:
    """記事タイトルから「アイコン1個」用の英語SDプロンプトを生成する。"""
    # ジャンルベースのコア被写体（Ollama に頼らず安定再現）
    genre_core = {
        "AI活用術": "a friendly robot head with a speech bubble",
        "SNS運用ノウハウ": "a smartphone with a heart icon floating above it",
        "副業・収益化ガイド": "a small plant growing from a stack of coins",
    }
    core = None
    for key, c in genre_core.items():
        if key in (genre or ""):
            core = c
            break

    # Claude (Maxプラン CLI) で記事タイトルに合った具体物を提案させる
    try:
        from core.llm.wrapper import call_llm
        prompt = f"""日本語の記事タイトルにふさわしい「単一の具体的なモノ」を1つ、英語で答えてください。
これは Stable Diffusion でフラットアイコンを描くための被写体です。

ルール:
- 3〜8語の英語のみ
- "a" または "an" で始める
- 物理的に描ける具体物だけ（例: a laptop, a smartphone with a heart, a stack of coins, a lightbulb, a plant in a pot）
- 抽象概念・人物・風景は禁止
- 句読点・引用符・説明文なし。被写体だけ1行で出力

タイトル: {title}
ジャンル: {genre}

被写体:"""
        result = call_llm(prompt, task_type="icon_subject", temperature=0.5, max_tokens=40)
        suggested = result.strip().split("\n")[0].strip(" .,:;\"'`*-").lower()[:80]
        if _validate_icon_subject(suggested):
            core = suggested
            print(f"  Claude 提案: {core}")
        else:
            print(f"  Claude 出力却下: {suggested!r} → ジャンル固定にフォールバック")
    except Exception as e:
        print(f"  Claude 呼び出し失敗: {e}")

    if not core:
        core = "a simple lightbulb idea icon"

    return core + ICON_STYLE_SUFFIX


if __name__ == "__main__":
    print("=== SD 利用可能性チェック ===")
    print(f"SD available: {is_sd_available()}")

    if is_sd_available():
        print("\n=== テスト生成 ===")
        path = generate_image(
            prompt="modern workspace with laptop, blue gradient background, minimalist illustration",
            output_path="data/sd_images/test.png",
            steps=15,
        )
        print(f"Result: {path}")
