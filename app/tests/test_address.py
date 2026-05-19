import pytest
from app.address import haversine_miles, classify_address


# Austin, TX as service center
SERVICE_LAT = 30.2672
SERVICE_LNG = -97.7431
SERVICE_RADIUS = 30  # miles


def _make_google_response(
    lat: float,
    lng: float,
    address_complete: bool = True,
    has_replaced_components: bool = False,
    formatted_address: str = "123 Test St, Austin, TX 78701",
) -> dict:
    return {
        "result": {
            "verdict": {
                "addressComplete": address_complete,
                "hasReplacedComponents": has_replaced_components,
                "hasInferredComponents": False,
            },
            "address": {
                "formattedAddress": formatted_address,
            },
            "geocode": {
                "location": {
                    "latitude": lat,
                    "longitude": lng,
                }
            },
        }
    }


class TestHaversine:
    def test_same_point(self):
        assert haversine_miles(30.0, -97.0, 30.0, -97.0) == 0.0

    def test_known_distance(self):
        # Austin to San Antonio: ~73 miles straight-line (~80 driving)
        dist = haversine_miles(30.2672, -97.7431, 29.4241, -98.4936)
        assert 70 < dist < 80

    def test_symmetry(self):
        d1 = haversine_miles(30.0, -97.0, 31.0, -98.0)
        d2 = haversine_miles(31.0, -98.0, 30.0, -97.0)
        assert abs(d1 - d2) < 0.001


class TestClassifyAddress:
    def test_in_radius(self):
        """5 miles away with 30 mile radius -> in_radius"""
        # ~5 miles north of Austin center
        resp = _make_google_response(lat=30.34, lng=-97.74)
        tier, details = classify_address(resp, SERVICE_LAT, SERVICE_LNG, SERVICE_RADIUS)
        assert tier == "in_radius"
        assert details["distance"] < SERVICE_RADIUS

    def test_borderline(self):
        """40 miles away with 30 mile radius -> borderline (within 1.5x)"""
        # ~40 miles away: roughly Temple, TX area
        resp = _make_google_response(lat=30.85, lng=-97.74)
        tier, details = classify_address(resp, SERVICE_LAT, SERVICE_LNG, SERVICE_RADIUS)
        # Verify distance is between radius and 1.5x radius
        dist = details["distance"]
        assert SERVICE_RADIUS < dist <= SERVICE_RADIUS * 1.5
        assert tier == "borderline"

    def test_out_of_radius(self):
        """100 miles away -> out_of_radius"""
        # Dallas area, ~195 miles away
        resp = _make_google_response(lat=32.7767, lng=-96.7970)
        tier, details = classify_address(resp, SERVICE_LAT, SERVICE_LNG, SERVICE_RADIUS)
        assert tier == "out_of_radius"
        assert details["distance"] > SERVICE_RADIUS * 1.5

    def test_replaced_components_invalid(self):
        """Google replaced components -> invalid even if close"""
        resp = _make_google_response(
            lat=30.27, lng=-97.74, has_replaced_components=True
        )
        tier, details = classify_address(resp, SERVICE_LAT, SERVICE_LNG, SERVICE_RADIUS)
        assert tier == "invalid"
        assert "replaced" in details["reason"].lower()

    def test_address_incomplete_invalid(self):
        """addressComplete=False -> invalid"""
        resp = _make_google_response(
            lat=30.27, lng=-97.74, address_complete=False
        )
        tier, details = classify_address(resp, SERVICE_LAT, SERVICE_LNG, SERVICE_RADIUS)
        assert tier == "invalid"
        assert "incomplete" in details["reason"].lower()
