"""Сервер «Звена»: проверка initData (контракт, раздел 4) и схема базы."""
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from zveno_api import auth, db  # noqa: E402

# Вектор посчитан независимо, openssl:
#   key  = HMAC-SHA256(key="WebAppData", msg=TOKEN)
#   hash = HMAC-SHA256(key=key, msg="auth_date=…\nquery_id=…\nuser=…")
TOKEN = "5768337691:AAH5YkoiEuPk8-FZa32hStHTqXiLPtAEhx8"
KEY_HEX = "a5c609aa52f63cb5e6d8ceb6e4138726ea82bbc36bb786d64482d445ea38ee5f"
USER = '{"id":279058397,"first_name":"Vladislav","last_name":"Kibenko","username":"vdkfrost","language_code":"ru"}'
PAIRS = {"query_id": "AAHdF6IQAAAAAN0XohDhrOrc", "user": USER, "auth_date": "1695000000"}
HASH = "5b521ca0bffd87a8c81f0164193f8d32033ad62d3eea7d530606518dc23d8230"
INIT = urlencode({**PAIRS, "hash": HASH})
NOW = 1695000000 + 3600


class Vectors(unittest.TestCase):
    def test_secret_key(self):
        self.assertEqual(auth.secret_key(TOKEN).hex(), KEY_HEX)

    def test_check_string_sorted_without_hash(self):
        self.assertEqual(auth.check_string({**PAIRS, "hash": HASH}),
                         f"auth_date=1695000000\nquery_id=AAHdF6IQAAAAAN0XohDhrOrc\nuser={USER}")

    def test_sign(self):
        self.assertEqual(auth.sign(PAIRS, TOKEN), HASH)

    def test_verify(self):
        u = auth.verify(INIT, TOKEN, now=NOW)
        self.assertEqual(u.id, 279058397)
        self.assertEqual(u.display, "Vladislav Kibenko")

    def test_header(self):
        self.assertEqual(auth.from_header(f"tma {INIT}", TOKEN, now=NOW).id, 279058397)
        with self.assertRaises(auth.AuthError):
            auth.from_header(f"Bearer {INIT}", TOKEN, now=NOW)
        with self.assertRaises(auth.AuthError):
            auth.from_header(None, TOKEN, now=NOW)


class Rejects(unittest.TestCase):
    def assertRejected(self, init, now=NOW, token=TOKEN):
        with self.assertRaises(auth.AuthError) as cm:
            auth.verify(init, token, now=now)
        self.assertTrue(cm.exception.args[0])   # текст для болельщика

    def test_wrong_token(self):
        self.assertRejected(INIT, token="1:other")

    def test_tampered_user(self):
        forged = urlencode({**PAIRS, "user": USER.replace("279058397", "1"), "hash": HASH})
        self.assertRejected(forged)

    def test_no_hash(self):
        self.assertRejected(urlencode(PAIRS))

    def test_stale(self):
        self.assertRejected(INIT, now=1695000000 + 24 * 3600 + 1)
        auth.verify(INIT, TOKEN, now=1695000000 + 24 * 3600)   # ровно сутки — ещё можно

    def test_future(self):
        self.assertRejected(INIT, now=1695000000 - 3600)

    def test_empty(self):
        self.assertRejected("")

    def test_no_user(self):
        init = auth.make_init_data({"id": 1}, TOKEN, 1695000000)
        self.assertEqual(auth.verify(init, TOKEN, now=NOW).id, 1)
        pairs = {"auth_date": "1695000000"}
        self.assertRejected(urlencode({**pairs, "hash": auth.sign(pairs, TOKEN)}))

    def test_make_init_data_roundtrip(self):
        init = auth.make_init_data({"id": 42, "first_name": "Аня"}, TOKEN, 1695000000, query_id="q")
        u = auth.verify(init, TOKEN, now=NOW)
        self.assertEqual((u.id, u.first_name), (42, "Аня"))


class Schema(unittest.TestCase):
    def test_migrate_and_wal(self):
        with tempfile.TemporaryDirectory() as d:
            conn = db.connect(Path(d) / "z.db")
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], len(db.MIGRATIONS))
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(db.migrate(conn), len(db.MIGRATIONS))   # повторно — ничего не делает
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            self.assertTrue({"managers", "holdings", "scores", "steps", "leagues", *db.PERSONAL} <= tables)
            conn.close()

    def test_tx_rollback(self):
        conn = db.connect(":memory:")
        with self.assertRaises(RuntimeError):
            with db.tx(conn):
                db.set_meta(conn, "x", 1)
                raise RuntimeError
        self.assertIsNone(db.get_meta(conn, "x"))
        with db.tx(conn):
            db.set_meta(conn, "x", {"a": 1})
        self.assertEqual(db.get_meta(conn, "x"), {"a": 1})


if __name__ == "__main__":
    unittest.main()
