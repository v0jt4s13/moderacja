import urllib.parse

def generate_google_fonts_section(fonts):
    # 1. Generowanie linka do Google Fonts
    families = [urllib.parse.quote_plus(f) for f in fonts]
    font_url = "https://fonts.googleapis.com/css2?" + "&".join([f"family={f}" for f in families]) + "&display=swap"
    link_tag = f'<link href="{font_url}" rel="stylesheet">'

    # 2. Generowanie bloków HTML
    html_blocks = []
    for font in fonts:
        css_font = font.replace(" ", "+")  # Google font format
        family_type = "cursive" if "Script" in font or font in ["Lobster", "Pacifico"] else \
                      "monospace" if "Code" in font or "Inconsolata" in font else \
                      "serif" if "Merriweather" in font or "Playfair" in font else \
                      "sans-serif"

        block = f"""<div class="font-preview" style="font-family: '{font}', {family_type};">
  <div class="font-name">{font}</div>
  <div class="font-text"></div>
</div>"""
        html_blocks.append(block)

    return link_tag, "\n".join(html_blocks)