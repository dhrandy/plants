import os
import subprocess
import time
from datetime import date, timedelta

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

PORT = 8775


@pytest.fixture(scope="module")
def app_url(tmp_path_factory):
    env = {**os.environ, "PLANTS_DATA_DIR": str(tmp_path_factory.mktemp("browser")),
           "PLANTS_NOTIFY_WORKER": "false", "PLANTS_WEATHER_OFFLINE": "true"}
    proc = subprocess.Popen(["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)], env=env)
    url = f"http://127.0.0.1:{PORT}"
    for _ in range(80):
        try:
            if httpx.get(url + "/api/status").status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    yield url
    proc.terminate()
    proc.wait(timeout=5)


def start_server(data_dir, port, extra=None):
    env = {**os.environ, "PLANTS_DATA_DIR": str(data_dir), "PLANTS_NOTIFY_WORKER": "false",
           "PLANTS_WEATHER_OFFLINE": "true", **(extra or {})}
    proc = subprocess.Popen(["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)], env=env)
    url = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            if httpx.get(url + "/api/status").status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    return proc, url


def sign_in(page, url):
    page.goto(url)
    page.wait_for_load_state("networkidle")
    page.locator("[name=username]").fill("admin-test")
    page.locator("[name=password]").fill("password-123")
    if page.get_by_role("heading", name="Set up Plants").count():
        page.get_by_role("button", name="Create administrator").click()
    else:
        page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("heading", name="Today", exact=True)).to_be_visible()


@pytest.mark.parametrize("width,height", [(1920, 1080), (390, 844)])
def test_core_flow_and_layout(app_url, width, height):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        sign_in(page, app_url)
        last = (date.today() - timedelta(days=10)).isoformat()
        page.evaluate("""async ([lastDate, w]) => { const last=[lastDate, w];
            const r = await fetch('/api/plants', {method:'POST', headers:{'Content-Type':'application/json'},
              body: JSON.stringify({name:'Browser fern '+last[1], room:'Bathroom', tasks:[{kind:'water', interval_days:3, last_done:lastDate}]})});
            if (!r.ok) throw new Error('seed failed ' + r.status);
        }""", [last, str(width)])
        page.reload()
        row = page.locator(".due", has_text=f"Browser fern {width}").first
        expect(row).to_be_visible()
        expect(row).to_contain_text("overdue")
        # touch targets on phones
        box = row.get_by_role("button", name="Done").bounding_box()
        assert width > 650 or box["height"] >= 44
        assert page.evaluate("document.documentElement.scrollWidth") <= width
        row.get_by_role("button", name="Done").click()
        expect(page.locator(".toast")).to_contain_text("Logged")
        expect(page.locator(".due", has_text=f"Browser fern {width}")).to_contain_text("In 3 days")
        # plants list and detail
        page.get_by_role("link", name="Plants", exact=True).click()
        page.locator(".plant-card", has_text=f"Browser fern {width}").first.click()
        expect(page.get_by_role("heading", name=f"Browser fern {width}")).to_be_visible()
        expect(page.locator(".timeline")).to_contain_text("Water · Done")
        assert page.evaluate("document.documentElement.scrollWidth") <= width
        # add from the starter library
        page.get_by_role("link", name="Plants", exact=True).click()
        page.get_by_role("button", name="Add plant").click()
        page.locator("#lib option").nth(3).wait_for(state="attached")
        page.select_option("#lib", "snake-plant")
        expect(page.locator("#f-species")).to_have_value("Dracaena trifasciata")
        if width < 650:
            assert page.evaluate("getComputedStyle(document.querySelector('#f-name')).fontSize") == "16px"
        page.locator("#f-name").fill(f"Snake {width}")
        page.get_by_role("button", name="Add plant").last.click()
        expect(page.get_by_role("heading", name=f"Snake {width}")).to_be_visible()
        expect(page.locator(".notes")).to_contain_text("overwater")
        # settings renders, weather card handles offline
        page.get_by_role("link", name="Settings").click()
        expect(page.get_by_role("heading", name="Weather location")).to_be_visible()
        # push section explains why it's off when the server has no VAPID keys
        expect(page.get_by_role("heading", name="Push notifications")).to_be_visible()
        expect(page.locator("#push-status")).to_contain_text("isn't set up on the server")
        expect(page.locator("#push-on")).to_be_disabled()
        assert page.evaluate("document.documentElement.scrollWidth") <= width
        page.get_by_role("link", name="Today").click()
        expect(page.locator("#weather")).to_be_visible()
        assert not errors, errors
        browser.close()


@pytest.fixture(scope="module")
def push_url(tmp_path_factory):
    from app import vapid

    public, private = vapid.generate()
    proc, url = start_server(tmp_path_factory.mktemp("push"), PORT + 1,
                             {"PLANTS_VAPID_PUBLIC_KEY": public, "PLANTS_VAPID_PRIVATE_KEY": private})
    yield url
    proc.terminate()
    proc.wait(timeout=5)


@pytest.mark.parametrize("width,height", [(1920, 1080), (390, 844)])
def test_push_toggle_and_service_worker_notification(push_url, tmp_path, width, height):
    # Full Chromium in a persistent profile: the headless shell and incognito contexts have no push.
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(tmp_path / "profile"), channel="chromium",
                                                   viewport={"width": width, "height": height})
        ctx.grant_permissions(["notifications"], origin=push_url)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        sign_in(page, push_url)
        page.get_by_role("link", name="Settings", exact=True).click()
        expect(page.get_by_role("heading", name="Push notifications")).to_be_visible()
        expect(page.locator("#push-status")).to_contain_text("Off for this device")
        expect(page.locator("#push-on")).to_be_enabled()
        toggle = page.locator("label.toggle", has=page.locator("#push-on")).bounding_box()
        assert width > 650 or toggle["height"] >= 44
        assert page.evaluate("document.documentElement.scrollWidth") <= width
        # the service worker turns a push message into a notification that points at Today
        cdp = ctx.new_cdp_session(page)
        regs = []
        cdp.on("ServiceWorker.workerRegistrationUpdated", lambda e: regs.extend(e["registrations"]))
        cdp.send("ServiceWorker.enable")
        page.evaluate("navigator.serviceWorker.ready.then(() => true)")
        for _ in range(50):
            if regs:
                break
            page.wait_for_timeout(100)
        reg_id = [r for r in regs if not r["isDeleted"]][0]["registrationId"]
        cdp.send("ServiceWorker.deliverPushMessage", {
            "origin": push_url, "registrationId": reg_id,
            "data": '{"title":"Plants: 1 plant to check","body":"Water check: Fern - due today","tag":"plants-due","url":"/#/"}'})
        shown = []
        for _ in range(50):
            shown = page.evaluate("navigator.serviceWorker.ready.then(r => r.getNotifications())"
                                  ".then(ns => ns.map(n => ({title: n.title, tag: n.tag, url: n.data.url})))")
            if shown:
                break
            page.wait_for_timeout(100)
        assert shown == [{"title": "Plants: 1 plant to check", "tag": "plants-due", "url": "/#/"}]
        assert not errors, errors
        ctx.close()
