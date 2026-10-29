import json
import logging
import re
import unicodedata
from datetime import date

import httpx
import pytest
from sqlalchemy import text

from tracker import cli
from tracker import notify as notify_mod
from tracker.ingest import ingest
from tracker.notify import UnknownBuild, change_line, description, notify

ORIGIN = "https://tracker.example"
HOOK = "https://discord.com/api/webhooks/123/SECRET-token-abc"


@pytest.fixture
def conn_db(db, fixtures):
    with db.begin() as conn:
        conn.execute(text("TRUNCATE app_user RESTART IDENTITY CASCADE"))
        conn.execute(text("INSERT INTO app_user (discord_id, username) VALUES ('1', 'u')"))
        ingest(conn, fixtures / "diff_b1", "b1", "b0")
        ingest(conn, fixtures / "diff_b2", "b2", "b1", None, date(2026, 10, 15))
    return db


def subscribe(engine, kind, target=None, asset_path=None, hook=HOOK):
    with engine.begin() as conn:
        return conn.execute(text(
            "INSERT INTO subscription (user_id, kind, asset_id, target, webhook_url) VALUES "
            "(1, :k, (SELECT id FROM asset WHERE path = :p), :t, :h) RETURNING id"),
            {"k": kind, "p": asset_path, "t": target, "h": hook}).scalar_one()


class Hook:
    """A fake Discord: answers each POST from `replies` (the last one repeats), records requests and sleeps."""

    def __init__(self, *replies):
        self.replies = list(replies) or [204]
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []
        self.use(self.handle)

    def use(self, handler):
        self.client = httpx.Client(transport=httpx.MockTransport(lambda r: self._record(r, handler)))

    def _record(self, request, handler):
        self.requests.append(request)
        return handler(request)

    def handle(self, request):
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, tuple):
            return httpx.Response(reply[0], json=reply[1])
        return httpx.Response(reply, text="" if reply < 300 else "boom")

    def run(self, engine, label="b2", dry_run=False):
        return notify(engine, label, ORIGIN, self.client, dry_run, sleep=self.sleeps.append)

    def bodies(self):
        return [json.loads(r.content) for r in self.requests]


def delivery_rows(engine):
    with engine.connect() as conn:
        return conn.execute(text("SELECT subscription_id, status, attempts, last_error FROM delivery ORDER BY 1")).all()


def disabled(engine, sub_id):
    with engine.connect() as conn:
        return conn.execute(text("SELECT disabled_at FROM subscription WHERE id = :i"), {"i": sub_id}).scalar_one()


# ---------- matching ----------

def test_asset_subscription_match_and_miss(conn_db):
    hit = subscribe(conn_db, "asset", asset_path="Game/Weapons/DA_Rifle.json")
    subscribe(conn_db, "asset", asset_path="Game/Vehicles/DA_Jeep.json", hook=HOOK + "2")  # listed in b2
    with conn_db.begin() as conn:
        conn.execute(text("INSERT INTO asset (path, name, type) VALUES ('Game/Other.json', 'Other', 'X')"))
    subscribe(conn_db, "asset", asset_path="Game/Other.json", hook=HOOK + "3")  # never listed
    h = Hook()
    s = h.run(conn_db)
    assert (s["matched"], s["sent"]) == (2, 2)
    assert [r.subscription_id for r in delivery_rows(conn_db)] == [hit, hit + 1]


def test_asset_subscription_follows_move(conn_db):
    # Subscribed by asset id: AnimSet_Mosin is listed in b1 at the old path and in b2 at the new one.
    subscribe(conn_db, "asset", asset_path="Game/New/AnimSet_Mosin.json")
    h = Hook()
    h.run(conn_db, "b1")
    h.run(conn_db, "b2")
    b1, b2 = h.bodies()
    assert b1["embeds"][0]["title"] == r"AnimSet\_Mosin changed in b1"
    assert b2["embeds"][0]["title"] == r"AnimSet\_Mosin moved in b2"
    assert b2["embeds"][0]["url"] == ORIGIN + "/asset?path=Game%2FNew%2FAnimSet_Mosin.json"


def test_area_subscription(conn_db):
    subscribe(conn_db, "area", "vehicles")
    subscribe(conn_db, "area", "nope", hook=HOOK + "2")
    h = Hook()
    assert h.run(conn_db)["matched"] == 1
    e = h.bodies()[0]["embeds"][0]
    assert e["title"] == "Vehicles changed in b2"
    assert e["url"] == ORIGIN + "/builds/b2?area=vehicles"
    assert e["description"] == "2 modified\n" r"modified  DA\_Jeep" "\n" r"modified  DA\_Truck"
    assert e["footer"]["text"] == "Watching: area vehicles"


