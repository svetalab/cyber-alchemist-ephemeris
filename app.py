from flask import Flask, request, jsonify, Response
import swisseph as swe
import math
import os
import html as _html
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

app = Flask(__name__)
app.json.ensure_ascii = False  # so degree symbols (°) show as-is, not as \u00b0 escapes


@app.route("/", methods=["GET"])
def health_check():
    """Simple check so we can confirm in a browser that the service is alive."""
    return jsonify({"status": "Cyber-Alchemist ephemeris service is running"})

# Planets we care about, mapped to their Swiss Ephemeris codes
PLANETS = {
    "sun": swe.SUN,
    "moon": swe.MOON,
    "mercury": swe.MERCURY,
    "venus": swe.VENUS,
    "mars": swe.MARS,
    "jupiter": swe.JUPITER,
    "saturn": swe.SATURN,
    "uranus": swe.URANUS,
    "neptune": swe.NEPTUNE,
    "pluto": swe.PLUTO,
    "lilith": swe.MEAN_APOG,  # Black Moon Lilith (mean lunar apogee point)
    "north_node": swe.TRUE_NODE,  # Rahu - karmic north node
    # "chiron": swe.CHIRON,  # temporarily disabled - needs seas_18.se1 ephemeris file, adding separately
}

ZODIAC_SIGNS = [
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"
]

# Standard aspect angles (orb now varies by point, not by aspect type - see below)
ASPECT_ANGLES = {
    "conjunction": 0,
    "semisextile": 30,
    "sextile": 60,
    "square": 90,
    "trine": 120,
    "quincunx": 150,
    "opposition": 180,
}
MINOR_ASPECTS = {"semisextile", "quincunx"}
MINOR_ASPECT_ORB = 2  # minor aspects only count when tight (natal only; transits use majors)

# Per-point orb for natal charts (matches Chronos's default orb table):
# wider orb for the luminaries, narrower for minor points like nodes/Lilith
NATAL_ORBS = {
    "sun": 9, "moon": 9,
    "mercury": 5, "venus": 5, "mars": 5, "jupiter": 5, "saturn": 5,
    "uranus": 5, "neptune": 5, "pluto": 5,
    "north_node": 1, "south_node": 1, "lilith": 1,
    "ascendant": 5, "midheaven": 5,
}
TRANSIT_ORB = 2  # flat orb for transit-to-natal aspects, matching Chronos's transit settings

# ---- Chart rendering constants (Elix palette "Чорнильна безодня": ink-blue night, brass-gold) ----
BG = "#0B0D22"            # ink-blue background (was pure black #050303)
SIGN_COLORS = {
    "Aries": "#3A1420", "Leo": "#3A1420", "Sagittarius": "#3A1420",        # deep wine (fire)
    "Taurus": "#0B3B45", "Virgo": "#0B3B45", "Capricorn": "#0B3B45",       # deep forest (earth)
    "Gemini": "#1D2548", "Libra": "#1D2548", "Aquarius": "#1D2548",        # deep indigo (air)
    "Cancer": "#241730", "Scorpio": "#241730", "Pisces": "#241730",        # deep plum (water)
}
SIGN_WEDGE_OPACITY = 0.55
GOLD_GRAD_STOPS = ("#C9A45E", "#F1E2B8")
ZODIAC_GLYPHS = {
    "Aries": "&#9800;&#xFE0E;", "Taurus": "&#9801;&#xFE0E;", "Gemini": "&#9802;&#xFE0E;", "Cancer": "&#9803;&#xFE0E;",
    "Leo": "&#9804;&#xFE0E;", "Virgo": "&#9805;&#xFE0E;", "Libra": "&#9806;&#xFE0E;", "Scorpio": "&#9807;&#xFE0E;",
    "Sagittarius": "&#9808;&#xFE0E;", "Capricorn": "&#9809;&#xFE0E;", "Aquarius": "&#9810;&#xFE0E;", "Pisces": "&#9811;&#xFE0E;",
}
PLANET_GLYPHS = {
    "sun": "&#9737;&#xFE0E;", "moon": "&#9789;&#xFE0E;", "mercury": "&#9791;&#xFE0E;", "venus": "&#9792;&#xFE0E;",
    "mars": "&#9794;&#xFE0E;", "jupiter": "&#9795;&#xFE0E;", "saturn": "&#9796;&#xFE0E;", "uranus": "&#9797;&#xFE0E;",
    "neptune": "&#9798;&#xFE0E;", "pluto": "&#9799;&#xFE0E;", "north_node": "&#9738;&#xFE0E;",
    "south_node": "&#9739;&#xFE0E;", "lilith": "&#9912;&#xFE0E;",
}
# Aspect styling: (core colour, facet highlight, dash, width, glitter density)
# Major aspects = solid bordeaux "jewel" lines; soft / minor = dashed emerald or sapphire.
# Tones lifted so they still read on the ink-blue ground (dark bordeaux vanished on it).
BORDEAUX = ("#A2465E", "#E39AAC")
EMERALD = ("#3F8A8A", "#A3D3D0")
SAPPHIRE = ("#5C6FC0", "#AFC0F2")
ASPECT_STYLES = {
    "conjunction": ("#CDAE72", "#F1E2B8", None, 0.25, 0.0),
    "opposition": (BORDEAUX[0], BORDEAUX[1], None, 0.42, 1.0),
    "square": (BORDEAUX[0], BORDEAUX[1], None, 0.42, 1.0),
    "trine": (BORDEAUX[0], BORDEAUX[1], None, 0.38, 1.0),
    "sextile": (EMERALD[0], EMERALD[1], "1.2,1.5", 0.28, 0.6),
    "semisextile": (SAPPHIRE[0], SAPPHIRE[1], "1.2,1.5", 0.25, 0.5),
    "quincunx": (SAPPHIRE[0], SAPPHIRE[1], "1.2,1.5", 0.25, 0.5),
}
SYMBOL_FONT = "'DejaVu Sans','Noto Sans Symbols 2','Segoe UI Symbol',sans-serif"
LABEL_FONT = "'IBM Plex Mono','DejaVu Sans Mono',monospace"
ANTIQUE_GOLD = "#CDAE72"
PLANET_TONE = "#F1E2B8"        # planet glyphs
DEGREE_TONE = "#D9C79B"        # planet degree labels (brighter than before -> crisper)
PNG_SCALE = 4                  # SVG width/height = viewBox side x 4 -> ~1760 px, sharp in Telegram


