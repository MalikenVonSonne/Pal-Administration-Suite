"""Framework-neutral navigation metadata for the offline Pal Admin editor."""

from __future__ import annotations

from dataclasses import dataclass


ROSTER = "roster"
RUNTIME = "runtime"
BLUEPRINTS = "blueprints"
LEDGER = "ledger"
SETTINGS_DATA = "settings_data"


@dataclass(frozen=True)
class NavigationDestination:
    """A destination or action that a UI can render without UI dependencies."""

    key: str
    label: str
    description: str
    order: int
    is_footer: bool = False


PRIMARY_DESTINATIONS = (
    NavigationDestination(
        key=ROSTER,
        label="Roster",
        description="Browse Palbox and party Pals.",
        order=1,
    ),
    NavigationDestination(
        key=RUNTIME,
        label="Live Roster",
        description="Read-only view of Pals exposed by the running game.",
        order=2,
    ),
    NavigationDestination(
        key=BLUEPRINTS,
        label="Blueprints",
        description="Manage reusable Pal templates and presets.",
        order=3,
    ),
    NavigationDestination(
        key=LEDGER,
        label="Ledger",
        description="Review changes, backups, provenance, and validation.",
        order=4,
    ),
)

FOOTER_ACTION = NavigationDestination(
    key=SETTINGS_DATA,
    label="Settings / Data",
    description="Configure build, catalog, validation, and backup data.",
    order=5,
    is_footer=True,
)

# Roster is the least destructive entry point: it starts the editor in a
# browse-first view and does not imply that a save will be written.
SAFE_DEFAULT = ROSTER


@dataclass(frozen=True)
class NavigationModel:
    """The complete offline navigation contract exposed to a UI layer."""

    destinations: tuple[NavigationDestination, ...] = PRIMARY_DESTINATIONS
    footer_action: NavigationDestination = FOOTER_ACTION
    default_key: str = SAFE_DEFAULT

    @property
    def all_destinations(self) -> tuple[NavigationDestination, ...]:
        """Return primary destinations followed by the footer action."""
        return self.destinations + (self.footer_action,)

    def resolve(self, key: str | None) -> NavigationDestination:
        """Resolve a requested key, falling back to the safe default."""
        for destination in self.all_destinations:
            if destination.key == key:
                return destination

        for destination in self.all_destinations:
            if destination.key == self.default_key:
                return destination

        # Keep the fallback safe even if a caller supplies a custom model with
        # an invalid default key.
        return self.destinations[0]


DEFAULT_NAVIGATION = NavigationModel()


def resolve_destination(key: str | None = None) -> NavigationDestination:
    """Resolve a destination using the standard offline navigation model."""
    return DEFAULT_NAVIGATION.resolve(key)


__all__ = [
    "BLUEPRINTS",
    "DEFAULT_NAVIGATION",
    "FOOTER_ACTION",
    "LEDGER",
    "RUNTIME",
    "NavigationDestination",
    "NavigationModel",
    "PRIMARY_DESTINATIONS",
    "ROSTER",
    "SAFE_DEFAULT",
    "SETTINGS_DATA",
    "resolve_destination",
]