def test_field_subscription_case_insensitive_and_literal_wildcards(conn_db):
    subscribe(conn_db, "field", "basedamage")
    subscribe(conn_db, "field", "Base_amage", hook=HOOK + "2")  # "_" must not match any character
    subscribe(conn_db, "field", "%", hook=HOOK + "3")  # "%" must not match everything
    subscribe(conn_db, "field", "Tags[", hook=HOOK + "4")
    h = Hook()
    assert h.run(conn_db)["matched"] == 2
    e = h.bodies()[0]["embeds"][0]
    assert e["title"] == "1 change matching “basedamage” in b2"
    assert e["url"] == ORIGIN + "/changes?field=basedamage&build=b2"
    assert e["description"] == r"**DA\_Rifle**  `Properties.BaseDamage`  40 ▶ 45  (+5, +13%)"


def test_no_match_no_row(conn_db):
    subscribe(conn_db, "field", "zzz")
    h = Hook()
    assert h.run(conn_db)["matched"] == 0 and not h.requests and delivery_rows(conn_db) == []


# ---------- message ----------

def test_exact_message(conn_db):
    subscribe(conn_db, "asset", asset_path="Game/Weapons/DA_Rifle.json")
    h = Hook()
    h.run(conn_db)
    assert h.bodies() == [{
        "username": "Wardogs Patch Tracker",
        "allowed_mentions": {"parse": []},
        "embeds": [{
            "title": r"DA\_Rifle changed in b2",
            "url": ORIGIN + "/asset?path=Game%2FWeapons%2FDA_Rifle.json",
            "description": "Status: modified\n`Properties.BaseDamage`  40 ▶ 45  (+5, +13%)\n"
                           "`Properties.Tags[2]`  `null` ▶ `\"Tag.X\"`",
            "footer": {"text": "Watching: asset DA_Rifle"},
        }],
    }]
    assert str(h.requests[0].url) == HOOK


def test_origin_trailing_slash_and_encoding(conn_db):
    with conn_db.begin() as conn:
        conn.execute(text("UPDATE asset SET path = 'Game/a b&c#d.json' WHERE name = 'DA_Rifle'"))
    subscribe(conn_db, "asset", asset_path="Game/a b&c#d.json")
    h = Hook()
    notify(conn_db, "b2", ORIGIN + "/", h.client, False, sleep=h.sleeps.append)
    assert h.bodies()[0]["embeds"][0]["url"] == ORIGIN + "/asset?path=Game%2Fa%20b%26c%23d.json"


def test_number_formats():
    def line(b, a):
        return change_line(type("C", (), {"field_path": "f", "before": b, "after": a})).split("  ", 1)[1]
    assert line(45, 50) == "45 ▶ 50  (+5, +11%)"
    assert line(50, 25) == "50 ▶ 25  (−25, −50%)"
    assert line(0, 3) == "0 ▶ 3  (+3)"
    assert line(0.1, 0.30000000000000004) == "0.1 ▶ 0.3  (+0.2, +200%)"
    assert line(1000, 1001) == "1000 ▶ 1001  (+1, +0.1%)"
    assert line(100000, 100001) == "100000 ▶ 100001  (+1, +<0.1%)"
    assert line(True, False) == "`true` ▶ `false`"
    assert line(None, "x" * 100) == "`null` ▶ `\"" + "x" * 78 + "…`"
    assert line([1, 2], {"a": 1}) == '`[1,2]` ▶ `{"a":1}`'


def test_truncation_with_more(conn_db):
    with conn_db.begin() as conn:
        conn.execute(text(
            "INSERT INTO change (build_id, asset_id, field_path, op, before, after) "
            "SELECT b.id, a.id, 'Extra.F' || g, 'changed', to_jsonb(g), to_jsonb(g + 1) "
            "FROM build b, asset a, generate_series(1, 14) g WHERE b.label = 'b2' AND a.name = 'DA_Rifle'"))
    subscribe(conn_db, "asset", asset_path="Game/Weapons/DA_Rifle.json")
    h = Hook()
    h.run(conn_db)
    lines = h.bodies()[0]["embeds"][0]["description"].split("\n")
    # 10 lines (the status line and 9 changes), then the rest: 16 changes + status line = 17 entries.
    assert len(lines) == 11
    assert lines[-1] == "and 7 more: " + ORIGIN + "/asset?path=Game%2FWeapons%2FDA_Rifle.json"


