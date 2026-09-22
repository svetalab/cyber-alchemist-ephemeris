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
    "sextile": 60,
    "square": 90,
    "trine": 120,
    "opposition": 180,
}

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
ELEMENT_COLORS = {
    "Aries": "#D9A441", "Leo": "#D9A441", "Sagittarius": "#D9A441",       # Fire
    "Taurus": "#2F6B4F", "Virgo": "#2F6B4F", "Capricorn": "#2F6B4F",      # Earth
    "Gemini": "#B8C9D9", "Libra": "#B8C9D9", "Aquarius": "#B8C9D9",       # Air
    "Cancer": "#3E6E7A", "Scorpio": "#3E6E7A", "Pisces": "#3E6E7A",       # Water
}
ZODIAC_GLYPHS = {
    "Aries": "&#9800;", "Taurus": "&#9801;", "Gemini": "&#9802;", "Cancer": "&#9803;",
    "Leo": "&#9804;", "Virgo": "&#9805;", "Libra": "&#9806;", "Scorpio": "&#9807;",
    "Sagittarius": "&#9808;", "Capricorn": "&#9809;", "Aquarius": "&#9810;", "Pisces": "&#9811;",
}
PLANET_GLYPHS = {
    "sun": "&#9737;", "moon": "&#9789;", "mercury": "&#9791;", "venus": "&#9792;",
    "mars": "&#9794;", "jupiter": "&#9795;", "saturn": "&#9796;", "uranus": "&#9797;",
    "neptune": "&#9798;", "pluto": "&#9799;", "north_node": "&#9738;",
    "south_node": "&#9739;", "lilith": "&#9912;",
}
ASPECT_COLORS = {
    "conjunction": "#D9F0E6",   # bright silver
    "sextile": "#5FA98F",       # soft teal-green
    "square": "#C77B6B",        # muted rose
    "trine": "#3F9268",         # emerald green
    "opposition": "#B0562F",    # deep amber-red
}


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
                orb = abs(diff - exact_angle)
                if orb <= orb_limit:
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
ANGLE_LABELS = {1: "ASC", 4: "IC", 7: "DSC", 10: "MC"}


def polar(cx, cy, r, angle_deg):
    """Standard polar-to-cartesian helper, oriented so the chart rotates
    the correct astrological direction (counter-clockwise from the Ascendant)."""
    rad = math.radians(angle_deg)
    return cx + r * math.cos(rad), cy - r * math.sin(rad)


