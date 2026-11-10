import io
import json
import shutil
import tempfile
import unittest
import urllib.error
from pathlib import Path

from synframe.generate import run, select, SpecError
from synframe.providers.lk888 import Client, ProviderError, quote

PNG = bytes.fromhex("89504e470d0a1a0a") + b"synthetic product, not a real image"
PRICES = {
    "tt-image-2.6": [{"group_name": "img", "is_active": True, "in_key_whitelist": True, "excluded_for_this_key": False,
                      "billing_method": "按次", "base_price": 0.029, "option_prices": []}],
    "omni_flash-10s": [{"group_name": "o1", "is_active": True, "in_key_whitelist": True, "excluded_for_this_key": False,
                        "billing_method": "按次", "base_price": 0.95, "option_prices": []},
                       {"group_name": "off", "is_active": False, "in_key_whitelist": True, "excluded_for_this_key": False,
                        "billing_method": "按次", "base_price": 9.0, "option_prices": []}],
    "minimax-h3": [{"group_name": "h3a", "is_active": True, "in_key_whitelist": True, "excluded_for_this_key": False,
                    "billing_method": "按秒", "base_price": 0.2,
                    "option_prices": [{"option_value": "768P", "final_price": 0.11}]},
                   {"group_name": "h3b", "is_active": True, "in_key_whitelist": True, "excluded_for_this_key": False,
                    "billing_method": "按秒", "base_price": 0.3,
                    "option_prices": [{"option_value": "768P", "final_price": 0.16}]}],
}


class FakeClient:
    def __init__(self, balance=100.0):
        self.sent, self.polls, self._balance = [], 0, balance

    def snapshot(self, models):
        return {"captured_at": "test", "balance": self._balance, "models": {m: PRICES[m] for m in models}}

    def balance(self):
        return self._balance

    def transport(self, frozen):
        assert isinstance(frozen, bytes)
        self.sent.append(json.loads(frozen))
        return {"task_id": "task-%d" % len(self.sent), "cost": None}

    def status(self, task_id):
        self.polls += 1
        return {"final": True, "state": "success", "result_url": "https://example.invalid/%s.png" % task_id,
                "cost": 0.03, "error": None}

    def download(self, url, destination):
        Path(destination).write_bytes(PNG + url.encode())
        return destination


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class GenerateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / "product.png").write_bytes(PNG)
        (self.tmp / "brief.md").write_text("plan")
        self.common = {"ledger": "ledger.sqlite3", "reviewer": "test", "creative_plan": "brief.md",
                       "product_images": ["product.png"], "poll": {"interval_sec": 0, "timeout_sec": 5}}

    def image_spec(self, cap=0.2):
        return write(self.tmp / "img.json", {**self.common, "batch_id": "img-1", "out_dir": "out/img",
                     "defaults": {"model": "tt-image-2.6"},
                     "authorization": {"spend_cap": cap, "max_unit_price": 0.05},
                     "jobs": [{"id": "i1", "prompt": "ceiling"}, {"id": "i2", "prompt": "creator"}]})

    def test_dry_run_spends_nothing_and_never_needs_a_client(self):
        result = run(self.image_spec(), "dry-run", client=None, log=lambda m: None)
        self.assertEqual(result["status"], "ready_offline")
        self.assertFalse((self.tmp / "ledger.sqlite3").exists())

    def test_image_select_video_flow_and_rerun_is_idempotent(self):
        client = FakeClient()
        result = run(self.image_spec(), "submit", client=client, log=lambda m: None)
        self.assertEqual(result["counts"], {"success": 2})
        self.assertEqual(len(client.sent), 2)
        again = run(self.image_spec(), "submit", client=client, log=lambda m: None)
        self.assertEqual(len(client.sent), 2, "a re-run must never resubmit")
        self.assertEqual(again["status"], "finished")
        picks = write(self.tmp / "select.json", {"ledger": "ledger.sqlite3", "image_batch": "img-1", "reviewer": "t",
                      "picks": [{"selection_id": "p1", "slot": "A", "candidate": "i1", "reason": "clean"}]})
        self.assertEqual(select(picks)["recorded"], ["p1"])
        self.assertEqual(select(picks)["already_recorded"], ["p1"])
        video = write(self.tmp / "vid.json", {**self.common, "batch_id": "vid-1", "out_dir": "out/vid",
                      "authorization": {"spend_cap": 3.0, "max_unit_price": 1.0},
                      "jobs": [{"id": "hooks", "model": "omni_flash-10s", "keyframes": ["p1"], "prompt": "three scenes"},
                               {"id": "body", "model": "minimax-h3", "duration": 12, "keyframes": ["p1"],
                                "prompt": "one take"}]})
        quoted = run(video, "quote", client=client, log=lambda m: None)
        self.assertEqual(quoted["quotes"]["hooks"]["estimate"], 0.95)
        self.assertEqual(quoted["quotes"]["body"]["estimate"], 1.92, "worst usable 768P price x 12 s")
        done = run(video, "submit", client=client, log=lambda m: None)
        self.assertEqual(done["counts"], {"success": 2})
        h3 = client.sent[-1]
        self.assertEqual(h3["params"]["mode"], "cankaosheng")
        self.assertEqual(len(h3["params"]["image_url"]), 2, "PDP identity + picked keyframe")

    def test_cap_stops_before_any_paid_call(self):
        client = FakeClient()
        result = run(self.image_spec(cap=0.03), "submit", client=client, log=lambda m: None)
        self.assertEqual(result["status"], "stopped_before_spend")
        self.assertEqual(client.sent, [])

    def test_low_balance_stops(self):
        client = FakeClient(balance=0.01)
        self.assertEqual(run(self.image_spec(), "submit", client=client, log=lambda m: None)["status"],
                         "stopped_before_spend")
        self.assertEqual(client.sent, [])

    def test_submit_requires_caps(self):
        spec = write(self.tmp / "nocap.json", {**self.common, "batch_id": "b", "out_dir": "o",
                     "jobs": [{"id": "x", "model": "tt-image-2.6", "prompt": "p"}]})
        with self.assertRaises(SpecError):
            run(spec, "submit", client=FakeClient(), log=lambda m: None)

    def test_h3_needs_integer_duration(self):
        spec = write(self.tmp / "h3.json", {**self.common, "batch_id": "b", "out_dir": "o",
                     "jobs": [{"id": "x", "model": "minimax-h3", "prompt": "p"}]})
        result = run(spec, "dry-run", log=lambda m: None)
        self.assertEqual(result["status"], "blocked")

    def test_changed_prompt_needs_new_batch(self):
        client = FakeClient()
        run(self.image_spec(), "submit", client=client, log=lambda m: None)
        data = json.loads((self.tmp / "img.json").read_text())
        data["jobs"][0]["prompt"] = "changed"
        write(self.tmp / "img.json", data)
        with self.assertRaises(SpecError):
            run(self.tmp / "img.json", "submit", client=client, log=lambda m: None)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    def __init__(self, body=b'{"balance": 12.5}', error=None):
        self.requests, self.body, self.error = [], body, error

    def open(self, request, timeout=None):
        self.requests.append(request)
        if self.error:
            raise self.error
        return FakeResponse(self.body)


