"""장면별 이미지(타이틀 카드) 생성.

Pillow로 그라데이션 배경 + 헤드라인 + 나레이션 자막을 그려
장면 한 장을 PNG로 저장한다. 외부 이미지 API 없이 동작한다.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import VideoConfig
from .script_gen import Scene


def _gradient(size: tuple[int, int], top, bottom) -> Image.Image:
    """세로 그라데이션 배경."""
    w, h = size
    base = Image.new("RGB", (1, h))
    for y in range(h):
        r = top[0] + (bottom[0] - top[0]) * y // h
        g = top[1] + (bottom[1] - top[1]) * y // h
        b = top[2] + (bottom[2] - top[2]) * y // h
        base.putpixel((0, y), (r, g, b))
    return base.resize(size)


def _fit_font(font_path: str, text: str, max_width: int, start: int, min_size: int = 28) -> ImageFont.FreeTypeFont:
    """한 줄이 max_width에 들어가도록 폰트 크기를 줄여가며 맞춘다."""
    size = start
    while size > min_size:
        font = ImageFont.truetype(font_path, size)
        if font.getbbox(text)[2] <= max_width:
            return font
        size -= 2
    return ImageFont.truetype(font_path, min_size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """글자 단위/공백 단위로 max_width에 맞춰 줄바꿈."""
    words = text.split(" ")
    lines: list[str] = []
    cur = ""
    for word in words:
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            # 단어 자체가 너무 길면 글자 단위로 쪼갠다
            if draw.textlength(word, font=font) > max_width:
                piece = ""
                for ch in word:
                    if draw.textlength(piece + ch, font=font) <= max_width:
                        piece += ch
                    else:
                        lines.append(piece)
                        piece = ch
                cur = piece
            else:
                cur = word
    if cur:
        lines.append(cur)
    return lines


def render_scene_image(scene: Scene, cfg: VideoConfig, index: int, total: int, out_path: Path) -> None:
    """장면 한 장을 PNG로 렌더링."""
    w, h = cfg.size
    img = _gradient((w, h), cfg.bg_top, cfg.bg_bottom)
    draw = ImageDraw.Draw(img)
    margin = int(w * 0.08)
    content_w = w - 2 * margin

    # 상단 진행 표시 점
    dot_r = 7
    gap = 26
    total_w = total * (dot_r * 2) + (total - 1) * (gap - dot_r * 2)
    x0 = (w - total_w) // 2
    cy = int(h * 0.09)
    for i in range(total):
        cx = x0 + i * gap
        color = cfg.accent if i == index else (255, 255, 255, 80)
        draw.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r],
                     fill=color if i == index else (110, 110, 130))

    # 헤드라인 (화면 중앙 위쪽)
    head_font = _fit_font(cfg.title_font, scene.headline, content_w, int(w * 0.13))
    head_lines = _wrap(draw, scene.headline, head_font, content_w)
    line_h = head_font.getbbox("가")[3] + 18
    block_h = line_h * len(head_lines)
    y = int(h * 0.34) - block_h // 2

    # 액센트 바
    bar_w = int(w * 0.14)
    draw.rounded_rectangle([margin, y - 40, margin + bar_w, y - 28],
                           radius=6, fill=cfg.accent)

    for ln in head_lines:
        tw = draw.textlength(ln, font=head_font)
        _draw_text_shadow(draw, ((w - tw) / 2, y), ln, head_font, cfg.text_color)
        y += line_h

    # 나레이션 자막 (하단)
    body_font = ImageFont.truetype(cfg.body_font, int(w * 0.045))
    body_lines = _wrap(draw, scene.narration, body_font, content_w)
    bl_h = body_font.getbbox("가")[3] + 14
    total_bh = bl_h * len(body_lines)
    by = int(h * 0.80) - total_bh // 2

    # 자막 배경 반투명 박스
    pad = 24
    box_top = by - pad
    box_bottom = by + total_bh + pad
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle([margin - pad, box_top, w - margin + pad, box_bottom],
                         radius=20, fill=(0, 0, 0, 110))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    for ln in body_lines:
        tw = draw.textlength(ln, font=body_font)
        draw.text(((w - tw) / 2, by), ln, font=body_font, fill=cfg.sub_color)
        by += bl_h

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)


def _draw_text_shadow(draw, xy, text, font, color) -> None:
    """살짝 그림자를 준 텍스트."""
    x, y = xy
    draw.text((x + 3, y + 3), text, font=font, fill=(0, 0, 0))
    draw.text((x, y), text, font=font, fill=color)
