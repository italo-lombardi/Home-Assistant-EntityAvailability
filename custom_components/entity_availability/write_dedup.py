"""Coordinator-aware entity bases that skip writes when state and attrs are unchanged.

Home Assistant writes a new history row every time ``async_write_ha_state`` is
called. ``CoordinatorEntity`` triggers that call on every coordinator tick
(every ``SCAN_INTERVAL`` seconds), regardless of whether the entity's value
actually changed. For sensors that aggregate offline counts, friendly-name
lists, or per-device dictionaries, the value is identical the vast majority of
ticks, so each refresh produces a redundant recorder row.

The mixin and base classes in this module compare the just-computed
``native_value``/``is_on``, ``extra_state_attributes`` (restricted to the keys
the recorder actually stores — see ``_ea_dedup_attrs``) and ``available``
against the previously published triple and short-circuit
``async_write_ha_state`` when all three match. The first write always goes
through (the cache starts empty), so an entity always publishes an initial
state, and a coordinator failure that flips ``available`` from True to False
still propagates even when the cached value would otherwise look unchanged.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import EntityAvailabilityCoordinator

_UNSET: Any = object()


class WriteDedupMixin:
    """Cache the last published triple and decide whether to write."""

    _ea_last_value: Any = _UNSET
    _ea_last_attrs: Any = _UNSET
    _ea_last_available: Any = _UNSET

    def _ea_current_value(self) -> Any:
        """Return the value subclasses publish (``native_value`` or ``is_on``)."""
        raise NotImplementedError

    def _ea_dedup_attrs(self) -> Any:
        """Return the attrs view used for the dedup comparison.

        Excludes any key the recorder strips so the write decision matches what
        the recorder actually stores. HA strips the *combined* unrecorded set
        (``_entity_component_unrecorded_attributes | _unrecorded_attributes``,
        exposed as the name-mangled ``__combined_unrecorded_attributes``), so we
        read that when present and fall back to the per-instance
        ``_unrecorded_attributes``. Volatile-but-unrecorded maps (``last_seen``,
        ``signal_levels``, ``battery_levels`` …) advance every coordinator tick,
        but the recorder strips them before storing — comparing the full dict
        would force a fresh state row per tick that is byte-identical once
        stored. Comparing the stored view instead means the sensor only writes
        when a *recorded* value changes, and no future volatile-but-unrecorded
        attribute (ours or a component-level one) can re-introduce this bug.
        """
        attrs = getattr(self, "extra_state_attributes", None)
        if not attrs:
            return attrs
        skip = getattr(
            self,
            "_Entity__combined_unrecorded_attributes",
            None,
        )
        if skip is None:
            skip = getattr(self, "_unrecorded_attributes", frozenset())
        if not skip:
            return attrs
        return {k: v for k, v in attrs.items() if k not in skip}

    def _ea_should_write(self) -> bool:
        """Return True when value, attrs, or availability differ from cache."""
        value = self._ea_current_value()
        attrs = self._ea_dedup_attrs()
        available = getattr(self, "available", True)
        if (
            value == self._ea_last_value
            and attrs == self._ea_last_attrs
            and available == self._ea_last_available
        ):
            return False
        self._ea_last_value = value
        self._ea_last_attrs = attrs
        self._ea_last_available = available
        return True

    def _ea_reset_cache(self) -> None:
        """Clear cached publish state so the next call always writes."""
        self._ea_last_value = _UNSET
        self._ea_last_attrs = _UNSET
        self._ea_last_available = _UNSET


class DedupCoordinatorSensor(
    WriteDedupMixin,
    CoordinatorEntity[EntityAvailabilityCoordinator],
    SensorEntity,
):
    """``SensorEntity`` base that skips unchanged coordinator-driven writes."""

    def _ea_current_value(self) -> Any:
        return self.native_value

    @callback
    def _handle_coordinator_update(self) -> None:
        """Write only when the published value, attrs, or availability changed."""
        if self._ea_should_write():
            self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Clear cache on removal so a re-added instance writes its first state."""
        self._ea_reset_cache()
        await super().async_will_remove_from_hass()


class DedupCoordinatorBinarySensor(
    WriteDedupMixin,
    CoordinatorEntity[EntityAvailabilityCoordinator],
    BinarySensorEntity,
):
    """``BinarySensorEntity`` base with the same dedup behavior."""

    def _ea_current_value(self) -> Any:
        return self.is_on

    @callback
    def _handle_coordinator_update(self) -> None:
        """Write only when the published value, attrs, or availability changed."""
        if self._ea_should_write():
            self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Clear cache on removal so a re-added instance writes its first state."""
        self._ea_reset_cache()
        await super().async_will_remove_from_hass()