def test_discord_limits():
    link = "https://x/" + "y" * 100
    text_ = description(["a" * 400] * 10, 25, link)
    assert len(text_) <= 4096 and text_.endswith(f" more: {link}")
    shown = text_.count("a" * 400)
    assert shown < 10 and f"and {25 - shown} more" in text_
    assert description(["x" * 5000], 1, link) == f"and 1 more: {link}"  # an oversized line is dropped
    assert len(description([], 1, "z" * 5000)) == 4096


def test_long_title_cut(conn_db):
    target = "Properties" + "x" * 300
    with conn_db.begin() as conn:
        conn.execute(text("UPDATE change SET field_path = :f WHERE field_path = 'Properties.BaseDamage'"), {"f": target})
    subscribe(conn_db, "field", target)
    h = Hook()
    h.run(conn_db)
    title = h.bodies()[0]["embeds"][0]["title"]
    assert len(title) <= 256 and "x…”" in title


def test_everyone_in_game_string_does_not_ping(conn_db):
    with conn_db.begin() as conn:
        conn.execute(text("UPDATE change SET after = '\"@everyone\"' WHERE field_path = 'Properties.Tags[2]'"))
    subscribe(conn_db, "asset", asset_path="Game/Weapons/DA_Rifle.json")
    h = Hook()
    h.run(conn_db)
    body = h.bodies()[0]
    assert "@everyone" in body["embeds"][0]["description"]
    assert body["allowed_mentions"] == {"parse": []}


# ---------- delivery ----------

def test_2xx_sent(conn_db):
    sub = subscribe(conn_db, "area", "vehicles")
    h = Hook(204)
    s = h.run(conn_db)
    assert (s["sent"], s["failed"]) == (1, 0)
    assert delivery_rows(conn_db) == [(sub, "sent", 1, None)]


def test_429_waits_retry_after_then_sends(conn_db):
    subscribe(conn_db, "area", "vehicles")
    h = Hook((429, {"retry_after": 2.5}), 204)
    h.run(conn_db)
    assert h.sleeps == [2.5] and len(h.requests) == 2
    assert delivery_rows(conn_db)[0][1:3] == ("sent", 2)


def test_429_retry_after_capped_at_30_seconds(conn_db):
    subscribe(conn_db, "area", "vehicles")
    h = Hook((429, {"retry_after": 600}))
    s = h.run(conn_db)
    assert s["failed"] == 1 and len(h.requests) == 2 and h.sleeps == [30]  # retry_after 600 -> 30; a second 30 is over the cap
    row = delivery_rows(conn_db)[0]
    assert row[1:4] == ("failed", 2, "time budget exhausted")


def test_5xx_backoff_then_success(conn_db):
    subscribe(conn_db, "area", "vehicles")
    h = Hook(503, 500, 204)
    h.run(conn_db)
    assert h.sleeps == [1, 4]
    assert delivery_rows(conn_db)[0][1:3] == ("sent", 3)


def test_5xx_three_times_failed(conn_db):
    sub = subscribe(conn_db, "area", "vehicles")
    h = Hook(502)
    s = h.run(conn_db)
    assert s["failed"] == 1 and len(h.requests) == 3 and h.sleeps == [1, 4]
    assert delivery_rows(conn_db) == [(sub, "failed", 3, "HTTP 502: boom")]
    assert disabled(conn_db, sub) is None


@pytest.mark.parametrize("code", [401, 403, 404])
def test_gone_webhook_disables_without_retry(conn_db, code):
    sub = subscribe(conn_db, "area", "vehicles")
    h = Hook(code)
    s = h.run(conn_db)
    assert (s["failed"], s["disabled"]) == (1, 1) and len(h.requests) == 1 and h.sleeps == []
    assert delivery_rows(conn_db)[0][1:3] == ("failed", 1)
    assert disabled(conn_db, sub) is not None
    h2 = Hook()
    assert h2.run(conn_db)["matched"] == 0  # disabled: never tried again


def test_other_4xx_failed_no_retry_not_disabled(conn_db):
    sub = subscribe(conn_db, "area", "vehicles")
    h = Hook(400)
    h.run(conn_db)
    assert len(h.requests) == 1 and delivery_rows(conn_db)[0][1:3] == ("failed", 1)
    assert disabled(conn_db, sub) is None


def test_timeout_retried_then_failed(conn_db):
    subscribe(conn_db, "area", "vehicles")
    h = Hook(httpx.ReadTimeout("timed out"))
    s = h.run(conn_db)
    assert s["failed"] == 1 and len(h.requests) == 3 and h.sleeps == [1, 4]
    assert delivery_rows(conn_db)[0][3] == "network error: ReadTimeout"
    assert h.requests[0].extensions["timeout"]["read"] == 10


