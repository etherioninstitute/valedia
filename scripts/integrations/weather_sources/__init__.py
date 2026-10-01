"""Per-site weather readers. NWS CLI settles daily climate. Research never creates.

CPC is not imported as a client: the capture was HTTP 403. See ecmwf.CPC_BLOCKED.
"""
from weather_sources import accuweather, ecmwf, nws_cli, weather_com
