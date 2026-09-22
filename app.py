from flask import Flask, request, jsonify, Response
import swisseph as swe
import math
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

# ---- Chart rendering constants (agreed visual design: black bg, emerald-silver) ----
SIGN_COLORS = {
    "Aries": "#3A1420", "Leo": "#3A1420", "Sagittarius": "#3A1420",        # deep wine (fire)
    "Taurus": "#16241C", "Virgo": "#16241C", "Capricorn": "#16241C",       # deep forest (earth)
    "Gemini": "#161B33", "Libra": "#161B33", "Aquarius": "#161B33",        # deep indigo (air)
    "Cancer": "#241730", "Scorpio": "#241730", "Pisces": "#241730",        # deep plum (water)
}
GOLD_GRAD_STOPS = ("#D4AF37", "#F4E4BC")
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
# Major aspects = solid bordeaux-plum "jewel" lines (fire-wedge hue family);
# soft / minor aspects = dashed emerald (earth) or sapphire (air) with finer glitter.
BORDEAUX = ("#56122B", "#94445F")
EMERALD = ("#1E5C45", "#63A58A")
SAPPHIRE = ("#294583", "#738CC4")
ASPECT_STYLES = {
    "conjunction": ("#C9A94E", "#F4E4BC", None, 0.25, 0.0),
    "opposition": (BORDEAUX[0], BORDEAUX[1], None, 0.42, 1.0),
    "square": (BORDEAUX[0], BORDEAUX[1], None, 0.42, 1.0),
    "trine": (BORDEAUX[0], BORDEAUX[1], None, 0.38, 1.0),
    "sextile": (EMERALD[0], EMERALD[1], "1.2,1.5", 0.28, 0.6),
    "semisextile": (SAPPHIRE[0], SAPPHIRE[1], "1.2,1.5", 0.25, 0.5),
    "quincunx": (SAPPHIRE[0], SAPPHIRE[1], "1.2,1.5", 0.25, 0.5),
}
SYMBOL_FONT = "'Noto Sans Symbols 2','Noto Sans Symbols','Segoe UI Symbol','DejaVu Sans',sans-serif"
LABEL_FONT = "'Cormorant Garamond','Cormorant','Times New Roman',serif"
ANTIQUE_GOLD = "#C9A94E"


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
        sign_info = degree_to_sign(longitude)
        positions[name] = {
            "longitude": round(longitude, 4),
            "sign": sign_info["sign"],
            "degree_in_sign": sign_info["degree"],
            "degree_display": sign_info["degree_display"],
            "retrograde": is_retrograde
        }

    if "north_node" in positions:
        south_longitude = (positions["north_node"]["longitude"] + 180) % 360
        sign_info = degree_to_sign(south_longitude)
        positions["south_node"] = {
            "longitude": round(south_longitude, 4),
            "sign": sign_info["sign"],
            "degree_in_sign": sign_info["degree"],
            "degree_display": sign_info["degree_display"],
            "retrograde": positions["north_node"]["retrograde"]
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


def render_chart_svg(natal_data):
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
    r_glyph, r_degree, r_leader_end = 146, 133.5, 126
    min_sep = 8.0  # degrees between neighbouring planet glyphs

    asc_sign_index = ZODIAC_SIGNS.index(natal_data["ascendant"]["sign"])
    asc_longitude = asc_sign_index * 30 + natal_data["ascendant"]["degree"]

    def angle_for(longitude):
        return (180 + (longitude - asc_longitude)) % 360

    # extra margin in the viewBox so outer labels (Asc / Dsc / MC / IC) are never cut off
    parts = ['<svg viewBox="-20 -20 440 440" width="880" height="880" xmlns="http://www.w3.org/2000/svg">']
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
        '<filter id="glow" filterUnits="userSpaceOnUse" x="-20" y="-20" width="440" height="440">'
        '<feGaussianBlur stdDeviation="0.9"/></filter>'
        '<filter id="softglow" filterUnits="userSpaceOnUse" x="-20" y="-20" width="440" height="440">'
        '<feGaussianBlur stdDeviation="1.6"/></filter>'
        '<filter id="mist" filterUnits="userSpaceOnUse" x="-20" y="-20" width="440" height="440">'
        '<feGaussianBlur stdDeviation="1.3"/></filter>'
        '<radialGradient id="fog" cx="0.5" cy="0.5" r="0.5">'
        '<stop offset="0" stop-color="#E8CF8A" stop-opacity="0.13"/>'
        '<stop offset="0.55" stop-color="#C9A94E" stop-opacity="0.05"/>'
        '<stop offset="1" stop-color="#C9A94E" stop-opacity="0"/></radialGradient>'
        '</defs>'
    )
    parts.append('<rect x="-20" y="-20" width="440" height="440" fill="#050303"/>')

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
            f'fill="{SIGN_COLORS[sign]}" opacity="0.5"/>'
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
        is_angle = i in ANGLE_LABELS
        p_over = polar(cx, cy, r_outer + 6, a)
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
    for asp in natal_data.get("aspects", []):
        a_name, b_name = asp["point_a"], asp["point_b"]
        if a_name not in ring_point or b_name not in ring_point:
            continue
        core, facet, dash, width, sparkle = ASPECT_STYLES.get(
            asp["aspect"], (SAPPHIRE[0], SAPPHIRE[1], "1.2,1.5", 0.25, 0.5))
        (x1, y1), (x2, y2) = ring_point[a_name], ring_point[b_name]
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        line = f'x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}"'
        if sparkle:
            parts.append(f'<line {line} stroke="{core}" stroke-width="{width * 3.0:.2f}" '
                          f'opacity="0.35" filter="url(#glow)"{dash_attr}/>')
        parts.append(f'<line {line} stroke="{core}" stroke-width="{width}" opacity="0.95"{dash_attr}/>')
        if sparkle:
            parts.append(f'<line {line} stroke="{facet}" stroke-width="{width * 0.28:.2f}" opacity="0.5"{dash_attr}/>')
            # deterministic glitter: same chart -> same sparkles every render
            rng = random.Random(f"{a_name}-{b_name}-{asp['aspect']}")
            length = math.hypot(x2 - x1, y2 - y1)
            count = max(2, int(length / 16 * sparkle))
            for _ in range(count):
                t = rng.uniform(0.08, 0.92)
                sx, sy = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
                size = rng.uniform(0.5, 1.25) * (1 if not dash else 0.8)
                tint = rng.choice(["#F6E7C8", facet, "#FFF4E0"])
                parts.append(_sparkle(sx, sy, size, tint, rng.uniform(0.45, 0.85)))

    # ---- Exact-degree dots + thin leader to the (possibly shifted) glyph ----
    for name, lon in planet_longitude.items():
        dot = ring_point[name]
        lead = polar(cx, cy, r_leader_end, angle_for(display_longitude[name]))
        parts.append(f'<line x1="{dot[0]:.1f}" y1="{dot[1]:.1f}" x2="{lead[0]:.1f}" y2="{lead[1]:.1f}" '
                      f'stroke="{ANTIQUE_GOLD}" stroke-width="0.18" opacity="0.45"/>')
        parts.append(f'<circle cx="{dot[0]:.1f}" cy="{dot[1]:.1f}" r="1.2" fill="#F4E4BC"/>')

    # ---- Centre emblem: young crescent moon with star-glints, drawn ABOVE the aspect lines ----
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="22" fill="#050303"/>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="22" fill="url(#halo)"/>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="22" fill="none" stroke="url(#gold)" stroke-width="0.35" opacity="0.75"/>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="19.6" fill="none" stroke="url(#gold)" stroke-width="0.2" '
                  f'opacity="0.4" stroke-dasharray="0.25,1.35"/>')
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
        parts.append(f'<text x="{p[0]:.1f}" y="{p[1]:.1f}" font-family="{SYMBOL_FONT}" font-size="8.5" '
                      f'font-weight="300" fill="#EAD9AA" opacity="0.88" text-anchor="middle" '
                      f'dominant-baseline="central">{ZODIAC_GLYPHS[sign]}</text>')

    # ---- Planet glyphs on ONE ring, degree label directly beneath (toward centre) ----
    for name in planet_longitude:
        a = angle_for(display_longitude[name])
        g = polar(cx, cy, r_glyph, a)
        d = polar(cx, cy, r_degree, a)
        parts.append(f'<text x="{g[0]:.1f}" y="{g[1]:.1f}" font-family="{SYMBOL_FONT}" font-size="9" '
                      f'fill="#F4E4BC" text-anchor="middle" dominant-baseline="central">'
                      f'{PLANET_GLYPHS.get(name, "?")}</text>')
        parts.append(f'<text x="{d[0]:.1f}" y="{d[1]:.1f}" font-family="{LABEL_FONT}" font-size="4.2" '
                      f'fill="#B8A77A" text-anchor="middle" dominant-baseline="central">'
                      f'{natal_data["planets"][name]["degree_display"]}</text>')

    # ---- House labels: stacked number + degree, wrapped in the same golden mist as the moon ----
    for i, (a, h) in cusp_angles.items():
        is_angle = i in ANGLE_LABELS
        p = polar(cx, cy, r_outer + 15, a)
        label = ANGLE_LABELS[i] if is_angle else ROMAN_NUMERALS[i - 1]
        size = 6.2 if is_angle else 5.8
        ly, dy = p[1] - 2.9, p[1] + 3.6
        deg_txt = format_dms(h["degree_in_sign"])
        # soft fog cloud behind the pair
        parts.append(f'<ellipse cx="{p[0]:.1f}" cy="{p[1]:.1f}" rx="12" ry="9" fill="url(#fog)"/>')
        # blurred glowing copy (the haze), then the crisp text on top
        parts.append(f'<text x="{p[0]:.1f}" y="{ly:.1f}" font-family="{LABEL_FONT}" font-size="{size}" '
                      f'fill="#F1D78E" stroke="#F1D78E" stroke-width="0.6" opacity="0.55" filter="url(#mist)" '
                      f'text-anchor="middle" dominant-baseline="central">{label}</text>')
        parts.append(f'<text x="{p[0]:.1f}" y="{ly:.1f}" font-family="{LABEL_FONT}" font-size="{size}" '
                      f'fill="{ANTIQUE_GOLD}" opacity="{0.9 if is_angle else 0.78}" text-anchor="middle" '
                      f'dominant-baseline="central">{label}</text>')
        parts.append(f'<text x="{p[0]:.1f}" y="{dy:.1f}" font-family="{LABEL_FONT}" font-size="3.9" '
                      f'fill="#F1D78E" opacity="0.35" filter="url(#mist)" text-anchor="middle" '
                      f'dominant-baseline="central">{deg_txt}</text>')
        parts.append(f'<text x="{p[0]:.1f}" y="{dy:.1f}" font-family="{LABEL_FONT}" font-size="3.9" '
                      f'fill="{ANTIQUE_GOLD}" opacity="0.6" text-anchor="middle" dominant-baseline="central">'
                      f'{deg_txt}</text>')
        # one faint star-glint beside each numeral, echoing the stars by the moon
        half_w = len(label) * size * 0.3
        parts.append(_sparkle(p[0] + half_w + 1.8, ly - 2.6, 0.9 if is_angle else 0.75, "#F6E7C8", 0.6))

    parts.append('</svg>')
    return "".join(parts)


