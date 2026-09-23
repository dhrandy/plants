import os
from datetime import date, timedelta

os.environ["PLANTS_DATA_DIR"] = "/tmp/plants-pytest-data"
os.environ["PLANTS_NOTIFY_WORKER"] = "false"
os.environ["PLANTS_WEATHER_OFFLINE"] = "true"
from fastapi.testclient import TestClient  # noqa: E402
from app import main  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def fresh(tmp_path):
    main.DB_PATH = tmp_path / "plants.db"
    main.PHOTOS_DIR = tmp_path / "photos"
    main.PHOTOS_DIR.mkdir()
    main._login_failures.clear()
    main._api_failures.clear()
    main._api_calls.clear()
    main.init_db()


def days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


def setup_admin(c):
    assert c.post("/api/setup", json={"username": "admin-test", "password": "password-123"}).status_code == 200


def test_setup_seed_and_auth(tmp_path):
    fresh(tmp_path)
    with TestClient(main.app) as c:
        assert c.get("/api/plants").status_code == 401
        assert c.get("/api/status").json()["setup_required"] is True
        setup_admin(c)
        assert c.post("/api/setup", json={"username": "x-user", "password": "password-123"}).status_code == 409
        plants = c.get("/api/plants").json()
        assert [p["name"] for p in plants] == ["Example pothos"]
        assert plants[0]["tasks"][0]["state"] == "today"
        r = c.get("/")
        assert "default-src 'self'" in r.headers["content-security-policy"]
        assert r.headers["x-frame-options"] == "DENY"


def test_login_rate_limit(tmp_path):
    fresh(tmp_path)
    with TestClient(main.app) as c:
        setup_admin(c)
    with TestClient(main.app) as c:
        for _ in range(5):
            assert c.post("/api/login", json={"username": "admin-test", "password": "wrong-pass"}).status_code == 401
        r = c.post("/api/login", json={"username": "admin-test", "password": "password-123"})
        assert r.status_code == 429 and "retry-after" in r.headers


def test_due_logic_done_skip_snooze_and_backdate(tmp_path):
    fresh(tmp_path)
    with TestClient(main.app) as c:
        setup_admin(c)
        p = c.post("/api/plants", json={"name": "Fern", "room": "Bathroom",
                                         "tasks": [{"kind": "water", "interval_days": 3, "last_done": days_ago(5)}]}).json()
        t = p["tasks"][0]
        assert t["state"] == "overdue" and t["days"] == -2
        assert "Every 3 days" in t["reason"]
        due = c.get("/api/due").json()["items"]
        assert any(i["task_id"] == t["id"] and i["room"] == "Bathroom" for i in due)
        # backdated done
        t2 = c.post(f"/api/tasks/{t['id']}/done", json={"date": days_ago(1), "note": "soaked"}).json()["tasks"][0]
        assert t2["days"] == 2 and t2["last_done"] == days_ago(1)
        # future dates are refused
        assert c.post(f"/api/tasks/{t['id']}/done", json={"date": (date.today() + timedelta(days=1)).isoformat()}).status_code == 400
        # skip restarts the cycle from today without changing last watered
        t3 = c.post(f"/api/tasks/{t['id']}/skip", json={}).json()["tasks"][0]
        assert t3["days"] == 3 and t3["last_done"] == days_ago(1)
        t4 = c.post(f"/api/tasks/{t['id']}/snooze", json={"days": 7}).json()["tasks"][0]
        assert t4["days"] == 7 and "snoozed" in t4["reason"]
        events = c.get(f"/api/plants/{p['id']}/events").json()
        assert [e["action"] for e in events][:3] == ["snooze", "skip", "done"] or len(events) >= 3
        assert all(e["user"] == "admin-test" for e in events)
        # deleting the skip entry recomputes the cycle
        skip = next(e for e in events if e["action"] == "skip")
        assert c.delete(f"/api/events/{skip['id']}").status_code == 200


def test_winter_multiplier_and_task_override(tmp_path):
    fresh(tmp_path)
    with TestClient(main.app) as c:
        setup_admin(c)
        month = date.today().month
        c.put("/api/settings", json={"winter_months": [month], "winter_multiplier": 2})
        p = c.post("/api/plants", json={"name": "Jade", "tasks": [
            {"kind": "water", "interval_days": 7, "last_done": days_ago(0)},
            {"kind": "fertilize", "interval_days": 30, "winter_interval_days": 90, "last_done": days_ago(0)}]}).json()
        by_kind = {t["kind"]: t for t in p["tasks"]}
        assert by_kind["water"]["effective_interval"] == 14
        assert by_kind["fertilize"]["effective_interval"] == 90


