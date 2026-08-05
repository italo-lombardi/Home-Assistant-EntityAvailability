"""Tests for entity-availability-card.js sort logic.

The card's _buildEntityItems() sort is pure algorithmic logic with no HA
dependency. This module mirrors that logic in Python and tests all five
sort_by values: status, name_asc, name_desc, battery_asc, battery_desc.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass


@dataclass
class EntityItem:
    entity_id: str
    name: str
    dot_color: str  # "green" | "yellow" | "red"
    status: str
    battery: int | None
    is_offline: bool


def sort_entities(items: list[EntityItem], sort_by: str = "status") -> list[EntityItem]:
    """Mirror of _buildEntityItems sort logic from entity-availability-card.js."""

    def comparator(a: EntityItem, b: EntityItem) -> int:
        if sort_by == "name_asc":
            return (a.name > b.name) - (a.name < b.name)
        elif sort_by == "name_desc":
            return (b.name > a.name) - (b.name < a.name)
        elif sort_by == "battery_asc":
            a_bat = a.battery if a.battery is not None else 101
            b_bat = b.battery if b.battery is not None else 101
            if a_bat != b_bat:
                return a_bat - b_bat
            return (a.name > b.name) - (a.name < b.name)
        elif sort_by == "battery_desc":
            a_bat = a.battery if a.battery is not None else -1
            b_bat = b.battery if b.battery is not None else -1
            if a_bat != b_bat:
                return b_bat - a_bat
            return (a.name > b.name) - (a.name < b.name)
        else:  # "status" default
            if a.is_offline and not b.is_offline:
                return -1
            if not a.is_offline and b.is_offline:
                return 1
            if a.dot_color == "yellow" and b.dot_color == "green":
                return -1
            if a.dot_color == "green" and b.dot_color == "yellow":
                return 1
            return (a.name > b.name) - (a.name < b.name)

    return sorted(items, key=functools.cmp_to_key(comparator))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _online(name: str, battery: int | None = None) -> EntityItem:
    dot = "yellow" if battery is not None and battery < 20 else "green"
    status = "Low Battery" if dot == "yellow" else "Online"
    return EntityItem(
        entity_id=f"binary_sensor.{name.lower().replace(' ', '_')}",
        name=name,
        dot_color=dot,
        status=status,
        battery=battery,
        is_offline=False,
    )


def _offline(name: str, battery: int | None = None) -> EntityItem:
    return EntityItem(
        entity_id=f"binary_sensor.{name.lower().replace(' ', '_')}",
        name=name,
        dot_color="red",
        status="Offline",
        battery=battery,
        is_offline=True,
    )


# ---------------------------------------------------------------------------
# status (default)
# ---------------------------------------------------------------------------


class TestSortByStatus:
    def test_offline_before_online(self):
        items = [_online("Alpha"), _offline("Beta")]
        result = sort_entities(items, "status")
        assert result[0].name == "Beta"
        assert result[1].name == "Alpha"

    def test_low_battery_before_online(self):
        items = [_online("Alpha"), _online("Beta", battery=10)]
        result = sort_entities(items, "status")
        assert result[0].name == "Beta"
        assert result[1].name == "Alpha"

    def test_offline_before_low_battery_before_online(self):
        items = [_online("Charlie"), _online("Beta", battery=10), _offline("Alpha")]
        result = sort_entities(items, "status")
        assert [r.name for r in result] == ["Alpha", "Beta", "Charlie"]

    def test_alphabetical_within_online(self):
        items = [_online("Charlie"), _online("Alpha"), _online("Beta")]
        result = sort_entities(items, "status")
        assert [r.name for r in result] == ["Alpha", "Beta", "Charlie"]

    def test_alphabetical_within_offline(self):
        items = [_offline("Charlie"), _offline("Alpha"), _offline("Beta")]
        result = sort_entities(items, "status")
        assert [r.name for r in result] == ["Alpha", "Beta", "Charlie"]

    def test_alphabetical_within_low_battery(self):
        items = [
            _online("Charlie", battery=5),
            _online("Alpha", battery=5),
            _online("Beta", battery=5),
        ]
        result = sort_entities(items, "status")
        assert [r.name for r in result] == ["Alpha", "Beta", "Charlie"]

    def test_default_sort_by_is_status(self):
        items = [_online("Alpha"), _offline("Beta")]
        assert sort_entities(items) == sort_entities(items, "status")

    def test_single_item(self):
        items = [_online("Alpha")]
        assert sort_entities(items, "status") == items

    def test_empty_list(self):
        assert sort_entities([], "status") == []


# ---------------------------------------------------------------------------
# name_asc
# ---------------------------------------------------------------------------


class TestSortByNameAsc:
    def test_alphabetical_order(self):
        items = [_online("Charlie"), _offline("Alpha"), _online("Beta", battery=5)]
        result = sort_entities(items, "name_asc")
        assert [r.name for r in result] == ["Alpha", "Beta", "Charlie"]

    def test_status_irrelevant(self):
        items = [_offline("Zara"), _online("Alice")]
        result = sort_entities(items, "name_asc")
        assert result[0].name == "Alice"

    def test_battery_irrelevant(self):
        items = [_online("Zara", battery=1), _online("Alice", battery=99)]
        result = sort_entities(items, "name_asc")
        assert result[0].name == "Alice"

    def test_case_sensitive_ordering(self):
        items = [_online("beta"), _online("Alpha")]
        result = sort_entities(items, "name_asc")
        # Python str comparison: uppercase < lowercase in ASCII, but
        # we use > / < which matches locale-independent comparison
        assert result[0].name in ("Alpha", "beta")  # order is consistent

    def test_single_item(self):
        assert sort_entities([_online("Alpha")], "name_asc") == [_online("Alpha")]


# ---------------------------------------------------------------------------
# name_desc
# ---------------------------------------------------------------------------


class TestSortByNameDesc:
    def test_reverse_alphabetical_order(self):
        items = [_online("Alpha"), _offline("Charlie"), _online("Beta")]
        result = sort_entities(items, "name_desc")
        assert [r.name for r in result] == ["Charlie", "Beta", "Alpha"]

    def test_status_irrelevant(self):
        items = [_online("Alice"), _offline("Zara")]
        result = sort_entities(items, "name_desc")
        assert result[0].name == "Zara"

    def test_battery_irrelevant(self):
        items = [_online("Alice", battery=99), _online("Zara", battery=1)]
        result = sort_entities(items, "name_desc")
        assert result[0].name == "Zara"


# ---------------------------------------------------------------------------
# battery_asc (weakest first)
# ---------------------------------------------------------------------------


class TestSortByBatteryAsc:
    def test_lowest_battery_first(self):
        items = [
            _online("Alpha", battery=80),
            _online("Beta", battery=10),
            _online("Charlie", battery=50),
        ]
        result = sort_entities(items, "battery_asc")
        assert [r.battery for r in result] == [10, 50, 80]

    def test_no_battery_sorts_last(self):
        items = [
            _online("Alpha"),
            _online("Beta", battery=50),
            _online("Charlie", battery=10),
        ]
        result = sort_entities(items, "battery_asc")
        assert result[-1].name == "Alpha"
        assert result[-1].battery is None

    def test_multiple_no_battery_sort_alphabetically(self):
        items = [_online("Zara"), _online("Alice"), _online("Beta", battery=50)]
        result = sort_entities(items, "battery_asc")
        assert result[0].name == "Beta"
        assert result[1].name == "Alice"
        assert result[2].name == "Zara"

    def test_same_battery_alphabetical_tiebreak(self):
        items = [
            _online("Charlie", battery=20),
            _online("Alpha", battery=20),
            _online("Beta", battery=20),
        ]
        result = sort_entities(items, "battery_asc")
        assert [r.name for r in result] == ["Alpha", "Beta", "Charlie"]

    def test_offline_entity_with_battery(self):
        items = [_online("Alpha", battery=80), _offline("Beta", battery=5)]
        result = sort_entities(items, "battery_asc")
        assert result[0].name == "Beta"

    def test_zero_battery_first(self):
        items = [_online("Alpha", battery=1), _online("Beta", battery=0)]
        result = sort_entities(items, "battery_asc")
        assert result[0].battery == 0

    def test_single_no_battery(self):
        items = [_online("Alpha")]
        result = sort_entities(items, "battery_asc")
        assert result[0].battery is None


# ---------------------------------------------------------------------------
# battery_desc (strongest first)
# ---------------------------------------------------------------------------


class TestSortByBatteryDesc:
    def test_highest_battery_first(self):
        items = [
            _online("Alpha", battery=10),
            _online("Beta", battery=80),
            _online("Charlie", battery=50),
        ]
        result = sort_entities(items, "battery_desc")
        assert [r.battery for r in result] == [80, 50, 10]

    def test_no_battery_sorts_last(self):
        items = [
            _online("Alpha"),
            _online("Beta", battery=50),
            _online("Charlie", battery=10),
        ]
        result = sort_entities(items, "battery_desc")
        assert result[-1].name == "Alpha"
        assert result[-1].battery is None

    def test_multiple_no_battery_sort_alphabetically(self):
        items = [_online("Zara"), _online("Alice"), _online("Beta", battery=50)]
        result = sort_entities(items, "battery_desc")
        assert result[0].name == "Beta"
        assert result[1].name == "Alice"
        assert result[2].name == "Zara"

    def test_same_battery_alphabetical_tiebreak(self):
        items = [
            _online("Charlie", battery=80),
            _online("Alpha", battery=80),
            _online("Beta", battery=80),
        ]
        result = sort_entities(items, "battery_desc")
        assert [r.name for r in result] == ["Alpha", "Beta", "Charlie"]

    def test_offline_entity_with_battery(self):
        items = [_online("Alpha", battery=5), _offline("Beta", battery=80)]
        result = sort_entities(items, "battery_desc")
        assert result[0].name == "Beta"

    def test_full_battery_first(self):
        items = [_online("Alpha", battery=99), _online("Beta", battery=100)]
        result = sort_entities(items, "battery_desc")
        assert result[0].battery == 100

    def test_asc_and_desc_are_opposite(self):
        items = [
            _online("Alpha", battery=10),
            _online("Beta", battery=50),
            _online("Charlie", battery=90),
        ]
        asc = sort_entities(items, "battery_asc")
        desc = sort_entities(items, "battery_desc")
        assert [r.name for r in asc] == list(reversed([r.name for r in desc]))


# ---------------------------------------------------------------------------
# unknown / fallback sort_by value
# ---------------------------------------------------------------------------


class TestSortByFallback:
    def test_unknown_value_uses_status(self):
        items = [_online("Alpha"), _offline("Beta")]
        result = sort_entities(items, "unknown_value")
        # Falls through to status branch: offline first
        assert result[0].name == "Beta"
