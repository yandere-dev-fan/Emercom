from app.domain.map_codec import decode_cells, encode_cells


def test_encode_decode_roundtrip() -> None:
    source = [0, 1, 2, 65535, 42, 7]
    encoded = encode_cells(source)
    decoded = decode_cells(encoded, len(source))
    assert decoded == source
