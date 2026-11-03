"""The network picture on the login page: Heritage in the middle, customers around it.

Customer names come from data/login_chains.txt (one per line, up to 8). That folder is ignored by git, so real
names never enter the code or the repository. With no file, generic channel names are shown.
"""
import math
import random
from html import escape

from . import data

GENERIC = ["Hypermarkets", "Supermarkets", "Quick commerce", "E-commerce", "Modern formats",
           "Regional chains", "Cash and carry"]
MAX_NAMES = 8
W, H, CX, CY, SY = 600, 420, 300, 210, 0.78


def labels():
    path = data.DATA_DIR / "login_chains.txt"
    try:
        names = [ln.strip()[:26] for ln in path.read_text(encoding="utf-8").splitlines()]
    except OSError:
        return list(GENERIC)
    names = [n for n in names if n and not n.startswith("#")][:MAX_NAMES]
    return names or list(GENERIC)


def _at(angle, radius):
    a = math.radians(angle)
    return CX + radius * math.cos(a), CY + radius * math.sin(a) * SY


def svg(names, logo_url):
    rnd = random.Random(11)
    n = len(names)
    inner = []
    for i, name in enumerate(names):
        ang = -150 + i * 360 / n
        x, y = _at(ang, 125 + (i % 2) * 8)
        inner.append((name, ang, x, y))
    outer = []
    for i, (_, ang, _, _) in enumerate(inner):
        for d in (-24, 24):
            x, y = _at(ang + d + rnd.uniform(-5, 5), rnd.uniform(190, 235))
            if 12 < x < W - 12 and 10 < y < H - 10:
                outer.append((i, x, y))

    def near_label(x, y):
        for name, _, nx, ny in inner:
            left = nx < CX - 10
            tx = nx + (-12 if left else 12)
            w = len(name) * 6.8
            x0, x1 = (tx - w, tx) if left else (tx, tx + w)
            if x0 - 6 <= x <= x1 + 6 and ny - 14 <= y <= ny + 6:
                return True
        return False

    s = [f'<svg class="brand-net" viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet" aria-hidden="true" focusable="false">',
         '<defs><radialGradient id="hub" cx="50%" cy="50%" r="50%"><stop offset="0" stop-color="#e0a526" stop-opacity=".5"/>'
         '<stop offset="1" stop-color="#e0a526" stop-opacity="0"/></radialGradient></defs>',
         f'<ellipse cx="{CX}" cy="{CY}" rx="115" ry="90" fill="url(#hub)"/>']
    for r in (130, 200):
        s.append(f'<ellipse cx="{CX}" cy="{CY}" rx="{r}" ry="{r * SY:.0f}" fill="none" stroke="#fff" stroke-opacity=".07" stroke-dasharray="2 6"/>')
    for i in range(n):
        j = (i + 1) % n
        s.append(f'<line x1="{inner[i][2]:.0f}" y1="{inner[i][3]:.0f}" x2="{inner[j][2]:.0f}" y2="{inner[j][3]:.0f}" stroke="#fff" stroke-opacity=".10"/>')
    keep = [(i, x, y) for i, x, y in outer if not near_label(x, y)]
    for i, x, y in keep:
        s.append(f'<line x1="{inner[i][2]:.0f}" y1="{inner[i][3]:.0f}" x2="{x:.0f}" y2="{y:.0f}" stroke="#fff" stroke-opacity=".15"/>')
    for k, (_, _, x, y) in enumerate(inner):
        s.append(f'<line class="flow" style="animation-delay:{k * 0.45:.2f}s" x1="{CX}" y1="{CY}" x2="{x:.0f}" y2="{y:.0f}" '
                 'stroke="#e0a526" stroke-opacity=".75" stroke-width="1.4"/>')
    for _, x, y in keep:
        s.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="3.2" fill="#9cc7a5" fill-opacity=".75"/>')
    for k, (name, _, x, y) in enumerate(inner):
        s.append(f'<circle class="pulse" style="animation-delay:{k * 0.45:.2f}s" cx="{x:.0f}" cy="{y:.0f}" r="8" fill="none" stroke="#e0a526" stroke-opacity=".6"/>')
        s.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="5.5" fill="#123524" stroke="#e0a526" stroke-width="2"/>')
        left = x < CX - 10
        dy = -10 if abs(x - CX) <= 10 else 4
        s.append(f'<text x="{x + (-12 if left else 12):.0f}" y="{y + dy:.0f}" text-anchor="{"end" if left else "start"}" '
                 f'fill="#e3efe6" font-size="12.5" font-weight="500">{escape(name)}</text>')
    s.append(f'<ellipse cx="{CX}" cy="{CY}" rx="47" ry="33" fill="#fff" stroke="#e0a526" stroke-width="2.5"/>'
             f'<ellipse cx="{CX}" cy="{CY}" rx="55" ry="40" fill="none" stroke="#e0a526" stroke-opacity=".35"/>'
             f'<image href="{escape(logo_url)}" x="{CX - 36}" y="{CY - 19}" width="72" height="38" preserveAspectRatio="xMidYMid meet"/>')
    s.append('</svg>')
    return "\n".join(s)