# ---- Text as vector outlines --------------------------------------------------
# Every label and glyph is converted into an SVG <path> straight from the font file,
# so the PNG looks identical everywhere (Cloudinary, browsers, phones) and never
# falls back to a random system font. Fonts live in ./fonts next to app.py.
try:
    from fontTools.ttLib import TTFont
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.recordingPen import DecomposingRecordingPen
except ImportError:            # fontTools missing -> graceful fallback to <text>
    TTFont = None

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
FONT_FILES = {  # first file that exists wins; absolute paths are local-machine fallbacks only
    "mono": ["IBMPlexMono-Regular.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"],
    "mono_md": ["IBMPlexMono-Medium.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"],
    "symbols": ["DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
    "logo": ["CourierPrime-Bold.ttf", "/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf"],
    "name": ["CormorantSC-Medium.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"],
}
FALLBACK_FAMILY = {"mono": LABEL_FONT, "mono_md": LABEL_FONT, "symbols": SYMBOL_FONT,
                   "logo": "'Courier Prime','Courier New',monospace"}
_font_cache = {}


def _load_font(key):
    if key in _font_cache:
        return _font_cache[key]
    font = None
    if TTFont is not None:
        for name in FONT_FILES[key]:
            path = name if os.path.isabs(name) else os.path.join(FONT_DIR, name)
            if not os.path.exists(path):
                continue
            try:
                tt = TTFont(path)
                upm = tt["head"].unitsPerEm
                cap = getattr(tt["OS/2"], "sCapHeight", 0) or int(upm * 0.7)
                font = {"gs": tt.getGlyphSet(), "cmap": tt.getBestCmap(), "upm": upm,
                        "hmtx": tt["hmtx"], "cap": cap, "key": key}
                break
            except Exception:
                continue
    _font_cache[key] = font
    return font


def _num(v):
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def text_width(text, size, font="mono", letter_spacing=0.0):
    s = _html.unescape(text).replace("\ufe0e", "")
    fonts = [f for f in (_load_font(font), _load_font("symbols")) if f]
    if not fonts:
        return len(s) * size * 0.6
    w = 0.0
    for ch in s:
        f = next((f for f in fonts if ord(ch) in f["cmap"]), None)
        if f:
            w += f["hmtx"][f["cmap"][ord(ch)]][0] * size / f["upm"] + letter_spacing
    return max(0.0, w - letter_spacing)


def svg_small_caps(text, x, y, size, font="name", fill="#FFFFFF", opacity=1.0, letter_spacing=0.0):
    """Antique small caps: full-height first letter, the rest as capitals at 78%.
    Looks the same with Cormorant SC or any fallback serif."""
    s = _html.unescape(text)
    if not s:
        return ""
    first, rest = s[0].upper(), s[1:].upper()
    out = svg_text(first, x, y, size, font, fill, opacity, anchor="start", baseline="central")
    x2 = x + text_width(first, size, font) + letter_spacing
    out += svg_text(rest, x2, y + size * 0.11, size * 0.78, font, fill, opacity,
                    anchor="start", baseline="central", letter_spacing=letter_spacing)
    return out


def svg_text(text, x, y, size, font="mono", fill="#FFFFFF", opacity=1.0,
             anchor="middle", baseline="central", letter_spacing=0.0):
    """
    Draws `text` as a filled <path>. anchor: start | middle | end.
    baseline "central": single symbols are centred on their real ink box,
    words/numbers on the cap height - so glyphs sit exactly on their radius.
    """
    s = _html.unescape(text).replace("\ufe0e", "").replace("\ufe0f", "")
    fonts = [f for f in (_load_font(font), _load_font("symbols")) if f]
    if not fonts:
        esc = _html.escape(s)
        return (f'<text x="{x:.2f}" y="{y:.2f}" font-family="{FALLBACK_FAMILY.get(font, LABEL_FONT)}" '
                f'font-size="{size}" fill="{fill}" fill-opacity="{opacity}" text-anchor="{anchor}" '
                f'dominant-baseline="{baseline}" letter-spacing="{letter_spacing}">{esc}</text>')
    placed, adv = [], 0.0
    for ch in s:
        f = next((f for f in fonts if ord(ch) in f["cmap"]), None)
        if f is None:
            continue
        gname = f["cmap"][ord(ch)]
        sc = size / f["upm"]
        placed.append((f, gname, adv, sc))
        adv += f["hmtx"][gname][0] * sc + letter_spacing
    if not placed:
        return ""
    total = adv - letter_spacing
    x0 = x - total / 2 if anchor == "middle" else (x - total if anchor == "end" else x)
    y0 = y
    if baseline == "central":
        if len(placed) == 1:
            f, g, _, sc = placed[0]
            bp = BoundsPen(f["gs"])
            f["gs"][g].draw(bp)
            if bp.bounds:
                xmin, ymin, xmax, ymax = bp.bounds
                y0 = y + (ymin + ymax) / 2 * sc
                x0 = x - (xmin + xmax) / 2 * sc if anchor == "middle" else x0
        else:
            f0 = placed[0][0]
            y0 = y + f0["cap"] * placed[0][3] / 2
    pen = SVGPathPen(None, ntos=_num)
    for f, g, ox, sc in placed:
        rec = DecomposingRecordingPen(f["gs"])   # flattens composite glyphs (é, ї, й ...)
        f["gs"][g].draw(rec)
        rec.replay(TransformPen(pen, (sc, 0, 0, -sc, x0 + ox, y0)))
    d = pen.getCommands()
    op = f' fill-opacity="{opacity:.2f}"' if opacity < 1 else ""
    return f'<path d="{d}" fill="{fill}"{op}/>'



def degree_to_sign(longitude):
    """Turns a raw 0-360 degree number into a sign name + degree within that sign."""
    sign_index = int(longitude // 30)
    degree_in_sign = longitude % 30
    return {
        "sign": ZODIAC_SIGNS[sign_index],
        "degree": round(degree_in_sign, 2),
        "degree_display": format_dms(degree_in_sign)
    }


def format_dms(decimal_degree):
    """
    Converts a decimal degree (e.g. 28.83) into the classic
    astrology degree-minute format (e.g. "28°50'") that users
    and astrologers actually recognize.
    """
    whole_degrees = int(decimal_degree)
    minutes = round((decimal_degree - whole_degrees) * 60)
    if minutes == 60:  # rounding edge case, e.g. 28.999 -> 29°00'
        whole_degrees += 1
        minutes = 0
    return f"{whole_degrees}°{minutes:02d}'"


def calculate_aspects(points, mode="natal"):
    """
    points: dict of {name: longitude}. Checks every unique pair against
    ASPECT_ANGLES - this is what lets full configurations (T-squares,
    Grand Trines, Kites) emerge naturally from the complete list.
    mode="natal": orb is the tighter of the two points' NATAL_ORBS
    (matches Chronos's natal orb table - same orb across all 5 major
    aspects, varies by point). mode="transit": flat TRANSIT_ORB for
    everything - for later use comparing transiting points to natal
    points in the daily alarm scan.
    """
    names = list(points.keys())
    found = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            diff = abs(points[a] - points[b]) % 360
            if diff > 180:
                diff = 360 - diff
            orb_limit = TRANSIT_ORB if mode == "transit" else min(NATAL_ORBS.get(a, 5), NATAL_ORBS.get(b, 5))
            for aspect_name, exact_angle in ASPECT_ANGLES.items():
                if aspect_name in MINOR_ASPECTS:
                    if mode == "transit":
                        continue
                    limit = min(orb_limit, MINOR_ASPECT_ORB)
                else:
                    limit = orb_limit
                orb = abs(diff - exact_angle)
                if orb <= limit:
                    found.append({
                        "point_a": a,
                        "point_b": b,
                        "aspect": aspect_name,
                        "exact_angle": exact_angle,
                        "actual_angle": round(diff, 2),
                        "orb": round(orb, 2)
                    })
                    break  # a pair matches at most one aspect type
    return found


def find_solar_return_jd(natal_sun_longitude, year, month, day):
    """
    Finds the exact Julian Day when the transiting Sun returns to the
    exact natal Sun longitude in the given year (a "solar return" /
    astrological birthday chart). The Sun moves at a very steady rate
    (~0.9856 deg/day), so a few iterations converge on the exact moment.
    """
    jd = swe.julday(year, month, day, 12.0)  # start near the birthday, noon UTC
    for _ in range(5):
        result, _ = swe.calc_ut(jd, swe.SUN, swe.FLG_MOSEPH)
        current_longitude = result[0]
        diff = (natal_sun_longitude - current_longitude + 540) % 360 - 180
        jd += diff / 0.9856
    return jd


def to_julian_day(year, month, day, hour, minute, utc_offset_hours):
    """
    Converts a birth date/time (in local time, with a UTC offset) into
    the Julian Day number that Swiss Ephemeris needs for all calculations.
    Used for /transits, where a manual offset is fine (current era, no
    historical timezone weirdness to worry about).
    """
    # Convert local time to UTC first
    local_hour_decimal = hour + minute / 60.0
    utc_hour_decimal = local_hour_decimal - utc_offset_hours
    return swe.julday(year, month, day, utc_hour_decimal)


def local_time_to_julian_day(year, month, day, hour, minute, timezone_name):
    """
    For natal charts: converts a birth date/time in its own local
    timezone into UTC using Python's built-in historical timezone
    database (zoneinfo) - this correctly handles old DST rules,
    Soviet-era decree time, etc. automatically, as long as we're told
    which IANA timezone name applies (e.g. "Europe/Kyiv"). Make resolves
    this name from the birth coordinates via a geocoding/timezone API
    step before calling us - we just do the correct historical math.
    """
    local_dt = datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(timezone_name))
    utc_dt = local_dt.astimezone(ZoneInfo("UTC"))
    jd = swe.julday(utc_dt.year, utc_dt.month, utc_dt.day, utc_dt.hour + utc_dt.minute / 60.0)
    return jd


# Typical daily motion (°/day) used to judge when a planet has "stopped".
# A planet is stationary when its speed drops below STATION_FRACTION of this -
# so Mercury counts as stationary for ~1-2 days, Jupiter/Saturn for ~a week, Pluto for weeks.
TYPICAL_SPEED = {
    "mercury": 1.2, "venus": 1.2, "mars": 0.52, "jupiter": 0.083, "saturn": 0.034,
    "uranus": 0.012, "neptune": 0.006, "pluto": 0.004,
}
STATION_FRACTION = 0.10


def station_state(julian_day, name, code, speed):
    """
    None if moving normally; "retrograde" if stopping before turning retrograde (SR);
    "direct" if stopping before turning direct (SD).
    Sun, Moon, nodes and Lilith never station, so they are skipped.
    """
    typical = TYPICAL_SPEED.get(name)
    if typical is None or abs(speed) >= typical * STATION_FRACTION:
        return None
    next_speed = swe.calc_ut(julian_day + 1, code, swe.FLG_MOSEPH | swe.FLG_SPEED)[0][3]
    return "retrograde" if next_speed < speed else "direct"


def calculate_planets(julian_day):
    """
    Calculates the position of every planet in PLANETS for a given moment.
    Uses the Moshier semi-analytical ephemeris (SEFLG_MOSEPH) so we don't
    need to bundle any external ephemeris data files with the deployment.
    Also derives the South Node (Ketu) as exactly opposite the North Node.
    """
    positions = {}
    for name, code in PLANETS.items():
        result, _ = swe.calc_ut(julian_day, code, swe.FLG_MOSEPH | swe.FLG_SPEED)
        longitude = result[0]
        is_retrograde = result[3] < 0  # negative daily speed = retrograde
        station = station_state(julian_day, name, code, result[3])
        sign_info = degree_to_sign(longitude)
        positions[name] = {
            "longitude": round(longitude, 4),
            "sign": sign_info["sign"],
            "degree_in_sign": sign_info["degree"],
            "degree_display": sign_info["degree_display"],
            "retrograde": is_retrograde,
            "station": station,
            "speed": round(result[3], 5)
        }

    if "north_node" in positions:
        south_longitude = (positions["north_node"]["longitude"] + 180) % 360
        sign_info = degree_to_sign(south_longitude)
        positions["south_node"] = {
            "longitude": round(south_longitude, 4),
            "sign": sign_info["sign"],
            "degree_in_sign": sign_info["degree"],
            "degree_display": sign_info["degree_display"],
            "retrograde": positions["north_node"]["retrograde"],
            "station": None,
            "speed": positions["north_node"]["speed"]
        }

    return positions


def calculate_houses(julian_day, latitude, longitude):
    """
    Calculates the 12 house cusps and the Ascendant/Midheaven,
    using the Placidus house system (the most widely used one).
    """
    cusps, ascmc = swe.houses(julian_day, latitude, longitude, b'P')
    houses = {}
    for i in range(12):
        sign_info = degree_to_sign(cusps[i])
        houses[f"house_{i + 1}"] = {
            "longitude": round(cusps[i], 4),
            "sign": sign_info["sign"],
            "degree_in_sign": sign_info["degree"]
        }
    ascendant_info = degree_to_sign(ascmc[0])
    midheaven_info = degree_to_sign(ascmc[1])
    return {
        "houses": houses,
        "ascendant": ascendant_info,
        "midheaven": midheaven_info
    }


ROMAN_NUMERALS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]
ANGLE_LABELS = {1: "Asc", 4: "IC", 7: "Dsc", 10: "MC"}