@app.route("/chart-svg", methods=["POST"])
def chart_svg():
    """
    Expects the same JSON body as /natal. Returns the rendered chart
    as raw SVG (content-type image/svg+xml) - Make can pass this
    straight to an SVG-to-PNG conversion step (e.g. Cloudinary) before
    sending it to the user via Telegram.
    """
    data = request.get_json()
    jd = local_time_to_julian_day(
        data["year"], data["month"], data["day"],
        data["hour"], data["minute"], data["timezone_name"]
    )
    planets = calculate_planets(jd)
    house_data = calculate_houses(jd, data["latitude"], data["longitude"])
    aspect_points = {name: p["longitude"] for name, p in planets.items()}
    aspect_points["ascendant"] = house_data["ascendant"]["degree"] + ZODIAC_SIGNS.index(house_data["ascendant"]["sign"]) * 30
    aspect_points["midheaven"] = house_data["midheaven"]["degree"] + ZODIAC_SIGNS.index(house_data["midheaven"]["sign"]) * 30
    aspects = calculate_aspects(aspect_points)

    natal_data = {
        "planets": planets,
        "houses": house_data["houses"],
        "ascendant": house_data["ascendant"],
        "midheaven": house_data["midheaven"],
        "aspects": aspects
    }
    svg = render_chart_svg(natal_data)
    return Response(svg, mimetype="image/svg+xml")