def test_batch_rooms_duplicate_and_photos(tmp_path):
    fresh(tmp_path)
    with TestClient(main.app) as c:
        setup_admin(c)
        a = c.post("/api/plants", json={"name": "A", "room": "Kitchen"}).json()
        b = c.post("/api/plants", json={"name": "B", "room": "kitchen"}).json()
        assert a["room_id"] == b["room_id"]
        ids = [a["tasks"][0]["id"], b["tasks"][0]["id"]]
        assert c.post("/api/care/batch", json={"task_ids": ids, "action": "done"}).json()["count"] == 2
        assert all(c.get(f"/api/plants/{x['id']}").json()["tasks"][0]["days"] == 7 for x in (a, b))
        dup = c.post(f"/api/plants/{a['id']}/duplicate").json()
        assert dup["name"] == "A (copy)" and len(dup["tasks"]) == 1
        r = c.post(f"/api/plants/{a['id']}/events", files={"photo": ("x.png", PNG, "image/png")}, data={"note": "first"})
        assert r.status_code == 201
        assert c.get(f"/api/plants/{a['id']}").json()["photo"] == r.json()["photo"]
        assert c.get(r.json()["photo"]).status_code == 200
        bad = c.post(f"/api/plants/{a['id']}/events", files={"photo": ("x.png", b"not an image", "image/png")})
        assert bad.status_code == 400
        assert c.get("/api/photos/..%2Fplants.db").status_code == 404


def test_tokens_v1_api_and_attribution(tmp_path):
    fresh(tmp_path)
    with TestClient(main.app) as c:
        setup_admin(c)
        tok = c.post("/api/tokens", json={"name": "Assistant"}).json()["token"]
        h = {"Authorization": f"Bearer {tok}"}
        assert c.get("/api/v1/plants").status_code == 401
        new = c.post("/api/v1/plants", headers=h, json={
            "name": "Tag photo plant", "species": "Spathiphyllum", "room": "Office",
            "notes": "From the plant tag", "tasks": [{"kind": "water", "interval_days": 5, "last_done": days_ago(1)}]})
        assert new.status_code == 201
        pid = new.json()["id"]
        tid = new.json()["tasks"][0]["id"]
        assert c.patch(f"/api/v1/plants/{pid}", headers=h, json={"light": "Low"}).json()["light"] == "Low"
        assert c.post(f"/api/v1/tasks/{tid}/done", headers=h, json={}).status_code == 200
        ev = c.get(f"/api/plants/{pid}/events").json()
        assert ev[0]["via"] == "Assistant" and ev[0]["user"] == "admin-test"
        assert "items" in c.get("/api/v1/due?days=30", headers=h).json()
        assert "/api/v1/plants" in c.get("/api/openapi.json").json()["paths"]
        assert "/api/plants" not in c.get("/api/openapi.json").json()["paths"]


def test_members_and_permissions(tmp_path):
    fresh(tmp_path)
    with TestClient(main.app) as admin:
        setup_admin(admin)
        assert admin.post("/api/users", json={"username": "member-test", "password": "password-456"}).status_code == 200
    with TestClient(main.app) as m:
        assert m.post("/api/login", json={"username": "member-test", "password": "password-456"}).status_code == 200
        assert m.get("/api/plants").status_code == 200
        assert m.put("/api/settings", json={"app_name": "x"}).status_code == 403
        assert m.get("/api/export").status_code == 403


def test_weather_not_configured_and_offline(tmp_path):
    fresh(tmp_path)
    with TestClient(main.app) as c:
        setup_admin(c)
        assert c.get("/api/weather").json() == {"configured": False}
        c.put("/api/settings", json={"weather_name": "Testville", "weather_lat": 10, "weather_lon": 20})
        w = c.get("/api/weather").json()
        assert w["configured"] and "error" in w


