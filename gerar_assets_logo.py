from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)

PRIMARY = "#7B3F7A"
TEXT = "#4A2060"
BG = "#F9F0F5"
WHITE = "#FFFFFF"
SOFT = "#EDD6ED"

OUTPUTS = ["logo_app.png", "logo_round.png", "app_icon.png", "app_icon.ico", "install_splash.png"]


def find_original_logo():
    for name in ("logo_original.png", "logo_original.jpg", "logo_original.jpeg", "logo_original.webp"):
        path = ASSETS / name
        if path.exists():
            return path
    return None


def font(size, preferred="segoeui.ttf"):
    candidates = [
        Path("C:/Windows/Fonts") / preferred,
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def fit_square(image, size, background=WHITE):
    image = ImageOps.exif_transpose(image).convert("RGBA")
    canvas = Image.new("RGBA", (size, size), background)
    image.thumbnail((size, size), Image.LANCZOS)
    x = (size - image.width) // 2
    y = (size - image.height) // 2
    canvas.alpha_composite(image, (x, y))
    return canvas


def make_rounded_logo(original):
    logo_app = fit_square(original, 512, PRIMARY)
    logo_app.save(ASSETS / "logo_app.png")

    round_logo = fit_square(original, 256, (0, 0, 0, 0))
    mask = Image.new("L", (256, 256), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, 255, 255), fill=255)
    round_logo.putalpha(mask)
    round_logo.save(ASSETS / "logo_round.png")

    icon = fit_square(original, 256, PRIMARY)
    icon.save(ASSETS / "app_icon.png")
    icon.save(ASSETS / "app_icon.ico", sizes=[(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)])


def centered_text(draw, box, text, text_font, fill):
    left, top, right, bottom = box
    bbox = draw.textbbox((0, 0), text, font=text_font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    x = left + (right - left - width) / 2
    y = top + (bottom - top - height) / 2 - bbox[1]
    draw.text((x, y), text, font=text_font, fill=fill)


def make_install_splash(original=None):
    image = Image.new("RGBA", (760, 430), BG)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((30, 30, 730, 400), radius=28, fill=WHITE, outline=SOFT, width=3)
    draw.rounded_rectangle((30, 30, 730, 126), radius=28, fill=PRIMARY)
    draw.rectangle((30, 86, 730, 126), fill=PRIMARY)

    centered_text(draw, (30, 53, 730, 104), "Instalador IoMarques Brechó", font(34, "segoeuib.ttf"), WHITE)
    if original is not None:
        logo = fit_square(original, 170, (0, 0, 0, 0))
        image.alpha_composite(logo, (295, 148))
    else:
        centered_text(draw, (30, 165, 730, 230), "IoMarques Brechó", font(34, "segoeuib.ttf"), TEXT)

    centered_text(draw, (30, 318, 730, 356), "Preparando aplicativo da loja", font(24), TEXT)
    centered_text(draw, (30, 354, 730, 382), "aguarde...", font(19), PRIMARY)
    image.save(ASSETS / "install_splash.png")


def remove_generated_logo_outputs():
    for name in OUTPUTS:
        path = ASSETS / name
        if path.exists():
            path.unlink()


if __name__ == "__main__":
    original_path = find_original_logo()
    if original_path is None:
        remove_generated_logo_outputs()
        make_install_splash(None)
        print("Logo original não encontrada.")
        print("Salve a arte enviada como assets/logo_original.png e rode este script novamente.")
    else:
        original = Image.open(original_path)
        make_rounded_logo(original)
        make_install_splash(original)
        print(f"Assets criados a partir de: {original_path}")
