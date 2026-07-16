from spending.importer.normalize import normalize_merchant


def test_strip_sq_prefix():
    assert normalize_merchant("SQ *COFFEE SHOP") == "COFFEE SHOP"


def test_strip_paypal_prefix():
    assert normalize_merchant("PAYPAL *NETFLIX") == "NETFLIX"


def test_strip_trailing_reference_number():
    assert normalize_merchant("COFFEE SHOP 8442") == "COFFEE SHOP"


def test_strip_trailing_transaction_id():
    # Transaction ID stripped, then alias collapses AMZN MKTP US → AMAZON
    assert normalize_merchant("AMZN MKTP US*2K7X9") == "AMAZON"


def test_strip_city_state_comma():
    assert normalize_merchant("COFFEE SHOP CHICAGO, IL") == "COFFEE SHOP"


def test_strip_city_state_zip():
    # State+ZIP stripped, city name remains (intentionally lossy)
    assert (
        normalize_merchant("TARGET STORE NEW YORK NY 10001") == "TARGET STORE NEW YORK"
    )


def test_strip_city_state_comma_zip():
    assert normalize_merchant("COFFEE SHOP CHICAGO, IL 60601") == "COFFEE SHOP"


def test_combined_prefix_and_trailing():
    assert normalize_merchant("SQ *COFFEE SHOP 8442 CHICAGO, IL") == "COFFEE SHOP"


def test_collapse_whitespace():
    assert normalize_merchant("SOME   STORE   NAME") == "SOME STORE NAME"


def test_uppercase():
    assert normalize_merchant("whole foods market") == "WHOLE FOODS MARKET"


def test_already_clean():
    assert normalize_merchant("NETFLIX") == "NETFLIX"


def test_custom_config(tmp_path):
    config = tmp_path / "norm.yaml"
    config.write_text("prefixes:\n  - 'XX*'\ntrailing_patterns: []\n")
    assert normalize_merchant("XX*MYSTORE", config_path=str(config)) == "MYSTORE"