def polar(cx, cy, r, angle_deg):
    """Standard polar-to-cartesian helper, oriented so the chart rotates
    the correct astrological direction (counter-clockwise from the Ascendant)."""
    rad = math.radians(angle_deg)
    return cx + r * math.cos(rad), cy - r * math.sin(rad)


def spread_glyph_angles(longitudes, min_sep):
    """
    Chronos-style single-ring layout: every planet keeps its true degree dot,
    but its glyph slides sideways just enough that no two glyphs (or their
    degree labels) touch. Close planets are grouped into clusters, each
    cluster is fanned out at exactly min_sep spacing around its own centre,
    and neighbouring clusters are merged until nothing overlaps.
    Returns {name: display_longitude}.
    """
    items = sorted(longitudes.items(), key=lambda kv: kv[1])
    n = len(items)
    if n < 2:
        return dict(longitudes)
    # start the sequence right after the widest empty stretch so we never split a cluster at 0°
    gaps = [((items[(k + 1) % n][1] - items[k][1]) % 360, k) for k in range(n)]
    _, widest = max(gaps)
    items = items[widest + 1:] + items[:widest + 1]
    base = items[0][1]
    names = [nm for nm, _ in items]
    lons = [(lon - base) % 360 for _, lon in items]  # monotonic, unwrapped

    clusters = [[k] for k in range(n)]

    def positions(cluster):
        centre = sum(lons[k] for k in cluster) / len(cluster)
        first = centre - min_sep * (len(cluster) - 1) / 2
        return [first + min_sep * j for j in range(len(cluster))]

    changed = True
    while changed:
        changed = False
        for c in range(len(clusters) - 1):
            if positions(clusters[c + 1])[0] - positions(clusters[c])[-1] < min_sep:
                clusters[c] = clusters[c] + clusters.pop(c + 1)
                changed = True
                break

    display = {}
    for cluster in clusters:
        for k, pos in zip(cluster, positions(cluster)):
            display[names[k]] = (pos + base) % 360
    return display


def _sparkle(x, y, size, fill, opacity):
    """A tiny 4-point glint (the 'glitter' on jewel lines and around the moon)."""
    s, t = size, size * 0.22
    pts = f"{x:.2f},{y - s:.2f} {x + t:.2f},{y - t:.2f} {x + s:.2f},{y:.2f} {x + t:.2f},{y + t:.2f} " \
          f"{x:.2f},{y + s:.2f} {x - t:.2f},{y + t:.2f} {x - s:.2f},{y:.2f} {x - t:.2f},{y - t:.2f}"
    return f'<polygon points="{pts}" fill="{fill}" opacity="{opacity:.2f}"/>'


def transit_natal_aspects(transit_planets, natal_planets):
    """Major aspects from each transiting planet to each natal planet, flat TRANSIT_ORB."""
    found = []
    for t_name, t in transit_planets.items():
        for n_name, n in natal_planets.items():
            diff = abs(t["longitude"] - n["longitude"]) % 360
            if diff > 180:
                diff = 360 - diff
            for aspect_name, exact in ASPECT_ANGLES.items():
                if aspect_name in MINOR_ASPECTS:
                    continue
                orb = abs(diff - exact)
                if orb <= TRANSIT_ORB:
                    found.append({"transit": t_name, "natal": n_name, "aspect": aspect_name,
                                  "orb": round(orb, 2)})
                    break
    return found


TRANSIT_TONES = {
    "amethyst": ("#B89CDB", "#8E7AB0"),   # cool violet - opposite gold on the colour wheel
    "copper": ("#D08A55", "#A26A40"),     # warm cognac copper
}
TRANSIT_TONES["teal"] = ("#8ED1CF", "#5E9E9C")   # "небо зараз" colour, same as the Passport
TRANSIT_TONE, TRANSIT_TONE_SOFT = TRANSIT_TONES["teal"]
TRANSIT_TIGHT_ORB = 1.0     # transit chart shows only the tightest contacts...
TRANSIT_MAX_ASPECTS = 6     # ...and at most this many, closest first


def retro_mark(name, planet):
    """Small ℞ after the degree for retrograde planets (nodes excluded - they are almost always retrograde)."""
    if planet.get("retrograde") and name not in ("north_node", "south_node"):
        return " &#8478;"
    return ""


def badge_anchor(cx, cy, r, angle, degree_outward):
    """
    Where the ℞ / S sits, ~7 units from the glyph centre. Tries, in order of
    preference: lower-right, lower-left, left, upper-right - and takes the first
    one that does NOT point toward the planet's degree label (outward on the
    transit ring, inward on the natal ring), so mark and degree never collide.
    """
    gx, gy = polar(cx, cy, r, angle)
    rad = math.radians(angle)
    ux, uy = math.cos(rad), -math.sin(rad)
    if not degree_outward:
        ux, uy = -ux, -uy
    candidates = [(5.3, 4.3), (-7.4, 3.9), (-8.2, 1.0), (5.3, -4.6)]
    for dx, dy in candidates:
        n = math.hypot(dx, dy)
        if (ux * dx + uy * dy) / n < 0.35:
            return gx + dx, gy + dy
    return gx + candidates[-1][0], gy + candidates[-1][1]


def retro_badge(gx, gy, name, planet):
    """
    Hair-thin hand-drawn ℞ (an SVG path, not a font glyph, so the line weight is
    fully under our control) in the same pale gold as the planet glyphs, sitting
    as a tiny subscript at the lower-right of the glyph, with one faint glint on its tail.
    """
    x, y = gx - 1.2, gy - 1.6          # gx, gy = badge centre -> top-left of the letter
    if planet.get("station"):
        # hair-thin upright S crossed by a fine vertical stem, like a "$",
        # with one glint at the end of the lower tail
        # hourglass S: slim pinched waist in the middle, rounder fuller lower bowl
        d = (f"M{x + 1.75:.2f},{y + 0.45:.2f} "
             f"C{x + 1.55:.2f},{y + 0.02:.2f} {x + 0.18:.2f},{y + 0.0:.2f} {x + 0.3:.2f},{y + 0.82:.2f} "
             f"C{x + 0.44:.2f},{y + 1.3:.2f} {x + 1.0:.2f},{y + 1.45:.2f} {x + 1.12:.2f},{y + 1.6:.2f} "
             f"C{x + 2.15:.2f},{y + 1.95:.2f} {x + 2.25:.2f},{y + 2.45:.2f} {x + 2.08:.2f},{y + 2.85:.2f} "
             f"C{x + 1.8:.2f},{y + 3.4:.2f} {x + 0.4:.2f},{y + 3.42:.2f} {x + 0.05:.2f},{y + 2.8:.2f}")
        return (f'<path d="{d}" fill="none" stroke="#F4E4BC" stroke-width="0.24" '
                f'stroke-linecap="round" stroke-linejoin="round" opacity="0.95"/>'
                + _sparkle(x - 0.1, y + 3.6, 0.45, "#FFF6DC", 0.8))
    if not retro_mark(name, planet):
        return ""
    d = (f"M{x:.2f},{y:.2f} V{y + 3.2:.2f} "                              # stem
         f"M{x:.2f},{y:.2f} H{x + 1.1:.2f} C{x + 2.15:.2f},{y:.2f} {x + 2.15:.2f},{y + 1.6:.2f} "
         f"{x + 1.1:.2f},{y + 1.6:.2f} H{x:.2f} "                          # bowl
         f"M{x + 0.9:.2f},{y + 1.6:.2f} L{x + 2.5:.2f},{y + 3.5:.2f} "      # leg / tail
         f"M{x + 1.45:.2f},{y + 3.15:.2f} L{x + 2.45:.2f},{y + 2.3:.2f}")   # the crossing stroke of ℞
    return (f'<path d="{d}" fill="none" stroke="#F4E4BC" stroke-width="0.24" '
            f'stroke-linecap="round" stroke-linejoin="round" opacity="0.95"/>'
            + _sparkle(x + 2.55, y + 3.55, 0.45, "#FFF6DC", 0.8))



