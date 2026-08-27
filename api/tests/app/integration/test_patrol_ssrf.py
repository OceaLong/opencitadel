from app.application.patrol_templates import load_patrol_template


def test_builtin_pack_exposes_registered_ids_never_raw_url_promql_or_command():
    config = load_patrol_template("kubernetes-baseline-v1")
    forbidden = {"url", "promql", "query", "command", "script", "host", "port"}
    for check in config.checks:
        assert not (forbidden & set(check.probe.args))
    assert {
        check.probe.args["query_id"] for check in config.checks if check.probe.tool == "prom_query"
    } == {"app-5xx-ratio"}
    assert {
        check.probe.args["probe_id"] for check in config.checks if check.probe.tool == "http_probe"
    } == {"primary-endpoint"}
