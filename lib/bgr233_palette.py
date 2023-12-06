# BGR233 palette

def generate_bgr233_palette():
    palette = []
    for b in range(4):
        for g in range(8):
            for r in range(4):
                red = int(r * 255 / 3)
                green = int(g * 255 / 7)
                blue = int(b * 255 / 3)
                palette.extend([red, green, blue])
    return palette


palette = generate_bgr233_palette()
