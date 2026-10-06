import unittest

from radio_monitor.matcher import Matcher, normalize


class MatcherTest(unittest.TestCase):
    def setUp(self):
        self.m = Matcher()

    def assertArtist(self, text):
        m = self.m.match(text)
        self.assertIsNotNone(m, text)
        self.assertEqual(m.kind, "artist", text)

    def test_artist_in_common_formats(self):
        for text in [
            "Backstreet Boys - I Want It That Way",
            "I Want It That Way - Backstreet Boys",
            "BACKSTREET BOYS/I WANT IT THAT WAY",
            "I Want It That Way by Backstreet Boys",
            "Backstreet Boys /// - Larger Than Life",
            "The Backstreet Boys - Shape of My Heart",
            "Steve Aoki & Backstreet Boys - Let It Be Me",
            "Everybody (Backstreet's Back)",
            "BackstreetBoys - The Call",
            "Back Street Boys - Drowning",
            "BSB - Incomplete",
        ]:
            self.assertArtist(text)

    def test_title_only_is_a_possible_match(self):
        for text in ["I Want It That Way", "QUIT PLAYING GAMES (WITH MY HEART)",
                     "As Long As You Love Me", "Show Me The Meaning Of Being Lonely"]:
            m = self.m.match(text)
            self.assertIsNotNone(m, text)
            self.assertEqual(m.kind, "title", text)
            self.assertFalse(m.confident)

    def test_no_false_alarms_on_other_music(self):
        for text in [
            "", "KiSS 92.5", "98.1 CHFI", "Taylor Swift - Shake It Off",
            "Bruno Mars - Get Down", "Sisqo - Incomplete", "Elton John & Kiki Dee - Don't Go Breaking My Heart",
            "Wham! - Last Christmas", "NSYNC - Bye Bye Bye", "Mariah Carey - Everybody",
            "absbx", "The Weeknd - Blinding Lights", "Katy Perry - The One That Got Away",
        ]:
            self.assertIsNone(self.m.match(text), text)

    def test_normalize(self):
        self.assertEqual(normalize("  Backstreet’s  BACK!! "), "backstreets back")
        self.assertEqual(normalize("Beyoncé & Jay-Z"), "beyonce and jay z")

    def test_custom_lists(self):
        m = Matcher(artist_aliases=["nsync"], song_titles=[])
        self.assertIsNotNone(m.match("NSYNC - Bye Bye Bye"))
        self.assertIsNone(m.match("Backstreet Boys - I Want It That Way"))


if __name__ == "__main__":
    unittest.main()