def test_weather_parsing(monkeypatch):
    sample = {
        "current": {"temperature_2m": 70.2, "relative_humidity_2m": 25, "precipitation": 0,
                    "weather_code": 3, "wind_speed_10m": 5},
        "daily": {"time": [days_ago(-i) for i in range(5)], "weather_code": [61, 3, 0, 0, 0],
                  "temperature_2m_max": [80, 95, 70, 70, 70], "temperature_2m_min": [60, 60, 35, 60, 60],
                  "precipitation_sum": [0.4, 0, 0, 0, 0], "precipitation_probability_max": [80, 0, 0, 0, 0]},
    }
    monkeypatch.setattr(main, "http_json", lambda url: sample)
    w = main.fetch_weather(1, 2, True)
    assert w["current"]["summary"] == "Cloudy"
    assert w["daily"][0]["label"] == "today"
    assert "Rain likely today" in w["hint"] and "Hot days" in w["hint"] and "Cold nights" in w["hint"]
    assert "Dry air" in w["hint"]


def test_notifications_digest_repeat_and_quiet_hours(tmp_path, monkeypatch):
    from datetime import datetime
    fresh(tmp_path)
    sent = []
    monkeypatch.setattr(main, "send_notification", lambda urls, title, body: (sent.append((title, body)) or (True, "")))
    with TestClient(main.app) as c:
        setup_admin(c)
        c.post("/api/plants", json={"name": "Fern", "room": "Bath",
                                     "tasks": [{"kind": "water", "interval_days": 3, "last_done": days_ago(5)}]})
        c.put("/api/notifications", json={"notify_urls": "json://localhost", "notify_hour": 8,
                                           "quiet_start": 21, "quiet_end": 7, "overdue_repeat_days": 2,
                                           "public_url": "https://plants.example.com"})
    today = date.today()
    at = lambda h, d=0: datetime(today.year, today.month, today.day, h) + timedelta(days=d)  # noqa: E731
    assert main.run_notification_check(at(22)) == 0  # quiet hours
    assert main.run_notification_check(at(7)) == 0  # before send hour
    assert main.run_notification_check(at(9)) == 2  # example pothos + fern
    assert "check soil" in sent[0][1].lower() or "Check the soil" in sent[0][1]
    assert "https://plants.example.com/#/plant/" in sent[0][1]
    assert main.run_notification_check(at(10)) == 0  # already sent
    assert main.run_notification_check(at(9, 2)) >= 1  # overdue repeat


def test_export_import_roundtrip(tmp_path):
    fresh(tmp_path)
    with TestClient(main.app) as c:
        setup_admin(c)
        p = c.post("/api/plants", json={"name": "Keep me"}).json()
        c.post(f"/api/plants/{p['id']}/events", files={"photo": ("x.png", PNG, "image/png")})
        backup = c.get("/api/export").json()
        assert backup["app"] == "plants" and backup["photos"]
        c.delete(f"/api/plants/{p['id']}")
        assert c.post("/api/import", json={"app": "nope"}).status_code == 400
        assert c.post("/api/import", json=backup).status_code == 200
    with TestClient(main.app) as c:
        assert c.post("/api/login", json={"username": "admin-test", "password": "password-123"}).status_code == 200
        names = [x["name"] for x in c.get("/api/plants").json()]
        assert "Keep me" in names
        photo = next(x for x in c.get("/api/plants").json() if x["name"] == "Keep me")["photo"]
        assert c.get(photo).status_code == 200


# ---------------------------------------------------------------- browser push

FCM = "https://fcm.googleapis.com/fcm/send/"


def fake_browser_keys():
    """A receiver key pair like the one a browser makes for a push subscription."""
    import base64
    import secrets as pysecrets

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = pysecrets.token_bytes(16)
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
    return key, auth, {"p256dh": b64(pub), "auth": b64(auth)}


def enable_push(monkeypatch):
    from app import vapid

    public, private = vapid.generate()
    monkeypatch.setenv("PLANTS_VAPID_PUBLIC_KEY", public)
    monkeypatch.setenv("PLANTS_VAPID_PRIVATE_KEY", private)
    return public


