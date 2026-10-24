"""Exceptions of the twin engine. Each one names a concrete, recoverable condition."""
from __future__ import annotations


class TwinError(Exception):
    """Base class of all Phase 3 errors."""


class ModelUnavailableError(TwinError):
    """The selected Phase 2 model artifact is missing, corrupt or incompatible with the configuration."""


class PatientMismatchError(TwinError):
    """A record (clinical or sensor) does not belong to the patient it was supplied for."""


class UnknownTwinError(TwinError, KeyError):
    """No twin exists for the requested patient/twin identifier."""

    def __str__(self) -> str:                       # KeyError would repr() the message
        return str(self.args[0]) if self.args else "unknown twin"


class TwinExistsError(TwinError):
    """A twin for this patient already exists and the configured exists_policy forbids replacing it."""


class StateStoreError(TwinError):
    """Persistent state is missing, corrupt, or cannot be written."""


class StateIncompatibleError(StateStoreError):
    """Persisted state was produced with a different model or state-affecting configuration."""


class ObservationError(TwinError):
    """An observation could not be interpreted at all (as opposed to being rejected by policy)."""
