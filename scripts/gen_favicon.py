"""Generate minimal DevFlow CI favicon."""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "static" / "favicon.ico"


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


BG = hex_rgb("#161b22")
ACCENT = hex_rgb("#58a6ff")


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = max(1, size // 8)
    r = max(2, size // 5)
    d.rounded_rectangle([m, m, size - m - 1, size - m - 1], radius=r, fill=BG)

    cy = size // 2
    w = max(1, size // 12)
    gap = max(1, size // 16)
    chev_w = max(3, size // 5)
    chev_h = max(3, size // 4)
    cx1 = size // 2 - gap - chev_w // 3
    cx2 = size // 2 + gap + chev_w // 3

    for cx in (cx1, cx2):
        pts = [
            (cx - chev_w // 2, cy - chev_h // 2),
            (cx + chev_w // 3, cy),
            (cx - chev_w // 2, cy + chev_h // 2),
        ]
        d.line([pts[0], pts[1]], fill=ACCENT, width=w)
        d.line([pts[1], pts[2]], fill=ACCENT, width=w)

    return img


def main() -> None:
    sizes = [16, 32, 48, 64, 128, 256]
    imgs = [draw_icon(s) for s in sizes]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    imgs[0].save(OUT, format="ICO", sizes=[(s, s) for s in sizes])
    print(f"Wrote {OUT} ({sizes})")


if __name__ == "__main__":
    main()
