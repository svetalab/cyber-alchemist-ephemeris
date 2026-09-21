from flask import Flask, request, jsonify
import swisseph as swe
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinder

app = Flask(__name__)
app.json.ensure_ascii = False  # so degree symbols (°) show as-is, not as \u00b0 escapes

tf = TimezoneFinder()


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


def local_time_to_julian_day(year, month, day, hour, minute, latitude, longitude):
    """
    For natal charts: figures out the correct historical UTC offset
    automatically from the birth coordinates, using the same worldwide
    timezone database phones and computers use - including historical
    rules like Soviet-era decree time, old DST changes, etc. This means
    Make never has to send us a manually guessed utc_offset for births.
    Returns both the Julian Day and the resolved timezone name (useful
    to double-check against, e.g., astro.com).
    """
    tz_name = tf.timezone_at(lat=latitude, lng=longitude)
    if tz_name is None:
        raise ValueError(f"Could not resolve a timezone for coordinates {latitude}, {longitude}")
    local_dt = datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(tz_name))
    utc_dt = local_dt.astimezone(ZoneInfo("UTC"))
    jd = swe.julday(utc_dt.year, utc_dt.month, utc_dt.day, utc_dt.hour + utc_dt.minute / 60.0)
    return jd, tz_name


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
      "latitude": 50.4501, "longitude": 30.5234
    }
    No utc_offset needed - the service resolves the correct historical
    timezone automatically from the coordinates (handles old DST rules,
    Soviet-era decree time, etc. correctly on its own).
    Latitude/longitude must be provided already resolved from the birth
    place (Make will look this up via a geocoding module before calling us).
    """
    data = request.get_json()
    jd, tz_name = local_time_to_julian_day(
        data["year"], data["month"], data["day"],
        data["hour"], data["minute"], data["latitude"], data["longitude"]
    )
    planets = calculate_planets(jd)
    house_data = calculate_houses(jd, data["latitude"], data["longitude"])

    return jsonify({
        "resolved_timezone": tz_name,
        "planets": planets,
        "houses": house_data["houses"],
        "ascendant": house_data["ascendant"],
        "midheaven": house_data["midheaven"]
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
    No utc_offset needed anymore - pass coordinates and local birth
    time, and the service resolves the correct historical timezone
    on its own. Example:
    /test-natal?year=1984&month=12&day=20&hour=16&minute=6&latitude=49.57&longitude=25.60
    """
    year = int(request.args.get("year"))
    month = int(request.args.get("month"))
    day = int(request.args.get("day"))
    hour = int(request.args.get("hour"))
    minute = int(request.args.get("minute"))
    latitude = float(request.args.get("latitude"))
    longitude = float(request.args.get("longitude"))

    jd, tz_name = local_time_to_julian_day(year, month, day, hour, minute, latitude, longitude)
    planets = calculate_planets(jd)
    house_data = calculate_houses(jd, latitude, longitude)

    return jsonify({
        "resolved_timezone": tz_name,
        "planets": planets,
        "houses": house_data["houses"],
        "ascendant": house_data["ascendant"],
        "midheaven": house_data["midheaven"]
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
