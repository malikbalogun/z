"""Market metadata helpers."""

from __future__ import annotations

import datetime as dt
import unittest

from bot.market_intel import hours_until_resolution_end


class TestResolutionHours(unittest.TestCase):
    def test_parses_iso_z(self):
        future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=72)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        m = {"raw": {"endDate": future}}
        hr = hours_until_resolution_end(m)
        self.assertIsNotNone(hr)
        self.assertGreater(hr, 71)
        self.assertLess(hr, 73)

    def test_unknown_returns_none(self):
        self.assertIsNone(hours_until_resolution_end({"raw": {}}))


if __name__ == "__main__":
    unittest.main()