def test_push_config_validation_and_per_user_subscriptions(tmp_path, monkeypatch):
    fresh(tmp_path)
    monkeypatch.delenv("PLANTS_VAPID_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("PLANTS_VAPID_PRIVATE_KEY", raising=False)
    keys = fake_browser_keys()[2]
    with TestClient(main.app) as c:
        assert c.get("/api/push").status_code == 401
        setup_admin(c)
        info = c.get("/api/push").json()
        assert info["configured"] is False and info["public_key"] == "" and info["problem"] == ""
        assert c.post("/api/push/subscribe", json={"endpoint": FCM + "a", "keys": keys}).status_code == 503
        # a mismatched pair is reported, not used
        from app import vapid
        monkeypatch.setenv("PLANTS_VAPID_PUBLIC_KEY", vapid.generate()[0])
        monkeypatch.setenv("PLANTS_VAPID_PRIVATE_KEY", vapid.generate()[1])
        info = c.get("/api/push").json()
        assert info["configured"] is False and "match" in info["problem"]
        public = enable_push(monkeypatch)
        info = c.get("/api/push").json()
        assert info["configured"] is True and info["public_key"] == public
        # only real push services, https only, well-formed keys
        for bad in ("https://evil.example.com/push", "http://fcm.googleapis.com/fcm/send/x",
                    "https://fcm.googleapis.com.evil.example/x", "https://user:pw@fcm.googleapis.com/x"):
            assert c.post("/api/push/subscribe", json={"endpoint": bad, "keys": keys}).status_code == 400, bad
        assert c.post("/api/push/subscribe", json={"endpoint": FCM + "a", "keys": {"p256dh": "not base64!" * 3, "auth": "x" * 16}}).status_code == 422
        r = c.post("/api/push/subscribe", json={"endpoint": FCM + "a", "keys": keys})
        assert r.status_code == 200 and r.json()["subscribed"] is True and r.json()["devices"] == 1
        # re-subscribing the same endpoint doesn't duplicate it
        assert c.post("/api/push/subscribe", json={"endpoint": FCM + "a", "keys": keys}).json()["devices"] == 1
        assert c.get("/api/push", params={"endpoint": FCM + "a"}).json()["subscribed"] is True
        assert c.post("/api/users", json={"username": "member-one", "password": "password-123"}).status_code == 200
    with TestClient(main.app) as m:
        assert m.post("/api/login", json={"username": "member-one", "password": "password-123"}).status_code == 200
        assert m.get("/api/push").json()["devices"] == 0
        m.post("/api/push/subscribe", json={"endpoint": "https://updates.push.services.mozilla.com/wpush/v2/b", "keys": keys})
        # can't remove or test someone else's device
        m.post("/api/push/unsubscribe", json={"endpoint": FCM + "a"})
        assert m.post("/api/push/test", json={"endpoint": FCM + "a"}).status_code == 404
        assert m.get("/api/push").json()["devices"] == 1
    with main.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0] == 2


def test_push_follows_notification_schedule_and_drops_expired(tmp_path, monkeypatch):
    from datetime import datetime
    fresh(tmp_path)
    enable_push(monkeypatch)
    keys = fake_browser_keys()[2]
    sent = []

    def fake_send(sub, payload, subject):
        import json as _json
        if sub["endpoint"].endswith("gone"):
            raise main.PushGone("410")
        sent.append((sub["endpoint"], _json.loads(payload)))

    monkeypatch.setattr(main, "webpush_send", fake_send)
    with TestClient(main.app) as c:
        setup_admin(c)
        c.post("/api/plants", json={"name": "Fern", "room": "Bath",
                                     "tasks": [{"kind": "water", "interval_days": 3, "last_done": days_ago(5)}]})
        c.put("/api/notifications", json={"notify_urls": "", "notify_hour": 8, "quiet_start": 21, "quiet_end": 7,
                                           "overdue_repeat_days": 2, "public_url": ""})
        c.post("/api/push/subscribe", json={"endpoint": FCM + "phone", "keys": keys})
        c.post("/api/push/subscribe", json={"endpoint": FCM + "gone", "keys": keys})
        c.post("/api/users", json={"username": "off-user", "password": "password-123"})
        uid = [u for u in c.get("/api/users").json() if u["username"] == "off-user"][0]["id"]
    with main.db() as conn:  # a disabled user's device gets nothing
        conn.execute("INSERT INTO push_subscriptions(user_id,endpoint,p256dh,auth,created_at) VALUES(?,?,?,?,?)",
                     (uid, FCM + "disabled", keys["p256dh"], keys["auth"], "2026-01-01"))
        conn.execute("UPDATE users SET active=0 WHERE id=?", (uid,))
    today = date.today()
    at = lambda h, d=0: datetime(today.year, today.month, today.day, h) + timedelta(days=d)  # noqa: E731
    assert main.run_notification_check(at(22)) == 0  # quiet hours
    assert main.run_notification_check(at(7)) == 0  # before send hour
    assert not sent
    assert main.run_notification_check(at(9)) == 2  # example pothos + fern, no Apprise URLs needed
    assert [e for e, _ in sent] == [FCM + "phone"]
    msg = sent[0][1]
    assert msg["title"].endswith("2 plants to check") and msg["url"] == "/#/"
    assert "Fern (Bath) - overdue 2 days" in msg["body"]
    with main.db() as conn:
        endpoints = {r[0] for r in conn.execute("SELECT endpoint FROM push_subscriptions")}
    assert FCM + "gone" not in endpoints and FCM + "phone" in endpoints
    assert main.run_notification_check(at(10)) == 0  # already announced
    assert main.run_notification_check(at(9, 2)) >= 1  # overdue repeat
    # one alert per plant uses a tag per task so repeats replace, not stack
    sent.clear()
    with TestClient(main.app) as c:
        c.post("/api/login", json={"username": "admin-test", "password": "password-123"})
        c.put("/api/notifications", json={"notify_urls": "", "notify_mode": "each"})
    with main.db() as conn:
        main.set_setting(conn, "push_state", "{}")
    assert main.run_notification_check(at(9)) == 2
    assert len(sent) == 2 and all(m["tag"].startswith("plants-task-") for _, m in sent)


