import importlib.util
import inspect
import os
import pathlib
import unittest


def load_app_module():
    os.environ.setdefault("FEISHU_APP_ID", "test_app")
    os.environ.setdefault("FEISHU_APP_SECRET", "test_secret")
    os.environ.setdefault("LX_PROXY_TOKEN", "test_token")
    app_path = pathlib.Path(__file__).resolve().parents[1] / "app.py"
    spec = importlib.util.spec_from_file_location("wanci_app_under_test", app_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ListingReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = load_app_module()

    def sample_listing(self):
        return {
            "title": "FUNLAB Wireless Switch Controller",
            "bullets": ["Wireless controller for Switch 2 with comfortable grip"],
            "desc": "A controller for Switch players who need stable wireless play.",
            "st": "",
            "status": ["BUYABLE"],
            "authored": True,
            "has_record": True,
        }

    def sample_meta(self):
        return {
            "product": "YS11 test",
            "site": "CA",
            "asin": "B0TEST",
            "sid": 1197,
            "sku": "TEST-SKU",
            "app": "app",
            "t1": "table",
            "cat": "controller",
            "op": "陈翔宇",
            "store": "FUNLAB-CA",
            "brand": "",
            "ip_assoc": "",
            "licensed": False,
            "品牌型号": "",
        }

    def test_report_keeps_old_14_dimension_sections(self):
        rows = [
            {"关键词": "hall effect controller", "矩阵": "意图词", "月搜索量": 1200, "已出单单量": 0, "我方自然排名": 0},
        ]
        meta = self.sample_meta()
        audit = self.app.audit14(meta, self.sample_listing(), rows)
        html = self.app.render14(meta, self.sample_listing(), audit)

        for text in ["14 维诊断", "标题诊断", "五点诊断", "后台搜索词", "核心重要词", "高价值漏埋词根"]:
            self.assertIn(text, html)

    def test_onboard_delivery_uses_14_dimension_renderer(self):
        source = inspect.getsource(self.app.process)
        self.assertIn("render14", source)
        self.assertIn("listing审计_14维.html", source)
        self.assertNotIn("html=make_html", source)

    def test_excluded_and_competitor_terms_do_not_enter_direct_copy_list(self):
        rows = [
            {"关键词": "hall effect controller", "矩阵": "意图词", "月搜索量": 1200, "已出单单量": 0, "我方自然排名": 0},
            {"关键词": "manette fire tv", "矩阵": "排除-别品类", "月搜索量": 2074, "已出单单量": 0, "我方自然排名": 0},
            {"关键词": "8bitdo controller", "矩阵": "品牌词-竞品", "月搜索量": 1800, "已出单单量": 0, "我方自然排名": 0},
            {"关键词": "legend of zelda controller", "矩阵": "IP词", "月搜索量": 900, "已出单单量": 1, "我方自然排名": 0},
            {"关键词": "g7 pro controller", "矩阵": "意图词", "月搜索量": 2500, "已出单单量": 0, "我方自然排名": 0},
            {"关键词": "backbone controller", "矩阵": "意图词", "月搜索量": 2000, "已出单单量": 0, "我方自然排名": 0},
        ]
        audit = self.app.audit14(self.sample_meta(), self.sample_listing(), rows)

        direct_terms = {row["kw"] for row in audit["miss"]}
        ugc_terms = {row["kw"] for row in audit["missu"]}
        noise_terms = {row["kw"] for row in audit["noise"]}

        self.assertIn("hall effect controller", direct_terms)
        self.assertNotIn("manette fire tv", direct_terms)
        self.assertNotIn("8bitdo controller", direct_terms)
        self.assertNotIn("legend of zelda controller", direct_terms)
        self.assertNotIn("g7 pro controller", direct_terms)
        self.assertNotIn("backbone controller", direct_terms)
        self.assertIn("manette fire tv", noise_terms)
        self.assertIn("8bitdo controller", ugc_terms)
        self.assertIn("legend of zelda controller", ugc_terms)
        self.assertIn("g7 pro controller", ugc_terms)
        self.assertIn("backbone controller", ugc_terms)


if __name__ == "__main__":
    unittest.main()
