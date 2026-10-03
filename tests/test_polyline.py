from routing import polyline


def test_google_reference_example_precision5():
    # From Google's polyline docs (lat,lon order, precision 5)
    pts = polyline.decode("_p~iF~ps|U_ulLnnqC_mqNvxq`@", precision=5)
    assert pts == [[38.5, -120.2], [40.7, -120.95], [43.252, -126.453]]


def test_round_trip_precision6_lon_lat():
    coords = [[-2.970, 56.462], [-2.9705, 56.4601], [-3.1883, 55.9533]]
    enc = polyline.encode(coords, precision=6)
    assert polyline.decode(enc, precision=6) == coords