@app.route("/test-chart-svg", methods=["GET"])
def test_chart_svg():
    """
    Browser-friendly test route - opens directly as an image. Example:
    /test-chart-svg?year=1984&month=12&day=20&hour=16&minute=6&timezone_name=Europe/Kyiv&latitude=49.57&longitude=25.60
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

    natal_data = {
        "planets": planets,
        "houses": house_data["houses"],
        "ascendant": house_data["ascendant"],
        "midheaven": house_data["midheaven"],
        "aspects": aspects
    }
    svg = render_chart_svg(natal_data)
    return Response(svg, mimetype="image/svg+xml")


@app.route("/natal", methods=["POST"])
def natal_chart():
    """
    Expects JSON like:
    {
      "year": 1995, "month": 6, "day": 14,
      "hour": 14, "minute": 30,
      "timezone_name": "Europe/Kyiv",
      "latitude": 50.4501, "longitude": 30.5234
    }
    timezone_name must be a valid IANA name (e.g. "Europe/Kyiv") - Make
    resolves this from the birth place via geocoding before calling us,
    so historical DST/decree-time rules are handled correctly.
    """
    data = request.get_json()
    jd = local_time_to_julian_day(
        data["year"], data["month"], data["day"],
        data["hour"], data["minute"], data["timezone_name"]
    )
    planets = calculate_planets(jd)
    house_data = calculate_houses(jd, data["latitude"], data["longitude"])
    aspect_points = {name: p["longitude"] for name, p in planets.items()}
    aspect_points["ascendant"] = house_data["ascendant"]["degree"] + ZODIAC_SIGNS.index(house_data["ascendant"]["sign"]) * 30
    aspect_points["midheaven"] = house_data["midheaven"]["degree"] + ZODIAC_SIGNS.index(house_data["midheaven"]["sign"]) * 30
    aspects = calculate_aspects(aspect_points)

    return jsonify({
        "planets": planets,
        "houses": house_data["houses"],
        "ascendant": house_data["ascendant"],
        "midheaven": house_data["midheaven"],
        "aspects": aspects
    })


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
        "aspects": aspects
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
