#!/usr/bin/env python3
"""
agentic_image.py — render the 6022 identity card (port of agent-node's image
generators) and pin it to IPFS. Card content is read from the chain, never typed.

    generate  default.svg (animated card) + icon.png. Read-only.
    build     generate + pin; unminted: CIDs written into identity.json,
              minted + --apply: addOrUpdateAgentImage (exit 3 = underfunded).

    --rpc-url / --base-image / [--config, unminted only] / [--agent-address, read-only] / [--out-dir]
    Pinning: PINATA_JWT, or IPFS_API_URL (+ IPFS_API_TOKEN). Exit 0 ok, 1 error.
"""

import argparse
import base64
import io
import json
import math
import os
import random
import sys
import time
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.stderr.write(
        "Missing dependencies. Run: pip install -r "
        + str(Path(__file__).with_name("requirements.txt"))
        + "\n"
    )
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agentic_identity import (  # noqa: E402
    PROPOSAL_SCAN_CAP,
    Web3,
    build_mint_addresses,
    extract_cid,
    find_minted_anywhere,
    connect,
    emit,
    fail,
    fail_context,
    fee_fields,
    funding_payload,
    gas_for_broadcast,
    gas_price_estimate,
    load_abi,
    load_config,
    load_deployments,
    load_wallet,
    resolve_chain,
    resolve_label,
    run_command,
    send_tx,
)

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets"
FONT_PATH = ASSETS / "Go-Mono-Bold.ttf"
MARK_PATH = ASSETS / "nft_mark.png"

ENS_BASE_DOMAIN = "6022.eth"
FONT_FAMILY = "Courier, Liberation Mono, monospace"

WIDTH, HEIGHT = 512, 768
BORDER = 20
INNER_W, INNER_H = WIDTH - 2 * BORDER, HEIGHT - 2 * BORDER
OUTER_RADIUS, INNER_RADIUS = 20, 16

# "6022 epoch": unix time shifted, as on agent-node cards.
CREATED_OFFSET = 6102950400

MAX_STARS = 8
TOKEN_6022_DECIMALS = 18


# --------------------------------------------------------------------------- #
# Shared helpers (AbstractImageGenerator in Go).
# --------------------------------------------------------------------------- #
def border_gradient_colors(creator):
    """3..10 colors seeded by the creator; same shape as Go, different PRNG (card/icon parity only)."""
    seed = sum(ord(ch) for ch in creator)
    rng = random.Random(seed)
    n_colors = rng.randint(3, 10)
    return ["#%06X" % rng.randint(0, 0xFFFFFF) for _ in range(n_colors)]


def estimate_str_len(value, font_size):
    # Go len() = UTF-8 bytes.
    return int(len(value.encode("utf-8")) * font_size * 0.6)


def xml_escape(s):
    """Same set as Go's html.EscapeString."""
    return (
        s.replace("&", "&amp;")
        .replace("'", "&#39;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&#34;")
    )


def data_uri(raw, mime):
    return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))