def test_long_response_cut_to_300(conn_db):
    subscribe(conn_db, "area", "vehicles")
    h = Hook()
    h.use(lambda r: httpx.Response(400, text="e" * 1000))
    h.run(conn_db)
    assert delivery_rows(conn_db)[0][3] == "HTTP 400: " + "e" * 299 + "…"


def test_rerun_never_double_posts_and_retries_only_failures(conn_db):
    ok = subscribe(conn_db, "area", "vehicles")
    bad = subscribe(conn_db, "field", "basedamage", hook=HOOK + "bad")
    first = Hook()
    first.use(lambda r: httpx.Response(204 if str(r.url) == HOOK else 500, text="x"))
    s = first.run(conn_db)
    assert (s["sent"], s["failed"]) == (1, 1)

    second = Hook(204)
    s = second.run(conn_db)
    assert (s["sent"], s["failed"]) == (1, 0)
    assert [str(r.url) for r in second.requests] == [HOOK + "bad"]
    assert delivery_rows(conn_db) == [(ok, "sent", 1, None), (bad, "sent", 4, None)]  # attempts add up, error cleared

    third = Hook()
    assert third.run(conn_db)["sent"] == 0 and not third.requests


def test_each_delivery_committed_before_the_next_attempt(conn_db):
    first = subscribe(conn_db, "area", "vehicles")
    subscribe(conn_db, "field", "basedamage", hook=HOOK + "2")

    def handle(request):
        if str(request.url).endswith("2"):  # the second message: the first must already be recorded
            assert delivery_rows(conn_db) == [(first, "sent", 1, None)]
            raise KeyboardInterrupt
        return httpx.Response(204)

    client = httpx.Client(transport=httpx.MockTransport(handle))
    with pytest.raises(KeyboardInterrupt):
        notify(conn_db, "b2", ORIGIN, client, False, sleep=lambda s: None)
    assert delivery_rows(conn_db) == [(first, "sent", 1, None)]


def test_disabled_subscription_skipped(conn_db):
    sub = subscribe(conn_db, "area", "vehicles")
    with conn_db.begin() as conn:
        conn.execute(text("UPDATE subscription SET disabled_at = now() WHERE id = :i"), {"i": sub})
    h = Hook()
    assert h.run(conn_db)["matched"] == 0 and not h.requests


def test_dry_run_writes_and_sends_nothing(conn_db):
    subscribe(conn_db, "area", "vehicles")
    h = Hook()
    s = h.run(conn_db, dry_run=True)
    assert s["matched"] == 1 and s["messages"][0]["subscription_id"] == 1
    assert not h.requests and delivery_rows(conn_db) == []


def test_unknown_or_unindexed_label(conn_db):
    with pytest.raises(UnknownBuild):
        Hook().run(conn_db, "nope")
    with conn_db.begin() as conn:
        conn.execute(text("INSERT INTO build (label) VALUES ('pending')"))
    with pytest.raises(UnknownBuild):
        Hook().run(conn_db, "pending")


# ---------- CLI and secrecy ----------

def run_cli(conn_db, monkeypatch, *args):
    monkeypatch.setenv("DATABASE_URL", conn_db.url.render_as_string(hide_password=False))
    monkeypatch.setenv("PUBLIC_ORIGIN", ORIGIN)
    return cli.main(["notify", *args])


def test_cli_unknown_label_exits_2(conn_db, monkeypatch, capsys):
    assert run_cli(conn_db, monkeypatch, "--label", "nope") == 2
    assert "nope" in capsys.readouterr().err


