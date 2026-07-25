from types import SimpleNamespace

from pal_editor import safety


def test_process_detection_matches_known_palworld_process(monkeypatch):
    monkeypatch.setattr(safety.sys, "platform", "win32")
    monkeypatch.setattr(
        safety.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout='"explorer.exe","1","Console","1","10,000 K"\n'
            '"Palworld-Win64-Shipping.exe","2","Console","1","20,000 K"\n'
        ),
    )

    status = safety.get_game_safety_status()

    assert status.game_open is True
    assert status.safe_for_offline_editing is False
    assert status.running_processes == ("Palworld-Win64-Shipping.exe",)


def test_process_detection_ignores_unrelated_processes(monkeypatch):
    monkeypatch.setattr(safety.sys, "platform", "win32")
    monkeypatch.setattr(
        safety.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout='"explorer.exe","1"\n'),
    )

    assert safety.get_game_safety_status().safe_for_offline_editing is True


def test_non_windows_safety_check_is_safe_by_default(monkeypatch):
    monkeypatch.setattr(safety.sys, "platform", "linux")

    assert safety.running_process_names() == ()