# --------------------------------------------------------------------------- #
# default — animated SVG card (DefaultImageGenerator in Go).
# --------------------------------------------------------------------------- #
class SvgCard:
    def __init__(self, params):
        self.p = params

    def render(self):
        p = self.p
        out = []
        out.append(
            '<svg width="512" height="768" viewBox="0 0 512 768" '
            'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        )
        out.append("<defs>")
        out.append('<clipPath id="clip-border"><rect width="512" height="768" rx="20" ry="20" /></clipPath>')
        out.append('<clipPath id="clip-image">'
                   '<rect x="20" y="20" width="472" height="728" rx="16" ry="16" /></clipPath>')
        out.append('<clipPath id="wallet-carousel-clip"><rect x="7" y="750" width="498" height="24"/></clipPath>')
        out.append('<linearGradient id="fadeOverlay" x1="0" y1="0" x2="1" y2="1">')
        out.append('<stop offset="0%" stop-color="black" stop-opacity="1"/>')
        out.append('<stop offset="10%" stop-color="black" stop-opacity="0"/>')
        out.append('<stop offset="90%" stop-color="black" stop-opacity="0"/>')
        out.append('<stop offset="100%" stop-color="black" stop-opacity="1"/>')
        out.append("</linearGradient>")
        out.append(self.border_gradient())
        out.append('<path id="walletCarouselPath" d="M 0,762 H 512" />')
        out.append("</defs>")

        out.append('<g clip-path="url(#clip-border)">')
        out.append('<rect width="512" height="768" fill="url(#borderGradient)" />')
        out.append('<g clip-path="url(#clip-image)">')
        out.append(
            '<image width="472" height="728" x="20" y="20" xlink:href="%s" '
            'preserveAspectRatio="xMidYMid slice" />' % data_uri(p["base_image"], p["base_mime"])
        )
        out.append('<rect x="20" y="20" width="472" height="728" fill="url(#fadeOverlay)" />')
        out.append("</g></g>")

        out.append("<g>")
        out.append(self.name_and_ens())
        out.append(self.agent_information())
        out.append(self.stars())
        out.append(self.carousel(p["wallets"], 10, 14, 512, "wallet-carousel-clip", "walletCarouselPath", False))
        out.append(self.mark())
        out.append("</g></svg>")
        return "".join(out)

    def border_gradient(self):
        colors = border_gradient_colors(self.p["creator"])
        stops = "".join(
            '<stop offset="%d%%" stop-color="%s"/>' % ((i * 100) // (len(colors) - 1), c)
            for i, c in enumerate(colors)
        )
        return '<linearGradient id="borderGradient" x1="0" y1="1" x2="1" y2="1">%s</linearGradient>' % stops

    def name_and_ens(self):
        name, ens = self.p["name"], self.p["ens_domain"]
        max_width, name_fs, ens_fs, padding, bg_h = 440, 48.0, 16.0, 12, 96

        bg_w = max_width
        need_name_carousel = True
        name_len = estimate_str_len(name, name_fs)
        if name_len < max_width:
            need_name_carousel = False
            bg_w = name_len

        need_ens_carousel = True
        ens_len = estimate_str_len(ens, ens_fs)
        if ens_len < max_width:
            need_ens_carousel = False
            bg_w = max(bg_w, ens_len)

        bg_w += 2 * padding
        x, y = (512 - bg_w) // 2, 40

        out = ['<g transform="translate(%d,%d)">' % (x, y)]
        out.append('<rect width="%d" height="%d" rx="12" ry="12" fill="rgba(0,0,0,0.7)" />' % (bg_w, bg_h))
        out.append('<svg width="%d" height="%d">' % (bg_w, bg_h))
        out.append("<defs>")
        if need_name_carousel:
            name_y = 48
            out.append(
                '<clipPath id="name-carousel-clip"><rect x="%d" y="%d" width="%d" height="%f"/></clipPath>'
                % (padding, name_y - int(name_fs * 0.75), bg_w - 2 * padding, name_fs + 8)
            )
            out.append('<path id="nameCarouselPath" d="M %d,%d H %d" />' % (padding, name_y, bg_w - padding))
        if need_ens_carousel:
            ens_y = 80
            out.append(
                '<clipPath id="ens-carousel-clip"><rect x="%d" y="%d" width="%d" height="%f"/></clipPath>'
                % (padding, ens_y - int(ens_fs * 0.75), bg_w - 2 * padding, ens_fs + 6)
            )
            out.append('<path id="ensCarouselPath" d="M %d,%d H %d" />' % (padding, ens_y, bg_w - padding))
        out.append("</defs>")

        if need_name_carousel:
            out.append(self.carousel([name], 20, name_fs, bg_w - 2 * padding,
                                     "name-carousel-clip", "nameCarouselPath", False))
        else:
            out.append(self.centered_text(bg_w // 2, 48, int(name_fs), name))

        if need_ens_carousel:
            out.append(self.carousel([ens], 20, ens_fs, bg_w, "ens-carousel-clip", "ensCarouselPath", True))
        else:
            out.append(self.centered_text(bg_w // 2, 80, int(ens_fs), ens))

        out.append("</svg></g>")
        return "".join(out)

    def centered_text(self, x, y, font_size, text):
        return (
            '<text x="%d" y="%d" text-anchor="middle" fill="white" font-size="%d" font-family="%s">%s</text>'
            % (x, y, font_size, FONT_FAMILY, xml_escape(text))
        )

    def carousel(self, values, duration_by_value, font_size, container_len, clip_id, path_id, right_to_left):
        if not values:
            return ""
        out = ['<g clip-path="url(#%s)">' % clip_id]
        out.append('<text fill="white" font-family="%s" font-size="%.0f">' % (FONT_FAMILY, font_size))

        first_len = estimate_str_len(values[0], font_size)
        spacing = first_len + container_len
        total = first_len + spacing * (len(values) - 1) + container_len
        duration = duration_by_value * len(values)

        for i, value in enumerate(values):
            if right_to_left:
                start, end = container_len + spacing * i, -first_len
            else:
                start = -(first_len + spacing * i)
                end = total + start
            out.append(
                '<textPath xlink:href="#%s" startOffset="%d">%s'
                '<animate attributeName="startOffset" from="%d" to="%d" dur="%ds" repeatCount="indefinite" />'
                "</textPath>" % (path_id, start, xml_escape(value), start, end, duration)
            )
        out.append("</text></g>")
        return "".join(out)

    def agent_information(self):
        p = self.p
        created = str(p["created_at"] + CREATED_OFFSET)
        fs, padding = 13.0, 24
        out = []
        if p.get("origin_ens_domain") is not None:
            origin = "%s (%d)" % (p["origin_ens_domain"], p.get("origin_generation") or 0)
            w = max(
                estimate_str_len("Origin: %s" % origin, fs) + padding,
                estimate_str_len(p["origin_ens_domain"], fs) + padding,
            )
            out.append(self.multi_line_badge(50, 522, "Origin", origin, p["origin_ens_domain"], w))
        out.append(self.badge(48, 580, "Role", p["role"], estimate_str_len("Role: %s" % p["role"], fs) + padding))
        out.append(self.badge(48, 620, "Created", created, estimate_str_len("Created: %s" % created, fs) + padding))
        out.append(self.badge(48, 660, "Creator", p["creator"],
                              estimate_str_len("Creator: %s" % p["creator"], fs) + padding))
        return "".join(out)

    def badge(self, x, y, label, value, width):
        return (
            '<g transform="translate(%d,%d)">'
            '<rect width="%d" height="32" rx="8" ry="8" fill="rgba(0,0,0,0.6)" />'
            '<text x="12" y="20" fill="white" font-family="%s" font-size="13">%s</text>'
            "</g>" % (x, y, width, FONT_FAMILY, xml_escape("%s: %s" % (label, value)))
        )

    def multi_line_badge(self, x, y, label, value, subtext, width):
        return (
            '<g transform="translate(%d,%d)">'
            '<rect width="%d" height="48" rx="8" ry="8" fill="rgba(0,0,0,0.6)" />'
            '<text x="12" y="18" fill="white" font-family="%s" font-size="13">%s</text>'
            '<text x="12" y="35" fill="white" font-family="%s" font-size="12">%s</text>'
            "</g>" % (x, y, width, FONT_FAMILY, xml_escape("%s: %s" % (label, value)), FONT_FAMILY, xml_escape(subtext))
        )

    def stars(self):
        n = min(self.p.get("stars") or 0, MAX_STARS)
        if n == 0:
            return ""
        font_size, last_badge_bottom, available = 64, 660 + 32, 728
        y = ((last_badge_bottom + available) // 2) + (font_size // 2) - 4
        return self.centered_text(256, y, font_size, "★" * n)

    def mark(self):
        cx, cy, r, size = 460, 56, 22, 40
        uri = data_uri(MARK_PATH.read_bytes(), "image/png")
        return (
            "<g>"
            '<clipPath id="agentMarkClip"><circle cx="%d" cy="%d" r="%d" /></clipPath>'
            '<circle cx="%d" cy="%d" r="%d" fill="white" stroke="black" stroke-width="2" />'
            '<image x="%d" y="%d" width="%d" height="%d" xlink:href="%s" clip-path="url(#agentMarkClip)" />'
            "</g>" % (cx, cy, size // 2, cx, cy, r, cx - size // 2, cy - size // 2, size, size, uri)
        )


# --------------------------------------------------------------------------- #
# icon — static PNG (IconImageGenerator in Go).
# --------------------------------------------------------------------------- #
class IconCanvas:
    def __init__(self, params):
        self.p = params
        self.img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        # BASIC layout = whole-pixel advances, like Go's hinted face.
        self.face12, self.face13, self.face14 = (self.face(n) for n in (12, 13, 14))

    def face(self, size):
        return ImageFont.truetype(str(FONT_PATH), size, layout_engine=ImageFont.Layout.BASIC)

    def render(self):
        p = self.p
        self.draw_border_gradient(p["creator"])
        if p["base_image"]:
            self.draw_base_image(p["base_image"])
        self.draw_fade_overlay()
        self.draw_agent_information()
        self.draw_wallet(p["wallets"])
        self.draw_mark()
        buf = io.BytesIO()
        self.img.save(buf, format="PNG")
        return buf.getvalue()

    # -- layers ------------------------------------------------------------- #
    def draw_border_gradient(self, creator):
        colors = [self.parse_hex(c) for c in border_gradient_colors(creator)]
        stops = [(i / (len(colors) - 1), c) for i, c in enumerate(colors)]
        grad = self.linear_gradient(0, 0, WIDTH, 0, stops)
        self.img.alpha_composite(self.masked(grad, self.rounded_rect_mask(0, 0, WIDTH, HEIGHT, OUTER_RADIUS)))

    def draw_base_image(self, raw):
        covered = self.cover_resize(self.decode(raw), INNER_W, INNER_H)
        layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        layer.paste(covered, (BORDER, BORDER))
        self.img.alpha_composite(self.masked(layer, self.inner_mask()))

    def draw_fade_overlay(self):
        grad = self.linear_gradient(
            BORDER, BORDER, BORDER + INNER_W, BORDER + INNER_H,
            [(0.0, (0, 0, 0, 255)), (0.10, (0, 0, 0, 0)), (0.90, (0, 0, 0, 0)), (1.0, (0, 0, 0, 255))],
        )
        self.img.alpha_composite(self.masked(grad, self.inner_mask()))

    def draw_agent_information(self):
        p = self.p
        if p.get("origin_ens_domain") is not None:
            origin = "%s (%d)" % (p["origin_ens_domain"], p.get("origin_generation") or 0)
            self.draw_multi_line_badge(50, 522, "Origin: %s" % origin, p["origin_ens_domain"])
        created = str(p["created_at"] + CREATED_OFFSET)
        self.draw_badge(48, 580, "Role: %s" % p["role"])
        self.draw_badge(48, 620, "Created: %s" % created)
        self.draw_badge(48, 660, "Creator: %s" % p["creator"])

    def draw_badge(self, x, y, text):
        width = self.measure(self.face13, text) + 24
        self.fill_rounded_rect(x, y, width, 32, 8, (0, 0, 0, 153))
        self.draw_text(self.face13, text, x + 12, y + 20)

    def draw_multi_line_badge(self, x, y, main, subtext):
        width = max(self.measure(self.face13, main), self.measure(self.face12, subtext)) + 24
        self.fill_rounded_rect(x, y, width, 48, 8, (0, 0, 0, 153))
        self.draw_text(self.face13, main, x + 12, y + 18)
        self.draw_text(self.face12, subtext, x + 12, y + 35)

    def draw_wallet(self, wallets):
        if not wallets:
            return
        width = self.measure(self.face14, wallets[0])
        self.draw_text(self.face14, wallets[0], 256 - width / 2, 762)

    def draw_mark(self):
        cx, cy, size = 460, 56, 40
        self.fill_circle(cx, cy, 23, (0, 0, 0, 255))
        self.fill_circle(cx, cy, 21, (255, 255, 255, 255))
        covered = self.cover_resize(Image.open(MARK_PATH).convert("RGBA"), size, size)
        layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        layer.paste(covered, (cx - size // 2, cy - size // 2))
        self.img.alpha_composite(self.masked(layer, self.circle_mask(cx, cy, size / 2)))

    def decode(self, raw):
        try:
            return self.decode_image(Image.open(io.BytesIO(raw)))
        except (OSError, ValueError) as e:
            fail("cannot decode base image: %s" % e)

    def decode_image(self, src):
        if src.format == "GIF" and src.tile:
            src = src.crop(src.tile[0][1])  # Go decodes the first frame's rect only
        key = src.info.get("transparency")
        # Sub-8-bit gray: Pillow scales samples to 0..255 but leaves the tRNS key raw.
        if src.format == "PNG" and isinstance(key, int) and src.mode in ("1", "L"):
            depth = {"1": 1, "L;2": 2, "L;4": 4}.get(getattr(src, "png", None) and src.png.im_rawmode)
            if depth:
                src.info["transparency"] = key * 255 // ((1 << depth) - 1)
        # 16-bit gray: Go takes the high byte and honors the tRNS key.
        if src.mode in ("I", "I;16", "I;16B", "I;16L"):
            alpha = None
            if isinstance(key, int):
                # point() on "I" images is linear-only.
                alpha = Image.new("L", src.size)
                alpha.putdata([0 if v == key else 255 for v in src.convert("I").getdata()])
            out = src.point(lambda v: v * (1 / 256)).convert("L").convert("RGBA")
            if alpha is not None:
                out.putalpha(alpha)
            return out
        if isinstance(key, tuple) and max(key) > 255:
            src.info["transparency"] = tuple(v >> 8 for v in key)
        return src.convert("RGBA")

    def cover_resize(self, img, w, h):
        """Go coverResize: max-ratio scale (ceil), integer center crop."""
        iw, ih = img.size
        if iw == 0 or ih == 0:
            return Image.new("RGBA", (w, h), (0, 0, 0, 0))
        scale = max(w / iw, h / ih)
        sw, sh = math.ceil(iw * scale), math.ceil(ih * scale)
        scaled = img.resize((sw, sh), Image.BICUBIC)  # == Go CatmullRom
        ox, oy = (sw - w) // 2, (sh - h) // 2
        return scaled.crop((ox, oy, ox + w, oy + h))

    # -- primitives --------------------------------------------------------- #
    def fill_rounded_rect(self, x, y, w, h, r, color):
        layer = Image.new("RGBA", (WIDTH, HEIGHT), color)
        self.img.alpha_composite(self.masked(layer, self.rounded_rect_mask(x, y, w, h, r)))

    def fill_circle(self, cx, cy, r, color):
        layer = Image.new("RGBA", (WIDTH, HEIGHT), color)
        self.img.alpha_composite(self.masked(layer, self.circle_mask(cx, cy, r)))

    def draw_text(self, face, text, x, y):
        # "ls" = left/baseline, like Go's Dot.
        ImageDraw.Draw(self.img).text((x, y), text, font=face, fill=(255, 255, 255, 255), anchor="ls")

    def measure(self, face, text):
        return face.getlength(text)

    def linear_gradient(self, x0, y0, x1, y1, stops):
        """Multi-stop gradient along (x0,y0)->(x1,y1), full canvas."""
        dx, dy = x1 - x0, y1 - y0
        length = dx * dx + dy * dy or 1
        img = Image.new("RGBA", (WIDTH, HEIGHT))
        px = img.load()
        for y in range(HEIGHT):
            for x in range(WIDTH):
                t = ((x - x0) * dx + (y - y0) * dy) / length
                px[x, y] = self.gradient_color_at(stops, t)
        return img

    def gradient_color_at(self, stops, t):
        if t <= stops[0][0]:
            return stops[0][1]
        if t >= stops[-1][0]:
            return stops[-1][1]
        for i in range(1, len(stops)):
            if t <= stops[i][0]:
                span = stops[i][0] - stops[i - 1][0]
                local = (t - stops[i - 1][0]) / span if span > 0 else 0.0
                return self.lerp(stops[i - 1][1], stops[i][1], local)
        return stops[-1][1]

    def lerp(self, a, b, t):
        # floor(x + 0.5) == Go math.Round for non-negative values.
        return tuple(int(math.floor(a[i] + (b[i] - a[i]) * t + 0.5)) for i in range(4))

    def inner_mask(self):
        return self.rounded_rect_mask(BORDER, BORDER, INNER_W, INNER_H, INNER_RADIUS)

    def rounded_rect_mask(self, x, y, w, h, r):
        # 4x supersample + box filter ≈ Go's vector rasterizer coverage.
        s = 4
        mask = Image.new("L", (WIDTH * s, HEIGHT * s), 0)
        ImageDraw.Draw(mask).rounded_rectangle([x * s, y * s, (x + w) * s - 1, (y + h) * s - 1], radius=r * s, fill=255)
        return mask.resize((WIDTH, HEIGHT), Image.BOX)

    def circle_mask(self, cx, cy, r):
        s = 4
        mask = Image.new("L", (WIDTH * s, HEIGHT * s), 0)
        ImageDraw.Draw(mask).ellipse([(cx - r) * s, (cy - r) * s, (cx + r) * s - 1, (cy + r) * s - 1], fill=255)
        return mask.resize((WIDTH, HEIGHT), Image.BOX)

    def masked(self, layer, mask):
        """layer with its alpha multiplied by mask."""
        from PIL import ImageChops
        out = layer.copy()
        out.putalpha(ImageChops.multiply(out.getchannel("A"), mask))
        return out

    def parse_hex(self, s):
        v = int(s.lstrip("#"), 16)
        return ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF, 255)


# --------------------------------------------------------------------------- #
# IPFS pinning — Pinata (JWT) or any Kubo RPC endpoint.
# --------------------------------------------------------------------------- #
def pin_bytes(raw, filename):
    import urllib.error
    import urllib.request

    jwt = os.environ.get("PINATA_JWT")
    api = os.environ.get("IPFS_API_URL")
    if not jwt and not api:
        fail(
            "no IPFS pinning target configured. Set PINATA_JWT (Pinata API key) or "
            "IPFS_API_URL (Kubo RPC, e.g. http://127.0.0.1:5001) and re-run. "
            "Images must be pinned to IPFS — an http(s) URL is NOT accepted as an agent image."
        )
    try:
        if jwt:
            req = multipart_request("https://uploads.pinata.cloud/v3/files", {"network": "public", "name": filename},
                                    "file", filename, raw, {"Authorization": "Bearer " + jwt})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())["data"]
            if data.get("network", "public") != "public":
                fail("pin of %s landed on Pinata's private network; the bridge and gateways need public" % filename)
            return checked_cid(data["cid"], filename)
        req = multipart_request(api.rstrip("/") + "/api/v0/add?pin=true", {}, "file", filename, raw, kubo_headers())
        with urllib.request.urlopen(req, timeout=120) as r:
            # Kubo streams one JSON object per line; the result carries "Hash".
            objects = [json.loads(line) for line in r.read().decode().splitlines() if line.strip()]
            cid = next(o["Hash"] for o in reversed(objects) if "Hash" in o)
    except urllib.error.HTTPError as e:
        fail("pin of %s failed: HTTP %d %s" % (filename, e.code, e.read()[:300].decode(errors="replace")))
    except (KeyError, StopIteration):
        fail("pin of %s failed: no CID in the response" % filename)
    except (urllib.error.URLError, OSError, ValueError) as e:
        fail("pin of %s failed: %s" % (filename, e))

    # Announce to the DHT so pin-by-CID finds it (best effort, like agent-node).
    try:
        urllib.request.urlopen(
            urllib.request.Request(api.rstrip("/") + "/api/v0/routing/provide?arg=" + cid, method="POST",
                                   headers=kubo_headers()), timeout=30
        ).close()
    except Exception as e:
        sys.stderr.write("routing/provide for %s failed (non-fatal): %s\n" % (cid, e))
    return checked_cid(cid, filename)


def checked_cid(cid, filename):
    if extract_cid(str(cid)) is None:
        fail("pin of %s returned an unsupported CID %r" % (filename, cid))
    return cid


def kubo_headers():
    token = os.environ.get("IPFS_API_TOKEN")
    return {"Authorization": "Bearer " + token} if token else {}


def multipart_request(url, fields, file_field, filename, raw, headers):
    import urllib.request
    import uuid

    boundary = "----agentic" + uuid.uuid4().hex
    body = io.BytesIO()
    for k, v in fields.items():
        body.write(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (boundary, k, v)).encode())
    import mimetypes
    body.write(
        ("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
         "Content-Type: %s\r\n\r\n"
         % (boundary, file_field, filename, mimetypes.guess_type(filename)[0] or "application/octet-stream")).encode()
    )
    body.write(raw)
    body.write(("\r\n--%s--\r\n" % boundary).encode())
    headers = dict(headers, **{"Content-Type": "multipart/form-data; boundary=" + boundary})
    return urllib.request.Request(url, data=body.getvalue(), headers=headers, method="POST")


# --------------------------------------------------------------------------- #
# Card inputs come from the chain; identity.json only fills what an unminted agent cannot have on-chain yet.
# --------------------------------------------------------------------------- #
SUPPORTED_MIMES = {
    "JPEG": "image/jpeg", "MPO": "image/jpeg", "PNG": "image/png", "GIF": "image/gif", "WEBP": "image/webp",
}


def sniff_mime(raw):
    try:
        img = Image.open(io.BytesIO(raw))
        fmt = img.format
    except Exception:
        img, fmt = None, None
    if fmt not in SUPPORTED_MIMES:
        fail("base image must be png, jpg, gif or webp (got %s)" % (fmt or "undecodable data"))
    if fmt == "WEBP" and getattr(img, "is_animated", False):
        fail("animated webp is not supported (agent-node rejects it too)")
    return SUPPORTED_MIMES[fmt]


class Subject:
    """The agent the card is about, resolved from chain (+ config if unminted)."""

    def __init__(self, args):
        # A re-run with the same created_at reproduces the same bytes/CIDs, so an interrupted --apply can resume.
        self.created_at = args.created_at if args.created_at is not None else int(time.time())
        fail_context["created_at"] = self.created_at
        cfg = load_config(args.config, require_images=False) if args.config else {}
        rpc_url = args.rpc_url or cfg.get("rpc_url")
        if not rpc_url:
            fail("no RPC: pass --rpc-url (or set rpc_url in identity.json)")
        self.w3 = connect({"rpc_url": rpc_url}, {})
        self.chain_id = self.w3.eth.chain_id
        self.chain = resolve_chain(self.chain_id)
        if cfg.get("chain_id") is not None and int(cfg["chain_id"]) != self.chain_id:
            fail("identity.json chain_id %s does not match the RPC's chain id %d" % (cfg["chain_id"], self.chain_id))

        self.acct = self.priv = None
        if args.agent_address:
            try:
                self.address = Web3.to_checksum_address(args.agent_address)
            except ValueError:
                fail("--agent-address is not a valid EVM address: %r" % args.agent_address)
        else:
            self.acct, self.priv, _, _ = load_wallet()
            self.address = self.acct.address

        self.mgr = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.chain["contracts"]["AgentCollectionsManager"]),
            abi=load_abi("AgentCollectionsManager"),
        )
        found = find_minted_anywhere(self.mgr, self.address)
        self.minted = found is not None
        if self.minted:
            self.collection_address, self.token_id, self.name = found
            if cfg and Web3.to_checksum_address(cfg["collection_address"]) != self.collection_address:
                fail("wallet %s is minted in collection %s (token %d), not in identity.json's %s"
                     % (self.address, self.collection_address, self.token_id, cfg["collection_address"]),
                     collection=self.collection_address, token_id=self.token_id)
        else:
            if not cfg:
                fail("wallet %s is not minted in any 6022 collection on chain %d; pass --config identity.json "
                     "so name/role/collection can be read from it (or mint first)" % (self.address, self.chain_id))
            self.collection_address = Web3.to_checksum_address(cfg["collection_address"])
            if not self.mgr.functions.isKnownCollection(self.collection_address).call():
                fail("collection %s is not registered with the 6022 AgentCollectionsManager on chain %d; "
                     "use a listCollections result or create one (see references/flow.md)"
                     % (self.collection_address, self.chain_id), collection=self.collection_address)
            self.token_id, self.name = 0, cfg["name"]
        self.cfg = cfg
        self.col = self.w3.eth.contract(address=self.collection_address, abi=load_abi("AgentCollectionV1"))
        self.collection_name = self.col.functions.name().call()

    def params(self, base_image_path):
        try:
            raw = Path(base_image_path).read_bytes()
        except OSError as e:
            fail("cannot read base image %s: %s" % (base_image_path, e))
        self.stars_value = 0
        if self.minted:
            info = self.col.functions.informationOf(self.token_id).call()
            name, creator, _images, attributes, _clone, addresses = info
            role = dict(attributes).get("role", "")
            # Contract stores evm values lowercase; the card shows EIP-55.
            wallets = [Web3.to_checksum_address(v) if t == "evm" else v for t, v in addresses]
            label = name
        else:
            # Mint stores the normalized label as the name.
            label = name = resolve_label(self.cfg)
            creator, role = self.address, self.cfg["role"]
            wallets = [v for _t, v in build_mint_addresses(self.cfg, self.address)]
        self.stars_value = self.stars(creator)
        return {
            "name": name,
            "ens_domain": "%s.%s.%s.%s" % (label, self.collection_name.lower(), self.chain_id, ENS_BASE_DOMAIN),
            "role": role,
            "creator": creator,
            "base_image": raw,
            "base_mime": sniff_mime(raw),
            "wallets": wallets,
            "created_at": self.created_at,
            "stars": self.stars_value,
            "origin_ens_domain": None,
            "origin_generation": None,
        }

    def stars(self, creator):
        """0-8 = digit count of the creator's whole $6022 balance across Token6022 chains (agent-node rule)."""
        total, decimals = 0, TOKEN_6022_DECIMALS
        for chain_id, chain in load_deployments().items():
            token = chain.get("contracts", {}).get("Token6022")
            if not token:
                continue
            try:
                w3 = self.w3 if int(chain_id) == self.chain_id else self.quiet_connect(chain)
                if w3 is None:
                    continue
                erc20 = w3.eth.contract(address=Web3.to_checksum_address(token), abi=load_abi("Token6022"))
                total += erc20.functions.balanceOf(creator).call()
            except Exception as e:
                sys.stderr.write("stars: skipping chain %s (%s)\n" % (chain_id, e))
                continue  # best effort per chain
        if total == 0:
            return 0
        whole = str(total)
        return min(max(len(whole) - decimals, 0), MAX_STARS)

    def quiet_connect(self, chain):
        """Web3 for another chain, or None; never exits like connect()."""
        rpc = next((u for u in chain.get("rpc_urls", []) if "<" not in u), None)
        if not rpc:
            return None
        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 15}))
        return w3 if w3.is_connected() else None

    def describe(self):
        return {
            "chain_id": self.chain_id,
            "agent_address": self.address,
            "minted": self.minted,
            "collection_address": self.collection_address,
            "collection_name": self.collection_name,
            "token_id": self.token_id,
            "stars": self.stars_value,
            "created_at": self.created_at,
        }


def write_outputs(params, out_dir):
    out_dir = Path(out_dir)
    svg = SvgCard(params).render().encode("utf-8")
    png = IconCanvas(params).render()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "default.svg").write_bytes(svg)
        (out_dir / "icon.png").write_bytes(png)
    except OSError as e:
        fail("cannot write images to %s: %s" % (out_dir, e))
    return {"default": (out_dir / "default.svg", svg), "icon": (out_dir / "icon.png", png)}


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_generate(args):
    subject = Subject(args)
    params = subject.params(args.base_image)
    files = write_outputs(params, args.out_dir)
    emit({"ok": True, **subject.describe(), "ens_domain": params["ens_domain"],
          "files": {k: str(v[0].resolve()) for k, v in files.items()}})


def cmd_build(args):
    subject = Subject(args)
    params = subject.params(args.base_image)
    files = write_outputs(params, args.out_dir)

    # Creator/stars must come from the wallet that signs the mint.
    if not subject.minted and subject.acct is None:
        fail("pre-mint build must use the local wallet (drop --agent-address): the card's creator and stars "
             "are derived from the minting wallet")

    label = params["name"]
    cids = fail_context["images"] = {}
    for key, (path, raw) in files.items():
        cids[key] = pin_bytes(raw, "%s-%s%s" % (label, key, path.suffix))
    result = {"ok": True, **subject.describe(), "ens_domain": params["ens_domain"], "images": cids,
              "files": {k: str(v[0].resolve()) for k, v in files.items()}}

    if not subject.minted:
        subject.cfg["default_image"] = cids["default"]
        subject.cfg.setdefault("images", {})["icon"] = cids["icon"]
        try:
            Path(args.config).write_text(json.dumps(subject.cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError as e:
            fail("images pinned but cannot write %s: %s" % (args.config, e),
                 **{k: v for k, v in result.items() if k != "ok"})
        emit({**result, "config_updated": args.config, "applied": False,
              "next": "run agentic_identity.py mint --config %s" % args.config,
              **({} if os.environ.get("PINATA_JWT") else
                 {"note": "images are pinned only on your Kubo node; keep it online until the mint is confirmed "
                          "so the 6022 pinning service can fetch and re-pin them"})})

    if not args.apply:
        emit({**result, "applied": False,
              "next": "re-run with --apply to write these images on-chain (addOrUpdateAgentImage)"})
    if subject.acct is None:
        fail("--apply needs the agent's own wallet (well-known path); drop --agent-address",
             **{k: v for k, v in result.items() if k != "ok"})

    mod_count = subject.col.functions.moderatorCount().call()
    is_mod = subject.col.functions.isModerator(subject.address).call()
    direct = mod_count == 0 or is_mod
    on_chain = dict(subject.col.functions.informationOf(subject.token_id).call()[2])
    if not direct:  # a proposal already waiting for a moderator counts as done
        on_chain.update(pending_image_proposals(subject.w3, subject.col, subject.token_id))
    unchanged = fail_context["unchanged"] = [k for k, cid in cids.items() if on_chain.get(k) == cid]
    calls = []
    for key, cid in cids.items():
        if key in unchanged:
            continue  # identical on-chain value; the contract would just burn gas
        action = "addOrUpdateAgentImage" if direct else "createAddOrUpdateAgentImageProposal"
        fn = getattr(subject.col.functions, action)(subject.token_id, (key, cid))
        calls.append((key, fn, gas_for_broadcast(fn, subject.address, 300_000, action)))

    # Same gate as fund-check; two txs must both be affordable.
    w3 = subject.w3
    fees = fee_fields(w3)
    required = sum(gas for _, _, gas in calls) * gas_price_estimate(w3, fees)
    funding = funding_payload(w3, subject.chain, subject.address, required, w3.eth.get_balance(subject.address),
                              gas_price_wei=gas_price_estimate(w3, fees))
    if not funding["funded"]:
        emit({**result, **funding, "applied": False,
              "note": "wallet underfunded for the image update — ask the owner for the shortfall, then re-run"},
             code=3)

    txs = fail_context["tx_hashes"] = {}
    for key, fn, gas in calls:
        txs[key], _ = send_tx(w3, subject.acct, subject.priv, fn, gas)
    notes = []
    if direct and not os.environ.get("PINATA_JWT"):
        notes.append("images are pinned only on your Kubo node (IPFS_API_URL); keep it online or use PINATA_JWT — "
                     "the 6022 pinning service re-pins proposals and mints, not direct image updates")
    emit({**result, "applied": direct and bool(txs), "proposal_submitted": (not direct) and bool(txs),
          "tx_hashes": txs, "unchanged": unchanged, **({"note": " ".join(notes)} if notes else {})})


def pending_image_proposals(w3, col, token_id):
    """{key: cid} of this token's image proposals still waiting for a moderator."""
    pending = {}
    total = min(col.functions.addOrUpdateAgentImageProposalsLength().call(), PROPOSAL_SCAN_CAP)
    for start in range(0, total, 50):
        idx = range(start, min(start + 50, total))
        props = None
        if hasattr(w3, "batch_requests"):
            try:
                with w3.batch_requests() as batch:
                    for i in idx:
                        batch.add(col.functions.addOrUpdateAgentImageProposal(i))
                    props = batch.execute()
            except Exception:
                props = None  # public RPCs often refuse batches
        if props is None:
            props = [col.functions.addOrUpdateAgentImageProposal(i).call() for i in idx]
        for _id, tid, (key, value) in props:
            if tid == token_id:
                pending[key] = value
    return pending


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        fail("bad arguments: %s" % message, usage=self.format_usage().strip())


def main():
    p = JsonArgumentParser(description="Build + pin the framed 6022 identity images")
    sub = p.add_subparsers(dest="cmd", required=True)
    for cmd in ["generate", "build"]:
        sp = sub.add_parser(cmd)
        sp.add_argument("--rpc-url", help="JSON-RPC endpoint (default: identity.json rpc_url); chain id read from it")
        sp.add_argument("--base-image", required=True, help="raw picture to frame (png/jpg/gif/webp)")
        sp.add_argument("--config", help="identity.json — only needed when the agent is not minted yet")
        sp.add_argument("--agent-address", help="read-only: build for this wallet instead of the local one")
        sp.add_argument("--out-dir", default="agent-images", help="where default.svg / icon.png are written")
        sp.add_argument("--created-at", type=int, help="unix time baked into the card; reuse a prior value to resume")
        if cmd == "build":
            sp.add_argument("--apply", action="store_true",
                            help="minted agent: write the new CIDs on-chain (costs gas)")
    args = p.parse_args()
    run_command({"generate": cmd_generate, "build": cmd_build}[args.cmd], args)


if __name__ == "__main__":
    main()
