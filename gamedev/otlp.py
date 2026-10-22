"""OTLP protobuf export to a locally configured Phoenix collector."""

import urllib.request
from urllib.parse import urlsplit

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span, Status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Telemetry endpoints must not redirect.")


def local_endpoint(url):
    value = urlsplit(url)
    if (
        value.scheme != "http"
        or value.hostname not in ("127.0.0.1", "localhost", "::1")
        or value.username
        or value.password
        or value.query
        or value.fragment
    ):
        raise ValueError("Configure a loopback HTTP telemetry endpoint without credentials or query parameters.")
    return url


def attribute(key, value):
    if isinstance(value, bool):
        wrapped = AnyValue(bool_value=value)
    elif isinstance(value, int):
        wrapped = AnyValue(int_value=value)
    elif isinstance(value, float):
        wrapped = AnyValue(double_value=value)
    else:
        wrapped = AnyValue(string_value=str(value)[:1000])
    return KeyValue(key=key, value=wrapped)


def encode_spans(spans):
    result = []
    for span in spans:
        values = {**span.attributes, "openinference.span.kind": span.kind, "hq.run.id": span.run_id}
        result.append(
            Span(
                trace_id=bytes.fromhex(span.trace_id),
                span_id=bytes.fromhex(span.span_id),
                parent_span_id=bytes.fromhex(span.parent_id) if span.parent_id else b"",
                name=span.name,
                kind=Span.SPAN_KIND_INTERNAL,
                start_time_unix_nano=int(span.started_at.timestamp() * 1_000_000_000),
                end_time_unix_nano=int(span.finished_at.timestamp() * 1_000_000_000),
                attributes=[attribute(key, value) for key, value in values.items()],
                status=Status(code=Status.STATUS_CODE_ERROR if span.failed else Status.STATUS_CODE_OK),
            )
        )
    resource = Resource(
        attributes=[
            attribute("service.name", "game-dev-hq-worker"),
            attribute("openinference.project.name", "Game Dev HQ"),
        ]
    )
    return ExportTraceServiceRequest(
        resource_spans=[ResourceSpans(resource=resource, scope_spans=[ScopeSpans(spans=result)])]
    ).SerializeToString()


def export_spans(spans, endpoint, api_key):
    local_endpoint(endpoint)
    request = urllib.request.Request(
        endpoint, encode_spans(spans), {"Content-Type": "application/x-protobuf", "Authorization": "Bearer " + api_key}
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=5) as response:
        body = response.read(65536)
        if response.status != 200:
            raise ValueError("Phoenix did not accept the telemetry batch.")
        accepted = ExportTraceServiceResponse.FromString(body)
        if accepted.partial_success.rejected_spans:
            raise ValueError("Phoenix rejected spans from the batch.")
        return len(body)
