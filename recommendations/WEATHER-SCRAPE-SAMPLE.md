# Weather source scrape

Capture timestamp: 2026-09-23 18:29 PDT (Wednesday)

## 1. Kalshi weather settlement help
URL: https://help.kalshi.com/en/articles/13823837-weather-markets
- Page title: Weather Markets
- Kalshi offers weather-related markets based on temperature movements or precipitation amounts.
- Markets cover cities including New York City and Los Angeles.
- Daily high/low temperature markets settle on the final climate report issued by NWS.
- Hourly temperature markets use The Weather Company's reported value as official and final.
- Daily temperature markets are typically ranges.
- The page notes NWS Climate Reports are used for daily temperature markets.
Screenshot: `/workspace/weather-scrape/01-kalshi-help.png`

## 2. NWS CLI product index
URL: https://forecast.weather.gov/product_sites.php?product=CLI&site=NWS
- Page heading: Climatological Report (Daily)
- Instruction: click a site below to view the Climatological Report (Daily) product(s).
- Table label: NWS Weather Forecast Office Identifier.
- Listed offices include ABE - Mt. Holly, NJ.
- Listed offices include ABI - San Angelo, TX.
- Listed offices include ABQ - Albuquerque, NM.
- Listed offices include ALB - Albany, NY.
- Listed offices include ATL - Atlanta, GA.
Screenshot: `/workspace/weather-scrape/02-nws-cli-index.png`

## 3. NWS NYC CLI sample
URL: https://forecast.weather.gov/product.php?site=OKX&issuedby=NYC&product=CLI&format=CI&version=1&glossary=0
- Product: Climatological Report (Daily), issued by NWS Upton, NY.
- Report: Central Park NY climate summary for September 23, 2026.
- Valid today as of 0400 PM local time; report issued 435 PM EDT Wed Sep 23 2026.
- Climate normal period: 1991 to 2020; climate record period: 1869 to 2026.
- Maximum temperature: 67 F at 259 PM; normal 74 F; departure -7.
- Minimum temperature: 55 F at 545 AM; normal 60 F; departure -5.
- Average temperature: 61 F; normal 67 F; departure -6.
- Today precipitation: 0.00 in; normal 0.14 in; departure -0.14.
- Highest wind: 23 mph; highest gust: 39 mph from NE (60).
- Tomorrow normal maximum/minimum: 73 F / 60 F.
Screenshot: `/workspace/weather-scrape/03-nws-nyc-cli.png`

## 4. The Weather Channel
URL: https://weather.com/
- Location shown: Wheaton, Illinois.
- Current condition: 62 F and cloudy.
- Feels like: 62 F.
- Today's high: 68 F; low: 56 F.
- Chance of rain: 2%; precipitation: 0 in.
- Daily forecast: Thu 24 high 71 F / low 58 F, 6% chance.
- Daily forecast: Fri 25 high 69 F / low 52 F, 8% chance.
- Wind: N 6 mph, NE.
- Air quality: 30, Good.
Screenshot: `/workspace/weather-scrape/04-weather-com.png`

## 5. AccuWeather
URL: https://www.accuweather.com/
- Home page recent location: Wheaton, Illinois.
- Current temperature shown: 62 F.
- RealFeel temperature: 63 F.
- Search/location control is visible for forecast lookup.
- Recent-location cards include Austin at 82 F.
- Recent-location cards include Brooklyn at 62 F.
- Recent-location cards include New York at 61 F.
- Banner headlines a significant September nor'easter for the East Coast.
- Banner also references Tropical Storm Nolo and Hawaii.
Screenshot: `/workspace/weather-scrape/05-accuweather.png`

## 6. ECMWF charts
URL: https://charts.ecmwf.int/
- Page title: ECMWF Charts; section: Charts catalogue.
- Catalogue filters include Medium (15 days), Sub-seasonal, and Seasonal.
- Product filters include Forecasts and Verification.
- Product filters include Surface and Atmosphere.
- Visible product: Mean sea level pressure and 850 hPa wind speed.
- Visible product: 500 hPa geopotential height and 850 hPa temperature.
- Visible product: 2 m temperature and 30 m wind.
- Additional product: 100 m wind and mean sea level pressure.
- The catalogue also lists Rain and mean sea level pressure.
Screenshot: `/workspace/weather-scrape/06-ecmwf.png`

## 7. NOAA CPC predictions
URL: https://www.cpc.ncep.noaa.gov/products/predictions/
- Result: HTTP 403 Forbidden.
- Page heading: Forbidden.
- Visible message: You don't have permission to access this resource.
- No CPC outlook title or prediction fields were available.
- No login form or CAPTCHA was shown.
Screenshot: `/workspace/weather-scrape/07-cpc.png`