ASPECT_SYMBOL_COLOR = {"square": "#C96A7C", "opposition": "#C96A7C", "trine": "#C96A7C", "sextile": "#6FA6A3"}


def aspect_symbol(kind, x, y, colour, k=2.1, gap=3.4):
    """Vector aspect sign sitting in a small gap cut into the line (style '2 · розрив')."""
    w = 0.42
    o = [f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{gap}" fill="{BG}"/>']
    if kind == "square":
        o.append(f'<rect x="{x - k * 0.8:.2f}" y="{y - k * 0.8:.2f}" width="{k * 1.6:.2f}" height="{k * 1.6:.2f}" '
                 f'fill="none" stroke="{colour}" stroke-width="{w}"/>')
    elif kind == "trine":
        o.append(f'<path d="M{x:.2f},{y - k:.2f} L{x + k * 0.92:.2f},{y + k * 0.62:.2f} L{x - k * 0.92:.2f},{y + k * 0.62:.2f} Z" '
                 f'fill="none" stroke="{colour}" stroke-width="{w}" stroke-linejoin="round"/>')
    elif kind == "sextile":
        for a in (90, 30, 150):
            dx, dy = k * math.cos(math.radians(a)), k * math.sin(math.radians(a))
            o.append(f'<line x1="{x - dx:.2f}" y1="{y - dy:.2f}" x2="{x + dx:.2f}" y2="{y + dy:.2f}" '
                     f'stroke="{colour}" stroke-width="{w}" stroke-linecap="round"/>')
    elif kind == "opposition":
        for sx in (-1, 1):
            o.append(f'<circle cx="{x + sx * k * 0.55:.2f}" cy="{y - sx * k * 0.55:.2f}" r="{k * 0.4:.2f}" '
                     f'fill="none" stroke="{colour}" stroke-width="{w}"/>')
        o.append(f'<line x1="{x - k * 0.27:.2f}" y1="{y + k * 0.27:.2f}" x2="{x + k * 0.27:.2f}" y2="{y - k * 0.27:.2f}" '
                 f'stroke="{colour}" stroke-width="{w}"/>')
    else:
        return ""
    return "".join(o)


def constellation_hat(tx, ty, sc, rot=9):
    """Elix's constellation wizard hat (brim of stars, cone of star-lines, golden glow, bright tip star)."""
    brim = [(-11, 0.4), (-5, 1.7), (1, 2), (7, 1.3), (11.5, -0.1)]
    c = {"L1": (-6, -0.8), "L2": (-3, -10), "T": (2.2, -18), "TIP": (8.6, -21.2), "R1": (5.6, -1.2), "R2": (3.2, -9.5)}
    lw = 0.9
    o = [f'<polygon points="{" ".join(f"{x},{y}" for x, y in (c["L1"], c["L2"], c["T"], c["R2"], c["R1"]))}" '
         f'fill="{ANTIQUE_GOLD}" opacity="0.38"/>',
         f'<polyline points="{" ".join(f"{x},{y}" for x, y in brim)}" fill="none" stroke="{ANTIQUE_GOLD}" stroke-width="{lw}"/>']
    for a, b in (("L1", "L2"), ("L2", "T"), ("T", "TIP"), ("R1", "R2"), ("R2", "T")):
        o.append(f'<line x1="{c[a][0]}" y1="{c[a][1]}" x2="{c[b][0]}" y2="{c[b][1]}" stroke="{ANTIQUE_GOLD}" stroke-width="{lw}"/>')
    for i, (x, y) in enumerate(brim):
        o.append(f'<circle cx="{x}" cy="{y}" r="{1.1 if i % 2 else 1.4}" fill="{ANTIQUE_GOLD}"/>')
    for k in ("L1", "L2", "R1", "R2"):
        o.append(f'<circle cx="{c[k][0]}" cy="{c[k][1]}" r="1.1" fill="{ANTIQUE_GOLD}"/>')
    o.append(f'<circle cx="{c["T"][0]}" cy="{c["T"][1]}" r="1.4" fill="#F4E4BC"/>')
    o.append(_sparkle(c["TIP"][0], c["TIP"][1], 3.6, "#F8ECC8", 1.0))
    return f'<g transform="translate({tx:.2f},{ty:.2f}) rotate({rot}) scale({sc})">{"".join(o)}</g>'

FIGURE_FILL_TENSE = "#5A1630"   # deep bordeaux, same family as the fire wedge
FIGURE_FILL_SOFT = "#0B4A3B"    # deep emerald
FIGURE_ASPECTS = {"opposition", "square", "trine", "sextile", "quincunx"}
FIGURE_NAMES = {  # triangle made of these three aspects -> configuration name (UA / EN)
    ("opposition", "square", "square"): ("Тау-квадрат", "T-square"),
    ("trine", "trine", "trine"): ("Великий трин", "Grand Trine"),
    ("sextile", "sextile", "trine"): ("Малий трин", "Minor Grand Trine"),
    ("quincunx", "quincunx", "sextile"): ("Йод", "Yod"),
    ("opposition", "sextile", "trine"): ("Косий парус", "Oblique Sail"),
}


def find_figures(aspect_list, present):
    """
    Finds every closed triangle in the aspect web (major aspects + quincunx) among the
    points actually drawn. Any aspect pattern - T-square, Grand Trine, Kite, Mystic
    Rectangle, Yod, Grand Cross - is built from such triangles, so drawing all their
    edges guarantees the FULL figure appears, including the Rahu-Ketu base of a
    nodal T-square. Returns (set of frozenset edges, list of figures).
    """
    adj = {}
    for a in aspect_list:
        x, y = a["point_a"], a["point_b"]
        if a["aspect"] in FIGURE_ASPECTS and x in present and y in present:
            adj.setdefault(x, {})[y] = a["aspect"]
            adj.setdefault(y, {})[x] = a["aspect"]
    names = sorted(adj)
    edges, figures = set(), []
    for i, x in enumerate(names):
        for j in range(i + 1, len(names)):
            y = names[j]
            if y not in adj[x]:
                continue
            for z in names[j + 1:]:
                if z in adj[x] and z in adj[y]:
                    kinds = tuple(sorted((adj[x][y], adj[x][z], adj[y][z])))
                    ua, en = FIGURE_NAMES.get(kinds, ("Аспектна фігура", "Aspect figure"))
                    figures.append({"points": [x, y, z], "aspects": list(kinds), "name_ua": ua, "name_en": en})
                    edges |= {frozenset((x, y)), frozenset((x, z)), frozenset((y, z))}
    # 4-point figures: if all six links between four points exist, name the whole shape
    from itertools import combinations
    FOUR = {
        (("opposition", 2), ("square", 4)): ("Великий хрест", "Grand Cross"),
        (("opposition", 1), ("sextile", 2), ("trine", 3)): ("Парус", "Kite"),
        (("opposition", 2), ("sextile", 2), ("trine", 2)): ("Містичний прямокутник", "Mystic Rectangle"),
        (("opposition", 1), ("sextile", 3), ("trine", 2)): ("Трапеція", "Cradle / Trapezium"),
    }
    members = sorted({p for f in figures for p in f["points"]})
    big = []
    for quad in combinations(members, 4):
        kinds = []
        for x, y in combinations(quad, 2):
            if y not in adj.get(x, {}):
                break
            kinds.append(adj[x][y])
        else:
            key = tuple(sorted((k, kinds.count(k)) for k in set(kinds)))
            if key in FOUR:
                ua, en = FOUR[key]
                big.append({"points": list(quad), "aspects": sorted(kinds), "name_ua": ua, "name_en": en})
    if big:  # triangles that are only pieces of a named 4-point shape are not listed separately
        covered = [set(b["points"]) for b in big]
        figures = [f for f in figures if not any(set(f["points"]) <= c for c in covered)] + big
    return edges, figures


def render_chart_svg(natal_data, transit_data=None):
    """
    Renders the natal chart in the agreed dark-jewel palette:
    black background, true-arc zodiac band in element tones, antique-gold
    linework, planets on ONE ring (Chronos-style fan-out so nothing overlaps),
    bordeaux jewel lines for major aspects and dashed emerald / sapphire
    glitter lines for soft & minor ones, all passing UNDER a young-moon
    emblem at the centre.
    """
    import random

    cx, cy = 200, 200
    r_outer, r_inner, r_small = 185, 160, 118
    has_transits = bool(transit_data)
    r_t_outer = 214                      # outer rim of the transit band (only used with transits)
    r_t_glyph, r_t_degree = 197.5, 207.5
    M = 50 if has_transits else 20       # viewBox margin
    r_house_label = (r_t_outer + 14) if has_transits else (r_outer + 15)
    r_cusp_end = (r_t_outer + 3) if has_transits else (r_outer + 6)
    r_glyph, r_degree, r_leader_end = 146, 133.5, 126
    min_sep = 8.0  # degrees between neighbouring planet glyphs

    tk = natal_data.get("time_known", True)   # False -> noon chart, solar houses, no angles
    lang = natal_data.get("lang", "ua")
    asc_sign_index = ZODIAC_SIGNS.index(natal_data["ascendant"]["sign"])
    asc_longitude = asc_sign_index * 30 + natal_data["ascendant"]["degree"]

    def angle_for(longitude):
        return (180 + (longitude - asc_longitude)) % 360

    # extra margin in the viewBox so outer labels (Asc / Dsc / MC / IC) are never cut off
    side = 400 + 2 * M
    parts = [f'<svg viewBox="{-M} {-M} {side} {side}" width="{side * PNG_SCALE}" height="{side * PNG_SCALE}" '
             'xmlns="http://www.w3.org/2000/svg">']
    parts.append(
        '<defs>'
        # userSpaceOnUse: gradients never vanish on perfectly vertical/horizontal lines
        f'<linearGradient id="gold" gradientUnits="userSpaceOnUse" x1="-20" y1="-20" x2="420" y2="420">'
        f'<stop offset="0" stop-color="{GOLD_GRAD_STOPS[0]}"/><stop offset="1" stop-color="{GOLD_GRAD_STOPS[1]}"/>'
        '</linearGradient>'
        '<linearGradient id="moonGold" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#FFF6DC"/><stop offset="0.45" stop-color="#F1D78E"/>'
        '<stop offset="1" stop-color="#B8912F"/></linearGradient>'
        '<radialGradient id="halo" cx="0.5" cy="0.5" r="0.5">'
        '<stop offset="0" stop-color="#F4E4BC" stop-opacity="0.16"/>'
        '<stop offset="0.6" stop-color="#D4AF37" stop-opacity="0.05"/>'
        '<stop offset="1" stop-color="#D4AF37" stop-opacity="0"/></radialGradient>'
        '<filter id="glow" filterUnits="userSpaceOnUse" x="-60" y="-60" width="520" height="520">'
        '<feGaussianBlur stdDeviation="0.9"/></filter>'
        '<filter id="softglow" filterUnits="userSpaceOnUse" x="-60" y="-60" width="520" height="520">'
        '<feGaussianBlur stdDeviation="1.6"/></filter>'
        '<filter id="mist" filterUnits="userSpaceOnUse" x="-60" y="-60" width="520" height="520">'
        '<feGaussianBlur stdDeviation="1.3"/></filter>'
        '<radialGradient id="fog" cx="0.5" cy="0.5" r="0.5">'
        '<stop offset="0" stop-color="#E8CF8A" stop-opacity="0.13"/>'
        '<stop offset="0.55" stop-color="#C9A94E" stop-opacity="0.05"/>'
        '<stop offset="1" stop-color="#C9A94E" stop-opacity="0"/></radialGradient>'
        '</defs>'
    )
    parts.append(f'<rect x="{-M}" y="{-M}" width="{side}" height="{side}" fill="{BG}"/>')

    # ---- Zodiac band as TRUE arcs ----
    for i, sign in enumerate(ZODIAC_SIGNS):
        a1, a2 = angle_for(i * 30), angle_for((i + 1) * 30)
        p_i1, p_i2 = polar(cx, cy, r_inner, a1), polar(cx, cy, r_inner, a2)
        p_o1, p_o2 = polar(cx, cy, r_outer, a1), polar(cx, cy, r_outer, a2)
        parts.append(
            f'<path d="M{p_i1[0]:.2f},{p_i1[1]:.2f} '
            f'A{r_inner},{r_inner} 0 0,0 {p_i2[0]:.2f},{p_i2[1]:.2f} '
            f'L{p_o2[0]:.2f},{p_o2[1]:.2f} '
            f'A{r_outer},{r_outer} 0 0,1 {p_o1[0]:.2f},{p_o1[1]:.2f} Z" '
            f'fill="{SIGN_COLORS[sign]}" opacity="{SIGN_WEDGE_OPACITY}"/>'
        )
        parts.append(f'<line x1="{p_i1[0]:.2f}" y1="{p_i1[1]:.2f}" x2="{p_o1[0]:.2f}" y2="{p_o1[1]:.2f}" '
                      f'stroke="url(#gold)" stroke-width="0.3" opacity="0.5"/>')

    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r_outer}" fill="none" stroke="url(#gold)" stroke-width="0.6"/>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r_inner}" fill="none" stroke="url(#gold)" stroke-width="0.35" opacity="0.7"/>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r_small}" fill="none" stroke="url(#gold)" stroke-width="0.3" opacity="0.6"/>')

    # ---- House cusp lines: translucent antique gold (labels are drawn last, on top) ----
    cusp_angles = {}
    for i in range(1, 13):
        h = natal_data["houses"][f"house_{i}"]
        a = angle_for(ZODIAC_SIGNS.index(h["sign"]) * 30 + h["degree_in_sign"])
        cusp_angles[i] = (a, h)
        is_angle = tk and i in ANGLE_LABELS
        p_over = polar(cx, cy, r_cusp_end, a)
        parts.append(f'<line x1="{cx}" y1="{cy}" x2="{p_over[0]:.1f}" y2="{p_over[1]:.1f}" '
                      f'stroke="{ANTIQUE_GOLD}" stroke-width="{0.35 if is_angle else 0.2}" '
                      f'opacity="{0.55 if is_angle else 0.28}"/>')

    # ---- Planet positions ----
    planet_longitude = {
        name: ZODIAC_SIGNS.index(p["sign"]) * 30 + p["degree_in_sign"]
        for name, p in natal_data["planets"].items()
    }
    display_longitude = spread_glyph_angles(planet_longitude, min_sep)
    ring_point = {name: polar(cx, cy, r_small, angle_for(lon)) for name, lon in planet_longitude.items()}

    # ---- Aspect lines: glow underlay -> jewel core -> bright facet -> glitter ----
    if has_transits:
        t_lon = {name: ZODIAC_SIGNS.index(p["sign"]) * 30 + p["degree_in_sign"]
                 for name, p in transit_data["planets"].items()}
        for name, lon in t_lon.items():
            ring_point["t_" + name] = polar(cx, cy, r_small, angle_for(lon))
        aspect_list = [{"point_a": "t_" + x["transit"], "point_b": x["natal"], "aspect": x["aspect"]}
                       for x in transit_data.get("aspects", [])]
    else:
        aspect_list = natal_data.get("aspects", [])

    # full aspect figures (natal only): every edge of every closed triangle is drawn,
    # and the figure gets a whisper-thin fill so the eye sees a SHAPE, not loose lines
    fig_edges, figures = (set(), []) if has_transits else find_figures(aspect_list, set(ring_point))
    for fig in figures:
        tense = any(k in ("opposition", "square") for k in fig["aspects"])
        pts = " ".join(f"{ring_point[p][0]:.2f},{ring_point[p][1]:.2f}" for p in fig["points"])
        parts.append(f'<polygon points="{pts}" fill="{FIGURE_FILL_TENSE if tense else FIGURE_FILL_SOFT}" opacity="0.28"/>')

    fig_lines, aspect_marks, mark_spots = [], [], []
    for asp in aspect_list:
        a_name, b_name = asp["point_a"], asp["point_b"]
        if a_name not in ring_point or b_name not in ring_point:
            continue
        if {a_name, b_name} == {"north_node", "south_node"} and frozenset((a_name, b_name)) not in fig_edges:
            continue  # the bare Rahu-Ketu axis is skipped - drawn only as the base of a figure
        core, facet, dash, width, sparkle = ASPECT_STYLES.get(
            asp["aspect"], (SAPPHIRE[0], SAPPHIRE[1], "1.2,1.5", 0.25, 0.5))
        (x1, y1), (x2, y2) = ring_point[a_name], ring_point[b_name]
        if frozenset((a_name, b_name)) in fig_edges:
            fig_lines.append((x1, y1, x2, y2, core, facet, width))
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        line = f'x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}"'
        if sparkle:
            parts.append(f'<line {line} stroke="{core}" stroke-width="{width * 3.0:.2f}" '
                          f'opacity="0.2" filter="url(#glow)"{dash_attr}/>')
        parts.append(f'<line {line} stroke="{core}" stroke-width="{width}" opacity="0.72"{dash_attr}/>')
        if sparkle:
            parts.append(f'<line {line} stroke="{facet}" stroke-width="{width * 0.28:.2f}" opacity="0.3"{dash_attr}/>')
            # deterministic glitter: same chart -> same sparkles every render
            rng = random.Random(f"{a_name}-{b_name}-{asp['aspect']}")
            length = math.hypot(x2 - x1, y2 - y1)
            count = max(2, int(length / 16 * sparkle))
            for _ in range(count):
                t = rng.uniform(0.08, 0.92)
                sx, sy = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
                size = rng.uniform(0.5, 1.25) * (1 if not dash else 0.8)
                tint = rng.choice(["#F6E7C8", facet, "#FFF4E0"])
                parts.append(_sparkle(sx, sy, size, tint, rng.uniform(0.3, 0.65)))
        if asp["aspect"] in ASPECT_SYMBOL_COLOR:
            # midpoint first; slide along the line if hidden by the emblem or touching another sign
            for t in (0.5, 0.4, 0.6, 0.3, 0.7, 0.25, 0.75):
                mx, my = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
                if math.hypot(mx - cx, my - cy) < 30:
                    continue
                if all(math.hypot(mx - px, my - py) > 8 for px, py in mark_spots):
                    break
            mark_spots.append((mx, my))
            aspect_marks.append(aspect_symbol(asp["aspect"], mx, my, ASPECT_SYMBOL_COLOR[asp["aspect"]]))

    parts.extend(aspect_marks)

    # ---- Unknown birth time: the arc the Moon travelled during the birth day ----
    mr = natal_data.get("moon_range")
    if mr and not tk:
        a0, a1 = mr["start"], mr["end"]
        if a1 < a0:
            a1 += 360
        steps = max(4, int((a1 - a0) / 1.5))
        pts = [polar(cx, cy, r_small, angle_for(a0 + (a1 - a0) * k / steps)) for k in range(steps + 1)]
        path = " ".join(f"{x:.2f},{y:.2f}" for x, y in pts)
        parts.append(f'<polyline points="{path}" fill="none" stroke="#F4E4BC" stroke-width="2.2" '
                     f'stroke-linecap="round" opacity="0.18"/>')
        parts.append(f'<polyline points="{path}" fill="none" stroke="#F4E4BC" stroke-width="0.6" '
                     f'stroke-linecap="round" stroke-dasharray="0.8,1.4" opacity="0.8"/>')

    # ---- Exact-degree dots + thin leader to the (possibly shifted) glyph ----
    for name, lon in planet_longitude.items():
        dot = ring_point[name]
        lead = polar(cx, cy, r_leader_end, angle_for(display_longitude[name]))
        parts.append(f'<line x1="{dot[0]:.1f}" y1="{dot[1]:.1f}" x2="{lead[0]:.1f}" y2="{lead[1]:.1f}" '
                      f'stroke="{ANTIQUE_GOLD}" stroke-width="0.18" opacity="0.45"/>')
        parts.append(f'<circle cx="{dot[0]:.1f}" cy="{dot[1]:.1f}" r="1.2" fill="#F4E4BC"/>')

    # ---- Owner caption: top-left corner (name large, birth data small beneath) ----
    if natal_data.get("owner_name"):
        parts.append(svg_small_caps(natal_data["owner_name"], -M + 10, -M + 14, 7, "name", PLANET_TONE, 0.95,
                                    letter_spacing=0.9))
        if natal_data.get("owner_details"):
            parts.append(svg_text(natal_data["owner_details"], -M + 10, -M + 23, 4.4, "mono", ANTIQUE_GOLD, 0.85,
                                  anchor="start", baseline="central"))

    if not tk:
        note = ("час народження невідомий · сонячні доми" if lang != "en"
                else "birth time unknown · solar houses")
        parts.append(svg_text(note, -M + 10, -M + (31 if natal_data.get("owner_details") else 14), 3.9, "mono",
                              ANTIQUE_GOLD, 0.7, anchor="start", baseline="central"))

    # ---- Elix signature: bottom-left corner ----
    sig_x, sig_y, sig_size = -M + 10, 400 + M - 12, 9
    parts.append(svg_text("elix", sig_x, sig_y, sig_size, "logo", ANTIQUE_GOLD, 0.85,
                          anchor="start", baseline="central"))
    x_right = sig_x + text_width("elix", sig_size, "logo")
    parts.append(constellation_hat(x_right - sig_size * 0.06, sig_y - sig_size * 0.19, sig_size * 0.03, rot=26))

    # ---- Transit band outside the zodiac: rose-gold glyphs, tick at exact degree ----
    if has_transits:
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r_t_outer}" fill="none" stroke="url(#gold)" '
                      f'stroke-width="0.3" opacity="0.45"/>')
        t_display = spread_glyph_angles(t_lon, 7.5)
        for name, lon in t_lon.items():
            a = angle_for(lon)
            # hollow rose marker where the transit touches the aspect circle
            m = ring_point["t_" + name]
            parts.append(f'<circle cx="{m[0]:.1f}" cy="{m[1]:.1f}" r="1.1" fill="{BG}" '
                          f'stroke="{TRANSIT_TONE}" stroke-width="0.3"/>')
            t1, t2 = polar(cx, cy, r_outer, a), polar(cx, cy, r_outer + 3.5, a)
            parts.append(f'<line x1="{t1[0]:.1f}" y1="{t1[1]:.1f}" x2="{t2[0]:.1f}" y2="{t2[1]:.1f}" '
                          f'stroke="{TRANSIT_TONE}" stroke-width="0.4" opacity="0.8"/>')
            lead = polar(cx, cy, r_t_glyph - 5, angle_for(t_display[name]))
            parts.append(f'<line x1="{t2[0]:.1f}" y1="{t2[1]:.1f}" x2="{lead[0]:.1f}" y2="{lead[1]:.1f}" '
                          f'stroke="{TRANSIT_TONE_SOFT}" stroke-width="0.18" opacity="0.5"/>')
            g = polar(cx, cy, r_t_glyph, angle_for(t_display[name]))
            d = polar(cx, cy, r_t_degree, angle_for(t_display[name]))
            tp = transit_data["planets"][name]
            parts.append(svg_text(PLANET_GLYPHS.get(name, "?"), g[0], g[1], 8.5, "symbols", TRANSIT_TONE))
            parts.append(retro_badge(*badge_anchor(cx, cy, r_t_glyph, angle_for(t_display[name]), True), name, tp))
            parts.append(svg_text(tp["degree_display"].replace("'", "′"), d[0], d[1], 4.0, "mono",
                                  TRANSIT_TONE, 0.85, letter_spacing=-0.1))
        # transit date: bottom-right corner (opposite the owner's name)
        for k, line in enumerate(reversed(transit_data.get("label_lines") or [transit_data.get("label", "")])):
            parts.append(svg_text(line, 400 + M - 10, 400 + M - 12 - k * 8, 4.6 if k else 5.4,
                                  "mono" if k else "mono_md", TRANSIT_TONE, 0.7 if k else 0.95,
                                  anchor="end", baseline="central"))

    # ---- Centre emblem: young crescent moon with star-glints, drawn ABOVE the aspect lines ----
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="22" fill="{BG}"/>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="22" fill="url(#halo)"/>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="22" fill="none" stroke="url(#gold)" stroke-width="0.35" opacity="0.75"/>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="19.6" fill="none" stroke="url(#gold)" stroke-width="0.2" '
                  f'opacity="0.4" stroke-dasharray="0.25,1.35"/>')
    # figure edges stay unbroken: they cross the emblem disc (still UNDER the moon itself),
    # so a nodal T-square base or any opposition through the centre reads as one closed line
    if fig_lines:
        parts.append(f'<clipPath id="emblemClip"><circle cx="{cx}" cy="{cy}" r="22.5"/></clipPath><g clip-path="url(#emblemClip)">')
        for x1, y1, x2, y2, core, facet, width in fig_lines:
            seg = f'x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}"'
            parts.append(f'<line {seg} stroke="{core}" stroke-width="{width}" opacity="0.8"/>')
            parts.append(f'<line {seg} stroke="{facet}" stroke-width="{width * 0.28:.2f}" opacity="0.35"/>')
        parts.append('</g>')
    R, R2 = 10.5, 12.2  # outer lit edge, inner shadow edge -> a slender waxing crescent
    crescent = (f'M0,{-R} A{R},{R} 0 0,1 0,{R} A{R2},{R2} 0 0,0 0,{-R} Z')
    moon_tf = f'translate({cx + 2.2},{cy}) rotate(-28)'
    parts.append(f'<path d="{crescent}" transform="{moon_tf}" fill="#F1D78E" opacity="0.55" filter="url(#softglow)"/>')
    parts.append(f'<path d="{crescent}" transform="{moon_tf}" fill="url(#moonGold)"/>')
    parts.append(f'<path d="{crescent}" transform="{moon_tf}" fill="none" stroke="#FFF6DC" stroke-width="0.15" opacity="0.8"/>')
    parts.append(_sparkle(cx - 6.2, cy - 5.0, 3.0, "#FFF6DC", 0.95))
    parts.append(_sparkle(cx - 2.6, cy + 6.4, 1.4, "#F4E4BC", 0.85))
    parts.append(_sparkle(cx - 9.6, cy + 2.2, 0.9, "#F4E4BC", 0.7))
    parts.append(f'<circle cx="{cx - 3.6:.1f}" cy="{cy - 9.6:.1f}" r="0.35" fill="#FFF6DC" opacity="0.8"/>')
    parts.append(f'<circle cx="{cx - 10.8:.1f}" cy="{cy - 4.0:.1f}" r="0.3" fill="#FFF6DC" opacity="0.6"/>')

    # ---- Zodiac glyphs: smaller, finer, no background ----
    for i, sign in enumerate(ZODIAC_SIGNS):
        p = polar(cx, cy, (r_outer + r_inner) / 2, angle_for(i * 30 + 15))
        parts.append(svg_text(ZODIAC_GLYPHS[sign], p[0], p[1], 8, "symbols", "#EAD9AA", 0.92))

    # ---- Planet glyphs on ONE ring, degree label directly beneath (toward centre) ----
    for name in planet_longitude:
        a = angle_for(display_longitude[name])
        g = polar(cx, cy, r_glyph, a)
        d = polar(cx, cy, r_degree, a)
        parts.append(svg_text(PLANET_GLYPHS.get(name, "?"), g[0], g[1], 9, "symbols", PLANET_TONE))
        parts.append(retro_badge(*badge_anchor(cx, cy, r_glyph, a, False), name, natal_data["planets"][name]))
        deg_txt = natal_data["planets"][name]["degree_display"].replace("'", "′")
        if not tk and name == "moon":
            deg_txt = "≈" + deg_txt.split("°")[0] + "°"   # the Moon's exact degree is unknown
        parts.append(svg_text(deg_txt, d[0], d[1], 4.3,
                              "mono", DEGREE_TONE, letter_spacing=-0.15))

    # ---- House labels: crisp, fine, semi-transparent antique gold (no haze) ----
    for i, (a, h) in cusp_angles.items():
        is_angle = tk and i in ANGLE_LABELS
        if not tk:   # solar houses: numerals only, placed mid-sign, no degrees
            p = polar(cx, cy, r_house_label, a + 15)
            parts.append(svg_text(ROMAN_NUMERALS[i - 1], p[0], p[1], 4.6, "mono_md", ANTIQUE_GOLD, 0.7))
            continue
        p = polar(cx, cy, r_house_label, a)
        label = ANGLE_LABELS[i] if is_angle else ROMAN_NUMERALS[i - 1]
        size = 5.2 if is_angle else 4.8
        ly, dy = p[1] - 2.9, p[1] + 3.4
        parts.append(svg_text(label, p[0], ly, size, "mono_md", ANTIQUE_GOLD, 0.95 if is_angle else 0.85))
        parts.append(svg_text(format_dms(h["degree_in_sign"]).replace("'", "′"), p[0], dy, 3.9, "mono",
                              ANTIQUE_GOLD, 0.75, letter_spacing=-0.1))

    parts.append('</svg>')
    return "".join(parts)


