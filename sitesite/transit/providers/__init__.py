from .base import DetailsProvider, VehicleProvider
from .details import DummyDetailsProvider
from .schedule import ScheduleProvider

__all__ = ["VehicleProvider", "DetailsProvider", "ScheduleProvider", "DummyDetailsProvider"]