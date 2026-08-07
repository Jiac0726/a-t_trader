from providers.demo import DemoProvider
from providers.health import check_provider_health


def test_demo_provider_health():
    report = check_provider_health(DemoProvider())
    assert report.ok
    assert report.universe_rows == 6
    assert report.history_rows > 0
