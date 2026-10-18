"""web/icon.svg と同じ絵柄の PNG アイコンを作る (アイコンを変えたときだけ実行)。

  python scripts/make_icons.py
"""
import pathlib

from PIL import Image, ImageDraw

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
ORANGE = (210, 86, 26)
SCALE = 4  # 大きく描いてから縮小して輪郭をなめらかにする


def bezier(p0, p1, p2, p3, n=40):
    return [tuple((1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * b + 3 * (1 - t) * t ** 2 * c + t ** 3 * d
                  for a, b, c, d in zip(p0, p1, p2, p3)) for t in (i / n for i in range(n + 1))]


def draw(size, rounded):
    s = 512 * SCALE
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if rounded:
        d.rounded_rectangle((0, 0, s, s), radius=112 * SCALE, fill=ORANGE)
    else:  # iOS は自分で角を丸めるので四角のまま
        d.rectangle((0, 0, s, s), fill=ORANGE)
    k = lambda pts: [(x * SCALE, y * SCALE) for x, y in pts]
    # icon.svg の道のり (スタート → ゴール) と同じ曲線
    path = (bezier((120, 372), (160, 372), (172, 308), (224, 308))
            + bezier((224, 308), (276, 308), (280, 380), (328, 380))
            + bezier((328, 380), (376, 380), (368, 230), (392, 148)))
    d.line(k(path), fill="white", width=40 * SCALE, joint="curve")
    for x, y in (path[0], path[-1]):
        r = 30 * SCALE
        d.ellipse((x * SCALE - r, y * SCALE - r, x * SCALE + r, y * SCALE + r), fill="white")
    for x, y in path[1:-1:10]:  # 線の継ぎ目を丸くする
        r = 20 * SCALE
        d.ellipse((x * SCALE - r, y * SCALE - r, x * SCALE + r, y * SCALE + r), fill="white")
    return img.resize((size, size), Image.LANCZOS)


draw(180, False).convert("RGB").save(WEB / "icon-180.png")
draw(192, True).save(WEB / "icon-192.png")
draw(512, True).save(WEB / "icon-512.png")
print("wrote icon-180.png, icon-192.png, icon-512.png")
