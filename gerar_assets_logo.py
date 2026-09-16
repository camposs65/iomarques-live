from pathlib import Path
from PIL import Image, ImageDraw, ImageOps


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)

PRIMARY = "#7B3F7A"
WHITE = "#FFFFFF"


def find_original_logo():
    for name in ("logo_original.png", "logo_original.jpg", "logo_original.jpeg", "logo_original.webp"):
        path = ASSETS / name
        if path.exists():
            return path
    return None


def fit_square(image, size, background=WHITE):
    image = ImageOps.exif_transpose(image).convert("RGBA")
    canvas = Image.new("RGBA", (size, size), background)
    image.thumbnail((size, size), Image.LANCZOS)
    x = (size - image.width) // 2
    y = (size - image.height) // 2
    canvas.alpha_composite(image, (x, y))
    return canvas


def make_rounded_logo(original):
    round_logo = fit_square(original, 256, (0, 0, 0, 0))
    mask = Image.new("L", (256, 256), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, 255, 255), fill=255)
    round_logo.putalpha(mask)
    round_logo.save(ASSETS / "logo_round.png")

    icon = fit_square(original, 256, PRIMARY)
    icon.save(ASSETS / "app_icon.png")
    icon.save(ASSETS / "app_icon.ico", sizes=[(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)])


if __name__ == "__main__":
    original_path = find_original_logo()
    if original_path is None:
        raise SystemExit("Logo original não encontrada. Restaure assets/logo_original.png.")
    with Image.open(original_path) as original:
        make_rounded_logo(original)
    print(f"Assets criados a partir de: {original_path}")
