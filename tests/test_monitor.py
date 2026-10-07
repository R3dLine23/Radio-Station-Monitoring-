import threading
import time
import unittest

from radio_monitor.matcher import Matcher
from radio_monitor.monitor import Dispatcher, Monitor, Settings, Station, StationWatcher, open_station

from .fake_icy_server import FakeIcyServer


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class RecordingDispatcher(Dispatcher):
    def __init__(self):
        self.alerts = []

    def send(self, alert):
        self.alerts.append(alert)
        return []


class RecordingNotifier:
    name = "recording"

    def __init__(self):
        self.alerts = []
        self.got_bsb = threading.Event()

    def send(self, alert):
        self.alerts.append(alert)
        if "BACKSTREET" in alert.title:
            self.got_bsb.set()


STATION = Station("KISS 92.5", "http://example.invalid/icy", text_number="92592", text_message="BSB")


class WatcherTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.d = RecordingDispatcher()
        self.w = StationWatcher(STATION, Matcher(), self.d, Settings(), clock=self.clock)

    def test_alerts_once_per_play(self):
        self.w.handle_title("Taylor Swift - Shake It Off")
        self.assertEqual(self.d.alerts, [])
        self.w.handle_title("Backstreet Boys - I Want It That Way")
        self.w.handle_title("Backstreet Boys - I Want It That Way")  # repeated metadata
        self.assertEqual(len(self.d.alerts), 1)
        a = self.d.alerts[0]
        self.assertIn("BACKSTREET BOYS on KISS 92.5", a.title)
        self.assertIn("I Want It That Way", a.message)
        self.assertIn("TEXT NOW: 92592", a.message)
        self.assertEqual(a.click_url, "sms:92592?&body=BSB")

    def test_metadata_flicker_does_not_realert(self):
        self.w.handle_title("Backstreet Boys - Larger Than Life")
        self.w.handle_title("KiSS 92.5")
        self.clock.t += 5
        self.w.handle_title("Backstreet Boys - Larger Than Life")
        self.assertEqual(len(self.d.alerts), 1)

    def test_same_song_later_alerts_again(self):
        self.w.handle_title("Backstreet Boys - Larger Than Life")
        self.w.handle_title("Dua Lipa - Levitating")
        self.clock.t += 3600
        self.w.handle_title("Backstreet Boys - Larger Than Life")
        self.assertEqual(len(self.d.alerts), 2)

    def test_back_to_back_bsb_songs_both_alert(self):
        self.w.handle_title("Backstreet Boys - Larger Than Life")
        self.w.handle_title("Backstreet Boys - The Call")
        self.assertEqual(len(self.d.alerts), 2)

    def test_title_only_match_is_flagged_possible(self):
        self.w.handle_title("I Want It That Way")
        self.assertEqual(len(self.d.alerts), 1)
        self.assertIn("Possible", self.d.alerts[0].title)

    def test_outage_and_recovery_reported_once(self):
        self.clock.t += 60
        self.assertFalse(self.w.check_stale())
        self.clock.t += 500
        self.assertTrue(self.w.check_stale())
        self.assertTrue(self.w.check_stale())
        self.assertEqual(len(self.d.alerts), 1)
        self.assertIn("DOWN", self.d.alerts[0].title)
        self.w.mark_alive()
        self.assertEqual(len(self.d.alerts), 2)
        self.assertIn("back", self.d.alerts[1].title)
        self.assertFalse(self.w.check_stale())


class EndToEndTest(unittest.TestCase):
    def test_monitor_against_fake_website(self):
        from .test_web import PollTest, _Pages, flight_page
        PollTest.setUpClass()
        try:
            _Pages.pages = [
                flight_page([("CKIS", {"artist": "Dua Lipa", "title": "Levitating"})]),
                flight_page([("CKIS", {"artist": "Backstreet Boys", "title": "Larger Than Life"})]),
            ]
            station = Station("KISS 92.5", PollTest.url, source="web", call_letters="CKIS", poll_seconds=0.05)
            notifier = RecordingNotifier()
            mon = Monitor([station], Matcher(), [notifier], Settings(notify_on_start=False))
            t = threading.Thread(target=mon.run, daemon=True)
            t.start()
            self.assertTrue(notifier.got_bsb.wait(10))
            mon.stop()
            t.join(timeout=5)
            hits = [a for a in notifier.alerts if "BACKSTREET" in a.title]
            self.assertEqual(len(hits), 1)  # polled many times, alerted once
            self.assertIn("Backstreet Boys - Larger Than Life", hits[0].message)
        finally:
            PollTest.tearDownClass()

    def test_monitor_against_fake_stream(self):
        server = FakeIcyServer(["Harry Styles - As It Was", "Backstreet Boys - I Want It That Way"],
                               status_line=b"ICY 200 OK")
        stations = [Station("KISS 92.5", server.url), Station("CHFI 98.1", server.url)]
        notifier = RecordingNotifier()
        settings = Settings(reconnect_min_seconds=0.2, read_timeout_seconds=5, notify_on_start=False)
        mon = Monitor(stations, Matcher(), [notifier], settings, source=open_station)
        t = threading.Thread(target=mon.run, daemon=True)
        t.start()
        try:
            deadline = time.time() + 10
            while time.time() < deadline:
                hits = [a for a in notifier.alerts if "BACKSTREET" in a.title]
                if {h.title for h in hits} >= {"BACKSTREET BOYS on KISS 92.5!", "BACKSTREET BOYS on CHFI 98.1!"}:
                    break
                time.sleep(0.05)
            titles = {a.title for a in notifier.alerts}
            self.assertIn("BACKSTREET BOYS on KISS 92.5!", titles)
            self.assertIn("BACKSTREET BOYS on CHFI 98.1!", titles)
        finally:
            mon.stop()
            t.join(timeout=5)
            server.close()


if __name__ == "__main__":
    unittest.main()