def test_cli_dry_run(conn_db, monkeypatch, capsys):
    subscribe(conn_db, "area", "vehicles")
    assert run_cli(conn_db, monkeypatch, "--label", "b2", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "subscription 1:" in out and "1 message(s) would be sent" in out and "SECRET" not in out
    assert delivery_rows(conn_db) == []


def test_cli_missing_origin_exits_2(conn_db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", conn_db.url.render_as_string(hide_password=False))
    monkeypatch.delenv("PUBLIC_ORIGIN", raising=False)
    with pytest.raises(SystemExit) as e:
        cli.main(["notify", "--label", "b2"])
    assert e.value.code == 2


def test_cli_exit_1_on_failure(conn_db, monkeypatch, capsys):
    subscribe(conn_db, "area", "vehicles")
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: real(transport=httpx.MockTransport(lambda r: httpx.Response(400))))
    assert run_cli(conn_db, monkeypatch, "--label", "b2") == 1
    assert "1 failed" in capsys.readouterr().out


def test_webhook_url_never_in_errors_logs_or_output(conn_db, caplog):
    caplog.set_level(logging.DEBUG)
    subscribe(conn_db, "area", "vehicles")
    subscribe(conn_db, "field", "basedamage", hook=HOOK + "b")
    subscribe(conn_db, "asset", asset_path="Game/Weapons/DA_Rifle.json", hook=HOOK + "c")

    def handle(r):
        if str(r.url).endswith("b"):
            raise httpx.ConnectError(f"cannot reach {r.url}")
        return httpx.Response(400, text=f"bad hook {r.url}")  # a hostile body echoing the secret URL

    h = Hook()
    h.use(handle)
    h.run(conn_db)
    errors = [r[3] for r in delivery_rows(conn_db)]
    assert len(errors) == 3 and all(errors)
    for blob in errors + [caplog.text]:
        assert "SECRET" not in blob and "discord.com" not in blob
    assert "subscription 1" in caplog.text


# ---------- hardening ----------

def bare(text_: str) -> str:
    """What is left once code spans and backslash escapes are removed: it must hold no Markdown syntax."""
    text_ = re.sub(r"`[^`\n]*`", "", text_)
    return re.sub(r"\\.", "", text_)


def test_poison_numbers_do_not_stop_the_run(conn_db):
    with conn_db.begin() as conn:
        conn.execute(text("UPDATE change SET before = '1e308'::jsonb, after = '-1e308'::jsonb "
                          "WHERE field_path = 'Properties.BaseDamage'"))
        conn.execute(text("INSERT INTO change (build_id, asset_id, field_path, op, before, after) "
                          "SELECT b.id, a.id, 'Big', 'changed', ('1' || repeat('0', 400))::jsonb, '5'::jsonb "
                          "FROM build b, asset a WHERE b.label = 'b2' AND a.name = 'DA_Rifle'"))
    subscribe(conn_db, "asset", asset_path="Game/Weapons/DA_Rifle.json")
    subscribe(conn_db, "area", "vehicles", hook=HOOK + "2")
    h = Hook()
    s = h.run(conn_db)
    assert (s["sent"], s["failed"]) == (2, 0)
    assert [r.status for r in delivery_rows(conn_db)] == ["sent", "sent"]
    desc = h.bodies()[0]["embeds"][0]["description"]
    assert "BaseDamage" in desc and "▶" in desc


def test_one_broken_subscription_does_not_stop_the_others(conn_db, monkeypatch, caplog):
    bad = subscribe(conn_db, "asset", asset_path="Game/Weapons/DA_Rifle.json")
    good = subscribe(conn_db, "area", "vehicles", hook=HOOK + "2")

    def boom(conn, sub, b, origin):
        raise OverflowError("secret https://discord.com/api/webhooks/1/x")

    monkeypatch.setitem(notify_mod.BUILDERS, "asset", boom)
    h = Hook()
    with caplog.at_level(logging.ERROR, logger="tracker.notify"):
        s = h.run(conn_db)
    assert (s["sent"], s["failed"]) == (1, 1)
    rows = {r.subscription_id: r for r in delivery_rows(conn_db)}
    assert rows[bad].status == "failed" and rows[bad].last_error == "internal error: OverflowError"
    assert rows[good].status == "sent" and len(h.requests) == 1
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert str(bad) in logged and "OverflowError" in logged and "secret" not in logged


def test_send_failure_of_one_subscription_is_isolated(conn_db):
    first = subscribe(conn_db, "area", "vehicles", hook="https://discord.com/api/webhooks/1/aaa")
    second = subscribe(conn_db, "area", "vehicles", hook="https://discord.com/api/webhooks/2/bbb")
    h = Hook()

    def handler(request):
        if "/1/" in str(request.url):
            raise ValueError("odd")
        return httpx.Response(204)

    h.use(handler)
    s = h.run(conn_db)
    rows = {r.subscription_id: r for r in delivery_rows(conn_db)}
    assert (s["sent"], s["failed"]) == (1, 1)
    assert rows[first].last_error == "internal error: ValueError" and rows[second].status == "sent"


def test_game_text_is_not_rendered_as_markdown(conn_db):
    evil_name = "[Click](http://evil) **@everyone** ||spoiler||"
    evil_path = "Properties.`x`.# heading.[0]"
    evil_value = "`; [Click](http://evil) **bold** ||spoiler||\n# heading https://evil.test/x"
    with conn_db.begin() as conn:
        conn.execute(text("UPDATE asset SET name = :n WHERE path = 'Game/Weapons/DA_Rifle.json'"), {"n": evil_name})
        conn.execute(text("UPDATE change SET field_path = :p, after = to_jsonb(CAST(:v AS text)) "
                          "WHERE field_path = 'Properties.BaseDamage'"), {"p": evil_path, "v": evil_value})
        conn.execute(text("UPDATE build_area SET title = :t WHERE area = 'vehicles'"), {"t": "[T](http://evil) **t**"})
    subscribe(conn_db, "asset", asset_path="Game/Weapons/DA_Rifle.json")
    subscribe(conn_db, "area", "vehicles", hook=HOOK + "2")
    subscribe(conn_db, "field", "heading", hook=HOOK + "3")
    h = Hook()
    h.run(conn_db)
    bodies = h.bodies()
    assert len(bodies) == 3
    for body in bodies:
        assert body["allowed_mentions"] == {"parse": []}
        e = body["embeds"][0]
        for part in (e["title"], e["description"]):
            rest = bare(part).replace("**", "")  # the only Markdown we add is the bold name prefix
            assert not any(c in rest for c in "[]()_~|>#<`"), part
            assert "://" not in rest and "\n#" not in part and "\n>" not in part, part
    asset_embed = bodies[0]["embeds"][0]
    assert asset_embed["title"] == "\\[Click\\]\\(http:\\/\\/evil\\) \\*\\*@everyone\\*\\* \\|\\|spoiler\\|\\| changed in b2"
    # Backticks in the field path and value cannot close the code span that holds them.
    assert "`Properties.ˋxˋ.# heading.[0]`" in asset_embed["description"]
    assert "`\"ˋ; [Click](http://evil) **bold** ||spoiler||\\n# heading https://evil.test/x\"`" in asset_embed["description"]
    assert bodies[1]["embeds"][0]["title"].startswith("\\[T\\]\\(http:\\/\\/evil\\) \\*\\*t\\*\\* changed")


def test_markdown_helpers():
    from tracker.notify import code, md
    assert md("[Click](http://evil)") == "\\[Click\\]\\(http:\\/\\/evil\\)"
    assert md("**@everyone** ||s|| # h") == "\\*\\*@everyone\\*\\* \\|\\|s\\|\\| \\# h"
    assert md("- a") == "\\- a" and md("1. a") == "1\\. a" and md("a\nb") == "a b"
    assert code("a`b") == "`aˋb`" and code("x" * 500, 10) == "`" + "x" * 9 + "…`" and code("") == "` `"


def test_huge_asset_name_keeps_the_embed_within_discord_limits(conn_db):
    name, path = "N" * 20_000, "Game/" + "p" * 5_000 + "/DA_Rifle.json"
    with conn_db.begin() as conn:
        conn.execute(text("UPDATE asset SET name = :n, path = :p WHERE name = 'DA_Rifle'"), {"n": name, "p": path})
    subscribe(conn_db, "asset", asset_path=path)
    h = Hook()
    h.run(conn_db)
    e = h.bodies()[0]["embeds"][0]
    assert len(e["footer"]["text"]) == 2048 and len(e["url"]) <= 2048
    assert e["url"] == ORIGIN + "/builds/b2"  # the asset link would be too long
    assert len(e["title"]) + len(e["description"]) + len(e["footer"]["text"]) <= 6000
    assert len(e["title"]) <= 256 and len(e["description"]) <= 4096


def test_invalid_stored_webhook_is_not_called_and_is_disabled(conn_db):
    bad = subscribe(conn_db, "area", "vehicles", hook="http://127.0.0.1:9/api/webhooks/1/x")
    good = subscribe(conn_db, "area", "vehicles", hook=HOOK)
    h = Hook()
    s = h.run(conn_db)
    assert [str(r.url) for r in h.requests] == [HOOK]
    rows = {r.subscription_id: r for r in delivery_rows(conn_db)}
    assert rows[bad].status == "failed" and rows[bad].last_error == "invalid webhook" and rows[bad].attempts == 0
    assert disabled(conn_db, bad) is not None and disabled(conn_db, good) is None
    assert (s["sent"], s["failed"], s["disabled"]) == (1, 1, 1)


# ---------- text hardening: domains, UTF-16 budgets, format characters ----------

ZWSP = "\N{ZERO WIDTH SPACE}"


def u16(text_: str) -> int:
    return len(text_.encode("utf-16-le")) // 2


@pytest.mark.parametrize("name", [
    "evil.com/login", "discord.gg/abc", "www.evil.com", "evil.com", "a.b.c/d", "xn--bcher-kva.example",
    "bücher.de/x", "例え.jp", "x.y", "see evil.com. now", "a..b", "(evil.com)",
])
def test_md_makes_bare_domains_unlinkable(name):
    from tracker.notify import md
    out = md(name)
    # No two word characters are left on either side of a dot: nothing for Discord to autolink.
    assert not re.search(r"\w\.\w", out), out
    assert out.replace(ZWSP, "").replace("\\", "") == name  # only zero-width spaces (and escapes) were added


def test_md_domain_output_stays_readable():
    from tracker.notify import md
    assert md("evil.com/login") == "evil." + ZWSP + "com/login"
    assert md("a.b.c/d") == "a." + ZWSP + "b." + ZWSP + "c/d"
    assert md("Mk. 2 and v1.5") == "Mk. 2 and v1." + ZWSP + "5"


def test_embed_budgets_count_utf16_units(conn_db):
    from tracker.notify import _cut
    # 130 emoji: 130 characters but 260 UTF-16 units, over Discord's 256 for a title.
    name = "😀" * 130
    assert len(name) < 256 < u16(name)
    assert u16(_cut(name, 256)) <= 256 and u16(_cut(name, 10)) == 9  # never half a surrogate pair
    with conn_db.begin() as conn:
        conn.execute(text("UPDATE asset SET name = :n WHERE name = 'DA_Rifle'"), {"n": name})
    subscribe(conn_db, "asset", asset_path="Game/Weapons/DA_Rifle.json")
    h = Hook()
    h.run(conn_db)
    e = h.bodies()[0]["embeds"][0]
    assert u16(e["title"]) <= 256 and u16(e["footer"]["text"]) <= 2048 and u16(e["description"]) <= 4096
    assert u16(e["title"]) + u16(e["description"]) + u16(e["footer"]["text"]) <= 6000


def test_emoji_heavy_description_and_total_fit_in_utf16(conn_db):
    name = "😀" * 20_000
    with conn_db.begin() as conn:
        conn.execute(text("UPDATE asset SET name = :n WHERE name = 'DA_Rifle'"), {"n": name})
        conn.execute(text("UPDATE build_area SET title = :n WHERE area = 'vehicles'"), {"n": name})
    subscribe(conn_db, "area", "weapons-ammo")
    subscribe(conn_db, "asset", asset_path="Game/Weapons/DA_Rifle.json", hook=HOOK + "2")
    h = Hook()
    h.run(conn_db)
    for body in h.bodies():
        e = body["embeds"][0]
        assert u16(e["title"]) <= 256 and u16(e["footer"]["text"]) <= 2048 and u16(e["description"]) <= 4096
        assert u16(e["title"]) + u16(e["description"]) + u16(e["footer"]["text"]) <= 6000


def test_description_limit_is_in_utf16_units():
    line = "😀" * 150  # 300 units
    out = description([line] * 10, 10, "https://x/y", limit=4096)
    assert u16(out) <= 4096 and out.count(line) == 10
    out = description([line] * 10, 10, "https://x/y", limit=1000)
    assert u16(out) <= 1000


@pytest.mark.parametrize("bad", ["\N{RIGHT-TO-LEFT OVERRIDE}", "\N{LEFT-TO-RIGHT ISOLATE}", "\N{RIGHT-TO-LEFT ISOLATE}", "\N{FIRST STRONG ISOLATE}", "\N{POP DIRECTIONAL ISOLATE}", "\N{ZERO WIDTH JOINER}", "\N{LEFT-TO-RIGHT MARK}", "\N{ZERO WIDTH NO-BREAK SPACE}", "\N{SOFT HYPHEN}"])
def test_format_characters_are_stripped(bad):
    from tracker.notify import code, md
    assert md(f"ab{bad}cd") == "abcd" and code(f"ab{bad}cd") == "`abcd`"
    assert md(f"a{ZWSP}b.c") == "ab." + ZWSP + "c"  # their zero-width space goes, ours stays


def test_bidi_override_in_game_text_does_not_reach_the_message(conn_db):
    with conn_db.begin() as conn:
        conn.execute(text("UPDATE asset SET name = :n WHERE name = 'DA_Rifle'"), {"n": "gpj.\N{RIGHT-TO-LEFT OVERRIDE}exe"})
    subscribe(conn_db, "asset", asset_path="Game/Weapons/DA_Rifle.json")
    h = Hook()
    h.run(conn_db)
    e = h.bodies()[0]["embeds"][0]
    assert not any(unicodedata.category(c) == "Cf" and c != ZWSP for c in json.dumps(e, ensure_ascii=False))


def test_md_fuzz_never_leaves_a_linkable_domain_or_a_format_character():
    import random
    from tracker.notify import md
    rng = random.Random(7)
    alphabet = ["a", "b", "Z", "9", "-", "_", ".", ".", "/", ":", " ", "é", "例", "😀", "\N{RIGHT-TO-LEFT OVERRIDE}",
                "\N{ZERO WIDTH SPACE}", "\N{FULLWIDTH FULL STOP}", "://", "xn--", "www.", "discord.gg/"]
    for _ in range(2000):
        out = md("".join(rng.choice(alphabet) for _ in range(rng.randint(0, 30))))
        assert not re.search(r"\w[.\N{IDEOGRAPHIC FULL STOP}\N{FULLWIDTH FULL STOP}\N{HALFWIDTH IDEOGRAPHIC FULL STOP}]\w", out), out
        assert "\N{RIGHT-TO-LEFT OVERRIDE}" not in out and "://" not in out


# ---------- bounded run: statement timeout and a time budget per subscription ----------

def test_notify_engine_has_a_60_second_statement_timeout(conn_db):
    engine = notify_mod.make_engine(conn_db.url.render_as_string(hide_password=False))
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SHOW statement_timeout")).scalar_one() == "1min"
    finally:
        engine.dispose()


def test_cli_notify_uses_that_engine(conn_db, monkeypatch):
    made = []
    real = cli.make_engine
    monkeypatch.setattr(cli, "make_engine", lambda url: made.append(url) or real(url))
    subscribe(conn_db, "area", "vehicles")
    assert run_cli(conn_db, monkeypatch, "--label", "b2", "--dry-run") == 0
    assert len(made) == 1


class FakeTime:
    """A clock the fake Discord and the fake sleep both advance: each request takes `took` seconds."""

    def __init__(self, took=0.0):
        self.now, self.took, self.timeouts = 1000.0, took, []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def hook(self, *replies):
        h = Hook(*replies)
        inner = h.handle

        def handler(request):
            self.timeouts.append(request.extensions["timeout"]["read"])
            self.now += self.took
            return inner(request)

        h.use(handler)
        h.sleeps = []
        return h

    def run(self, h, engine):
        def sleep(seconds):
            h.sleeps.append(seconds)
            self.sleep(seconds)
        return notify(engine, "b2", ORIGIN, h.client, False, sleep=sleep, clock=self.clock)


def test_time_budget_stops_the_attempts_and_says_so(conn_db):
    sub = subscribe(conn_db, "area", "vehicles")
    t = FakeTime(took=35)
    h = t.hook(503)
    s = t.run(h, conn_db)
    assert (s["sent"], s["failed"]) == (0, 1)
    assert len(h.requests) == 2 and h.sleeps == [1]  # the 4 s backoff would end past the 60 s budget
    assert delivery_rows(conn_db) == [(sub, "failed", 2, "time budget exhausted")]


def test_the_budget_is_per_subscription_and_each_attempt_timeout_shrinks_to_it(conn_db):
    first = subscribe(conn_db, "area", "vehicles")
    second = subscribe(conn_db, "area", "vehicles", hook=HOOK + "2")
    t = FakeTime(took=25)
    h = t.hook(503, 503, 503, 204)
    t.run(h, conn_db)
    rows = {r.subscription_id: r for r in delivery_rows(conn_db)}
    assert t.timeouts[:3] == [10, 10, pytest.approx(5.0)]  # the third attempt only has 5 s of the budget left
    assert rows[first].status == "failed" and rows[first].last_error == "HTTP 503: boom"
    assert rows[second].status == "sent"  # it got a full budget of its own


def test_429_waits_are_capped_in_total(conn_db):
    sub = subscribe(conn_db, "area", "vehicles")
    t = FakeTime()
    h = t.hook((429, {"retry_after": 30}))
    s = t.run(h, conn_db)
    assert s["failed"] == 1 and len(h.requests) == 2 and h.sleeps == [30]  # a second 30 s would pass the 40 s cap
    assert delivery_rows(conn_db) == [(sub, "failed", 2, "time budget exhausted")]


def test_429_waits_up_to_the_cap_are_taken(conn_db):
    sub = subscribe(conn_db, "area", "vehicles")
    t = FakeTime()
    h = t.hook((429, {"retry_after": 20}))
    t.run(h, conn_db)
    assert len(h.requests) == 3 and h.sleeps == [20, 20]
    row = delivery_rows(conn_db)[0]
    assert row[:3] == (sub, "failed", 3) and row[3].startswith("HTTP 429")
