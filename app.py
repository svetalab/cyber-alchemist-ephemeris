from flask import Flask, request, jsonify
import swisseph as swe
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
