from __future__ import annotations

import signal
from threading import Event

from launcher.cli.main import _stop_on_interrupt, build_parser


class _DummyAgent:
    def __init__(self) -> None:
        self.stop_requested = Event()


def test_ctrl_c_routes_to_stop_requested() -> None:
    """Ctrl-C must ask the agent to stop gracefully, not kill the process."""
    agent = _DummyAgent()
    with _stop_on_interrupt(agent):
        signal.raise_signal(signal.SIGINT)
    assert agent.stop_requested.is_set()


def test_sigint_handler_is_restored() -> None:
    before = signal.getsignal(signal.SIGINT)
    with _stop_on_interrupt(_DummyAgent()):
        assert signal.getsignal(signal.SIGINT) is not before
    assert signal.getsignal(signal.SIGINT) is before


def test_parser_tunnel_subcommands() -> None:
    parser = build_parser()
    args = parser.parse_args(["tunnel", "status"])
    assert args.command == "tunnel"
    assert args.tunnel_command == "status"

    args = parser.parse_args(["tunnel", "set", "playit"])
    assert args.provider == "playit"


def test_parser_play_tunnel_override() -> None:
    parser = build_parser()
    assert parser.parse_args(["play", "wid", "--tunnel", "playit"]).tunnel == "playit"
    assert parser.parse_args(["play", "wid"]).tunnel is None