def test_push_and_apprise_fail_independently(tmp_path, monkeypatch):
    from datetime import datetime
    fresh(tmp_path)
    enable_push(monkeypatch)
    keys = fake_browser_keys()[2]
    pushed = []
    monkeypatch.setattr(main, "webpush_send", lambda sub, payload, subject: pushed.append(payload))
    monkeypatch.setattr(main, "send_notification", lambda urls, title, body: (False, "down"))
    with TestClient(main.app) as c:
        setup_admin(c)
        c.put("/api/notifications", json={"notify_urls": "json://localhost", "notify_hour": 0, "quiet_start": 0, "quiet_end": 0})
        c.post("/api/push/subscribe", json={"endpoint": FCM + "x", "keys": keys})
    now = datetime.now().replace(hour=12)
    try:
        main.run_notification_check(now)
        raise AssertionError("expected the Apprise failure to be reported")
    except RuntimeError as exc:
        assert "apprise" in str(exc)
    assert len(pushed) == 1  # push still went out
    with main.db() as conn:
        assert main.get_setting(conn, "notify_state", "{}") in ("", "{}")  # Apprise will retry


def test_push_payload_is_encrypted_and_vapid_signed(tmp_path, monkeypatch):
    """Real pywebpush encryption: the browser's key must be able to decrypt what we send."""
    import base64
    import json as _json

    import http_ece
    import pywebpush

    fresh(tmp_path)
    public = enable_push(monkeypatch)
    receiver, auth, keys = fake_browser_keys()
    posted = []

    class Resp:
        status_code = 201
        text = ""
        headers: dict = {}

    def fake_post(url, data=None, headers=None, timeout=None, **kw):
        posted.append((url, data, headers))
        return Resp()

    monkeypatch.setattr(pywebpush.requests, "post", fake_post)
    with TestClient(main.app) as c:
        setup_admin(c)
        c.post("/api/push/subscribe", json={"endpoint": FCM + "real", "keys": keys})
        assert c.post("/api/push/test", json={"endpoint": FCM + "real"}).json() == {"ok": True}
    url, body, headers = posted[0]
    assert url == FCM + "real"
    assert headers["content-encoding"] == "aes128gcm" and int(headers["ttl"]) == main.PUSH_TTL_SECONDS
    assert f"k={public}" in headers["authorization"]
    plain = http_ece.decrypt(body, private_key=receiver, auth_secret=auth, version="aes128gcm")
    msg = _json.loads(plain)
    assert msg["title"].endswith("test notification") and msg["url"] == "/#/"
    # JWT audience is the push service origin
    jwt = headers["authorization"].split("t=")[1].split(",")[0]
    claims = _json.loads(base64.urlsafe_b64decode(jwt.split(".")[1] + "=="))
    assert claims["aud"] == "https://fcm.googleapis.com" and claims["sub"] == "mailto:admin@example.com"
