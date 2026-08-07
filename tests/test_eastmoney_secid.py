from providers.eastmoney import EastmoneyProvider


def test_eastmoney_secid_routes_bse_920_to_market_zero():
    assert EastmoneyProvider._secid("920002") == "0.920002"
    assert EastmoneyProvider._market("920002") == "BJ"


def test_eastmoney_secid_keeps_shanghai_and_shenzhen_routes():
    assert EastmoneyProvider._secid("600519") == "1.600519"
    assert EastmoneyProvider._market("600519") == "SH"
    assert EastmoneyProvider._secid("300750") == "0.300750"
    assert EastmoneyProvider._market("300750") == "SZ"


def test_eastmoney_legacy_bse_codes_remain_market_zero():
    assert EastmoneyProvider._secid("832000") == "0.832000"
    assert EastmoneyProvider._market("832000") == "BJ"
