"""Where each test file lives, and why that is a table rather than a rule.

`tests/` mirrors `src/fantabot/`: a test sits in the layer of the thing it is about. Two
mechanical ways to derive that were tried and both are wrong often enough to mislead,
which is worse than not mirroring at all -- a misfiled test tells a reader the module is
in a layer it is not.

* **Voting on the file's imports** puts `test_token_store.py` in `domain/` because the
  store's tests build claims, ciphers and errors around one adapter call.
* **Matching the filename to a module leaf** puts `test_state.py` under `domain/asta/`
  (there is a `state.py` there) when it is about `adapters/browser/storage_state.py`, and
  `test_apileague_client.py` under the harvest client.

So the subject of each file is written down. The entries that are not obvious carry the
reason; the rest are their own explanation.

`tests/` deliberately has no `__init__.py`, here or in any subdirectory. pytest derives a
module name from the path relative to rootdir, and the suite's helpers (`_paths`,
`_golden`, `_importgraph`) are imported by bare name, which works because `conftest.py`
puts `tests/` on `sys.path`. Adding `__init__.py` would change both and is not needed:
no two test files share a basename, which `test_testtree.py` checks.
"""

from __future__ import annotations

#: file -> directory under `tests/`. The subject's layer, then its feature.
TREE: dict[str, str] = {
    # -- domain/asta: the decision logic ------------------------------------------------
    "test_asta_bargain.py": "domain/asta",
    "test_asta_bid.py": "domain/asta",
    "test_asta_drain.py": "domain/asta",
    "test_asta_edge.py": "domain/asta",
    "test_asta_legality.py": "domain/asta",
    "test_asta_live.py": "domain/asta",
    "test_asta_opponents.py": "domain/asta",
    "test_asta_optimizer.py": "domain/asta",
    "test_asta_prices.py": "domain/asta",
    "test_asta_report.py": "domain/asta",
    "test_asta_reservation.py": "domain/asta",
    "test_asta_sentiment.py": "domain/asta",
    "test_asta_stateentry.py": "domain/asta",
    "test_asta_rules_for_room.py": "domain/asta",
    "test_asta_room_url.py": "domain/asta",
    "test_asta_seconds_left.py": "domain/asta",
    "test_asta_listone_rows.py": "domain/asta",
    "test_asta_copilot.py": "domain/asta",
    "test_asta_max_cap.py": "domain/asta",
    "test_asta_unvaluable.py": "domain/asta",
    "test_asta_value.py": "domain/asta",
    "test_roster.py": "domain/lineup",
    # -- domain/lega: the platform's own JSON, translated
    "test_lega_parse.py": "domain/lega",
    "test_schema.py": "domain/lineup",
    "test_value.py": "domain/lineup",
    "test_build.py": "domain/lineup",
    "test_bench.py": "domain/lineup",
    "test_marle.py": "domain/lineup",
    "test_competition.py": "domain/lineup",
    "test_payload.py": "domain/lineup",
    # The rule is about the whole asta feature including its command, but what it
    # protects -- one calendar seam for the golden harness -- is a property of the
    # decision layer, which is the half that must be deterministic.
    "test_asta_clock.py": "domain/asta",
    # `ruolo_campo` must never reach a decision module. That is a domain rule about
    # domain modules, checked over the domain package.
    "test_asta_sentiment_wiring.py": "domain/asta",
    "test_asta_cycle_cost.py": "domain/asta",
    # -- domain/harvest ------------------------------------------------------------------
    "test_aste_backfill.py": "domain/harvest",
    "test_aste_incremental.py": "domain/harvest",
    "test_aste_models.py": "domain/harvest",
    "test_aste_reconstruct.py": "domain/harvest",
    "test_aste_reducer.py": "domain/harvest",
    "test_aste_registry.py": "domain/harvest",
    "test_aste_sse.py": "domain/harvest",
    "test_aste_fixtures.py": "domain/harvest",
    # `compare.py` is the domain module; the `scripts/` file of nearly the same name was
    # deleted in W2, and the two were conflated once already.
    "test_compare_collectors.py": "domain/harvest",
    # Review fixes across the reducer and reconstruct; the subject is the fold.
    "test_aste_review_fixes.py": "domain/harvest",
    # -- domain/news ----------------------------------------------------------------------
    "test_news_mantra.py": "domain/news",
    "test_news_models.py": "domain/news",
    "test_news_prompt.py": "domain/news",
    "test_news_pool.py": "domain/news",
    "test_news_sink.py": "domain/news",
    "test_news_store.py": "domain/news",
    "test_news_store_contract.py": "domain/news",
    "test_news_cost_report.py": "domain/news",
    # -- domain/classic: the Classic (P/D/C/A) engine -------------------------------------
    "test_classic_roles.py": "domain/classic",
    "test_classic_formations.py": "domain/classic",
    "test_classic_optimizer.py": "domain/classic",
    # -- domain/mantra, domain/shared, domain/tokens --------------------------------------
    "test_mantra_grid_gates.py": "domain/mantra",
    "test_club_names.py": "domain/shared",
    "test_league.py": "domain/shared",
    "test_parsing.py": "domain/shared",
    "test_resources.py": "domain/shared",
    "test_token_capture.py": "domain/tokens",
    "test_token_claims.py": "domain/tokens",
    "test_token_crypto.py": "domain/tokens",
    "test_token_status.py": "domain/tokens",
    "test_fantalab_session.py": "domain/tokens",
    # -- application ----------------------------------------------------------------------
    "test_asta_bench.py": "application",
    "test_lineup_planner.py": "application",
    "test_lega_sync.py": "application",
    "test_asta_calibrate.py": "application",
    "test_plan_inputs.py": "application",
    "test_asta_room_resolve.py": "application",
    "test_asta_room_tracker.py": "application",
    "test_asta_copilot_worker.py": "application",
    "test_aste_loader.py": "application",
    "test_aste_load_catchup.py": "application",
    "test_aste_load_windowing.py": "application",
    "test_aste_supervisor.py": "application",
    "test_aste_outage.py": "application",
    "test_news_pipeline.py": "application",
    "test_news_pipeline_limits.py": "application",
    "test_asta_planner.py": "application",
    "test_pricing.py": "application",
    # -- adapters -------------------------------------------------------------------------
    "test_agentkit_env.py": "adapters/agent",
    "test_agentkit_options.py": "adapters/agent",
    "test_agentkit_runner.py": "adapters/agent",
    "test_agentkit_usage.py": "adapters/agent",
    "test_apileague_client.py": "adapters/http",
    "test_apileague_teamlineup.py": "adapters/http",
    "test_fantalab_feed.py": "adapters/http",
    "test_fantalab_rest.py": "adapters/http",
    "test_fantalab_listone.py": "adapters/http",
    "test_fantalab_room.py": "adapters/http",
    "test_fantalab_room_visibility.py": "adapters/http",
    "test_fantalab_lot_router.py": "adapters/http",
    "test_fantalab_rtdb.py": "adapters/http",
    "test_fantalab_write.py": "adapters/http",
    "test_aste_client.py": "adapters/http",
    "test_aste_stream.py": "adapters/http",
    "test_aste_no_sockets.py": "adapters/http",
    # The claim is about the modules that collect, which now span three layers; it is
    # filed with the transport that would carry a filtered query.
    "test_aste_both_formats.py": "adapters/http",
    "test_aste_landing.py": "adapters/files",
    "test_db_boundary.py": "adapters/persistence",
    "test_db_models.py": "adapters/persistence",
    "test_repositories_fake.py": "adapters/persistence",
    "test_upserts.py": "adapters/persistence",
    "test_migrations.py": "adapters/persistence",
    "test_token_store.py": "adapters/tokens",
    "test_token_secrecy.py": "adapters/tokens",
    # About `storage_state.py`, not `domain/asta/state.py`. The filename collision with a
    # domain module is the reason a rule cannot do this.
    "test_state.py": "adapters/browser",
    "test_config_agent_model.py": "adapters",
    # -- interface --------------------------------------------------------------------------
    "test_cli_aste_backfill.py": "interface",
    "test_cli_aste_collect.py": "interface",
    "test_cli_command_set.py": "interface",
    "test_cli_config_check.py": "interface",
    "test_cli_db_check.py": "interface",
    "test_cli_db_price.py": "interface",
    "test_cli_entrypoints.py": "interface",
    "test_lineup_cli.py": "interface",
    "test_cli_fantalab_login.py": "interface",
    "test_cli_login.py": "interface",
    "test_cli_news_fetch.py": "interface",
    "test_cli_token_forget.py": "interface",
    "test_cli_token_status.py": "interface",
    "test_asta_bench_cli.py": "interface",
    "test_asta_id_bridge.py": "interface",
    "test_asta_callable_pool.py": "interface",
    "test_asta_arming.py": "interface",
    "test_room_view.py": "interface",
    "test_options.py": "interface",
    # -- about the repository itself, not about one layer -------------------------------------
    "test_layers.py": ".",
    "test_importgraph.py": ".",
    "test_destinations.py": ".",
    "test_golden.py": ".",
    "test_integration_isolation.py": ".",
    "test_scripts_resolve.py": ".",
    "test_testtree.py": ".",
    "test_docs.py": ".",
    "test_links.py": ".",
}