class ProviderTests(unittest.TestCase):
    def test_auth_header_and_balance(self):
        opener = FakeOpener()
        self.assertEqual(Client("sk-secret", opener=opener).balance(), 12.5)
        self.assertEqual(opener.requests[0].get_header("Authorization"), "Bearer sk-secret")

    def test_errors_never_contain_the_key(self):
        err = urllib.error.HTTPError("u", 401, "no", {}, io.BytesIO(b"bad key sk-secret"))
        with self.assertRaises(ProviderError) as ctx:
            Client("sk-secret", opener=FakeOpener(error=err)).balance()
        self.assertNotIn("sk-secret", str(ctx.exception))

    def test_missing_key(self):
        with self.assertRaises(ProviderError):
            Client("", opener=FakeOpener()).balance()

    def test_quote_billing(self):
        snap = {"models": PRICES}
        per_call = quote({"model": "omni_flash-10s", "params": {}}, snap, "s")
        self.assertEqual((per_call["estimate"], per_call["billing"]), (0.95, "per_call"))
        per_sec = quote({"model": "minimax-h3", "params": {"duration": 5, "resolution": "768P"}}, snap, "s")
        self.assertEqual((per_sec["estimate"], per_sec["billing"]), (0.8, "per_second"))
        with self.assertRaises(ProviderError):
            quote({"model": "minimax-h3", "params": {"resolution": "768P"}}, snap, "s")

    def test_download_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.mp4"
            target.write_bytes(b"keep")
            with self.assertRaises(ProviderError):
                Client("k", opener=FakeOpener(b"x")).download("https://e.invalid/a.mp4", target)
            self.assertEqual(target.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