def render_chart_svg(natal_data):
    """
    Renders the agreed chart design (black background, emerald-silver
    accents, element-colored zodiac ring) plus the Chronos-style layout
    she asked to match/beat: Roman numeral houses, labeled angles
    (ASC/DSC/MC/IC), planets shown both as inner glyphs AND as exact
    marker dots on the inner ring circumference (with a thin leader
    line connecting the two), aspect lines drawn between those ring
    dots, and a solid, fully-filled element-colored zodiac band.
    """
    cx, cy = 200, 200
    r_outer, r_inner, r_planet_a, r_planet_b = 185, 160, 128, 105

    asc_sign_index = ZODIAC_SIGNS.index(natal_data["ascendant"]["sign"])
    asc_longitude = asc_sign_index * 30 + natal_data["ascendant"]["degree"]

    def angle_for(longitude):
        return (180 + (longitude - asc_longitude)) % 360

    parts = ['<svg viewBox="0 0 400 400" width="800" height="800" xmlns="http://www.w3.org/2000/svg">']
    parts.append('<defs><linearGradient id="es" x1="0" y1="0" x2="1" y2="1">'
                  '<stop offset="0" stop-color="#0F3D30"/><stop offset="0.5" stop-color="#5FA98F"/>'
                  '<stop offset="1" stop-color="#D9F0E6"/></linearGradient></defs>')
    parts.append('<rect x="0" y="0" width="400" height="400" fill="#000000"/>')

    # Solid element-colored zodiac band, filling exactly between r_inner and r_outer
    for i, sign in enumerate(ZODIAC_SIGNS):
        a1, a2 = angle_for(i * 30), angle_for((i + 1) * 30)
        p1i, p2i = polar(cx, cy, r_inner, a1), polar(cx, cy, r_inner, a2)
        p1o, p2o = polar(cx, cy, r_outer, a1), polar(cx, cy, r_outer, a2)
        parts.append(f'<path d="M{p1i[0]:.2f},{p1i[1]:.2f} L{p2i[0]:.2f},{p2i[1]:.2f} '
                      f'L{p2o[0]:.2f},{p2o[1]:.2f} L{p1o[0]:.2f},{p1o[1]:.2f} Z" '
                      f'fill="{ELEMENT_COLORS[sign]}" opacity="0.4"/>')
        # thin divider between signs
        parts.append(f'<line x1="{p1i[0]:.2f}" y1="{p1i[1]:.2f}" x2="{p1o[0]:.2f}" y2="{p1o[1]:.2f}" '
                      f'stroke="#000000" stroke-width="0.6" opacity="0.5"/>')

    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r_outer}" fill="none" stroke="url(#es)" stroke-width="0.6"/>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r_inner}" fill="none" stroke="url(#es)" stroke-width="0.5" opacity="0.85"/>')

    # Zodiac glyphs, centered in their band, on a deeper tint of the same element color
    for i, sign in enumerate(ZODIAC_SIGNS):
        a = angle_for(i * 30 + 15)
        p = polar(cx, cy, (r_outer + r_inner) / 2, a)
        parts.append(f'<circle cx="{p[0]:.1f}" cy="{p[1]:.1f}" r="11" fill="#000000" opacity="0.35" '
                      f'stroke="#EAF7F1" stroke-width="0.4"/>')
        parts.append(f'<text x="{p[0]:.1f}" y="{p[1]:.1f}" font-family="var(--font-voice)" font-size="13" '
                      f'fill="#EAF7F1" text-anchor="middle" dominant-baseline="central">{ZODIAC_GLYPHS[sign]}</text>')

    # House spokes, Roman numerals, cusp degrees, and labeled angles (ASC/DSC/MC/IC)
    for i in range(1, 13):
        h = natal_data["houses"][f"house_{i}"]
        h_longitude = ZODIAC_SIGNS.index(h["sign"]) * 30 + h["degree_in_sign"]
        a = angle_for(h_longitude)
        p_out = polar(cx, cy, r_inner, a)
        is_angle = i in ANGLE_LABELS
        stroke = "url(#es)" if is_angle else "#3A5C52"
        width = 0.8 if is_angle else 0.3
        parts.append(f'<line x1="{cx}" y1="{cy}" x2="{p_out[0]:.1f}" y2="{p_out[1]:.1f}" '
                      f'stroke="{stroke}" stroke-width="{width}"/>')
        # house number placed inside, at mid-radius between center and inner ring, offset into the house
        next_h = natal_data["houses"][f"house_{(i % 12) + 1}"]
        next_longitude = ZODIAC_SIGNS.index(next_h["sign"]) * 30 + next_h["degree_in_sign"]
        mid_a = angle_for((h_longitude + ((next_longitude - h_longitude) % 360) / 2))
        p_num = polar(cx, cy, 45, mid_a)
        parts.append(f'<text x="{p_num[0]:.1f}" y="{p_num[1]:.1f}" font-family="var(--font-voice)" font-size="9" '
                      f'fill="#7FA396" text-anchor="middle" dominant-baseline="central">{ROMAN_NUMERALS[i - 1]}</text>')
        p_deg = polar(cx, cy, r_inner + 8, a)
        parts.append(f'<text x="{p_deg[0]:.1f}" y="{p_deg[1]:.1f}" font-family="var(--font-voice)" '
                      f'font-size="6.5" fill="#4E8776" text-anchor="middle">{format_dms(h["degree_in_sign"])}</text>')
        if is_angle:
            p_lbl = polar(cx, cy, r_outer + 12, a)
            parts.append(f'<text x="{p_lbl[0]:.1f}" y="{p_lbl[1]:.1f}" font-family="var(--font-voice)" '
                          f'font-size="9" fill="#D9F0E6" text-anchor="middle" dominant-baseline="central">'
                          f'{ANGLE_LABELS[i]}</text>')

    # Planet longitudes + automatic inner-glyph radius alternation to avoid overlap when planets cluster
    planet_longitude = {
        name: ZODIAC_SIGNS.index(p["sign"]) * 30 + p["degree_in_sign"]
        for name, p in natal_data["planets"].items()
    }
    ordered = sorted(planet_longitude.items(), key=lambda kv: kv[1])
    radii, last_lon, current_r = {}, None, r_planet_a
    for name, lon in ordered:
        if last_lon is not None:
            gap = min((lon - last_lon) % 360, (last_lon - lon) % 360)
            current_r = (r_planet_b if current_r == r_planet_a else r_planet_a) if gap < 10 else r_planet_a
        radii[name] = current_r
        last_lon = lon

    # Ring-dot marker for every planet (exact degree on the inner ring) + thin leader line to its inner glyph
    ring_point = {}
    for name, lon in planet_longitude.items():
        a = angle_for(lon)
        dot = polar(cx, cy, r_inner, a)
        ring_point[name] = dot
        glyph_p = polar(cx, cy, radii[name], a)
        parts.append(f'<line x1="{dot[0]:.1f}" y1="{dot[1]:.1f}" x2="{glyph_p[0]:.1f}" y2="{glyph_p[1]:.1f}" '
                      f'stroke="#4E8776" stroke-width="0.3" opacity="0.6"/>')
        parts.append(f'<circle cx="{dot[0]:.1f}" cy="{dot[1]:.1f}" r="2" fill="#D9F0E6"/>')

    # Aspect lines, drawn between the ring-dots (Chronos-style), colored distinctly per aspect type
    for asp in natal_data.get("aspects", []):
        a_name, b_name = asp["point_a"], asp["point_b"]
        if a_name not in ring_point or b_name not in ring_point:
            continue
        color = ASPECT_COLORS.get(asp["aspect"], "#5FA98F")
        pa, pb = ring_point[a_name], ring_point[b_name]
        parts.append(f'<line x1="{pa[0]:.1f}" y1="{pa[1]:.1f}" x2="{pb[0]:.1f}" y2="{pb[1]:.1f}" '
                      f'stroke="{color}" stroke-width="0.6" opacity="0.85"/>')

    # Planet glyphs + degree labels (drawn last, sit on top of everything)
    for name, lon in planet_longitude.items():
        a = angle_for(lon)
        r = radii[name]
        p = polar(cx, cy, r, a)
        parts.append(f'<circle cx="{p[0]:.1f}" cy="{p[1]:.1f}" r="9" fill="#000000" opacity="0.75"/>')
        parts.append(f'<text x="{p[0]:.1f}" y="{p[1]:.1f}" font-family="var(--font-voice)" font-size="13" '
                      f'fill="#D9F0E6" text-anchor="middle" dominant-baseline="central">'
                      f'{PLANET_GLYPHS.get(name, "?")}</text>')
        p_label = polar(cx, cy, r - 14, a)
        parts.append(f'<text x="{p_label[0]:.1f}" y="{p_label[1]:.1f}" font-family="var(--font-voice)" '
                      f'font-size="6.2" fill="#8FCBB8" text-anchor="middle">'
                      f'{natal_data["planets"][name]["degree_display"]}</text>')

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
