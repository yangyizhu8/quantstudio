from quantstudio.pipeline.config_lint import lint_configs


def test_enabled_source_requires_registered_adapter():
    errors, _ = lint_configs(
        {}, {"sources": {"ghost": {"enabled": True}}},
        {"tasks": [{"name": "x", "source": "ghost", "table": "stock_daily",
                    "codes": ["ALL"]}]},
        {"schemas": {"stock_daily": {"primary_key": ["code", "time"]}}})
    assert any("没有注册 Adapter" in error for error in errors)
