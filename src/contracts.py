"""EPM-017: parcel inbound and recipient pickup contracts."""
from abc import ABC, abstractmethod
from dataclasses import dataclass


class ParcelError(Exception):
    pass


class Conflict(ParcelError):
    pass


class Forbidden(ParcelError):
    pass


@dataclass(frozen=True)
class InboundRequest:
    operator: str
    tracking_no: str
    recipient: str
    location: str
    request_key: str

    def validate(self):
        for value in (self.operator, self.tracking_no, self.recipient,
                      self.location, self.request_key):
            if not isinstance(value, str) or not value.strip():
                raise ValueError('all inbound fields must be nonempty strings')
            if value != value.strip():
                raise ValueError('normalize surrounding whitespace before submission')


@dataclass(frozen=True)
class Parcel:
    id: int
    tracking_no: str
    recipient: str
    location: str
    status: str


class ParcelRepository(ABC):
    @abstractmethod
    def receive(self, request: InboundRequest) -> Parcel:
        """Atomically register one waybill and its audit event."""

    @abstractmethod
    def pickup(self, parcel_id: int, recipient: str) -> Parcel:
        """Recipient-verified transition from stored to picked_up."""


class ParcelService:
    def __init__(self, repository: ParcelRepository):
        self.repository = repository

    def receive(self, request: InboundRequest) -> Parcel:
        request.validate()
        return self.repository.receive(request)

    def pickup(self, parcel_id: int, recipient: str) -> Parcel:
        if type(parcel_id) is not int or parcel_id <= 0:
            raise ValueError('parcel_id must be a positive integer')
        if not isinstance(recipient, str) or not recipient.strip():
            raise ValueError('recipient identity is required')
        return self.repository.pickup(parcel_id, recipient)
