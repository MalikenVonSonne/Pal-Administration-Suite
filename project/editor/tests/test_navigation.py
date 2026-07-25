from pal_editor.navigation import (
    BLUEPRINTS,
    DEFAULT_NAVIGATION,
    FOOTER_ACTION,
    LEDGER,
    PRIMARY_DESTINATIONS,
    ROSTER,
    RUNTIME,
    SAFE_DEFAULT,
    SETTINGS_DATA,
    resolve_destination,
)


def test_primary_destinations_are_ordered_and_descriptive() -> None:
    assert [destination.key for destination in PRIMARY_DESTINATIONS] == [
        ROSTER,
        RUNTIME,
        BLUEPRINTS,
        LEDGER,
    ]
    assert [destination.order for destination in PRIMARY_DESTINATIONS] == [1, 2, 3, 4]
    assert all(destination.label and destination.description for destination in PRIMARY_DESTINATIONS)


def test_settings_data_is_a_footer_action() -> None:
    assert FOOTER_ACTION.key == SETTINGS_DATA
    assert FOOTER_ACTION.label == "Settings / Data"
    assert FOOTER_ACTION.is_footer is True
    assert FOOTER_ACTION.order == 5
    assert DEFAULT_NAVIGATION.all_destinations[-1] == FOOTER_ACTION


def test_roster_is_the_safe_default_for_missing_or_unknown_selection() -> None:
    assert SAFE_DEFAULT == ROSTER
    assert resolve_destination() == PRIMARY_DESTINATIONS[0]
    assert resolve_destination("not-a-destination") == PRIMARY_DESTINATIONS[0]
    assert resolve_destination(LEDGER).key == LEDGER
