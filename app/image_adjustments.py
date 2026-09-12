"""Shared pixel adjustment policy for normal images and PDF loupe workers."""
from PIL import Image, ImageEnhance


def apply_image_adjustments(image: Image.Image, adjustments: tuple[float, float, float]) -> Image.Image:
    brightness, contrast, gamma = adjustments
    if (brightness, contrast, gamma) == (1.0, 1.0, 1.0):
        return image
    adjusted = image
    if brightness != 1.0:
        adjusted = ImageEnhance.Brightness(adjusted).enhance(brightness)
    if contrast != 1.0:
        adjusted = ImageEnhance.Contrast(adjusted).enhance(contrast)
    if gamma != 1.0:
        inverse_gamma = 1.0 / gamma
        lut = [min(255, max(0, int(((value / 255.0) ** inverse_gamma) * 255.0 + 0.5))) for value in range(256)]
        if adjusted.mode == "RGBA":
            red, green, blue, alpha = adjusted.split()
            adjusted = Image.merge(
                "RGBA",
                (
                    red.point(lut),
                    green.point(lut),
                    blue.point(lut),
                    alpha,
                ),
            )
        elif adjusted.mode == "RGB":
            adjusted = Image.merge(
                "RGB",
                tuple(channel.point(lut) for channel in adjusted.split()),
            )
        elif adjusted.mode == "L":
            adjusted = adjusted.point(lut)
        else:
            adjusted = adjusted.convert("RGBA")
    return adjusted
