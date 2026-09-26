"""Domain errors. Messages are safe (and suitably sarky) to show users."""


class DomainError(Exception):
    """A rule was broken."""


class NotFound(DomainError):
    pass


class NotAllowed(DomainError):
    pass


class OverClaimed(DomainError):
    pass
