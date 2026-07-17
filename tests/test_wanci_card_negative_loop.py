import importlib.util
import os
import pathlib
import unittest


def load_app_module():
    os.environ.setdefault("FEISHU_APP_ID", "test_app")
    os.environ.setdefault("FEISHU_APP_SECRET", "test_secret")
    os.environ.setdefault("LX_PROXY_TOKEN", "test_token")
    app_path = pathlib.Path(__file__).resolve().parents[1] / "app.py"
    spec = importlib.util.spec_from_file_location("wanci_app_cards_under_test", app_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WanciCardNegativeLoopTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = load_app_module()

    def test_issue_card_contains_required_actions(self):
        card = self.app.build_wanci_issue_card({
            "计划记录ID": "rec1",
            "产品": "Test Product",
            "站点": "CA",
            "ASIN": "B0TEST1234",
            "负责人": "陈翔宇",
            "店铺编号": "1197",
            "店铺 SKU": "SKU-1",
            "问题类型": "Listing 需先处理",
            "问题说明": "后台搜索词没填",
        })
        actions = []
        for el in card["elements"]:
            for btn in el.get("actions", []):
                actions.append(btn["value"]["action"])
        self.assertEqual({
            "wanci_issue_done",
            "wanci_issue_skip",
            "wanci_issue_reassign",
            "wanci_issue_help",
        }, set(actions))

    def test_issue_callback_writes_dry_ledger_when_table_not_configured(self):
        payload = {
            "event": {
                "operator": {"open_id": "ou_test"},
                "context": {"open_message_id": ""},
                "action": {
                    "value": {
                        "action": "wanci_issue_done",
                        "事项键": "issue-1",
                        "产品": "Test Product",
                        "站点": "CA",
                        "ASIN": "B0TEST1234",
                        "负责人": "陈翔宇",
                        "问题类型": "Listing 需先处理",
                        "问题说明": "后台搜索词没填",
                    },
                    "form_value": {"处理说明": "后台搜索词已补"},
                },
            }
        }
        result = self.app.handle_wanci_card_callback(payload, patch_original=False)
        self.assertTrue(result["ok"])
        self.assertEqual("issue", result["type"])
        self.assertTrue(result["write"]["skipped"])

    def test_negative_candidates_are_grouped_by_ad_group_and_exclude_asin(self):
        rows = [
            {"campaign_id": "c1", "ad_group_id": "g1", "query": "bad keyword", "cost": 4, "orders": 0, "sales": 0, "clicks": 5},
            {"campaign_id": "c1", "ad_group_id": "g1", "query": "bad keyword", "cost": 3, "orders": 0, "sales": 0, "clicks": 2},
            {"campaign_id": "c1", "ad_group_id": "g2", "query": "bad keyword", "cost": 7, "orders": 1, "sales": 4, "clicks": 10},
            {"campaign_id": "c1", "ad_group_id": "g2", "query": "B0ABCDEF12", "cost": 20, "orders": 0, "sales": 0, "clicks": 10},
        ]
        out = self.app.build_negative_candidates_from_rows(rows, sid="1197", sku="SKU-1", plan_asin="B0PLAN")
        keys = {(x["广告组ID"], x["搜索词"]) for x in out}
        self.assertIn(("g1", "bad keyword"), keys)
        self.assertIn(("g2", "bad keyword"), keys)
        self.assertNotIn(("g2", "B0ABCDEF12"), keys)
        self.assertEqual(2, len(out))

    def test_negative_lines_parse_after_operator_deletes_one_line(self):
        candidates = [
            {"建议键": "k1", "广告活动ID": "c1", "广告组ID": "g1", "搜索词": "bad keyword", "建议原因": "花费高0订单"},
            {"建议键": "k2", "广告活动ID": "c2", "广告组ID": "g2", "搜索词": "worse keyword", "建议原因": "ACoS高"},
        ]
        text, line_map = self.app.negative_lines(candidates)
        kept = text.splitlines()[1]
        parsed, errors = self.app.parse_negative_lines(kept, line_map, {"店铺编号": "1197"})
        self.assertEqual([], errors)
        self.assertEqual(1, len(parsed))
        self.assertEqual("k2", parsed[0]["建议键"])
        self.assertEqual("1197", parsed[0]["店铺编号"])

    def test_negative_write_defaults_to_dry_run_or_blocked(self):
        item = {
            "建议键": "k1",
            "店铺编号": "1197",
            "广告活动ID": "c1",
            "广告组ID": "g1",
            "搜索词": "bad keyword",
            "建议否定方式": "精准否定",
        }
        dry = self.app.apply_negative_keywords([item], commit=False, operator="ou_test")
        self.assertEqual("dry_run", dry[0]["status"])
        blocked = self.app.apply_negative_keywords([dict(item, 建议键="k2")], commit=True, operator="ou_test")
        self.assertEqual("blocked", blocked[0]["status"])


if __name__ == "__main__":
    unittest.main()
