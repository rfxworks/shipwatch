import base64
import hashlib
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

import shipwatch

MINT = "Gg36bR4gXvN1hqDu6eoFNynhpfxoYg8oe47a2teZpump"
PROJECT = {"mint": MINT, "symbol": "TEST", "name": "Observer fixture", "url": "https://example.com/", "link_source": "https://dexscreener.com/solana/test"}


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state_patch = patch.object(shipwatch, "STATE", Path(self.temp.name))
        self.state_patch.start()
        shipwatch.initialize()

    def tearDown(self):
        self.state_patch.stop()
        self.temp.cleanup()

    def target(self):
        with shipwatch.database() as con:
            return dict(con.execute("SELECT * FROM targets WHERE mint=?", (MINT,)).fetchone())

    def sample(self, text="Version one", asset="a"):
        # Mirrors observe(): clock-driven ages are stabilized before the
        # fingerprint, which covers the title and the visible text.
        text = shipwatch.stabilize_text(text)
        return {
            "title": "Test project",
            "text": text,
            "fingerprint": hashlib.sha256(("Test project\n" + text).encode()).hexdigest(),
            "assets": [{"kind": "script", "url": "https://example.com/app.js", "sha256": asset * 64}],
        }

    def scan(self, sample):
        with patch.object(shipwatch, "observe", return_value=sample):
            shipwatch.scan_target(self.target())

    def add(self):
        with patch.object(shipwatch, "linked_project", return_value=PROJECT):
            return shipwatch.add_target(MINT, "fixture")

    def test_baseline_change_unavailable_and_recovery_are_persisted(self):
        self.add()
        with patch.object(shipwatch, "observe", return_value=self.sample()):
            shipwatch.scan_target(self.target())
            shipwatch.scan_target(self.target())
        self.assertEqual([entry["kind"] for entry in shipwatch.feed()["events"]], ["baseline"])
        with patch.object(shipwatch, "observe", return_value=self.sample("Version two", "b")):
            shipwatch.scan_target(self.target())
        events = shipwatch.feed()["events"]
        self.assertEqual({entry["kind"] for entry in events}, {"baseline", "page", "assets"})
        asset_event = next(entry for entry in events if entry["kind"] == "assets")
        self.assertNotEqual(asset_event["before_hash"], asset_event["after_hash"])
        with patch.object(shipwatch, "observe", side_effect=OSError("connection failed")):
            shipwatch.scan_target(self.target())
            self.assertEqual(len(shipwatch.feed()["events"]), 3)
            shipwatch.scan_target(self.target())
            shipwatch.scan_target(self.target())
        self.assertEqual(sum(entry["kind"] == "unavailable" for entry in shipwatch.feed()["events"]), 1)
        with patch.object(shipwatch, "observe", return_value=self.sample("Version two", "b")):
            shipwatch.scan_target(self.target())
        self.assertEqual(shipwatch.feed()["events"][0]["kind"], "recovered")
        self.assertEqual(self.target()["failures"], 0)

    def test_missing_asset_coverage_does_not_report_a_code_change(self):
        self.add()
        with patch.object(shipwatch, "observe", return_value=self.sample()):
            shipwatch.scan_target(self.target())
        incomplete = self.sample()
        incomplete["assets"] = [{"url": "https://example.com/app.js", "error": "HTTP 403"}]
        with patch.object(shipwatch, "observe", return_value=incomplete):
            shipwatch.scan_target(self.target())
        self.assertEqual(len(shipwatch.feed()["events"]), 1)
        self.assertEqual(shipwatch.feed()["targets"][0]["assets_read"], 0)
        with patch.object(shipwatch, "observe", return_value=self.sample(asset="b")):
            shipwatch.scan_target(self.target())
        self.assertEqual(shipwatch.feed()["events"][0]["kind"], "assets")

    def test_failed_lookups_still_consume_the_rate_budget(self):
        with patch.object(shipwatch, "linked_project", side_effect=ValueError("no website")) as lookup:
            for _ in range(6):
                with self.assertRaisesRegex(ValueError, "no website"):
                    shipwatch.add_target(MINT, "fixture")
            with self.assertRaisesRegex(ValueError, "hourly"):
                shipwatch.add_target(MINT, "fixture")
            self.assertEqual(lookup.call_count, 6)

    def test_release_changes_survive_a_temporary_lookup_failure(self):
        self.add()
        first = self.sample()
        first["release"] = {"repo": "owner/project", "id": 1, "tag": "v1", "url": "https://github.com/owner/project/releases/tag/v1"}
        with patch.object(shipwatch, "observe", return_value=first):
            shipwatch.scan_target(self.target())
        failed = self.sample()
        failed["release"] = {"repo": "owner/project", "error": "HTTP 403"}
        with patch.object(shipwatch, "observe", return_value=failed):
            shipwatch.scan_target(self.target())
        second = self.sample()
        second["release"] = {"repo": "owner/project", "id": 2, "tag": "v2", "url": "https://github.com/owner/project/releases/tag/v2"}
        with patch.object(shipwatch, "observe", return_value=second):
            shipwatch.scan_target(self.target())
        event = shipwatch.feed()["events"][0]
        self.assertEqual(event["kind"], "release")
        self.assertEqual(event["removed"], ["v1"])
        self.assertEqual(event["added"], ["v2"])
        self.assertTrue(event["detail_url"].endswith("/v2"))

    def test_github_cache_reuses_a_public_lookup(self):
        raw = json.dumps([{"id": 1, "tag_name": "v1", "published_at": "2026-11-12T00:00:00Z"}]).encode()
        with patch.object(shipwatch, "fetch", return_value=(raw, "application/json", "")) as fetch:
            first = shipwatch.github_release("owner/project")
            second = shipwatch.github_release("owner/project")
        self.assertEqual(first, second)
        self.assertEqual(fetch.call_count, 1)

    def test_private_destination_is_blocked_before_connection(self):
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("169.254.169.254", 0))]
        with patch.object(socket, "getaddrinfo", return_value=addresses), patch.object(socket, "create_connection") as connect:
            with self.assertRaisesRegex(ValueError, "public internet"):
                shipwatch.fetch("http://example.com/")
        connect.assert_not_called()

    def test_relative_age_counters_do_not_produce_page_edits(self):
        self.assertEqual(shipwatch.stabilize_text("Deployed 12hold build, 3.5mopush"), "Deployed <age>old build, <age>push")
        self.assertEqual(shipwatch.stabilize_text("Version 12 holds"), "Version 12 holds")
        self.add()
        self.scan(self.sample("Latest commit 5mold"))
        self.scan(self.sample("Latest commit 9mold"))
        self.assertEqual([entry["kind"] for entry in shipwatch.feed()["events"]], ["baseline"])

    def test_recurring_page_edits_are_counted_and_excluded_from_latest_signal(self):
        self.add()
        self.scan(self.sample("Banner A"))
        self.scan(self.sample("Banner B"))
        first_edit = shipwatch.feed()["events"][0]
        self.assertEqual((first_edit["kind"], first_edit["repeat_count"]), ("page", 0))
        # Flipping back and forth shows the same before/after excerpts, so each
        # later flip counts the earlier stored observations of that pattern.
        self.scan(self.sample("Banner A"))
        self.assertEqual(shipwatch.feed()["events"][0]["repeat_count"], 1)
        self.scan(self.sample("Banner B"))
        repeated = shipwatch.feed()["events"][0]
        self.assertEqual((repeated["kind"], repeated["repeat_count"]), ("page", 2))
        latest = shipwatch.feed()["targets"][0]["latest_signal"]
        self.assertEqual((latest["kind"], latest["id"]), ("page", first_edit["id"]))

    def test_alternating_asset_sets_are_counted_and_listed_once(self):
        self.add()
        self.scan(self.sample(asset="a"))
        self.scan(self.sample(asset="b"))
        first_change = shipwatch.feed()["events"][0]
        self.assertEqual((first_change["kind"], first_change["repeat_count"]), ("assets", 0))
        # A site that alternates between two builds returns to bytes already seen.
        for asset in "abab":
            self.scan(self.sample(asset=asset))
        with shipwatch.database() as con:
            stored = [row["repeat_count"] for row in con.execute("SELECT repeat_count FROM events WHERE kind='assets' ORDER BY id")]
        self.assertEqual(stored, [0, 1, 1, 2, 2])
        feed = shipwatch.feed()
        listed = [(entry["kind"], entry["repeat_count"]) for entry in feed["events"]]
        self.assertEqual(listed, [("assets", 2), ("assets", 0), ("baseline", 0)])
        self.assertEqual(feed["targets"][0]["latest_signal"]["id"], first_change["id"])

    def test_stored_asset_changes_are_counted_once_on_upgrade(self):
        self.add()
        self.scan(self.sample(asset="a"))
        for asset in "bab":
            self.scan(self.sample(asset=asset))
        with shipwatch.database() as con:
            con.execute("UPDATE events SET repeat_count=0")
            con.execute("PRAGMA user_version=0")
        shipwatch.initialize()
        with shipwatch.database() as con:
            stored = [row["repeat_count"] for row in con.execute("SELECT repeat_count FROM events WHERE kind='assets' ORDER BY id")]
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertEqual(stored, [0, 1, 1])

    def test_recurring_changes_do_not_push_out_first_time_observations(self):
        self.add()
        self.scan(self.sample(asset="a"))
        self.scan(self.sample("Version two", "a"))
        for asset in "ba" * 45:
            self.scan(self.sample("Version two", asset))
        kinds = [entry["kind"] for entry in shipwatch.feed()["events"]]
        self.assertEqual(kinds, ["assets", "assets", "page", "baseline"])

    def test_linked_repositories_skip_reserved_paths_and_duplicates(self):
        links = [
            "https://github.com/owner/project", "https://github.com/owner/project.git",
            "https://www.github.com/owner/project/releases", "https://github.com/sponsors/owner",
            "https://github.com/login", "https://example.com/owner/project", "/relative/path",
        ]
        self.assertEqual(shipwatch.linked_repositories(links, "https://example.com/"), ["owner/project"])
        links.append("https://github.com/other/tool")
        self.assertEqual(shipwatch.linked_repositories(links, "https://example.com/"), ["owner/project", "other/tool"])

    def test_several_linked_repositories_pause_release_attribution(self):
        html = (b"<html><head><title>Project</title></head><body><p>Docs</p>"
                b"<a href='https://github.com/owner/one'>one</a><a href='https://github.com/owner/two'>two</a></body></html>")
        with patch.object(shipwatch, "fetch", return_value=(html, "text/html", "https://example.com/")), \
                patch.object(shipwatch, "github_release") as lookup:
            sample = shipwatch.observe("https://example.com/")
        lookup.assert_not_called()
        self.assertTrue(sample["release"]["ambiguous"])
        self.assertEqual(sample["release"]["count"], 2)
        self.add()
        first = self.sample()
        first["release"] = {"repo": "owner/one", "id": 1, "tag": "v1", "url": "https://github.com/owner/one/releases/tag/v1"}
        self.scan(first)
        paused = self.sample()
        paused["release"] = sample["release"]
        self.scan(paused)
        self.assertEqual([entry["kind"] for entry in shipwatch.feed()["events"]], ["baseline"])
        self.assertTrue(shipwatch.feed()["targets"][0]["release_info"]["ambiguous"])

    def test_repository_link_changes_mark_earlier_evidence_as_ambiguous(self):
        self.add()
        for repo in (None, "owner/one", "owner/two"):
            sample = self.sample()
            sample["release"] = {"repo": repo, "id": 1, "tag": "v1"} if repo else None
            self.scan(sample)
        repository_events = [entry for entry in shipwatch.feed()["events"] if entry["kind"] == "repository"]
        self.assertEqual(len(repository_events), 2)
        self.assertTrue(all(entry["association_ambiguous"] for entry in repository_events))

    def test_health_reports_the_running_source_hash(self):
        expected = hashlib.sha256(Path(shipwatch.__file__).read_bytes()).hexdigest()
        self.assertEqual(shipwatch.CODE_SHA, expected)

    def test_visits_are_kept_as_daily_totals_and_one_connection_cannot_inflate_them(self):
        shipwatch.VISIT_TALLY.update(day="", connections={})
        self.assertTrue(shipwatch.record_visit("connection-a", True))
        self.assertTrue(shipwatch.record_visit("connection-b", False))
        for _ in range(40):
            shipwatch.record_visit("connection-a", True)
        totals = shipwatch.stats()
        self.assertEqual(totals["opens"], shipwatch.VISIT_OPENS_PER_CONNECTION + 1)
        self.assertEqual(totals["visitors"], shipwatch.VISIT_NEW_PER_CONNECTION)
        self.assertEqual(len(totals["days"]), 1)
        self.assertEqual(totals["counting_since"], totals["days"][0]["day"])
        with shipwatch.database() as con:
            columns = [row[1] for row in con.execute("PRAGMA table_info(visits)")]
        self.assertEqual(columns, ["day", "opens", "new_visitors"], "only daily totals are stored")

    def test_visit_and_stats_http_entry_points(self):
        shipwatch.VISIT_TALLY.update(day="", connections={})
        server = shipwatch.ThreadingHTTPServer(("127.0.0.1", 0), shipwatch.Handler)
        server.bucket_secret = "test-secret"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for body, counted in (({"new": True}, True), ({"new": "yes"}, True), ({}, True)):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                connection.request("POST", "/api/visit", body=json.dumps(body), headers={"Content-Type": "application/json"})
                response = connection.getresponse()
                self.assertEqual((response.status, json.load(response)["counted"]), (200, counted))
                connection.close()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            connection.request("GET", "/api/stats")
            totals = json.load(connection.getresponse())
            connection.close()
            self.assertEqual((totals["visitors"], totals["opens"]), (1, 3), "only a literal true counts as a new visitor")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_watch_http_entry_point_and_duplicate_submission(self):
        server = shipwatch.ThreadingHTTPServer(("127.0.0.1", 0), shipwatch.Handler)
        server.bucket_secret = "test-secret"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(shipwatch, "linked_project", return_value=PROJECT) as lookup:
                for expected in (True, False):
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                    connection.request("POST", "/api/watch", body=json.dumps({"mint": MINT}), headers={"Content-Type": "application/json"})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.load(response)["created"], expected)
                    connection.close()
                self.assertEqual(lookup.call_count, 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def metadata_fixture(self, website):
        raw = json.dumps({"name": "Fixture", "symbol": "FIX", "website": website}).encode()
        address = "b" + base64.b32encode(b"\x01\x55\x12\x20" + hashlib.sha256(raw).digest()).decode().lower().rstrip("=")
        return raw, address, {"name": "Fixture coin", "symbol": "FIX", "uri": "https://ipfs.io/ipfs/" + address}

    def test_a_coin_without_a_listed_website_is_read_from_its_own_metadata(self):
        raw, address, record = self.metadata_fixture("https://project.example/")

        def answers(url, limit=shipwatch.MAX_BYTES, redirects=3):
            if url.startswith("https://api.dexscreener.com/"):
                return json.dumps([{"baseToken": {"address": MINT}, "pairAddress": "pair"}]).encode(), "application/json", url
            if url.startswith("https://ipfs.io/"):
                raise ValueError("Website returned HTTP 429.")
            if url == shipwatch.IPFS_GATEWAYS[0] + address:
                self.assertEqual(limit, shipwatch.MAX_METADATA_BYTES)
                return raw, "application/json", url
            raise AssertionError("unexpected request: " + url)

        with patch.object(shipwatch, "fetch", side_effect=answers), patch.object(shipwatch, "mint_metadata", return_value=record):
            project = shipwatch.linked_project(MINT)
        self.assertEqual(project, {"mint": MINT, "symbol": "FIX", "name": "Fixture coin", "url": "https://project.example/",
                                   "link_source": "https://ipfs.io/ipfs/" + address})

    def test_a_listed_website_is_used_without_reading_the_chain(self):
        listing = [{"baseToken": {"address": MINT, "symbol": "TEST", "name": "Listed"}, "pairAddress": "pair",
                    "info": {"websites": [{"url": "https://listed.example/"}]}}]
        with patch.object(shipwatch, "fetch", return_value=(json.dumps(listing).encode(), "application/json", "")), \
                patch.object(shipwatch, "mint_metadata") as chain:
            project = shipwatch.linked_project(MINT)
        self.assertEqual((project["url"], project["link_source"]), ("https://listed.example/", "https://dexscreener.com/solana/pair"))
        chain.assert_not_called()

    def test_metadata_that_does_not_match_its_content_address_is_refused(self):
        raw, address, record = self.metadata_fixture("https://project.example/")
        forged = json.dumps({"website": "https://elsewhere.example/"}).encode()

        def answers(url, limit=shipwatch.MAX_BYTES, redirects=3):
            if url.startswith("https://api.dexscreener.com/"):
                return b"[]", "application/json", url
            return forged, "application/json", url

        with patch.object(shipwatch, "fetch", side_effect=answers), patch.object(shipwatch, "mint_metadata", return_value=record):
            with self.assertRaisesRegex(ValueError, "metadata file could not be read"):
                shipwatch.linked_project(MINT)

    def test_metadata_without_a_project_website_is_reported_plainly(self):
        for website, message in (("https://x.com/someone/status/1", "social or launchpad page"), ("", "in DEX Screener or in the token's own metadata"),
                                 ("javascript:alert(1)", "unsupported"), ("https://user@project.example/", "unsupported")):
            raw, address, record = self.metadata_fixture(website)

            def answers(url, limit=shipwatch.MAX_BYTES, redirects=3, raw=raw):
                return (b"[]" if url.startswith("https://api.dexscreener.com/") else raw), "application/json", url

            with patch.object(shipwatch, "fetch", side_effect=answers), patch.object(shipwatch, "mint_metadata", return_value=record):
                with self.assertRaisesRegex(ValueError, message):
                    shipwatch.linked_project(MINT)
        with patch.object(shipwatch, "fetch", return_value=(b"[]", "application/json", "")), patch.object(shipwatch, "mint_metadata", return_value=None):
            with self.assertRaisesRegex(ValueError, "in DEX Screener or in the token's own metadata"):
                shipwatch.linked_project(MINT)

    def test_the_on_chain_record_is_read_from_the_fixed_rpc_host(self):
        state = {"name": "Fixture coin", "symbol": "FIX", "uri": "ipfs://" + "b" * 59, "updateAuthority": None}
        answer = {"result": {"value": {"data": {"parsed": {"info": {"extensions": [
            {"extension": "metadataPointer", "state": {}}, {"extension": "tokenMetadata", "state": state}]}}}}}}
        seen = {}

        class Connection:
            def __init__(self, host, port, timeout):
                seen["host"] = host

            def request(self, method, path, body, headers):
                seen["call"] = json.loads(body)

            def getresponse(self):
                reply = unittest.mock.Mock(status=200)
                reply.read.return_value = json.dumps(answer).encode()
                return reply

            def close(self):
                seen["closed"] = True

        with patch.object(http.client, "HTTPSConnection", Connection):
            record = shipwatch.mint_metadata(MINT)
            answer["result"]["value"] = None
            self.assertIsNone(shipwatch.mint_metadata(MINT))
        self.assertEqual(record, {"name": "Fixture coin", "symbol": "FIX", "uri": "ipfs://" + "b" * 59})
        self.assertEqual((seen["host"], seen["call"]["method"], seen["call"]["params"][0], seen["closed"]),
                         ("api.mainnet-beta.solana.com", "getAccountInfo", MINT, True))


if __name__ == "__main__":
    unittest.main()
