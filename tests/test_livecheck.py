"""The livecheck, checked.

A livecheck that passes because it silently skipped half its checks is worse
than no livecheck, so it is run end to end against the contract mock.
"""
import pytest

from iamagnus.livecheck import main


@pytest.mark.unit
class TestLivecheck:
    def test_a_conforming_server_is_a_clean_pass(self, magnus, capsys):
        code = main(["--base-url", magnus.url, "--api-key", "k", "--no-color"])
        out = capsys.readouterr().out
        assert code == 0, out
        assert "all 14 checks passed" in out
        assert "FAIL" not in out

    def test_every_check_actually_runs(self, magnus, capsys):
        main(["--base-url", magnus.url, "--api-key", "k", "--no-color"])
        out = capsys.readouterr().out
        for number in range(1, 15):
            assert f"{number:>2}. " in out, f"check {number} never ran"

    def test_a_bad_key_fails_the_gate(self, magnus, capsys):
        magnus.api_key = "the_right_key"
        code = main(["--base-url", magnus.url, "--api-key", "wrong_key", "--no-color"])
        out = capsys.readouterr().out
        assert code == 1
        assert "does not meet the contract" in out

    def test_a_dead_host_fails_the_gate_without_crashing(self, capsys):
        code = main(["--base-url", "http://127.0.0.1:1", "--api-key", "k", "--no-color"])
        assert code == 1
        assert "FAIL" in capsys.readouterr().out

    def test_an_agent_outside_the_key_is_named(self, magnus, capsys):
        code = main(["--base-url", magnus.url, "--api-key", "k",
                     "--agent", "no_such_agent", "--no-color"])
        out = capsys.readouterr().out
        assert code == 1
        assert "no_such_agent" in out

    def test_a_server_that_accepts_tools_fails_check_11(self, magnus, capsys):
        """The check must fail loudly if the server stops refusing what it documents."""
        import tests.mock_magnus as mock

        original = mock.UNSUPPORTED_PARAMS
        mock.UNSUPPORTED_PARAMS = ()
        try:
            code = main(["--base-url", magnus.url, "--api-key", "k", "--no-color"])
        finally:
            mock.UNSUPPORTED_PARAMS = original
        out = capsys.readouterr().out
        assert code == 1
        assert "documented to refuse" in out

    def test_an_estimated_usage_source_is_called_out(self, magnus, capsys):
        """`estimated` is a character heuristic; the report should say so."""
        magnus.usage_source = "estimated"
        main(["--base-url", magnus.url, "--api-key", "k", "--no-color"])
        out = capsys.readouterr().out
        assert "do not bill on this" in out

    def test_missing_credentials_is_a_usage_error(self, capsys):
        with pytest.raises(SystemExit):
            main([])