@app.route("/chart-svg", methods=["POST"])
def chart_svg():
    """
    JSON body as /natal (+ optional time_known, name, details, lang).
    Returns the rendered chart as raw SVG for Make -> Cloudinary -> Telegram.
    """
    return Response(render_chart_svg(natal_from_request(request.get_json())), mimetype="image/svg+xml")



@app.route("/test-chart-svg", methods=["GET"])
def test_chart_svg():
    """
    Browser test. Example:
    /test-chart-svg?year=1984&month=12&day=20&hour=16&minute=6&timezone_name=Europe/Kyiv&latitude=49.57&longitude=25.60
    Optional: &name=...&details=...&lang=en&time_known=false
    """
    return Response(render_chart_svg(natal_from_request(request.args)), mimetype="image/svg+xml")



MOON_PHASES = [  # (key, UA, EN) by Sun-Moon angle, 45 degrees each
    ("new", "Молодик", "New Moon"), ("crescent", "Молодий серп", "Crescent"),
    ("first_quarter", "Перша чверть", "First Quarter"), ("gibbous", "Місяць, що росте", "Gibbous"),
    ("full", "Повня", "Full Moon"), ("disseminating", "Місяць, що спадає", "Disseminating"),
    ("last_quarter", "Остання чверть", "Last Quarter"), ("balsamic", "Бальзамічний місяць", "Balsamic"),
]


