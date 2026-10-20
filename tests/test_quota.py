import unittest

from app import local_time, window_text
from quota import QuotaError, Snapshot, Window, mood, parse_limits

NOW = 1792248000


def payload(used=37, weekly=32):
    return {"rateLimitsByLimitId":{"codex":{
        "primary":{"usedPercent":used,"windowDurationMins":300,"resetsAt":NOW+100},
        "secondary":{"usedPercent":weekly,"windowDurationMins":10080,"resetsAt":NOW+1000},
    }}}


class QuotaTests(unittest.TestCase):
    def test_remaining_not_used_percentage(self):
        result = parse_limits(payload(), NOW)
        self.assertEqual(result.short.remaining,63)
        self.assertEqual(result.week.remaining,68)

    def test_new_bucket_wins_over_legacy(self):
        data=payload(80,90)
        data["rateLimits"]={"limitId":"codex","primary":{"usedPercent":0,"windowDurationMins":300}}
        result=parse_limits(data,NOW)
        self.assertEqual(result.short.remaining,20)

    def test_legacy_supported(self):
        result=parse_limits({"rateLimits":{"primary":{"usedPercent":25,"windowDurationMins":300}}},NOW)
        self.assertEqual(result.short.remaining,75)
        self.assertIsNone(result.week)

    def test_other_model_bucket_never_masquerades_as_general_quota(self):
        with self.assertRaises(QuotaError):
            parse_limits({"rateLimitsByLimitId":{"codex_other":payload()["rateLimitsByLimitId"]["codex"]}},NOW)

    def test_duration_identifies_windows_not_field_position(self):
        data=payload()
        bucket=data["rateLimitsByLimitId"]["codex"]
        bucket["primary"],bucket["secondary"]=bucket["secondary"],bucket["primary"]
        result=parse_limits(data,NOW)
        self.assertEqual(result.short.remaining,63)
        self.assertEqual(result.week.remaining,68)

    def test_missing_is_not_zero(self):
        data=payload(None,32)
        result=parse_limits(data,NOW)
        self.assertIsNone(result.short)
        self.assertEqual(window_text(result.short,result,NOW),"—")

    def test_invalid_percentages_are_missing(self):
        for invalid in (True,False,"37",float("nan"),float("inf")):
            with self.subTest(invalid=invalid):
                self.assertIsNone(parse_limits(payload(invalid,32),NOW).short)

    def test_clamp_percentages(self):
        result=parse_limits(payload(120,-5),NOW)
        self.assertEqual(result.short.remaining,0)
        self.assertEqual(result.week.remaining,100)

    def test_lower_window_controls_mood(self):
        self.assertEqual(mood(parse_limits(payload(1,90),NOW),NOW),"low")

    def test_mood_boundaries(self):
        for remaining,expected in [(100,"full"),(61,"full"),(60,"steady"),(26,"steady"),(25,"low"),(1,"low"),(0,"empty")]:
            with self.subTest(remaining=remaining):
                self.assertEqual(mood(parse_limits(payload(100-remaining,0),NOW),NOW),expected)

    def test_stale_reading_hides_values(self):
        result=parse_limits(payload(),NOW)
        self.assertEqual(mood(result,NOW+181),"unknown")
        self.assertEqual(window_text(result.short,result,NOW+181),"—")

    def test_reset_does_not_infer_refill(self):
        result=parse_limits(payload(100,0),NOW)
        self.assertEqual(window_text(result.short,result,NOW+100),"—")
        self.assertNotEqual(window_text(result.short,result,NOW+100),"100%")

    def test_both_expired_are_unknown(self):
        result=Snapshot(Window(99,300,NOW),Window(99,10080,NOW),NOW)
        self.assertEqual(mood(result,NOW),"unknown")

    def test_exact_percent_format(self):
        for remaining,expected in [(100,"100%"),(0,"0%"),(10,"10%"),(12.5,"12.5%")]:
            result=Snapshot(Window(remaining,300,None),None,NOW)
            self.assertEqual(window_text(result.short,result,NOW),expected)

    def test_reset_uses_shanghai_time(self):
        self.assertEqual(local_time(1792262920),"10/18 02:48")


if __name__=="__main__":
    unittest.main()
