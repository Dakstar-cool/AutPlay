"""Low-cardinality Prometheus hooks for the API and CPU worker."""

from __future__ import annotations

from dataclasses import dataclass, field

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client.exposition import generate_latest


@dataclass(slots=True)
class RuntimeMetrics:
    """Own an isolated registry so application factories and tests never collide."""

    registry: CollectorRegistry = field(default_factory=CollectorRegistry)
    _http_requests: Counter = field(init=False, repr=False)
    _http_duration: Histogram = field(init=False, repr=False)
    _readiness: Gauge = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._http_requests = Counter(
            "autplay_http_requests_total",
            "Completed HTTP requests.",
            ("method", "route", "status_code"),
            registry=self.registry,
        )
        self._http_duration = Histogram(
            "autplay_http_request_duration_seconds",
            "HTTP request duration by route template.",
            ("method", "route"),
            registry=self.registry,
        )
        self._readiness = Gauge(
            "autplay_readiness",
            "Whether a required process component is ready.",
            ("component",),
            registry=self.registry,
        )

    def observe_http(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        """Record one request using only bounded, low-cardinality labels."""

        self._http_requests.labels(method, route, str(status_code)).inc()
        self._http_duration.labels(method, route).observe(duration_seconds)

    def set_readiness(self, component: str, *, ready: bool) -> None:
        """Publish the last readiness state of one required component."""

        self._readiness.labels(component).set(1 if ready else 0)

    def render(self) -> tuple[bytes, str]:
        """Return the registry in Prometheus' negotiated text format."""

        return generate_latest(self.registry), CONTENT_TYPE_LATEST


__all__ = ("RuntimeMetrics",)