def moon_phase(sun_lon, moon_lon):
    angle = (moon_lon - sun_lon) % 360
    key, ua, en = MOON_PHASES[int(angle // 45) % 8]
    return {"key": key, "name_ua": ua, "name_en": en, "angle": round(angle, 1)}


def solar_houses(sun_lon):
    """Whole-sign 'solar houses' for an unknown birth time: the Sun's sign is the 1st house."""
    first = int(sun_lon // 30)
    houses = {}
    for i in range(12):
        sign = ZODIAC_SIGNS[(first + i) % 12]
        houses[f"house_{i + 1}"] = {"longitude": ((first + i) % 12) * 30.0, "sign": sign, "degree_in_sign": 0.0}
    return houses


def _aspect_kind(a_name, a_lon, b_name, b_lon):
    found = calculate_aspects({a_name: a_lon, b_name: b_lon})
    return found[0]["aspect"] if found else None


def build_natal_data(year, month, day, hour, minute, timezone_name, latitude, longitude,
                     time_known=True, name="", details="", lang="ua"):
    """
    Everything the chart renderer and the bot need for one natal chart.
    time_known=False -> chart for local noon, solar (whole-sign) houses, no ASC/MC,
    the Moon shown as the arc it travelled that day, and only Moon aspects that
    hold for the whole day. The birth Moon phase replaces the Ascendant in the Passport.
    """
    if not time_known:
        hour, minute = 12, 0
    jd = local_time_to_julian_day(year, month, day, hour, minute, timezone_name)
    planets = calculate_planets(jd)
    sun_lon, moon_lon = planets["sun"]["longitude"], planets["moon"]["longitude"]
    points = {n: p["longitude"] for n, p in planets.items()}
    data = {"time_known": bool(time_known), "owner_name": name, "owner_details": details, "lang": lang,
            "planets": planets, "birth_moon_phase": moon_phase(sun_lon, moon_lon)}

    if time_known:
        house_data = calculate_houses(jd, latitude, longitude)
        points["ascendant"] = house_data["ascendant"]["degree"] + ZODIAC_SIGNS.index(house_data["ascendant"]["sign"]) * 30
        points["midheaven"] = house_data["midheaven"]["degree"] + ZODIAC_SIGNS.index(house_data["midheaven"]["sign"]) * 30
        data.update(houses=house_data["houses"], ascendant=house_data["ascendant"],
                    midheaven=house_data["midheaven"], aspects=calculate_aspects(points))
    else:
        m0 = calculate_planets(jd - 0.5)["moon"]["longitude"]   # local midnight before
        m1 = calculate_planets(jd + 0.5)["moon"]["longitude"]   # local midnight after
        aspects = []
        for a in calculate_aspects(points):
            if "moon" in (a["point_a"], a["point_b"]):
                other = a["point_b"] if a["point_a"] == "moon" else a["point_a"]
                if not (_aspect_kind("moon", m0, other, points[other]) == a["aspect"]
                        == _aspect_kind("moon", m1, other, points[other])):
                    continue   # this Moon aspect is not certain for the whole day
            aspects.append(a)
        s0, s1 = degree_to_sign(m0)["sign"], degree_to_sign(m1)["sign"]
        data.update(houses=solar_houses(sun_lon),
                    ascendant={"sign": ZODIAC_SIGNS[int(sun_lon // 30)], "degree": 0.0, "degree_display": "0°00'"},
                    midheaven=None, aspects=aspects,
                    moon_range={"start": round(m0, 4), "end": round(m1, 4),
                                "signs": [s0] if s0 == s1 else [s0, s1]})
    data["figures"] = find_figures(data["aspects"], set(planets))[1]
    return data


def natal_from_request(d):
    """Shared parser for JSON bodies and query strings."""
    tk = str(d.get("time_known", "true")).lower() not in ("false", "0", "no", "ні")
    return build_natal_data(int(d["year"]), int(d["month"]), int(d["day"]),
                            int(d.get("hour", 12)), int(d.get("minute", 0)), d["timezone_name"],
                            float(d["latitude"]), float(d["longitude"]), time_known=tk,
                            name=d.get("name", ""), details=d.get("details", ""), lang=d.get("lang", "ua"))


def build_transit_data(natal_planets, t=None, lang="ua"):
    """
    t: optional dict {year, month, day, hour, minute, timezone_name}.
    Missing -> this exact moment (UTC). Returns planets, transit->natal aspects and a caption.
    """
    if t and t.get("year"):
        tz = t.get("timezone_name") or "UTC"
        jd = local_time_to_julian_day(int(t["year"]), int(t["month"]), int(t["day"]),
                                      int(t.get("hour", 12)), int(t.get("minute", 0)), tz)
        label = f'Транзити · {int(t["day"]):02d}.{int(t["month"]):02d}.{int(t["year"])} ' \
                f'{int(t.get("hour", 12)):02d}:{int(t.get("minute", 0)):02d}'
    else:
        now = datetime.now(timezone.utc)
        jd = swe.julday(now.year, now.month, now.day, now.hour + now.minute / 60.0)
        label = f'Транзити · {now:%d.%m.%Y %H:%M} UTC'
    planets = calculate_planets(jd)
    tight = sorted((a for a in transit_natal_aspects(planets, natal_planets) if a["orb"] <= TRANSIT_TIGHT_ORB),
                   key=lambda a: a["orb"])[:TRANSIT_MAX_ASPECTS]
    if lang == "en":
        label = label.replace("Транзити", "Transits")
    return {"planets": planets, "aspects": tight, "label": label}


@app.route("/transit-chart-svg", methods=["POST"])
def transit_chart_svg():
    """
    Body: the same natal fields as /chart-svg, plus optional
    "transit": {"year", "month", "day", "hour", "minute", "timezone_name"}.
    Without "transit" the chart shows the sky right now.
    """
    data = request.get_json()
    natal = natal_from_request(data)
    transit = build_transit_data(natal["planets"], data.get("transit"), lang=data.get("lang", "ua"))
    return Response(render_chart_svg(natal, transit), mimetype="image/svg+xml")


@app.route("/test-transit-chart-svg", methods=["GET"])
def test_transit_chart_svg():
    """
    Natal params as in /test-chart-svg, plus optional t_year, t_month, t_day,
    t_hour, t_minute, t_timezone_name (default: now).
    """
    a = request.args
    natal = natal_from_request(a)
    t = {k[2:]: a[k] for k in a if k.startswith("t_")}
    transit = build_transit_data(natal["planets"], t or None, lang=a.get("lang", "ua"))
    return Response(render_chart_svg(natal, transit), mimetype="image/svg+xml")


@app.route("/natal", methods=["POST"])
def natal_chart():
    """
    Expects JSON: year, month, day, hour, minute, timezone_name, latitude, longitude
    (+ optional time_known: false when the birth time is unknown).
    Returns planets, houses, ascendant, midheaven, aspects, figures, birth_moon_phase
    and, for an unknown time, moon_range.
    """
    return jsonify(natal_from_request(request.get_json()))



@app.route("/transits", methods=["POST"])
def transits():
    """
    Expects JSON like:
    { "year": 2026, "month": 9, "day": 17, "hour": 15, "minute": 0, "utc_offset": 1.0 }
    Returns current planetary positions for that moment - no birth
    place needed, since transits are just "where are the planets right now",
    not tied to a house system.
    If no body is sent at all, defaults to this exact moment (UTC).
    """
    data = request.get_json(silent=True) or {}
    if data:
        jd = to_julian_day(
            data["year"], data["month"], data["day"],
            data["hour"], data["minute"], data.get("utc_offset", 0)
        )
    else:
        now = datetime.now(timezone.utc)
        jd = swe.julday(now.year, now.month, now.day, now.hour + now.minute / 60.0)

    planets = calculate_planets(jd)
    return jsonify({"planets": planets})


@app.route("/test-transits", methods=["GET"])
def test_transits():
    """
    Temporary browser-friendly test route - shows current planetary
    positions right now, no POST request needed. Useful for a quick
    sanity check straight from a browser address bar.
    """
    now = datetime.now(timezone.utc)
    jd = swe.julday(now.year, now.month, now.day, now.hour + now.minute / 60.0)
    planets = calculate_planets(jd)
    return jsonify({"checked_at_utc": now.isoformat(), "planets": planets})


@app.route("/test-natal", methods=["GET"])
def test_natal():
    """
    Temporary browser-friendly test route for the natal chart.
    Pass a valid IANA timezone name directly (e.g. "Europe/Kyiv") -
    look it up on Wikipedia's "List of tz database time zones" if unsure.
    Example:
    /test-natal?year=1984&month=12&day=20&hour=16&minute=6&timezone_name=Europe/Kyiv&latitude=49.57&longitude=25.60
    """
    year = int(request.args.get("year"))
    month = int(request.args.get("month"))
    day = int(request.args.get("day"))
    hour = int(request.args.get("hour"))
    minute = int(request.args.get("minute"))
    timezone_name = request.args.get("timezone_name")
    latitude = float(request.args.get("latitude"))
    longitude = float(request.args.get("longitude"))

    jd = local_time_to_julian_day(year, month, day, hour, minute, timezone_name)
    planets = calculate_planets(jd)
    house_data = calculate_houses(jd, latitude, longitude)
    aspect_points = {name: p["longitude"] for name, p in planets.items()}
    aspect_points["ascendant"] = house_data["ascendant"]["degree"] + ZODIAC_SIGNS.index(house_data["ascendant"]["sign"]) * 30
    aspect_points["midheaven"] = house_data["midheaven"]["degree"] + ZODIAC_SIGNS.index(house_data["midheaven"]["sign"]) * 30
    aspects = calculate_aspects(aspect_points)

    return jsonify({
        "planets": planets,
        "houses": house_data["houses"],
        "ascendant": house_data["ascendant"],
        "midheaven": house_data["midheaven"],
        "aspects": aspects,
        "figures": find_figures(aspects, set(planets))[1]
    })


@app.route("/solar-return", methods=["POST"])
def solar_return():
    """
    Expects JSON like:
    {
      "year": 1984, "month": 12, "day": 20, "hour": 16, "minute": 6,
      "timezone_name": "Europe/Kyiv",
      "latitude": 49.57, "longitude": 25.60,
      "target_year": 2026
    }
    Returns the chart for the exact moment the Sun returns to its natal
    degree in target_year - the "solar return" / astrological birthday chart.
    Uses the birth location for houses by default; swap latitude/longitude
    for the person's current location if that convention is preferred.
    """
    data = request.get_json()
    natal_jd = local_time_to_julian_day(
        data["year"], data["month"], data["day"],
        data["hour"], data["minute"], data["timezone_name"]
    )
    natal_sun_longitude = calculate_planets(natal_jd)["sun"]["longitude"]
    sr_jd = find_solar_return_jd(natal_sun_longitude, data["target_year"], data["month"], data["day"])
    planets = calculate_planets(sr_jd)
    house_data = calculate_houses(sr_jd, data["latitude"], data["longitude"])

    return jsonify({
        "planets": planets,
        "houses": house_data["houses"],
        "ascendant": house_data["ascendant"],
        "midheaven": house_data["midheaven"]
    })


@app.route("/test-solar-return", methods=["GET"])
def test_solar_return():
    """
    Browser-friendly test route. Example:
    /test-solar-return?year=1984&month=12&day=20&hour=16&minute=6&timezone_name=Europe/Kyiv&latitude=49.57&longitude=25.60&target_year=2026
    """
    year = int(request.args.get("year"))
    month = int(request.args.get("month"))
    day = int(request.args.get("day"))
    hour = int(request.args.get("hour"))
    minute = int(request.args.get("minute"))
    timezone_name = request.args.get("timezone_name")
    latitude = float(request.args.get("latitude"))
    longitude = float(request.args.get("longitude"))
    target_year = int(request.args.get("target_year"))

    natal_jd = local_time_to_julian_day(year, month, day, hour, minute, timezone_name)
    natal_sun_longitude = calculate_planets(natal_jd)["sun"]["longitude"]
    sr_jd = find_solar_return_jd(natal_sun_longitude, target_year, month, day)
    planets = calculate_planets(sr_jd)
    house_data = calculate_houses(sr_jd, latitude, longitude)

    return jsonify({
        "planets": planets,
        "houses": house_data["houses"],
        "ascendant": house_data["ascendant"],
        "midheaven": house_data["midheaven"]
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
